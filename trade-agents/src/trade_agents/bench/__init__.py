"""Agent bench: fixed buyer requests in frozen synthetic worlds, checked deterministically.

scenario (TOML) -> world (catalogue + Valio API answers + frozen clock) -> buyer agent
-> verifiers over the gateway's final state -> accuracy per category.

`uv run tta bench` runs the suite; see `bench/README.md` for the scenario format.
"""

from .runner import BenchReport, ScenarioResult, run_bench
from .scenario import Scenario, load_scenarios

__all__ = ["BenchReport", "Scenario", "ScenarioResult", "load_scenarios", "run_bench"]
