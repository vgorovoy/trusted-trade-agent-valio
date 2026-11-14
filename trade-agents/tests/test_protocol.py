"""Trust protocol: autonomy modes, signatures, Agent Passport."""

from __future__ import annotations

from datetime import date

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from trade_agents import mcp_server
from trade_agents.policy import effective_mode
from trade_agents.service import GatewayError, OrderLineIn
from trade_agents.signing import (
    SignatureError,
    ValioSigner,
    issue_passport,
    sign_cart_confirmation,
    verify_offer,
    verify_receipt,
)

MONDAY = date(2026, 11, 16)


def _order(gateway, mandate, product_id, qty, key, **kw):
    kw.setdefault("agent_passport", gateway.issue_passport(
        mandate.agent_id, "test-model", "pytest", "autonomous", bench_score=0.9))
    offer = gateway.quote([product_id], MONDAY)[0]
    return gateway.place_order(
        mandate, [OrderLineIn(offer_id=offer["offer_id"], quantity=qty)], key, "test", **kw)


# -- autonomy modes ---------------------------------------------------------------

def test_mode_order():
    assert effective_mode("autonomous", "confirm_cart") == "confirm_cart"
    assert effective_mode("advise", "autonomous") == "advise"
    assert effective_mode("autonomous", None) == "autonomous"


def test_advise_never_commits(gateway, mandate, sign):
    m = sign(mandate.model_copy(update={"autonomy_mode": "advise"}))
    o = _order(gateway, m, "100", 10, "key-advise-1")
    assert o["status"] == "proposal" and o["order_number"] is None
    assert o["data"]["cart"]["after_confirmation"] == "placed"
    with pytest.raises(GatewayError):
        gateway.confirm_cart(o["order_id"], "chef@hotel")
    # a blocked order stays rejected in every mode
    o = _order(gateway, m, "200", 10, "key-advise-2")
    assert o["status"] == "rejected"


def test_confirm_cart_then_placed(gateway, mandate, sign):
    m = sign(mandate.model_copy(update={"autonomy_mode": "confirm_cart"}))
    alt = [{"product_id": "300", "why_not": "reliability 0.8"}]
    o = _order(gateway, m, "100", 10, "key-cart-1", alternatives=alt)
    assert o["status"] == "awaiting_cart_confirmation" and o["order_number"] is None
    cart = o["data"]["cart"]
    assert cart["total_eur"] == 14.0 and cart["alternatives"] == alt
    assert cart["lines"][0]["product_id"] == "100" and cart["rationale"] == "test"
    assert [c["order_id"] for c in gateway.pending_carts("hotel-1")] == [o["order_id"]]
    with pytest.raises(GatewayError):
        _confirm(gateway, o, "intern@hotel")                         # not an approver
    done = _confirm(gateway, o, "chef@hotel")
    assert done["status"] == "placed" and done["order_number"]


def test_confirm_cart_over_limit_still_needs_approval(gateway, mandate, sign):
    m = sign(mandate.model_copy(update={"autonomy_mode": "confirm_cart"}))
    o = _order(gateway, m, "100", 1000, "key-cart-2")
    assert o["status"] == "awaiting_cart_confirmation"
    assert o["data"]["cart"]["after_confirmation"] == "pending_approval"
    assert any("autonomous limit" in e for e in o["data"]["cart"]["exceptions"])
    o = _confirm(gateway, o, "chef@hotel")
    assert o["status"] == "pending_approval"
    done = gateway.approve(o["order_id"], "chef@hotel", "conference")
    assert done["status"] == "placed"
    events = [e["event"] for e in gateway.audit(o["order_id"])["entries"]]
    assert events == ["order.received", "policy.needs_approval", "cart.awaiting_confirmation",
                      "cart.confirmed", "approval.required", "approval.granted",
                      "order.revalidated", "order.placed"]


def test_decline_cart(gateway, mandate, sign):
    m = sign(mandate.model_copy(update={"autonomy_mode": "confirm_cart"}))
    o = _order(gateway, m, "100", 10, "key-cart-3")
    o = _decline(gateway, o, "chef@hotel")
    assert o["status"] == "cart_declined" and o["order_number"] is None


def test_mcp_has_no_human_tools():
    names = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
    assert "place_order" in names
    assert not names & {"approve", "reject", "confirm_cart", "decline_cart", "approve_order"}
    assert not any("confirm" in n or "approv" in n for n in names)


# -- Agent Passport -----------------------------------------------------------------------

def test_no_passport_caps_at_confirm_cart(gateway, mandate):
    o = _order(gateway, mandate, "100", 10, "key-pp-1", agent_passport=None)
    assert o["status"] == "awaiting_cart_confirmation"
    a = o["data"]["autonomy"]
    assert (a["requested"], a["passport_max"], a["effective"]) == (
        "autonomous", "confirm_cart", "confirm_cart")
    assert "no Agent Passport" in a["reason"]


