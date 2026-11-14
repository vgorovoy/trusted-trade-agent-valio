"""Human trade-off choices never weaken gateway safety or place both baskets."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta

import pytest

from trade_agents.buyer_agent.profiles import BuyerProfile
from trade_agents.choices import ChoiceService
from trade_agents.service import GatewayError


@pytest.fixture
def choice_setup(gateway, mandate):
    passport = gateway.issue_passport(mandate.agent_id, "test", "test", "autonomous")
    profile = BuyerProfile("hotel", "Test hotel", mandate, passport=passport, max_premium_pct=5)
    offers = gateway.quote(["100", "300"], date(2026, 11, 16), amount=200, unit="L")
    options = [{"lines": [{**o, "quantity": 200}], "total_eur": -999} for o in offers]
    return ChoiceService(gateway), profile, options


def test_choice_creates_no_order_and_uses_canonical_data(choice_setup, gateway):
    service, profile, options = choice_setup
    options[0]["lines"][0]["delivery_reliability"] = 0.01  # not authoritative UI data
    c = service.create(profile, options, "same-request")
    assert c["status"] == "awaiting_choice"
    assert [o["total_eur"] for o in c["options"]] == [280, 240]
    assert c["options"][0]["delivery_reliability"] == .99
    assert "mandate" not in c and "passport" not in c
    assert gateway.store.list_orders() == []
    assert service.create(profile, options, "same-request")["choice_id"] == c["choice_id"]
    assert ChoiceService(gateway).list(profile.mandate.buyer_id)[0]["choice_id"] == c["choice_id"]


@pytest.mark.parametrize("selection", [0, 1])
def test_human_selects_either_basket_once(choice_setup, gateway, selection):
    service, profile, options = choice_setup
    c = service.create(profile, options, "choose")
    done = service.decide(c["choice_id"], "chef@hotel", selection, "Reviewed trade-off")
    assert done["status"] == "selected"
    assert done["order"]["status"] == "placed"
    assert len(gateway.store.list_orders()) == 1
    assert done["order"]["data"]["lines"][0]["offer"]["product_id"] == ["100", "300"][selection]
    assert service.decide(c["choice_id"], "chef@hotel", selection)["order"] == done["order"]
    with pytest.raises(GatewayError, match="resolved"):
        service.decide(c["choice_id"], "chef@hotel", 1 - selection)
    assert gateway.store.verify_audit()[0]
    if selection == 1:
        assert done["order"]["data"]["approvals"][0]["by"] == "chef@hotel"


def test_decline_both_and_authorization(choice_setup, gateway):
    service, profile, options = choice_setup
    c = service.create(profile, options, "decline")
    with pytest.raises(GatewayError, match="approver"):
        service.decide(c["choice_id"], "agent", 0)
    done = service.decide(c["choice_id"], "chef@hotel", None)
    assert done["status"] == "declined" and done["order"] is None
    assert service.decide(c["choice_id"], "chef@hotel", None)["status"] == "declined"
    with pytest.raises(GatewayError, match="resolved"):
        service.decide(c["choice_id"], "chef@hotel", 0)
    assert gateway.store.list_orders() == []


def test_concurrent_selection_cannot_place_two_orders(choice_setup, gateway):
    service, profile, options = choice_setup
    c = service.create(profile, options, "concurrent")

    def choose(selection):
        try:
            return service.decide(c["choice_id"], "chef@hotel", selection)["status"]
        except GatewayError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(choose, [0, 1]))
    assert sorted(result) == ["conflict", "selected"]
    assert len(gateway.store.list_orders()) == 1


def test_unavailable_and_expired_choices_cannot_be_selected(choice_setup, gateway, monkeypatch):
    service, profile, options = choice_setup
    c = service.create(profile, options, "unavailable")
    monkeypatch.setattr(gateway.client, "availability", lambda *_, **__: {})
    with pytest.raises(GatewayError, match="blocked"):
        service.decide(c["choice_id"], "chef@hotel", 0)
    assert gateway.store.list_orders() == []
    from trade_agents import choices
    clock = choices.now()
    monkeypatch.setattr(choices, "now", lambda: clock + timedelta(days=5))
    with pytest.raises(GatewayError, match="expired"):
        service.decide(c["choice_id"], "chef@hotel", 0)


def test_hard_allergens_and_unverified_options_not_presented(choice_setup, sign):
    service, profile, options = choice_setup
    profile.mandate = sign(profile.mandate.model_copy(update={"excluded_allergens": ["milk"]}))
    with pytest.raises(GatewayError, match="eligible"):
        service.create(profile, options, "allergen")
    profile.mandate = sign(profile.mandate.model_copy(update={"excluded_allergens": []}))
    options[0]["lines"][0]["signature"] = "tampered"
    with pytest.raises(GatewayError, match="eligible"):
        service.create(profile, options, "tampered")


def test_fail_closed_if_availability_cannot_be_checked(choice_setup, gateway, monkeypatch):
    service, profile, options = choice_setup
    c = service.create(profile, options, "network-failure")

    def fail(*_, **__):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(gateway.client, "availability", fail)
    with pytest.raises(GatewayError, match="cannot verify"):
        service.decide(c["choice_id"], "chef@hotel", 0)
    assert gateway.store.list_orders() == []


def test_advice_cannot_be_promoted_and_confirm_cart_remains_signed(choice_setup, sign, gateway):
    service, profile, options = choice_setup
    profile.mandate = sign(profile.mandate.model_copy(update={"autonomy_mode": "advise"}))
    with pytest.raises(GatewayError, match="advice-only"):
        service.create(profile, options, "advice")
    profile.mandate = sign(profile.mandate.model_copy(update={"autonomy_mode": "confirm_cart"}))
    c = service.create(profile, options, "confirm")
    done = service.decide(c["choice_id"], "chef@hotel", 0)
    assert done["order"]["status"] == "awaiting_cart_confirmation"
    assert "receipt" not in done["order"]["data"]
    assert len(gateway.store.list_orders()) == 1


def test_invalid_selection_and_duplicate_baskets(choice_setup):
    service, profile, options = choice_setup
    with pytest.raises(GatewayError, match="differ"):
        service.create(profile, [options[0], deepcopy(options[0])], "duplicates")
    c = service.create(profile, options, "invalid-index")
    for index in (True, -1, 2, "0"):
        with pytest.raises(GatewayError, match="invalid"):
            service.decide(c["choice_id"], "chef@hotel", index)


def test_retry_after_commit_before_resolution_is_idempotent(choice_setup, gateway, monkeypatch):
    service, profile, options = choice_setup
    c = service.create(profile, options, "interrupted")
    audit = gateway.store.audit

    def interrupted(actor, event, *args, **kwargs):
        if event == "choice.resolved":
            raise RuntimeError("process interrupted after order commit")
        return audit(actor, event, *args, **kwargs)

    monkeypatch.setattr(gateway.store, "audit", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        service.decide(c["choice_id"], "chef@hotel", 0)
    monkeypatch.setattr(gateway.store, "audit", audit)
    from trade_agents import choices
    clock = choices.now()
    monkeypatch.setattr(choices, "now", lambda: clock + timedelta(days=5))
    result = service.decide(c["choice_id"], "chef@hotel", 0)
    assert result["order"]["status"] == "placed"
    assert len(gateway.store.list_orders()) == 1


@pytest.mark.parametrize("commit_first", [False, True])
def test_creation_crash_never_publishes_two_actionable_choices(
        choice_setup, gateway, monkeypatch, commit_first):
    service, profile, options = choice_setup
    audit = gateway.store.audit
    saved_ids = []
    save_offer = gateway.store.save_offer

    def capture_document(*args, **kwargs):
        oid = save_offer(*args, **kwargs)
        saved_ids.append(oid)
        return oid

    def interrupted(actor, event, *args, **kwargs):
        if event == "choice.created":
            if commit_first:
                audit(actor, event, *args, **kwargs)
            raise RuntimeError("interrupted publishing choice")
        return audit(actor, event, *args, **kwargs)

    monkeypatch.setattr(gateway.store, "save_offer", capture_document)
    monkeypatch.setattr(gateway.store, "audit", interrupted)
    with pytest.raises(RuntimeError, match="publishing"):
        service.create(profile, options, "creation-crash")
    orphan_id = saved_ids[0]
    if not commit_first:
        with pytest.raises(GatewayError, match="not published"):
            service.decide(orphan_id, "chef@hotel", 0)
    monkeypatch.setattr(gateway.store, "audit", audit)
    c = ChoiceService(gateway).create(profile, options, "creation-crash")
    assert len(service.list(profile.mandate.buyer_id)) == 1
    assert (c["choice_id"] == orphan_id) is commit_first
    service.decide(c["choice_id"], "chef@hotel", 0)
    assert len(gateway.store.list_orders()) == 1
    assert gateway.store.verify_audit()[0]


def test_creation_no_longer_has_separate_index_commit(choice_setup, gateway, monkeypatch):
    service, profile, options = choice_setup
    audit = gateway.store.audit

    def fail_old_index(actor, event, *args, **kwargs):
        if event == "choice.indexed":
            raise RuntimeError("former crash window")
        return audit(actor, event, *args, **kwargs)

    monkeypatch.setattr(gateway.store, "audit", fail_old_index)
    first = service.create(profile, options, "former-index-window")
    again = ChoiceService(gateway).create(profile, options, "former-index-window")
    assert first["choice_id"] == again["choice_id"]
    assert len(service.list()) == 1


def test_choice_rechecks_real_availability_instead_of_cached_stock(choice_setup, gateway, monkeypatch):
    from conftest import AVAIL

    service, profile, options = choice_setup
    c = service.create(profile, options, "fresh-stock")
    # The first query/create cached an available schedule. The supplier now returns empty.
    monkeypatch.setitem(AVAIL, "100", {"salesBlock": False, "scheduleLines": []})
    with pytest.raises(GatewayError, match="blocked"):
        service.decide(c["choice_id"], "chef@hotel", 0)
    assert gateway.store.list_orders() == []
    assert service.get(c["choice_id"])["status"] == "awaiting_choice"
