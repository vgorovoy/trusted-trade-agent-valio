"""Protocol hardening: malformed tokens, passport expiry, key registry, idempotency scope,
note ownership, concurrent orders, tampering and replay."""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timedelta

import pytest

from trade_agents.buyer_agent import profiles
from trade_agents.config import TZ
from trade_agents.service import GatewayError, OrderLineIn
from trade_agents.signing import (
    TYP_PASSPORT,
    BuyerRegistry,
    SignatureError,
    b64u,
    canonical,
    decode_unverified,
    passport_problem,
    sign_jws,
    verify_jws,
)
from trade_agents.store import Store

MONDAY = date(2026, 11, 16)


def _passport(gateway, agent_id="agent-1", **kw):
    return gateway.issue_passport(agent_id, "test-model", "pytest", "autonomous", **kw)


def _order(gateway, mandate, product_id, qty, key, **kw):
    kw.setdefault("agent_passport", _passport(gateway, mandate.agent_id))
    offer = gateway.quote([product_id], MONDAY)[0]
    return gateway.place_order(
        mandate, [OrderLineIn(offer_id=offer["offer_id"], quantity=qty)], key, "test", **kw)


def _token(header, payload=b"{}", sig=b"x"):
    h = header if isinstance(header, bytes) else canonical(header)
    return f"{b64u(h)}.{b64u(payload)}.{b64u(sig)}"


# -- 1. malformed JWS ------------------------------------------------------------------

@pytest.mark.parametrize("token", [
    _token([]),                                   # header is JSON, but not an object
    _token(b'"EdDSA"'),
    _token(b"not json"),
    "only.two",
    "a.b.c.d",
    "",
])
def test_malformed_jws_is_a_signature_error(gateway, token):
    with pytest.raises(SignatureError):
        verify_jws(token, gateway.jwks())


def test_payload_that_is_not_an_object_is_rejected(gateway):
    token = sign_jws(b"[1,2,3]", gateway.signer.key, TYP_PASSPORT)
    with pytest.raises(SignatureError, match="payload"):
        verify_jws(token, gateway.jwks(), typ=TYP_PASSPORT)
    token = sign_jws(b"\xff\xfe", gateway.signer.key, TYP_PASSPORT)
    with pytest.raises(SignatureError):
        verify_jws(token, gateway.jwks(), typ=TYP_PASSPORT)


def test_malformed_mandate_signature_and_passport_do_not_crash(gateway, mandate):
    from fastapi.testclient import TestClient

    from trade_agents import api

    broken = mandate.model_copy(update={"signature": _token([], b"")})
    o = _order(gateway, broken, "100", 10, "key-malformed-1", agent_passport=_token([]))
    assert o["status"] == "rejected"
    assert "mandate signature invalid or missing" in o["data"]["decision"]["reasons"]
    assert "not accepted" in o["data"]["autonomy"]["reason"]
    offer = gateway.quote(["100"], MONDAY)[0]
    api.app.dependency_overrides[api.gateway] = lambda: gateway
    try:
        r = TestClient(api.app).post("/v1/orders", json={
            "mandate": broken.model_dump(mode="json"), "idempotency_key": "key-malformed-2",
            "lines": [{"offer_id": offer["offer_id"], "quantity": 1}],
            "agent_passport": _token([])})
    finally:
        api.app.dependency_overrides.clear()
    assert r.status_code == 200 and r.json()["status"] == "rejected"


# -- 2. passport expiry ----------------------------------------------------------------

def test_passport_carries_exp_and_expires(gateway, mandate):
    token = _passport(gateway, valid_days=7)
    claims = decode_unverified(token)
    issued = datetime.fromisoformat(claims["issued_at"])
    assert abs(claims["exp"] - (issued + timedelta(days=7)).timestamp()) < 2
    assert passport_problem(claims) is None
    assert "expired" in passport_problem(claims, datetime.now(TZ) + timedelta(days=8))
    with pytest.raises(ValueError):
        _passport(gateway, valid_days=0)


