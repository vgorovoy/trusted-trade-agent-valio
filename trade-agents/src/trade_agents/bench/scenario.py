"""Scenario and world files (TOML) under `trade-agents/bench/`.

A world is a synthetic slice of the Valio Aimo catalogue with fixed prices, stock and
cut-offs at a frozen moment. A scenario picks a world, patches it, sets the buyer and the
mandate, gives one fixed request and says what the final state must look like.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT

BENCH_DIR = PROJECT_ROOT / "bench"

# Defaults of a product entry in a world file; see bench/README.md.
PRODUCT_DEFAULTS: dict[str, Any] = {
    "name_en": "", "unit": "ST", "base_per_unit": 1.0, "lot": 1.0, "allergens": [],
    "may_contain": [], "claims": [], "ingredients": "", "substitutes": [], "price": None,
    "comparison": None, "comparison_unit": None, "reliability": None, "stock": "unlimited",
    "cutoff": None, "available": None, "sales_block": False, "country": "fi", "price_error": False,
}


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass
class WorldSpec:
    name: str
    now: datetime
    delivery: date                       # default delivery date of the buyer
    cutoff: str                          # default order cut-off (Valio closingTime format)
    products: dict[str, dict[str, Any]]  # product_id -> entry with defaults applied

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> WorldSpec:
        products = {str(pid): {**PRODUCT_DEFAULTS, **p} for pid, p in d["products"].items()}
        return cls(name, datetime.fromisoformat(d["now"]), date.fromisoformat(d["delivery"]),
                   d["cutoff"], products)


@dataclass
class Scenario:
    id: str
    category: str
    title: str
    buyer: str                           # buyer profile: hotel | school | hospital
    request: str
    world: WorldSpec
    expect: dict[str, Any]
    mandate: dict[str, Any] = field(default_factory=dict)  # overrides of the profile mandate
    passport: str | None = "autonomous"  # max_mode of the agent's passport; None = no passport
    prior_orders: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""


def load_world(name: str, patch: dict[str, Any] | None = None,
               bench_dir: Path = BENCH_DIR) -> WorldSpec:
    if name == "live":  # real catalogue; clock and dates come from the scenario's cassette
        return WorldSpec("live", datetime.fromisoformat("2026-11-13T20:00:00+02:00"),
                         date(2026, 11, 16), "", {})
    raw = tomllib.loads((bench_dir / "worlds" / f"{name}.toml").read_text())
    return WorldSpec.from_dict(name, _merge(raw, patch or {}))


def load_scenarios(bench_dir: Path = BENCH_DIR, only: list[str] | None = None,
                   *, tier: str = "full") -> list[Scenario]:
    """All `[[scenario]]` entries of `bench/scenarios/*.toml`, in file order.

    `only`: scenario ids or categories to keep.
    """
    if tier not in {"smoke", "core", "full"}:
        raise ValueError(f"unknown benchmark tier {tier!r}")
    out: list[Scenario] = []
    seen: set[str] = set()
    for path in sorted((bench_dir / "scenarios").glob("*.toml")):
        doc = tomllib.loads(path.read_text())
        for s in doc.get("scenario", []):
            sid = s["id"]
            if sid in seen:
                raise ValueError(f"duplicate scenario id {sid} in {path.name}")
            seen.add(sid)
            passport = s.get("passport", "autonomous")
            out.append(Scenario(
                id=sid, category=s["category"], title=s.get("title", sid), buyer=s["buyer"],
                request=s["request"],
                world=load_world(s.get("world", "dairy"), s.get("world_patch"), bench_dir),
                expect=s["expect"], mandate=s.get("mandate", {}),
                passport=None if passport == "none" else passport,
                prior_orders=s.get("prior_orders", []), source=path.name,
            ))
    selectors = set(only or [])
    known = {s.id for s in out} | {s.category for s in out}
    if unknown := selectors - known:
        raise ValueError(f"unknown scenario ids or categories: {', '.join(sorted(unknown))}")
    if tier != "full":
        manifest = tomllib.loads((bench_dir / "tiers.toml").read_text())
        smoke, core = manifest["smoke"], manifest["core"]
        if (not set(smoke) <= set(core) or not set(core) <= {s.id for s in out}
                or len(set(smoke)) != len(smoke) or len(set(core)) != len(core)):
            raise ValueError("tier manifest must contain unique, known, nested scenario ids")
        allowed = set(manifest[tier])
    else:
        allowed = {s.id for s in out}
    return [s for s in out if s.id in allowed
            and (not selectors or s.id in selectors or s.category in selectors)]
