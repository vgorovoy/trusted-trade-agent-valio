"""Open schemas: committed files match the models, and real gateway documents validate."""

from __future__ import annotations

import json
from datetime import date

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from trade_agents import protocol
from trade_agents.service import OrderLineIn
from trade_agents.signing import TYP_CART, decode_unverified, verify_jws, verify_offer

MONDAY = date(2026, 11, 16)


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(protocol.schema(name))


def test_committed_schemas_match_the_models():
    files = sorted(p.name for p in protocol.SCHEMAS_DIR.glob("*.schema.json"))
    assert files == sorted(f"{n}.schema.json" for n in protocol.DOCUMENTS)
    for name, doc in protocol.schemas().items():
        on_disk = (protocol.SCHEMAS_DIR / f"{name}.schema.json").read_text()
        assert on_disk == protocol.render(doc), f"{name}: run `uv run tta schemas`"
        Draft202012Validator.check_schema(doc)
        assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_real_documents_validate(gateway, mandate, sign):
    jwks = gateway.jwks()
    _validator("buyer-mandate").validate(mandate.model_dump(mode="json"))

    offer = gateway.quote(["100"], MONDAY)[0]
    _validator("signed-offer").validate(verify_offer(offer, jwks))
    line = gateway.plan("maito", 200, "L", MONDAY)["options"][0]["lines"][0]
    _validator("signed-offer").validate(verify_offer(line, jwks))

    passport = gateway.issue_passport(mandate.agent_id, "m", "pytest", "autonomous",
                                      bench_score=0.9)
    _validator("agent-passport").validate(decode_unverified(passport))

    placed = gateway.place_order(mandate, [OrderLineIn(offer_id=offer["offer_id"], quantity=10)],
                                 "key-schema-1", "test", agent_passport=passport)
    _validator("order-receipt").validate(placed["data"]["receipt"]["claims"])

    # confirm_cart over the limit: cart confirmation, then approval -> receipt with both
    m = sign(mandate.model_copy(update={"autonomy_mode": "confirm_cart"}))
    big = gateway.quote(["100"], MONDAY)[0]
    o = gateway.place_order(m, [OrderLineIn(offer_id=big["offer_id"], quantity=1000)],
                            "key-schema-2", "test", agent_passport=passport)
    jws = gateway.demo_cart_confirmation(o["order_id"], "chef@hotel", note="ok")
    _validator("cart-confirmation").validate(
        verify_jws(jws, gateway.buyers.jwks("hotel-1"), typ=TYP_CART))
    gateway.confirm_cart(o["order_id"], jws)
    done = gateway.approve(o["order_id"], "chef@hotel")
    claims = done["data"]["receipt"]["claims"]
    assert claims["cart_confirmed_by"] == "chef@hotel" and claims["rules_fired"]
    _validator("order-receipt").validate(claims)


def test_schemas_reject_drift(gateway, mandate):
    with pytest.raises(ValidationError):
        _validator("buyer-mandate").validate(
            {**mandate.model_dump(mode="json"), "autonomy_mode": "unlimited"})
    passport = decode_unverified(gateway.issue_passport("a", "m", "h", "advise"))
    with pytest.raises(ValidationError):
        _validator("agent-passport").validate({**passport, "max_mode": "god"})
    with pytest.raises(ValidationError):
        _validator("agent-passport").validate({k: v for k, v in passport.items() if k != "exp"})
    with pytest.raises(ValidationError):
        _validator("agent-passport").validate({**passport, "unexpected": 1})


def test_schemas_served(gateway):
    from fastapi.testclient import TestClient

    from trade_agents import api

    c = TestClient(api.app)
    index = c.get("/schemas").json()
    assert set(index) == set(protocol.DOCUMENTS)
    assert index["agent-passport"]["jws_typ"] == "valio.agent_passport.v1"
    for suffix in ("", ".schema.json"):
        r = c.get(f"/schemas/buyer-mandate{suffix}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(
            "application/schema+json")
        assert r.json() == json.loads(
            (protocol.SCHEMAS_DIR / "buyer-mandate.schema.json").read_text())
    assert c.get("/schemas/nope").status_code == 404
