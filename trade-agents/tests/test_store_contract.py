"""One contract for every state store: SQLite always, Postgres when TTA_TEST_PG_URL is set.

    TTA_TEST_PG_URL=postgresql://user:pass@host:port/db?sslmode=require uv run pytest \
        tests/test_store_contract.py

Each Postgres test runs in its own schema, dropped afterwards.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import timedelta

import pytest

from trade_agents.store import Store, now, open_store

PG_URL = os.getenv("TTA_TEST_PG_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "sqlite":
        yield Store(tmp_path / "state.sqlite")
        return
    if not PG_URL:
        pytest.skip("TTA_TEST_PG_URL not set")
    from trade_agents.store_pg import PgStore

    schema = "tta_test_" + uuid.uuid4().hex[:10]
    s = PgStore(PG_URL, schema=schema)
    yield s
    s.con.execute(f'DROP SCHEMA "{schema}" CASCADE')
    s.close()


def _rec(buyer="b1", key=None, status="placed", total=100.0, mandate="m1", **data):
    return {"idempotency_key": key or uuid.uuid4().hex, "buyer_id": buyer, "agent_id": "a1",
            "mandate_id": mandate, "status": status, "total_eur": total,
            "data": {"approvals": [], **data}}


def test_offers_roundtrip(store):
    exp = now() + timedelta(minutes=5)
    oid = store.save_offer("100", {"unit_price": 1.4, "name": "Maito"}, exp)
    data, expires = store.get_offer(oid)
    assert data == {"unit_price": 1.4, "name": "Maito"}
    assert expires.isoformat() == exp.isoformat()
    assert store.get_offer("of_missing") is None


def test_orders_idempotency_scoped_to_buyer(store):
    o = store.create_order(_rec(key="k-1", lines=[{"q": 1}]))
    assert o["order_id"].startswith("ord_") and o["data"]["lines"] == [{"q": 1}]
    assert store.order_by_key("b1", "k-1")["order_id"] == o["order_id"]
    assert store.order_by_key("b2", "k-1") is None
    store.create_order(_rec(buyer="b2", key="k-1"))          # same key, other buyer: fine
    with pytest.raises(Exception):                           # noqa: B017 backend-specific
        store.create_order(_rec(key="k-1"))


def test_list_update_and_numbers(store):
    a = store.create_order(_rec(status="pending_approval"))
    time.sleep(0.002)
    b = store.create_order(_rec(buyer="b2"))
    assert [o["order_id"] for o in store.list_orders()] == [b["order_id"], a["order_id"]]
    assert [o["order_id"] for o in store.list_orders("pending_approval")] == [a["order_id"]]
    assert [o["order_id"] for o in store.list_orders(buyer_id="b2")] == [b["order_id"]]
    first = store.next_order_number()
    u = store.update_order(a["order_id"], status="placed", order_number=first,
                           data={**a["data"], "x": 1})
    assert u["status"] == "placed" and u["data"]["x"] == 1 and u["updated_at"] >= u["created_at"]
    assert store.next_order_number() != first


def test_autonomous_spend(store):
    since = now() - timedelta(hours=1)
    store.create_order(_rec(total=100))                                       # counts
    store.create_order(_rec(total=50, cart_confirmation=None))                # null: counts
    store.create_order(_rec(total=1000, status="pending_approval"))           # not placed
    approved = _rec(total=200)
    approved["data"]["approvals"] = [{"by": "chef", "decision": "approved"}]
    store.create_order(approved)                                              # human approved
    store.create_order(_rec(total=300, cart_confirmation={"by": "chef"}))     # cart confirmed
    store.create_order(_rec(total=400, mandate="m2"))                         # other mandate
    store.create_order(_rec(total=500, buyer="b2"))                           # other buyer
    assert store.autonomous_spend_since("b1", "m1", since) == 150.0
    assert store.autonomous_spend_since("b1", "m1", now() + timedelta(hours=1)) == 0.0


def test_audit_chain(store):
    for i in range(3):
        e = store.audit("actor", "evt", "ord_1" if i else "other", f"entry {i}", {"i": i})
    assert e["hash"] and e["seq"]
    assert store.verify_audit() == (True, None)
    assert [x["summary"] for x in store.audit_log("ord_1")] == ["entry 1", "entry 2"]
    assert [x["data"]["i"] for x in store.audit_log()] == [0, 1, 2]
    seq2 = store.audit_log()[1]["seq"]
    store.con.execute(f"UPDATE audit SET summary = 'edited' WHERE seq = {int(seq2)}")
    assert store.verify_audit() == (False, seq2)


def test_lock_is_reentrant(store):
    with store.lock, store.lock:
        o = store.create_order(_rec())
        assert store.get_order(o["order_id"])
    assert store.get_order(o["order_id"])


def test_valio_reviews_live_outside_order_data(store):
    o = store.create_order(_rec())
    assert store.get_valio_review(o["order_id"]) is None
    store.save_valio_review(o["order_id"], {"risk": "low", "summary": "ä"})
    store.save_valio_review(o["order_id"], {"risk": "high", "summary": "ö"})   # upsert
    assert store.get_valio_review(o["order_id"]) == {"risk": "high", "summary": "ö"}
    assert "valio_review" not in store.get_order(o["order_id"])["data"]


def test_writes_inside_lock_survive_a_python_error(store):
    """Like the gateway's `order.expired` before raising GatewayError."""
    o = store.create_order(_rec(status="pending_approval"))
    with pytest.raises(ValueError), store.lock:
        store.update_order(o["order_id"], status="expired")
        raise ValueError("confirmation window expired")
    assert store.get_order(o["order_id"])["status"] == "expired"


