"""Valio mentor clarifications: unknown stock, empty means unavailable, 429 cooldown."""
from __future__ import annotations

from datetime import UTC, date, datetime
from email.utils import format_datetime

import httpx
import pytest

from trade_agents import valio_client
from trade_agents.config import TZ
from trade_agents.offers import assess_delivery
from trade_agents.service import OrderLineIn
from trade_agents.valio_client import ValioApiError, ValioClient

DAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 13, 12, tzinfo=TZ)


def availability(amount, **overrides):
    return {'closingTime': '2026-11-15T17:00:00+0200', 'logisticModel': 'Terminal',
            'scheduleLines': [{'availableDate': DAY.isoformat(), 'availableAmount': amount}],
            **overrides}


@pytest.mark.parametrize('amount', [99999, 100000, 999998])
def test_below_sentinel_is_finite_stock(amount):
    delivery = assess_delivery(availability(amount), DAY, amount + 1, NOW)
    assert delivery.status == 'partial'
    assert delivery.available_amount == amount
    assert not any('stock unknown' in n for n in delivery.notes)


@pytest.mark.parametrize('amount', [999999, 666666666, 9999999999])
def test_sentinel_allows_quantity_without_claiming_verified_stock(amount):
    delivery = assess_delivery(availability(amount), DAY, amount * 2, NOW)
    assert delivery.status == 'available'
    assert delivery.available_amount is None
    assert any('stock unknown' in n and 'not verified unlimited stock' in n for n in delivery.notes)


@pytest.mark.parametrize(('change', 'status'), [
    ({'salesBlock': True}, 'sales_blocked'),
    ({'closingTime': '2026-11-12T10:00:00+0200'}, 'cutoff_passed'),
    ({'scheduleLines': [{'availableAmount': 999999, 'availableDate': '2026-11-17'}]}, 'late'),
])
def test_unknown_stock_still_obeys_delivery_constraints(change, status):
    assert assess_delivery(availability(999999, **change), DAY, 10, NOW).status == status


@pytest.mark.parametrize('record', [None, {}, {'scheduleLines': []}])
def test_empty_availability_is_not_available(record):
    delivery = assess_delivery(record, DAY, 10, NOW)
    assert delivery.status == 'out_of_stock'
    assert delivery.available_amount == 0
    assert 'not available' in delivery.notes[0]


def test_empty_availability_cannot_be_planned_or_ordered(gateway, mandate, monkeypatch):
    from conftest import AVAIL

    # Other legacy tests alter the shared API fixture; define this scenario explicitly.
    monkeypatch.setitem(AVAIL, '400', {'salesBlock': False, 'scheduleLines': []})
    plan = gateway.plan('oat', 10, 'L', DAY, product_ids=['400'])
    assert not plan['options']
    assert 'not available' in plan['ruled_out'][0]['reason']
    offer = gateway.quote(['400'], DAY)[0]
    order = gateway.place_order(mandate, [OrderLineIn(offer_id=offer['offer_id'], quantity=10)],
                                'empty-availability-block', 'must not place')
    assert order['status'] == 'rejected'


def test_unknown_stock_explanation_reaches_signed_quote_and_plan(gateway):
    quote = gateway.quote(['100'], DAY)[0]
    assert any('stock unknown' in n for n in quote['delivery']['notes'])
    assert not any('99999 treated' in a for a in quote['assumptions'])
    plan = gateway.plan('milk', 10, 'L', DAY, product_ids=['100'])
    assert any('stock unknown' in n for n in plan['options'][0]['lines'][0]['delivery_notes'])


@pytest.fixture
def virtual_time(monkeypatch):
    clock = {'now': 0.0, 'sleeps': []}
    def sleep(seconds):
        assert seconds >= 0
        clock['sleeps'].append(seconds)
        clock['now'] += seconds
    monkeypatch.setattr(valio_client.time, 'monotonic', lambda: clock['now'])
    monkeypatch.setattr(valio_client.time, 'sleep', sleep)
    monkeypatch.setattr(valio_client.time, 'time', lambda: 1800000000.0 + clock['now'])
    return clock


def client_for(settings, statuses, clock, headers=None):
    seen, responses = [], []
    def handler(request):
        seen.append((request.method, request.url.path, clock['now']))
        status = statuses.pop(0)
        response = httpx.Response(status, headers=headers if status == 429 else {},
                                  json={'products': [], 'status': 'ok'})
        responses.append(response)
        return response
    return ValioClient(settings, transport=httpx.MockTransport(handler)), seen, responses


@pytest.mark.parametrize(('header', 'wait'), [(None, 30), ('2', 30), ('60', 60),
                                             ('bad-date', 30), ('-1', 30)])
def test_429_minimum_cooldown_and_retry_after(settings, virtual_time, header, wait):
    headers = {'Retry-After': header} if header is not None else {}
    client, seen, responses = client_for(settings, [429, 200], virtual_time, headers)
    assert client.prices(['100'], DAY) == {}
    assert virtual_time['sleeps'] == [wait]
    assert [t for _, _, t in seen] == [0, wait]
    assert all(r.is_closed for r in responses)


def test_429_http_date(settings, virtual_time):
    at = datetime.fromtimestamp(1800000000 + 75, UTC)
    client, seen, _ = client_for(settings, [429, 200], virtual_time,
                                {'Retry-After': format_datetime(at, usegmt=True)})
    client.availability(['100'], DAY)
    assert seen[-1][2] == 75


def test_repeated_429_bounded_and_next_request_keeps_cooldown(settings, virtual_time):
    client, seen, _ = client_for(settings, [429, 429, 429, 429, 200], virtual_time)
    with pytest.raises(ValioApiError, match='HTTP 429'):
        client.prices(['100'], DAY)
    assert virtual_time['sleeps'] == [30, 30, 30]
    assert len(seen) == 4
    client.availability(['100'], DAY)
    assert virtual_time['sleeps'] == [30, 30, 30, 30]
    assert seen[-1][2] == 120


def test_500_backoff_stays_short(settings, virtual_time):
    client, _, _ = client_for(settings, [503, 503, 200], virtual_time)
    client.prices(['100'], DAY)
    assert virtual_time['sleeps'] == [1, 2]


@pytest.mark.parametrize('operation', ['ping', 'download'])
def test_cooldown_also_applies_to_get_endpoints(settings, virtual_time, tmp_path, operation):
    client, seen, responses = client_for(settings, [429, 200], virtual_time)
    if operation == 'ping':
        assert client.ping()
    else:
        target = tmp_path / 'products.json'
        client.download('products', target)
        assert target.exists()
        assert not target.with_suffix('.json.part').exists()
    assert seen[-1][2] == 30
    assert all(r.is_closed for r in responses)


def test_nonretryable_failure_does_not_sleep(settings, virtual_time):
    client, seen, _ = client_for(settings, [400], virtual_time)
    with pytest.raises(ValioApiError, match='HTTP 400'):
        client.prices(['100'], DAY)
    assert len(seen) == 1 and not virtual_time['sleeps']
