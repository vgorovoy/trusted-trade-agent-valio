"""Wall receipt time remains independent of the frozen business clock."""
from datetime import UTC, datetime

from trade_agents.bench.scenario import Scenario, load_world
from trade_agents.bench.world import World
from trade_agents.service import OrderLineIn


def test_wall_receipt_time_does_not_change_business_clock_or_idempotency():
    sc = Scenario(id="wall-time", category="regression", title="Independent clocks",
                  buyer="hotel", request="milk", expect={},
                  world=load_world("dairy"), mandate={"autonomy_mode": "confirm_cart"})
    before = datetime.now(UTC)
    with World(sc) as world:
        offer = world.gateway.quote(["1001"], world.spec.delivery)[0]
        args = (world.profile().mandate,
                [OrderLineIn(offer_id=offer["offer_id"], quantity=1)], "wall-time-key")
        first = world.gateway.place_order(*args)
        second = world.gateway.place_order(*args)
        assert first["status"] == "awaiting_cart_confirmation"
        assert first["created_at"] != first["data"]["received_at_wall"]
        assert first["data"]["received_at_wall"] == second["data"]["received_at_wall"]
    assert before <= datetime.fromisoformat(first["data"]["received_at_wall"]) <= datetime.now(UTC)
