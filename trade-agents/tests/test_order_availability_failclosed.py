"""A fresh-availability failure cannot turn a formerly valid quote into an order."""
from datetime import date

import pytest

from trade_agents.service import GatewayError, OrderLineIn


@pytest.mark.parametrize("mode", ["autonomous", "confirm_cart", "advise"])
def test_initial_availability_failure_has_no_order_or_private_diagnostics(
        gateway, mandate, sign, monkeypatch, mode):
    mandate = sign(mandate.model_copy(update={"autonomy_mode": mode}))
    passport = gateway.issue_passport(mandate.agent_id, "test", "test", "autonomous")
    quote = gateway.quote(["100"], date(2026, 11, 16))[0]
    assert quote["delivery"]["status"] == "available"
    submitted, refresh_calls = [], []

    def cannot_verify(*args, **kwargs):
        refresh_calls.append(kwargs)
        raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    def submit(order):
        submitted.append(order)
        raise AssertionError("an unverified basket must never reach the backend")

    monkeypatch.setattr(gateway.client, "availability", cannot_verify)
    monkeypatch.setattr(gateway.backend, "submit", submit)
    with pytest.raises(GatewayError) as caught:
        gateway.place_order(mandate, [OrderLineIn(offer_id=quote["offer_id"], quantity=10)],
                            "fresh-check-failed", agent_passport=passport)
    assert str(caught.value) == ("current availability could not be verified; "
                                 "retry with fresh offers")
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in str(caught.value)
    assert caught.value.__suppress_context__ is True
    assert gateway.store.list_orders() == []
    assert submitted == []
    assert len(refresh_calls) == 1 and refresh_calls[0]["force_refresh"] is True
    log = gateway.store.audit_log()
    assert all(entry["event"] not in ("order.received", "order.placed") for entry in log)
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in str(log)
    assert gateway.store.verify_audit()[0]
