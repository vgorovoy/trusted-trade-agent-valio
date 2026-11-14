"""Local synthetic Exchange with a ready-to-review price/reliability choice.

No provider credentials, Valio calls or production state. Restart to reset decisions.
Run as a standalone process; the benchmark world intentionally fixes this process's clock.
"""
from __future__ import annotations

import os

import uvicorn

from trade_agents.bench import load_scenarios
from trade_agents.bench.world import World
from trade_agents.buyer_agent.tools import AgentTools, InProcessTransport


def main() -> None:
    os.environ["TTA_LOCAL"] = "1"
    os.environ["TTA_DEMO_WALLET"] = "1"
    os.environ["TTA_LLM_PROVIDER"] = "offline"
    os.environ["TTA_VALIO_REVIEW"] = "off"
    os.environ.pop("TTA_LLM_MODEL", None)
    os.environ.pop("TTA_DEMO_CLOCK", None)
    scenario = load_scenarios(only=["pref-reliability"])[0]
    with World(scenario) as world:
        from trade_agents import api
        from web import server
        profile = world.profile(model="offline-demo")
        tools = AgentTools(InProcessTransport(world.gateway), profile)
        planned = tools.call("plan_need", {"query": "maito", "amount": 200, "unit": "L",
                            "requested_date": world.scenario.world.delivery.isoformat(),
                            "product_ids": ["1001", "1002"]})
        if planned.error or tools.choice is None:
            raise RuntimeError("could not prepare the synthetic human-choice fixture")
        server.gateway = lambda: world.gateway
        api.app.dependency_overrides[api.gateway] = lambda: world.gateway
        print("SYNTHETIC OFFLINE DEMO: no real supplier orders or model calls. Restart to reset.")
        uvicorn.run(server.app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