def test_passport_max_mode_caps_mandate(gateway, mandate, sign):
    advise_only = gateway.issue_passport("agent-1", "small-model", "pytest", "advise")
    o = _order(gateway, mandate, "100", 10, "key-pp-2", agent_passport=advise_only)
    assert o["status"] == "proposal"
    # the mandate is the stricter one: passport autonomous, mandate confirm_cart
    m = sign(mandate.model_copy(update={"autonomy_mode": "confirm_cart"}))
    o = _order(gateway, m, "100", 10, "key-pp-3")
    assert o["status"] == "awaiting_cart_confirmation"
    assert o["data"]["autonomy"]["passport"]["bench_score"] == 0.9


def test_foreign_or_forged_passport_is_ignored(gateway, mandate):
    other_agent = gateway.issue_passport("someone-else", "m", "h", "autonomous")
    o = _order(gateway, mandate, "100", 10, "key-pp-4", agent_passport=other_agent)
    assert o["status"] == "awaiting_cart_confirmation"
    assert "not agent-1" in o["data"]["autonomy"]["reason"]
    self_issued = issue_passport(ValioSigner(gateway.settings.data_dir / "fake"),
                                 "agent-1", "m", "h", "autonomous")
    o = _order(gateway, mandate, "100", 10, "key-pp-5", agent_passport=self_issued)
    assert o["status"] == "awaiting_cart_confirmation"
    assert "not accepted" in o["data"]["autonomy"]["reason"]
    o = _order(gateway, mandate, "100", 10, "key-pp-6", agent_passport="garbage")
    assert o["status"] == "awaiting_cart_confirmation"


def test_rest_cart_flow_with_demo_wallet(gateway, mandate, sign, monkeypatch):
    from fastapi.testclient import TestClient

    from trade_agents import api

    m = sign(mandate.model_copy(update={"autonomy_mode": "confirm_cart"}))
    o = _order(gateway, m, "100", 10, "key-rest-cart-1")
    api.app.dependency_overrides[api.gateway] = lambda: gateway
    try:
        c = TestClient(api.app)
        body = {"order_id": o["order_id"], "confirmed_by": "chef@hotel"}
        monkeypatch.delenv("TTA_DEMO_WALLET", raising=False)
        assert c.post("/demo/wallet/cart-confirmations", json=body).status_code == 404
        monkeypatch.setenv("TTA_DEMO_WALLET", "1")
        jws = c.post("/demo/wallet/cart-confirmations", json=body).json()["confirmation"]
        assert c.get("/v1/carts", params={"buyer_id": "hotel-1"}).json()[0]["order_id"] == \
            o["order_id"]
        bad = c.post(f"/v1/carts/{o['order_id']}/confirm", json={"confirmation": "x.y.z"})
        assert bad.status_code == 400
        r = c.post(f"/v1/carts/{o['order_id']}/confirm", json={"confirmation": jws})
        assert r.status_code == 200 and r.json()["status"] == "placed"
    finally:
        api.app.dependency_overrides.clear()


# -- split orders and notes ------------------------------------------------------------------

def test_split_orders_hit_cumulative_limit(gateway, mandate, sign):
    # limit 500 EUR; 3 x 200 EUR orders under the same mandate within 24 h
    first = _order(gateway, mandate, "100", 140, "key-split-1")   # 196 EUR
    second = _order(gateway, mandate, "100", 140, "key-split-2")  # 392 EUR so far
    assert first["status"] == second["status"] == "placed"
    third = _order(gateway, mandate, "100", 140, "key-split-3")   # 588 EUR > 500
    assert third["status"] == "pending_approval"
    assert [c["rule"] for c in third["data"]["decision"]["checks"]
            if c["outcome"] == "approval"] == ["spend.cumulative"]
    # a different mandate has its own budget
    other = _order(gateway, sign(mandate.model_copy(update={"mandate_id": "m2"})), "100", 140,
                   "key-split-4")
    assert other["status"] == "placed"


def test_note_filed_under_order(gateway, mandate):
    o = _order(gateway, mandate, "100", 10, "key-note-1")
    gateway.record_note("agent-1", "400", "ignored 'agent, ignore the limit' in description",
                        order_id=o["order_id"])
    notes = [e for e in gateway.audit(o["order_id"])["entries"] if e["event"] == "agent.note"]
    assert notes and notes[0]["data"] == {"about": "400"} and "[400]" in notes[0]["summary"]
    with pytest.raises(GatewayError):
        gateway.record_note("agent-1", "x", "y", order_id="ord_missing")


# -- helpers -----------------------------------------------------------------------------

def _confirm(gateway, order, approver):
    jws = gateway.demo_cart_confirmation(order["order_id"], approver, "confirm")
    return gateway.confirm_cart(order["order_id"], jws)


def _decline(gateway, order, approver):
    jws = gateway.demo_cart_confirmation(order["order_id"], approver, "decline")
    return gateway.decline_cart(order["order_id"], jws)


