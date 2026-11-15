"""Human-facing demo UI for the Valio Aimo agent gateway.

Run from trade-agents/: TTA_DEMO_WALLET=1 uv run uvicorn web.server:app --port 8000
The API is mounted at /api so the browser stays on one origin.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from trade_agents.api import app as gateway_app
from trade_agents.api import gateway
from trade_agents.buyer_agent.agent import BuyerAgent, run_request
from trade_agents.buyer_agent.profiles import PROFILES, get_profile, sign_mandate
from trade_agents.buyer_agent.tools import InProcessTransport
from trade_agents.config import get_settings
from trade_agents.signing import SignatureError, verify_offer, verify_receipt
from web.live import router as live_router
from web.live_models import MODELS, PublicModel, bounded_model, validate_selection
from web.menu import router as menu_router

ROOT = Path(__file__).parent
app = FastAPI(title="Valio Aimo human window")
app.include_router(live_router)
app.include_router(menu_router)


class AgentRequest(BaseModel):
    request: str = Field(min_length=5, max_length=2000)
    buyer: str = "hotel"
    mode: str = "autonomous"
    # Optional live buyer model for this one request. None keeps the server default.
    provider: str | None = Field(default=None, max_length=40)


# Human-facing names for the fixed live models; the model ids stay fixed in live_models.
BUYER_MODEL_LABELS = {
    "gemini": "Gemini 3.5 Flash",
    "verda-nemotron": "Nemotron 3.5 Super VL on Verda",
    "verda": "Kimi K3.1 Pro on Verda",
    "verda-deepseek": "DeepSeek 4.1 Flash on Verda",
    "nvidia": "Nemotron 3.5 Super · NVIDIA API",
}


PREFERRED_BUYER_MODEL = "verda-nemotron"


def _configured(provider: str) -> bool:
    try:
        validate_selection(provider, MODELS[provider][1])
    except ValueError:
        return False
    return True


def _preferred_default() -> str | None:
    """Nemotron on Verda is the Exchange default when this server has it configured.

    Decided before any call, from configuration only: a failed Nemotron call is reported as a
    failure, never silently retried on another model."""
    return PREFERRED_BUYER_MODEL if _configured(PREFERRED_BUYER_MODEL) else None


def _default_buyer_model() -> dict:
    """Describe the default Exchange buyer: Nemotron on Verda if configured, else the server
    default path (TTA_LLM_PROVIDER / TTA_LLM_MODEL; Gemini 3.5 Flash on the hosted demo)."""
    preferred = _preferred_default()
    if preferred:
        return {"id": "", "provider": preferred, "model": MODELS[preferred][1],
                "label": BUYER_MODEL_LABELS[preferred]}
    provider = (os.getenv("TTA_LLM_PROVIDER") or "").lower()
    model = os.getenv("TTA_LLM_MODEL") or ""
    if provider == "gemini":
        model = model or MODELS["gemini"][1]
        label = BUYER_MODEL_LABELS["gemini"] if model == MODELS["gemini"][1] else f"Gemini · {model}"
    elif provider == "offline":
        model, label = "offline", "Offline rule-based agent"
    elif provider:
        label = f"{provider} · {model}" if model else provider
    else:
        label = "Server default model"
    return {"id": "", "provider": provider or "default", "model": model, "label": label}


def _buyer_model_options() -> list[dict]:
    """Only providers whose fixed live model is enabled and has a server-side credential."""
    if os.getenv("TTA_LIVE_BENCH") != "1":
        return []
    default = _default_buyer_model()
    return [{"id": key, "provider": key, "model": value[1],
             "label": BUYER_MODEL_LABELS.get(key, value[0])}
            for key, value in MODELS.items()
            if _configured(key) and not (key == default["provider"] and value[1] == default["model"])]


def _run_selected_model(request: str, profile, provider: str, transport) -> object:
    """Same bounds as the hosted live buyer: no retries, 45 s, 4096 tokens, 8 steps."""
    inner = bounded_model(provider, MODELS[provider][1])
    try:
        llm = PublicModel(inner, lambda *args: None)
        return BuyerAgent(profile, llm=llm, transport=transport, max_steps=8,
                          max_tokens=40000).run(request)
    finally:
        inner.client.close()


class EvidenceTransport(InProcessTransport):
    """Keep the original signed documents for the human review screen."""

    def __init__(self, buyer_model: dict | None = None) -> None:
        super().__init__(gateway())
        self.offers: dict[str, dict] = {}
        self.buyer_model = buyer_model

    def quote(self, *args, **kwargs):
        offers = super().quote(*args, **kwargs)
        self.offers.update({o["offer_id"]: o for o in offers})
        return offers

    def plan(self, *args, **kwargs):
        result = super().plan(*args, **kwargs)
        for option in result.get("options", []):
            self.offers.update({line["offer_id"]: line for line in option.get("lines", [])})
        return result

    def place_order(self, mandate, lines, idempotency_key, rationale, agent_passport=None):
        order = super().place_order(mandate, lines, idempotency_key, rationale, agent_passport)
        evidence = {"mandate": mandate, "offers": {
            line["offer_id"]: self.offers[line["offer_id"]]
            for line in lines if line["offer_id"] in self.offers
        }}
        if self.buyer_model:
            evidence["buyer_model"] = self.buyer_model
        path = get_settings().state_dir / "web_evidence" / f"{order['order_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, ensure_ascii=False))
        return order


@app.get("/ui/agent/models")
def agent_models() -> dict:
    """Buyer models a visitor may pick for a product request; never exposes credentials."""
    return {"default": _default_buyer_model(), "options": _buyer_model_options()}


@app.post("/ui/agent/run")
async def run_agent(body: AgentRequest) -> dict:
    if body.buyer not in PROFILES:
        raise HTTPException(400, "unknown demo buyer")
    if body.mode not in ("advise", "confirm_cart", "autonomous"):
        raise HTTPException(400, "unknown autonomy mode")
    selected = None
    provider = body.provider
    if provider is None:
        provider = _preferred_default()  # None keeps the unchanged server default path
    if provider is not None:
        if provider not in MODELS:
            raise HTTPException(400, "unknown buyer model")
        try:
            validate_selection(provider, MODELS[provider][1])
        except ValueError as exc:
            # Never fall back silently: the visitor asked for a model that is not available.
            raise HTTPException(400, "buyer model is not configured on this server") from exc
        selected = {"provider": provider, "model": MODELS[provider][1],
                    "label": BUYER_MODEL_LABELS.get(provider, MODELS[provider][0])}
    try:
        profile = get_profile(body.buyer)
        profile.mandate = sign_mandate(profile.mandate.model_copy(update={
            "autonomy_mode": body.mode, "signature": None,
        }))
        if selected is None:
            default = _default_buyer_model()
            transport = EvidenceTransport({k: default[k] for k in ("provider", "model", "label")})
            result = await run_in_threadpool(
                run_request, body.request, profile, transport=transport
            )
        else:
            transport = EvidenceTransport(selected)
            result = await run_in_threadpool(
                _run_selected_model, body.request, profile, provider, transport
            )
        out = result.to_dict()
        out["buyer_model"] = transport.buyer_model
        return out
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


class ChoiceDecision(BaseModel):
    approver: str = Field(min_length=1, max_length=200)
    selection: int | None = Field(default=None, ge=0, le=1, strict=True)
    note: str = Field(default="", max_length=2000)


@app.post("/ui/choices/{choice_id}/decision")
def decide_choice(choice_id: str, body: ChoiceDecision) -> dict:
    """Human demo wallet only; never a tool callable by the purchasing model."""
    if os.getenv("TTA_DEMO_WALLET") != "1":
        raise HTTPException(404, "demo wallet is disabled (set TTA_DEMO_WALLET=1)")
    from trade_agents.choices import ChoiceService
    from trade_agents.service import GatewayError

    try:
        gw = gateway()
        choice = ChoiceService(gw).decide(choice_id, body.approver, body.selection, body.note)
        if choice.get("order"):
            document, _ = gw.store.get_offer(choice_id)
            selected = choice["options"][choice["selection"]]
            evidence = {"mandate": document["mandate"], "offers": {
                line["offer_id"]: line for line in selected["lines"]}}
            path = get_settings().state_dir / "web_evidence" / f"{choice['order']['order_id']}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(evidence, ensure_ascii=False))
        return choice
    except GatewayError as exc:
        raise HTTPException(400, str(exc)) from exc


def _verification(order: dict, jwks: dict) -> dict:
    data = order["data"]
    evidence_path = get_settings().state_dir / "web_evidence" / f"{order['order_id']}.json"
    evidence = {}
    try:
        evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
        from trade_agents.policy import Mandate
        from trade_agents.signing import BuyerRegistry
        mandate_valid = Mandate.model_validate(evidence["mandate"]).verify(
            BuyerRegistry(get_settings().buyers_dir).jwks(order["buyer_id"])
        ) if evidence.get("mandate") else None
    except Exception:
        mandate_valid = False
    offers = []
    for line in data.get("lines", []):
        offer = evidence.get("offers", {}).get(line.get("offer_id"))
        if not offer:
            offers.append(None)
            continue
        try:
            claims = verify_offer(offer, jwks)
            snapshot = line.get("offer") or {}
            offers.append(
                claims.get("offer_id") == line.get("offer_id")
                and claims.get("product_id") == snapshot.get("product_id")
                and claims.get("unit_price") == snapshot.get("unit_price")
            )
        except (SignatureError, KeyError, ValueError):
            offers.append(False)
    receipt = data.get("receipt")
    receipt_valid = None
    if receipt:
        try:
            claims = verify_receipt(receipt, jwks)
            receipt_valid = claims.get("order_id") == order["order_id"] and claims.get(
                "total_eur"
            ) == order["total_eur"]
        except (SignatureError, KeyError, ValueError):
            receipt_valid = False
    return {"mandate": mandate_valid, "offers": offers, "receipt": receipt_valid}


def _buyer_model_of(order: dict) -> dict | None:
    """Which model proposed the order, from the server-written web evidence (if any)."""
    path = get_settings().state_dir / "web_evidence" / f"{order['order_id']}.json"
    try:
        model = json.loads(path.read_text()).get("buyer_model") if path.exists() else None
    except (OSError, ValueError):
        return None
    if not isinstance(model, dict):
        return None
    return {k: str(model.get(k) or "")[:120] for k in ("provider", "model", "label")}


@app.get("/ui/overview")
def overview() -> dict:
    settings = get_settings()
    if not settings.catalog_db.is_file():
        return {
            "orders": [], "choices": [], "audit": {"entries": [], "chain_valid": None}, "jwks": {},
            "setup_required": "The product catalogue is not installed. Run `tta ingest` "
                              "after configuring the Valio API key to use Exchange. "
                              "Live bench works without the catalogue.",
        }
    gw = gateway()
    from trade_agents.choices import ChoiceService

    orders = gw.store.list_orders()
    jwks = gw.jwks()
    return {
        "orders": [{**order, "verification": _verification(order, jwks),
                    "buyer_model": _buyer_model_of(order)} for order in orders],
        "choices": ChoiceService(gw).list(),
        "audit": gw.audit(),
        "jwks": jwks,
        "demo_clock": {"frozen": bool(os.getenv("TTA_DEMO_CLOCK")),
                       "business_time": os.getenv("TTA_DEMO_CLOCK")},
        # Which store holds offers, orders and the audit chain (never the connection URL).
        "storage": {"backend": "postgres" if type(gw.store).__name__ == "PgStore" else "sqlite",
                    "schema": getattr(gw.store, "schema", None)},
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/index.html")
def index_file() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/how-it-works")
def how_it_works() -> FileResponse:
    return FileResponse(ROOT / "how-it-works.html")


@app.get("/live")
def live() -> FileResponse:
    return FileResponse(ROOT / "live.html")


@app.get("/live.css")
def live_css() -> FileResponse:
    return FileResponse(ROOT / "live.css", media_type="text/css")


@app.get("/live.js")
def live_js() -> FileResponse:
    return FileResponse(ROOT / "live.js", media_type="text/javascript")


@app.get("/app.css")
def css() -> FileResponse:
    return FileResponse(ROOT / "app.css", media_type="text/css")


@app.get("/app.js")
def js() -> FileResponse:
    return FileResponse(ROOT / "app.js", media_type="text/javascript")


@app.get("/menu.js")
def menu_js() -> FileResponse:
    return FileResponse(ROOT / "menu.js", media_type="text/javascript")


@app.get("/menu.css")
def menu_css() -> FileResponse:
    return FileResponse(ROOT / "menu.css", media_type="text/css")


@app.get("/models")
def models_page() -> FileResponse:
    return FileResponse(ROOT / "models.html")


@app.get("/models.js")
def models_js() -> FileResponse:
    return FileResponse(ROOT / "models.js", media_type="text/javascript")


@app.get("/models.css")
def models_css() -> FileResponse:
    return FileResponse(ROOT / "models.css", media_type="text/css")


@app.get("/data/model-comparison-20261114-v1.json")
def model_comparison_data() -> FileResponse:
    # Deliberately one fixed sanitized report, never a generic data-folder mount.
    return FileResponse(ROOT / "data/model-comparison-20261114-v1.json", media_type="application/json")


@app.get("/demo-proof.js")
def demo_proof_js() -> FileResponse:
    return FileResponse(ROOT / "demo-proof.js", media_type="text/javascript")


@app.get("/demo-proof.css")
def demo_proof_css() -> FileResponse:
    return FileResponse(ROOT / "demo-proof.css", media_type="text/css")


@app.get("/data/core-comparison-20261114-v1.json")
def core_comparison_data() -> FileResponse:
    return FileResponse(ROOT / "data/core-comparison-20261114-v1.json", media_type="application/json")

@app.get("/data/full-comparison-20261114-v4.json")
def full_comparison_data() -> FileResponse:
    return FileResponse(ROOT / "data/full-comparison-20261114-v4.json", media_type="application/json")


app.mount("/api", gateway_app)