def test_expired_or_exp_less_passport_counts_as_none(gateway, mandate):
    past = int((datetime.now(TZ) - timedelta(minutes=1)).timestamp())
    expired = gateway.signer.sign({"agent_id": "agent-1", "model": "m", "harness": "h",
                                   "max_mode": "autonomous", "exp": past}, TYP_PASSPORT)
    o = _order(gateway, mandate, "100", 10, "key-exp-1", agent_passport=expired)
    a = o["data"]["autonomy"]
    assert o["status"] == "awaiting_cart_confirmation" and a["passport"] is None
    assert "passport expired" in a["reason"]
    legacy = gateway.signer.sign({"agent_id": "agent-1", "model": "m", "harness": "h",
                                  "max_mode": "autonomous"}, TYP_PASSPORT)
    o = _order(gateway, mandate, "100", 10, "key-exp-2", agent_passport=legacy)
    assert o["status"] == "awaiting_cart_confirmation"
    assert "no expiry" in o["data"]["autonomy"]["reason"]
    o = _order(gateway, mandate, "100", 10, "key-exp-3")
    assert o["status"] == "placed" and o["data"]["autonomy"]["passport"]["exp"]


# -- 3. demo wallet cannot sign for a buyer it holds no key for ------------------------

def test_demo_key_refuses_buyer_registered_without_private_key(gateway, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    reg = BuyerRegistry(tmp_path / "buyers")
    reg.register("own-device-buyer", Ed25519PrivateKey.generate().public_key())
    with pytest.raises(SignatureError, match="without a demo private key"):
        reg.demo_key("own-device-buyer")
    assert not (tmp_path / "buyers" / "own-device-buyer.key.pem").exists()
    assert len(reg.jwks("own-device-buyer")["keys"]) == 1          # nothing new registered
    # a demo buyer: created once, then the same key every time
    k1, k2 = reg.demo_key("demo-buyer"), reg.demo_key("demo-buyer")
    assert k1.public_key().public_bytes_raw() == k2.public_key().public_bytes_raw()
    assert len(reg.jwks("demo-buyer")["keys"]) == 1


# -- 4. the buyer agent does not mint its own passport by default ----------------------

def test_agent_passport_comes_from_env_unless_self_issue(gateway, monkeypatch):
    for var in ("TTA_AGENT_PASSPORT", "TTA_AGENT_PASSPORT_HOTEL_BUYER_AGENT",
                profiles.SELF_ISSUE_ENV):
        monkeypatch.delenv(var, raising=False)
    keys = gateway.settings.keys_dir
    assert profiles.demo_passport("hotel-buyer-agent", keys) is None
    issued = _passport(gateway, "hotel-buyer-agent")
    monkeypatch.setenv("TTA_AGENT_PASSPORT", issued)
    assert profiles.demo_passport("hotel-buyer-agent", keys) == issued
    monkeypatch.setenv("TTA_AGENT_PASSPORT_HOTEL_BUYER_AGENT", "specific")
    assert profiles.demo_passport("hotel-buyer-agent", keys) == "specific"
    monkeypatch.delenv("TTA_AGENT_PASSPORT")
    monkeypatch.delenv("TTA_AGENT_PASSPORT_HOTEL_BUYER_AGENT")
    monkeypatch.setenv(profiles.SELF_ISSUE_ENV, "1")
    token = profiles.demo_passport("hotel-buyer-agent", keys)
    assert gateway.signer.verify(token, TYP_PASSPORT)["agent_id"] == "hotel-buyer-agent"


# -- 5. idempotency keys are per buyer -------------------------------------------------

def test_idempotency_key_is_scoped_to_the_buyer(gateway, mandate, sign):
    a = _order(gateway, mandate, "100", 10, "shared-key-1")
    assert _order(gateway, mandate, "100", 99, "shared-key-1")["order_id"] == a["order_id"]
    other = sign(mandate.model_copy(update={"buyer_id": "hotel-2", "mandate_id": "m-other"}))
    b = _order(gateway, other, "100", 20, "shared-key-1")
    assert b["order_id"] != a["order_id"] and b["buyer_id"] == "hotel-2"
    assert b["total_eur"] == 28.0


def test_old_store_schema_is_migrated(tmp_path):
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE orders (order_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, "
                "buyer_id TEXT, agent_id TEXT, mandate_id TEXT, status TEXT, total_eur REAL, "
                "order_number TEXT, data TEXT, created_at TEXT, updated_at TEXT, "
                "hold_until TEXT)")
    con.execute("INSERT INTO orders VALUES ('ord_1','k1','b1','a','m','placed',1,NULL,'{}',"
                "'t','t',NULL)")
    con.commit()
    con.close()
    store = Store(db)
    assert store.order_by_key("b1", "k1")["order_id"] == "ord_1"
    assert store.order_by_key("b2", "k1") is None
    rec = {"idempotency_key": "k1", "buyer_id": "b2", "agent_id": "a", "mandate_id": "m",
           "status": "proposal", "total_eur": 1.0, "data": {}}
    assert store.create_order(rec)["buyer_id"] == "b2"


# -- 6. notes only on the agent's own orders -------------------------------------------

def test_note_on_another_agents_order_is_refused(gateway, mandate):
    o = _order(gateway, mandate, "100", 10, "key-note-own-1")
    with pytest.raises(GatewayError, match="another agent"):
        gateway.record_note("intruder-agent", "400", "this order is fine", order_id=o["order_id"])
    gateway.record_note("agent-1", "400", "own note", order_id=o["order_id"])
    notes = [e for e in gateway.audit(o["order_id"])["entries"] if e["event"] == "agent.note"]
    assert [n["actor"] for n in notes] == ["agent-1"]


# -- 7. parallel orders cannot overspend the cumulative limit --------------------------

def test_parallel_orders_respect_cumulative_limit(gateway, mandate):
    # limit 500 EUR, 6 parallel orders of 196 EUR: at most two may be placed on their own
    passport = _passport(gateway)
    offers = [gateway.quote(["100"], MONDAY)[0] for _ in range(6)]
    results: list[dict] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(offers))

    def run(i: int) -> None:
        try:
            barrier.wait()
            results.append(gateway.place_order(
                mandate, [OrderLineIn(offer_id=offers[i]["offer_id"], quantity=140)],
                f"key-race-{i}", "race", agent_passport=passport))
        except BaseException as e:  # noqa: BLE001 - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(offers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    placed = [o for o in results if o["status"] == "placed"]
    assert len(placed) == 2 and sum(o["total_eur"] for o in placed) <= 500
    assert sorted(o["status"] for o in results).count("pending_approval") == 4