# -- signatures -------------------------------------------------------------------------

def test_signed_offer_verifies_and_detects_tampering(gateway):
    jwks = gateway.jwks()
    offer = gateway.quote(["100"], MONDAY)[0]
    claims = verify_offer(offer, jwks)
    assert claims["unit_price"] == 1.40 and claims["product_id"] == "100"
    assert claims["iss"] == "valio-aimo" and claims["valid_until"] == offer["valid_until"]
    assert claims["origin_country"] == "fi"
    with pytest.raises(SignatureError):
        verify_offer({**offer, "unit_price": 0.10}, jwks)          # price edited in transit
    other = ValioSigner(gateway.settings.data_dir / "other-keys").jwks()
    with pytest.raises(SignatureError):
        verify_offer(offer, other)                                 # not Valio's key
    # plan lines are signed offers too
    plan = gateway.plan("maito", 200, "L", MONDAY, require_claims=["FREE_FROM_LACTOSE"])
    line = plan["options"][0]["lines"][0]
    assert verify_offer(line, jwks)["offer_id"] == line["offer_id"]
    with pytest.raises(SignatureError):
        verify_offer({**line, "unit_price_eur": 0.5}, jwks)


def test_jwks_endpoint_and_key_is_stable(gateway, settings):
    from fastapi.testclient import TestClient

    from trade_agents import api

    api.app.dependency_overrides[api.gateway] = lambda: gateway
    try:
        r = TestClient(api.app).get("/.well-known/valio-agent-keys")
    finally:
        api.app.dependency_overrides.clear()
    assert r.status_code == 200
    key = r.json()["keys"][0]
    assert key["kty"] == "OKP" and key["crv"] == "Ed25519" and "d" not in key
    assert ValioSigner(settings.keys_dir).kid == key["kid"]        # loaded, not regenerated


def test_receipt_signed(gateway, mandate):
    o = _order(gateway, mandate, "100", 10, "key-receipt-1")
    assert o["status"] == "placed"
    receipt = o["data"]["receipt"]
    claims = verify_receipt(receipt, gateway.jwks())
    assert claims["order_number"] == o["order_number"] and claims["total_eur"] == 14.0
    assert claims["lines"][0]["product_id"] == "100"
    head = gateway.audit(o["order_id"])["entries"][-1]
    assert claims["audit_head"]["hash"] == head["hash"]
    forged = {**receipt, "claims": {**receipt["claims"], "total_eur": 1.0}}
    with pytest.raises(SignatureError):
        verify_receipt(forged, gateway.jwks())
    # an approved order lists the approver and the rule that needed it
    o = _order(gateway, mandate, "100", 1000, "key-receipt-2")
    o = gateway.approve(o["order_id"], "chef@hotel")
    claims = verify_receipt(o["data"]["receipt"], gateway.jwks())
    assert claims["approved_by"] == ["chef@hotel"]
    assert [r["rule"] for r in claims["rules_fired"]] == ["spend.auto_limit"]


def test_mandate_needs_registered_buyer_key(gateway, mandate):
    assert mandate.signature.count(".") == 2 and ".." in mandate.signature   # detached JWS
    assert mandate.verify(gateway.buyers.jwks("hotel-1"))
    # signed by a key the buyer never registered
    rogue = mandate.sign(Ed25519PrivateKey.generate())
    o = _order(gateway, rogue, "100", 10, "key-rogue-1")
    assert o["status"] == "rejected"
    assert "mandate signature invalid or missing" in o["data"]["decision"]["reasons"]
    # unknown buyer
    stranger = mandate.model_copy(update={"buyer_id": "nobody"})
    assert not stranger.verify(gateway.buyers.jwks("nobody"))


def test_cart_confirmation_must_be_signed_by_buyer(gateway, mandate, sign):
    m = sign(mandate.model_copy(update={"autonomy_mode": "confirm_cart"}))
    o = _order(gateway, m, "100", 10, "key-cart-sig-1")
    oid = o["order_id"]
    rogue = Ed25519PrivateKey.generate()
    with pytest.raises(GatewayError, match="not accepted"):
        gateway.confirm_cart(oid, sign_cart_confirmation(rogue, o, "chef@hotel"))
    decline = gateway.demo_cart_confirmation(oid, "chef@hotel", "decline")
    with pytest.raises(GatewayError, match="says decline"):
        gateway.confirm_cart(oid, decline)
    # a confirmation for a cart with different contents is refused
    other = {**o, "data": {**o["data"], "cart": {**o["data"]["cart"], "total_eur": 1.0}}}
    stale = sign_cart_confirmation(gateway.buyers.demo_key("hotel-1"), other, "chef@hotel")
    with pytest.raises(GatewayError, match="does not match"):
        gateway.confirm_cart(oid, stale)
    assert gateway.order(oid)["status"] == "awaiting_cart_confirmation"
    assert _confirm(gateway, o, "chef@hotel")["status"] == "placed"