def test_with_sslmode():
    from trade_agents.store_pg import with_sslmode

    assert with_sslmode("postgres://u:p@x.aivencloud.com:1/db") == \
        "postgres://u:p@x.aivencloud.com:1/db?sslmode=require"
    assert with_sslmode("postgres://u@h/db?a=1") == "postgres://u@h/db?a=1&sslmode=require"
    assert with_sslmode("postgres://u@h/db?sslmode=verify-full").endswith("verify-full")
    assert with_sslmode("postgresql://u@127.0.0.1:5432/db") == "postgresql://u@127.0.0.1:5432/db"


@pytest.mark.skipif(not PG_URL, reason="TTA_TEST_PG_URL not set")
def test_pg_reconnects_after_dropped_connection(store):
    from trade_agents.store_pg import PgStore

    if not isinstance(store, PgStore):
        pytest.skip("Postgres only")
    o = store.create_order(_rec())
    pid = store.con.info.backend_pid
    killer = PgStore(PG_URL, schema=store.schema)
    killer.con.execute("SELECT pg_terminate_backend(%s)", (pid,))
    killer.close()
    assert store.get_order(o["order_id"])["order_id"] == o["order_id"]
    store.audit("a", "e", "s", "after reconnect")
    assert store.verify_audit() == (True, None)


def test_open_store_defaults_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("TTA_STORE_URL", raising=False)
    monkeypatch.delenv("AIVEN_PG_URL", raising=False)
    assert isinstance(open_store(tmp_path / "s.sqlite"), Store)


@pytest.mark.skipif(not PG_URL, reason="TTA_TEST_PG_URL not set")
def test_pg_lock_is_cross_process(store):
    """Two PgStore instances (= two gateway processes) cannot hold the order lock at once."""
    from trade_agents.store_pg import PgStore

    if not isinstance(store, PgStore):
        pytest.skip("Postgres only")
    other = PgStore(PG_URL, schema=store.schema)
    events = []

    def second():
        with other.lock:
            events.append("other-in")

    with store.lock:
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.5)
        events.append("first-out")
    t.join(5)
    other.close()
    assert events == ["first-out", "other-in"]


@pytest.mark.skipif(not PG_URL, reason="TTA_TEST_PG_URL not set")
def test_pg_parallel_gateways_respect_cumulative_limit(settings, catalog, mandate):
    """Like test_hardening's race, but each order goes through its own gateway with its own
    database connection: the in-process lock cannot help, only the advisory lock."""
    from datetime import date

    import httpx
    from conftest import fake_api

    from trade_agents.service import Gateway, OrderLineIn
    from trade_agents.store_pg import PgStore
    from trade_agents.valio_client import ValioClient

    schema = "tta_test_" + uuid.uuid4().hex[:10]
    stores = [PgStore(PG_URL, schema=schema) for _ in range(6)]
    client = ValioClient(settings, transport=httpx.MockTransport(fake_api))
    gws = [Gateway(settings, catalog=catalog, client=client, store=s) for s in stores]
    passport = gws[0].issue_passport(mandate.agent_id, "test", "pytest", "autonomous")
    offers = [gws[0].quote(["100"], date(2026, 11, 16))[0] for _ in gws]
    results, errors = [], []
    barrier = threading.Barrier(len(gws))

    def run(i):
        try:
            barrier.wait()
            results.append(gws[i].place_order(
                mandate, [OrderLineIn(offer_id=offers[i]["offer_id"], quantity=140)],
                f"key-pg-race-{i}", "race", agent_passport=passport))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(gws))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert not errors
        placed = [o for o in results if o["status"] == "placed"]
        assert len(placed) == 2 and sum(o["total_eur"] for o in placed) <= 500
        assert stores[0].verify_audit() == (True, None)
    finally:
        stores[0].con.execute(f'DROP SCHEMA "{schema}" CASCADE')
        for s in stores:
            s.close()
