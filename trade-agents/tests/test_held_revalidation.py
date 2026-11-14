"""A human decision authorises a basket, not stale stock or a passed delivery cutoff."""
from datetime import datetime

import pytest

from trade_agents.bench.scenario import Scenario, load_world
from trade_agents.bench.world import World, frozen
from trade_agents.service import GatewayError, OrderLineIn


def scenario(flow, cutoff="2026-11-12T10:00:00+0200", **mandate):
    spec = load_world("dairy", {
        "now": "2026-11-12T09:59:00+02:00", "cutoff": cutoff,
        "products": {"1001": {"price": 1.4, "comparison": 1.4, "stock": 2000}},
    })
    return Scenario(id=f"held-{flow}", category="regression", title="Held basket",
                    buyer="hotel", request="milk Monday", world=spec, expect={},
                    mandate={"auto_order_limit_eur": 1000, "hard_limit_eur": 2000,
                             "autonomy_mode": "confirm_cart" if flow == "confirm" else "autonomous",
                             **mandate})


def held(world, flow):
    profile, gateway = world.profile(), world.gateway
    quantity = 100 if flow == "confirm" else 1000
    offer = gateway.quote(["1001"], world.spec.delivery)[0]
    order = gateway.place_order(
        profile.mandate, [OrderLineIn(offer_id=offer["offer_id"], quantity=quantity)],
        "held-regression", agent_passport=profile.passport)
    assert order["status"] == ("awaiting_cart_confirmation" if flow == "confirm" else "pending_approval")
    approver = profile.mandate.approvers[0]
    confirmation = (gateway.demo_cart_confirmation(order["order_id"], approver)
                    if flow == "confirm" else None)

    def decide():
        if flow == "confirm":
            return gateway.confirm_cart(order["order_id"], confirmation)
        return gateway.approve(order["order_id"], approver)

    return order, decide


def count_submissions(gateway, monkeypatch):
    submitted, submit = [], gateway.backend.submit

    def counted(order):
        submitted.append(order["order_id"])
        return submit(order)

    monkeypatch.setattr(gateway.backend, "submit", counted)
    return submitted


@pytest.mark.parametrize("flow", ["approve", "confirm"])
@pytest.mark.parametrize("change", ["cutoff", "zero-stock", "partial-stock", "network", "changed-date"])
def test_held_commit_rejects_changed_or_unverified_delivery(flow, change, monkeypatch):
    with World(scenario(flow)) as world:
        gateway = world.gateway
        submitted = count_submissions(gateway, monkeypatch)
        order, decide = held(world, flow)
        product = world.spec.products["1001"]
        at = "2026-11-12T09:59:30+02:00"
        if change == "cutoff":
            at = "2026-11-12T10:01:00+02:00"
        elif change == "zero-stock":
            product["stock"] = 0
        elif change == "partial-stock":
            product["stock"] = 1
        elif change == "changed-date":
            product["available"] = "2026-11-17"
        else:
            def unavailable(*args, **kwargs):
                raise RuntimeError("SENSITIVE_DEBUG must not enter audit")
            monkeypatch.setattr(gateway.client, "availability", unavailable)
        with frozen(datetime.fromisoformat(at), f"decision-{change}"):
            done = decide()
            assert done["status"] == "rejected"
            assert done["order_number"] is None
            assert "receipt" not in done["data"]
            assert done["data"]["commit_revalidation"]["status"] == "blocked"
            assert done["data"]["decision"]["outcome"] == "rejected"
            assert submitted == []
            with pytest.raises(GatewayError):
                decide()
            # Even a stale internal order snapshot cannot become a second submit attempt.
            with pytest.raises(GatewayError, match="not eligible"):
                gateway._commit(order, "stale-callback")
        assert gateway.store.verify_audit()[0]
        log = str(gateway.store.audit_log(order["order_id"]))
        assert "order.commit_blocked" in log
        assert "SENSITIVE_DEBUG" not in log


@pytest.mark.parametrize("flow", ["approve", "confirm"])
def test_held_price_survives_offer_ttl_within_human_hold(flow, monkeypatch):
    with World(scenario(flow, cutoff="2026-11-12T17:00:00+0200")) as world:
        gateway = world.gateway
        submitted = count_submissions(gateway, monkeypatch)
        order, decide = held(world, flow)
        # The one-hour offer TTL is over, while the separate 24-hour human price hold is valid.
        world.spec.products["1001"]["price"] = 9.9
        with frozen(datetime.fromisoformat("2026-11-12T12:00:00+02:00"), "later-within-hold"):
            done = decide()
            assert done["status"] == "placed"
            assert done["total_eur"] == order["total_eur"]
            assert done["data"]["receipt"]
            assert done["data"]["commit_revalidation"]["status"] == "verified"
            assert gateway._commit(order, "stale-callback")["order_number"] == done["order_number"]
            assert submitted == [order["order_id"]]
            with pytest.raises(GatewayError):
                decide()
            assert submitted == [order["order_id"]]


