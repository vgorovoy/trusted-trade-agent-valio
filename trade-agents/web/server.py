"""Human-facing demo UI for the Valio Aimo agent gateway.

Run from trade-agents/: TTA_DEMO_WALLET=1 uv run uvicorn web.server:app --port 8000
The API is mounted at /api so the browser stays on one origin.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from trade_agents.api import app as gateway_app
from trade_agents.api import gateway
from trade_agents.buyer_agent.agent import run_request
from trade_agents.buyer_agent.profiles import PROFILES, get_profile, sign_mandate
from trade_agents.buyer_agent.tools import InProcessTransport
from trade_agents.config import get_settings
from trade_agents.signing import SignatureError, verify_offer, verify_receipt

ROOT = Path(__file__).parent
app = FastAPI(title="Valio Aimo human window")


class AgentRequest(BaseModel):
    request: str = Field(min_length=5, max_length=2000)
    buyer: str = "hotel"
    mode: str = "autonomous"


class EvidenceTransport(InProcessTransport):
    """Keep the original signed documents for the human review screen."""

    def __init__(self) -> None:
        super().__init__(gateway())
        self.offers: dict[str, dict] = {}

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
        path = get_settings().state_dir / "web_evidence" / f"{order['order_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, ensure_ascii=False))
        return order


@app.post("/ui/agent/run")
async def run_agent(body: AgentRequest) -> dict:
    if body.buyer not in PROFILES:
        raise HTTPException(400, "unknown demo buyer")
    if body.mode not in ("advise", "confirm_cart", "autonomous"):
        raise HTTPException(400, "unknown autonomy mode")
    try:
        profile = get_profile(body.buyer)
        profile.mandate = sign_mandate(profile.mandate.model_copy(update={
            "autonomy_mode": body.mode, "signature": None,
        }))
        result = await run_in_threadpool(
            run_request, body.request, profile, transport=EvidenceTransport()
        )
        return result.to_dict()
    except Exception as exc:
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


@app.get("/ui/overview")
def overview() -> dict:
    gw = gateway()
    orders = gw.store.list_orders()
    jwks = gw.jwks()
    return {
        "orders": [{**order, "verification": _verification(order, jwks)} for order in orders],
        "audit": gw.audit(),
        "jwks": jwks,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/app.css")
def css() -> FileResponse:
    return FileResponse(ROOT / "app.css", media_type="text/css")


@app.get("/app.js")
def js() -> FileResponse:
    return FileResponse(ROOT / "app.js", media_type="text/javascript")


app.mount("/api", gateway_app)
