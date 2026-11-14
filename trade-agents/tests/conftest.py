from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from trade_agents.catalog import Catalog, build_catalog
from trade_agents.config import TZ, Settings
from trade_agents.policy import Mandate
from trade_agents.service import Gateway
from trade_agents.store import Store
from trade_agents.valio_client import ValioClient


def product(pid, name_fi, name_en, sales_unit="ST", units=None, allergens=(), claims=(),
            substitutions=None, lot=1.0, ingredients="MAITO"):
    return {
        "id": pid, "salesUnit": sales_unit, "baseUnit": "ST", "category": "1",
        "allowedLotSize": lot, "deleted": False, "vendorName": "VALIO OY",
        "countryOfOrigin": "fi",
        "units": units or [{"unitId": sales_unit, "sizeInBaseUnits": 1.0}],
        "deposits": [],
        "substitutions": substitutions,
        "synkkaData": {
            "names": [{"value": name_fi, "language": "fi"}, {"value": name_en, "language": "en"}],
            "keyIngredients": [{"value": ingredients, "language": "fi"}],
            "classifications": [
                {"type": "ENUM", "name": "allergen",
                 "values": [{"id": a, "unit": "CONTAINS"} for a in allergens]},
                {"type": "ENUM", "name": "nutritionalClaim",
                 "values": [{"id": c, "unit": "FREE_FROM"} for c in claims]},
                {"type": "ENUM", "name": "packagingMarkedLabel", "values": []},
            ],
        },
    }


PRODUCTS = [
    # 1 L cartons sold per piece, plenty in stock, cheap
    product("100", "Maito 1 l laktoositon", "Milk 1 l lactose-free", allergens=["AM"],
            claims=["FREE_FROM_LACTOSE"]),
    # 10 x 1 L box, cut-off already passed
    product("200", "Kevytmaito 1 l laktoositon", "Light milk 1 l lactose-free", sales_unit="KI",
            units=[{"unitId": "KI", "sizeInBaseUnits": 10.0}], allergens=["AM"],
            claims=["FREE_FROM_LACTOSE"], lot=0.01),
    # only 300 pieces available
    product("300", "Täysmaito 1 l laktoositon", "Whole milk 1 l lactose-free", allergens=["AM"],
            claims=["FREE_FROM_LACTOSE"], substitutions=[{"product": "300", "substituteProduct": "100"}]),
    # oat drink: no milk allergen, not lactose-free claim
    product("400", "Kaurajuoma 1 l", "Oat drink 1 l", allergens=["GO"], ingredients="KAURA"),
]

FUTURE = (datetime.now(TZ) + timedelta(days=2)).strftime("%Y-%m-%dT17:00:00+0200")
PAST = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%dT15:00:00+0200")

AVAIL = {
    "100": {"closingTime": FUTURE, "logisticModel": "Stock", "salesBlock": False,
            "scheduleLines": [{"salesBlock": False, "availableAmount": 9999999999.0,
                               "availableDate": "2026-11-16", "salesUnit": "ST"}]},
    "200": {"closingTime": PAST, "logisticModel": "Terminal", "salesBlock": False,
            "scheduleLines": [{"salesBlock": False, "availableAmount": 999999.0,
                               "availableDate": "2026-11-16", "salesUnit": "KI"}]},
    "300": {"closingTime": FUTURE, "logisticModel": "Stock", "salesBlock": False,
            "scheduleLines": [{"salesBlock": False, "availableAmount": 300.0,
                               "availableDate": "2026-11-16", "salesUnit": "ST"}]},
    "400": {"salesBlock": False, "scheduleLines": []},
}

PRICES = {"100": (1.40, 1.40), "200": (13.32, 1.33), "300": (1.20, 1.20), "400": (2.0, 2.0)}


def fake_api(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    ids = body["products"]
    if request.url.path == "/api/availability":
        return httpx.Response(200, json={"products": [{"productId": i, **AVAIL[i]} for i in ids]})
    if request.url.path == "/api/price":
        out = []
        for i in ids:
            unit, comp = PRICES[i]
            out.append({"productId": i, "quantity": 1.0, "netPriceFound": False, "error": False,
                        "catalogPrice": {"value": unit, "valueWithTax": round(unit * 1.135, 2)},
                        "tax": {"taxRate": 13.5}, "comparisonPrice": {"value": comp},
                        "comparisonUnit": "L"})
        return httpx.Response(200, json={"products": out})
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def _valio_review_offline(monkeypatch):
    """Tests never call a live model from the Valio review hook, and the hook is off unless
    a test turns it on (other tests assert exact audit sequences)."""
    monkeypatch.setenv("TTA_VALIO_LLM_PROVIDER", "offline")
    monkeypatch.setenv("TTA_VALIO_REVIEW", "off")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    return replace(s, valio_api_key="test", data_dir=tmp_path)


@pytest.fixture
def catalog(settings: Settings, tmp_path: Path) -> Catalog:
    src = tmp_path / "products.json"
    src.write_text(json.dumps(PRODUCTS))
    rel = tmp_path / "rel.csv"
    rel.write_text("productId,deliveryReliability\n100,0.99\n300,0.8\n")
    build_catalog(settings.catalog_db, src, rel, None)
    return Catalog(settings.catalog_db)


@pytest.fixture
def gateway(settings: Settings, catalog: Catalog) -> Gateway:
    client = ValioClient(settings, transport=httpx.MockTransport(fake_api))
    return Gateway(settings, catalog=catalog, client=client, store=Store(settings.state_db))


@pytest.fixture
def sign(gateway: Gateway):
    """Re-sign a mandate after a test changed its fields."""
    return lambda m: m.sign(gateway.buyers.demo_key(m.buyer_id))


@pytest.fixture
def mandate(sign) -> Mandate:
    return sign(Mandate(
        mandate_id="m1", buyer_id="hotel-1", buyer_name="Hotel", agent_id="agent-1",
        valid_until=datetime.now(TZ) + timedelta(days=1), auto_order_limit_eur=500,
        hard_limit_eur=5000, excluded_allergens=[], required_claims=["FREE_FROM_LACTOSE"],
        substitution_policy="official_auto", min_delivery_reliability=0.9,
        approvers=["chef@hotel"],
    ))