@pytest.mark.parametrize("flow", ["approve", "confirm"])
def test_mandate_expiry_during_human_wait_is_not_overridden(flow, monkeypatch):
    with World(scenario(flow, valid_until="2026-11-12T09:59:15+02:00")) as world:
        submitted = count_submissions(world.gateway, monkeypatch)
        order, decide = held(world, flow)
        with frozen(datetime.fromisoformat("2026-11-12T09:59:30+02:00"), "mandate-expired"):
            done = decide()
        assert done["status"] == "expired"
        assert "receipt" not in done["data"] and done["order_number"] is None
        assert submitted == []
        assert any("mandate expired" in reason for reason in done["data"]["decision"]["reasons"])
        assert world.gateway.store.verify_audit()[0]


@pytest.mark.parametrize("flow", ["approve", "confirm"])
def test_human_hold_expiry_still_blocks_before_committing(flow, monkeypatch):
    with World(scenario(flow, cutoff="2026-11-14T17:00:00+0200")) as world:
        submitted = count_submissions(world.gateway, monkeypatch)
        order, decide = held(world, flow)
        with frozen(datetime.fromisoformat("2026-11-13T10:00:00+02:00"), "hold-expired"):
            with pytest.raises(GatewayError, match="window expired"):
                decide()
        expired = world.gateway.order(order["order_id"])
        assert expired["status"] == "expired"
        assert expired["order_number"] is None and "receipt" not in expired["data"]
        assert submitted == []


def test_cart_confirmation_does_not_exempt_later_approval_from_fresh_stock(monkeypatch):
    with World(scenario("confirm", auto_order_limit_eur=50)) as world:
        gateway = world.gateway
        submitted = count_submissions(gateway, monkeypatch)
        order, decide = held(world, "confirm")
        pending = decide()
        assert pending["status"] == "pending_approval"
        world.spec.products["1001"]["stock"] = 0
        result = gateway.approve(order["order_id"], world.profile().mandate.approvers[0])
        assert result["status"] == "rejected"
        assert submitted == [] and result["order_number"] is None
        assert "receipt" not in result["data"]
        assert result["data"]["cart_confirmation"]["decision"] == "confirmed"


def test_original_explicitly_approved_late_delivery_is_not_silently_redefined(monkeypatch):
    spec = scenario("approve")
    spec.world.products["1001"]["available"] = "2026-11-17"
    with World(spec) as world:
        submitted = count_submissions(world.gateway, monkeypatch)
        order, decide = held(world, "approve")
        assert order["data"]["lines"][0]["offer"]["delivery"]["status"] == "late"
        result = decide()
        assert result["status"] == "placed"
        assert submitted == [order["order_id"]]
        assert result["data"]["commit_revalidation"]["deliveries"][0]["delivery"]["earliest_date"] \
            == "2026-11-17"


def test_availability_call_latency_crossing_cutoff_is_checked_at_response(monkeypatch):
    from trade_agents import service

    with World(scenario("approve")) as world:
        gateway = world.gateway
        submitted = count_submissions(gateway, monkeypatch)
        order, decide = held(world, "approve")
        availability = gateway.client.availability

        def slow_availability(*args, **kwargs):
            response = availability(*args, **kwargs)
            monkeypatch.setattr(service, "now", lambda: datetime.fromisoformat("2026-11-12T10:01:00+02:00"))
            return response

        monkeypatch.setattr(gateway.client, "availability", slow_availability)
        result = decide()
        assert result["status"] == "rejected"
        assert any("cutoff_passed" in reason for reason in result["data"]["decision"]["reasons"])
        assert result["order_number"] is None and "receipt" not in result["data"]
        assert submitted == []


@pytest.mark.parametrize("flow", ["approve", "confirm"])
def test_approved_late_delivery_never_waives_finite_stock(flow, monkeypatch):
    spec = scenario(flow, allow_late_delivery=flow == "confirm")
    spec.world.products["1001"]["available"] = "2026-11-17"
    with World(spec) as world:
        gateway = world.gateway
        submitted = count_submissions(gateway, monkeypatch)
        order, decide = held(world, flow)
        assert order["data"]["lines"][0]["offer"]["delivery"]["status"] == "late"
        world.spec.products["1001"]["stock"] = 1
        result = decide()
        assert result["status"] == "rejected"
        assert result["order_number"] is None and "receipt" not in result["data"]
        checks = result["data"]["commit_revalidation"]["checks"]
        assert any(c["rule"] == "commit.delivery.partial" for c in checks)
        assert "only 1 available" in "; ".join(result["data"]["decision"]["reasons"])
        assert submitted == []
        assert gateway.store.verify_audit()[0]


@pytest.mark.parametrize("flow", ["approve", "confirm"])
def test_approved_late_delivery_with_unknown_terminal_stock_remains_permitted(flow, monkeypatch):
    spec = scenario(flow, allow_late_delivery=flow == "confirm")
    spec.world.products["1001"]["available"] = "2026-11-17"
    with World(spec) as world:
        submitted = count_submissions(world.gateway, monkeypatch)
        order, decide = held(world, flow)
        world.spec.products["1001"]["stock"] = "unlimited"
        result = decide()
        assert result["status"] == "placed"
        assert submitted == [order["order_id"]]
        check = result["data"]["commit_revalidation"]
        assert check["status"] == "verified"
        assert check["deliveries"][0]["delivery"]["available_amount"] is None