# -- 8. tampering and replay -----------------------------------------------------------

def test_mandate_modified_without_resigning_is_rejected(gateway, mandate):
    raised = mandate.model_copy(update={"auto_order_limit_eur": 100000.0})
    assert raised.signature == mandate.signature
    o = _order(gateway, raised, "100", 10, "key-tamper-1")
    assert o["status"] == "rejected"
    assert "mandate signature invalid or missing" in o["data"]["decision"]["reasons"]
    widened = mandate.model_copy(update={"approvers": ["agent@evil"]})
    assert _order(gateway, widened, "100", 10, "key-tamper-2")["status"] == "rejected"


def test_cart_confirmation_cannot_be_replayed_for_another_order(gateway, mandate, sign):
    m = sign(mandate.model_copy(update={"autonomy_mode": "confirm_cart"}))
    first = _order(gateway, m, "100", 10, "key-replay-1")
    second = _order(gateway, m, "100", 10, "key-replay-2")
    jws = gateway.demo_cart_confirmation(first["order_id"], "chef@hotel")
    with pytest.raises(GatewayError, match="different order"):
        gateway.confirm_cart(second["order_id"], jws)
    assert gateway.order(second["order_id"])["status"] == "awaiting_cart_confirmation"
    assert gateway.confirm_cart(first["order_id"], jws)["status"] == "placed"
    with pytest.raises(GatewayError):                                 # and not twice
        gateway.confirm_cart(first["order_id"], jws)
