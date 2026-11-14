"""A scenario's world: isolated gateway over a synthetic catalogue, a fake Valio API that
answers from the world file, a frozen clock and deterministic ids.

Also `Cassette`: record / replay of Valio API answers, so a run against the live mock
API can be repeated byte for byte later (cut-offs move on, stock changes).
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .. import offers as offers_mod
from .. import signing as signing_mod
from .. import store as store_mod
from ..buyer_agent import profiles as profiles_mod
from ..buyer_agent import tools as tools_mod
from ..buyer_agent.profiles import PROFILES, BuyerProfile
from ..catalog import Catalog, build_catalog
from ..config import TZ, Settings, get_settings
from ..policy import Mandate
from ..service import Gateway, OrderLineIn
from ..store import Store
from ..valio_client import ValioClient
from .scenario import BENCH_DIR, Scenario, WorldSpec

UNLIMITED_STOCK = 9_999_999_999.0
VAT = 13.5

# Modules that read the wall clock through their own `datetime` name (the menu reader is
# not part of the bench; the agent gets `today` explicitly).
_CLOCK_MODULES = (store_mod, signing_mod, offers_mod, profiles_mod)
# Modules that mint offer / order / session ids through their own `uuid` name. The global
# `uuid.uuid4` is left alone: provider SDKs draw from it per request (the openai SDK's
# idempotency keys), and recording and replaying a run call the model a different number
# of times, so a shared sequence would hand out different offer ids on replay.
_ID_MODULES = (store_mod, tools_mod)


class SeededUuid:
    """Stands in for the `uuid` module: `uuid4()` is a seeded sequence, the rest is real."""

    def __init__(self, seed: str) -> None:
        self._rng = random.Random(seed)

    def uuid4(self) -> uuid.UUID:
        return uuid.UUID(int=self._rng.getrandbits(128), version=4)

    def __getattr__(self, name: str) -> Any:
        return getattr(uuid, name)


@contextmanager
def frozen(at: datetime, seed: str) -> Iterator[None]:
    """Freeze `datetime.now` for the gateway and the agent, and make `uuid4` in the
    gateway and agent-tool modules (offer, order and session ids) a seeded sequence, so a
    scenario replays identically."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    ids = SeededUuid(seed)
    saved = [(m, m.datetime) for m in _CLOCK_MODULES]
    saved_uuid = [(m, m.uuid) for m in _ID_MODULES]
    demo_clock = os.environ.pop("TTA_DEMO_CLOCK", None)
    try:
        for m, _ in saved:
            m.datetime = FrozenDatetime  # type: ignore[attr-defined]
        for m, _ in saved_uuid:
            m.uuid = ids  # type: ignore[attr-defined]
        yield
    finally:
        if demo_clock is not None:
            os.environ["TTA_DEMO_CLOCK"] = demo_clock
        for m, dt in saved:
            m.datetime = dt  # type: ignore[attr-defined]
        for m, u in saved_uuid:
            m.uuid = u  # type: ignore[attr-defined]


# -- synthetic catalogue and API ---------------------------------------------------------

def product_record(pid: str, p: dict[str, Any]) -> dict[str, Any]:
    """A world entry as a Valio product_data.json record (what `build_catalog` reads)."""
    names = [{"value": p["name_fi"], "language": "fi"}]
    if p["name_en"]:
        names.append({"value": p["name_en"], "language": "en"})
    allergens = ([{"id": a, "unit": "CONTAINS"} for a in p["allergens"]]
                 + [{"id": a, "unit": "MAY_CONTAIN"} for a in p["may_contain"]])
    return {
        "id": pid, "salesUnit": p["unit"], "baseUnit": "ST", "category": "1",
        "allowedLotSize": p["lot"], "deleted": False, "vendorName": "VALIO OY",
        "countryOfOrigin": p["country"],
        "units": [{"unitId": p["unit"], "sizeInBaseUnits": p["base_per_unit"]}],
        "deposits": [],
        "substitutions": [{"product": pid, "substituteProduct": s} for s in p["substitutes"]],
        "synkkaData": {
            "names": names,
            "keyIngredients": [{"value": p["ingredients"], "language": "fi"}],
            "classifications": [
                {"type": "ENUM", "name": "allergen", "values": allergens},
                {"type": "ENUM", "name": "nutritionalClaim",
                 "values": [{"id": c, "unit": "FREE_FROM"} for c in p["claims"]]},
                {"type": "ENUM", "name": "packagingMarkedLabel", "values": []},
            ],
        },
    }


class WorldApi:
    """Answers /api/availability and /api/price from the world file."""

    def __init__(self, world: WorldSpec) -> None:
        self.world = world
        self.calls = 0

    def availability(self, pid: str) -> dict[str, Any]:
        p = self.world.products.get(pid)
        if p is None:
            return {"productId": pid, "salesBlock": False, "scheduleLines": []}
        stock = UNLIMITED_STOCK if p["stock"] == "unlimited" else float(p["stock"])
        return {"productId": pid, "closingTime": p["cutoff"] or self.world.cutoff,
                "logisticModel": "Stock", "salesBlock": bool(p["sales_block"]),
                "scheduleLines": [{
                    "salesBlock": bool(p["sales_block"]), "availableAmount": stock,
                    "availableDate": p["available"] or self.world.delivery.isoformat(),
                    "salesUnit": p["unit"]}]}

    def price(self, pid: str) -> dict[str, Any]:
        p = self.world.products.get(pid)
        if p is None or p["price"] is None or p.get("price_error"):
            return {"productId": pid, "error": True, "netPriceFound": False}
        out = {"productId": pid, "quantity": 1.0, "netPriceFound": False, "error": False,
               "catalogPrice": {"value": p["price"],
                                "valueWithTax": round(p["price"] * (1 + VAT / 100), 2)},
               "tax": {"taxRate": VAT}}
        if p["comparison"] is not None:
            out["comparisonPrice"] = {"value": p["comparison"]}
            out["comparisonUnit"] = p["comparison_unit"]
        return out

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        ids = json.loads(request.content)["products"]
        if request.url.path == "/api/availability":
            return httpx.Response(200, json={"products": [self.availability(i) for i in ids]})
        if request.url.path == "/api/price":
            return httpx.Response(200, json={"products": [self.price(i) for i in ids]})
        return httpx.Response(404, json={"error": f"no such endpoint {request.url.path}"})


class ApiLog(httpx.BaseTransport):
    """Every product id sent to /api/price or /api/availability, in order. Valio's rule:
    query the APIs only when needed, never for the whole catalogue."""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self.inner = inner
        self.calls: list[tuple[str, list[str]]] = []   # (path, product ids)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/price", "/api/availability"):
            ids = [str(i) for i in json.loads(request.content).get("products", [])]
            self.calls.append((request.url.path, ids))
        return self.inner.handle_request(request)

    def products(self, since: int = 0) -> set[str]:
        return {i for _, ids in self.calls[since:] for i in ids}


class CassetteMiss(RuntimeError):
    pass


class Cassette(httpx.BaseTransport):
    """Record / replay of HTTP answers keyed by method, path and canonical JSON body.

    mode `replay`: answers only from the file, a miss raises `CassetteMiss`.
    mode `record`: forwards to `inner` and stores every answer (`save()` writes the file).
    """

    def __init__(self, path: Path, mode: str = "replay",
                 inner: httpx.BaseTransport | None = None) -> None:
        if mode not in ("replay", "record"):
            raise ValueError(f"unknown cassette mode {mode!r}")
        self.path, self.mode = path, mode
        self.inner = inner or httpx.HTTPTransport()
        self.tape: dict[str, dict[str, Any]] = (
            json.loads(path.read_text()) if path.exists() else {})

    @staticmethod
    def key(request: httpx.Request) -> str:
        body = request.content or b""
        try:
            body = json.dumps(json.loads(body), sort_keys=True, separators=(",", ":")).encode()
        except ValueError:
            pass
        digest = hashlib.sha256(body).hexdigest()[:16]
        return f"{request.method} {request.url.path} {digest}"

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        k = self.key(request)
        if self.mode == "replay":
            hit = self.tape.get(k)
            if hit is None:
                raise CassetteMiss(f"{k} not in {self.path.name}; record it with --record")
            return httpx.Response(hit["status"], json=hit["json"])
        r = self.inner.handle_request(request)
        r.read()
        self.tape[k] = {"status": r.status_code, "json": r.json()}
        return httpx.Response(r.status_code, json=self.tape[k]["json"])

    def save(self) -> None:
        if self.mode == "record":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.tape, indent=1, sort_keys=True,
                                            ensure_ascii=False))


# -- the world ---------------------------------------------------------------------------

def cassette_path(scenario_id: str, bench_dir: Path = BENCH_DIR) -> Path:
    return bench_dir / "cassettes" / f"{scenario_id}.json"


class World:
    """One scenario's isolated gateway and buyer. Use as a context manager: it freezes
    the clock and points `TTA_DATA_DIR` at a temporary directory for its lifetime.

    World `live`: the real catalogue (`tta ingest`) and Valio API answers from the
    scenario's cassette; `record=True` calls the live API at the real time and stores
    the answers and that moment, so later runs replay the same world.
    """

    def __init__(self, scenario: Scenario, *, settings: Settings | None = None,
                 record: bool = False, live_transport: httpx.BaseTransport | None = None,
                 bench_dir: Path = BENCH_DIR) -> None:
        self.scenario = scenario
        self.spec = scenario.world
        self._base = settings or get_settings()
        self._stack: list[Any] = []
        self.setup_order_ids: list[str] = []
        self.record = record
        self.cassette: Cassette | None = None
        if self.spec.name == "live":
            self.cassette = Cassette(cassette_path(scenario.id, bench_dir),
                                     "record" if record else "replay", live_transport)
            if record:
                at = datetime.now(TZ).replace(microsecond=0)
                self.cassette.tape["__meta__"] = {"status": 0, "json": {"now": at.isoformat()}}
            elif "__meta__" not in self.cassette.tape:
                raise CassetteMiss(f"no cassette for live scenario {scenario.id}: "
                                   "record it with `tta bench --record`")
            at = datetime.fromisoformat(self.cassette.tape["__meta__"]["json"]["now"])
            self.spec = replace(self.spec, now=at,
                                delivery=profiles_mod.next_weekday(0, at.date()))

    def __enter__(self) -> World:
        tmp = tempfile.TemporaryDirectory(prefix=f"tta-bench-{self.scenario.id}-")
        self._stack.append(tmp)
        self.dir = Path(tmp.name)
        self._old_env = os.environ.get("TTA_DATA_DIR")
        os.environ["TTA_DATA_DIR"] = str(self.dir)   # demo buyer keys land here, not in data/
        clock = frozen(self.spec.now, self.scenario.id)
        clock.__enter__()
        self._stack.append(clock)

        self.settings = replace(self._base, data_dir=self.dir,
                                valio_api_key=self._base.valio_api_key or "bench")
        if self.cassette is not None:
            self.api_log = ApiLog(self.cassette)
            client = ValioClient(self.settings, transport=self.api_log)
            self.gateway = Gateway(self.settings, catalog=Catalog(self._base.catalog_db),
                                   client=client, store=Store(self.settings.state_db))
            return self
        src = self.dir / "products.json"
        src.write_text(json.dumps([product_record(pid, p)
                                   for pid, p in self.spec.products.items()]))
        rel = self.dir / "reliability.csv"
        rel.write_text("productId,deliveryReliability\n" + "".join(
            f"{pid},{p['reliability']}\n" for pid, p in self.spec.products.items()
            if p["reliability"] is not None))
        build_catalog(self.settings.catalog_db, src, rel, None)
        self.api = WorldApi(self.spec)
        self.api_log = ApiLog(httpx.MockTransport(self.api))
        client = ValioClient(self.settings, transport=self.api_log)
        self.gateway = Gateway(self.settings, catalog=Catalog(self.settings.catalog_db),
                               client=client, store=Store(self.settings.state_db))
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.cassette is not None:
            self.cassette.save()
        for item in reversed(self._stack):
            if isinstance(item, tempfile.TemporaryDirectory):
                item.cleanup()
            else:
                item.__exit__(None, None, None)
        self._stack.clear()
        if self._old_env is None:
            os.environ.pop("TTA_DATA_DIR", None)
        else:
            os.environ["TTA_DATA_DIR"] = self._old_env

    @property
    def today(self) -> date:
        return self.spec.now.date()

    def profile(self, model: str = "bench") -> BuyerProfile:
        """The scenario's buyer: base profile, mandate overrides, signed by the buyer key in
        this world's registry, plus a Valio-issued passport unless the scenario has none."""
        base = PROFILES[self.scenario.buyer]()
        fields = {"valid_until": self.spec.now + timedelta(days=30), **self.scenario.mandate}
        mandate = Mandate.model_validate({**base.mandate.model_dump(), **fields,
                                          "signature": None})
        mandate = mandate.sign(self.gateway.buyers.demo_key(mandate.buyer_id))
        passport = None
        if self.scenario.passport:
            passport = self.gateway.issue_passport(mandate.agent_id, model, "bench",
                                                   self.scenario.passport)
        return replace(base, mandate=mandate, default_delivery=self.spec.delivery,
                       passport=passport)

    def place_prior_orders(self, profile: BuyerProfile) -> None:
        """Orders the buyer already made in the spend window (split-order scenarios)."""
        for i, o in enumerate(self.scenario.prior_orders):
            offer = self.gateway.quote([str(o["product"])], self.spec.delivery,
                                       agent_id=profile.mandate.agent_id)[0]
            order = self.gateway.place_order(
                profile.mandate, [OrderLineIn(offer_id=offer["offer_id"],
                                              quantity=float(o["quantity"]))],
                f"setup-{i}", "earlier order (scenario setup)",
                agent_passport=profile.passport)
            self.setup_order_ids.append(order["order_id"])

    def agent_orders(self) -> list[dict[str, Any]]:
        return [o for o in self.gateway.store.list_orders()
                if o["order_id"] not in self.setup_order_ids]
