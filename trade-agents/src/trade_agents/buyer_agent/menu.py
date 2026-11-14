"""Menu photo -> purchasing needs -> one agent run per need.

A vision model reads a weekly menu or a handwritten shopping list and, for a given number
of guests, estimates the Valio Aimo dairy/wholesale ingredients to buy: ingredient,
amount, unit and delivery date. Each need then goes through the normal buyer agent.
"""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..llm import LLM, LLMError
from .agent import AgentResult, BuyerAgent
from .profiles import BuyerProfile

MENU_PROMPT = """\
You plan purchasing for a professional kitchen.
The image is a weekly menu or a handwritten shopping list. {guests} guests per meal.
Today is {today}. Delivery dates: for a dish on a weekday, the delivery is due the day \
before it is served (or the menu's own date if it is a shopping list); default {default}.
{kitchen}

List the ingredients this kitchen must buy from a food wholesaler for these meals, focusing \
on dairy and chilled staples (milk, cream, butter, cheese, yoghurt, quark, eggs, plant \
drinks). Estimate amounts for {guests} guests with normal professional portions and round \
up to whole litres or kilograms. Merge the same ingredient for the same date.

Answer only JSON:
{{"needs": [{{"ingredient": "<English name>", "search_hint_fi": "<Finnish word, e.g. maito>",
  "amount": <number>, "unit": "L" | "KG" | "ST", "date": "YYYY-MM-DD",
  "for_dishes": ["..."], "constraints": ["lactose-free", ...]}}],
 "notes": "<anything unreadable or assumed>"}}
"""


@dataclass
class Need:
    ingredient: str
    amount: float
    unit: str
    date: str
    search_hint_fi: str = ""
    for_dishes: list[str] | None = None
    constraints: list[str] | None = None

    def as_request(self) -> str:
        hint = f" (Finnish: {self.search_hint_fi})" if self.search_hint_fi else ""
        cons = f", {', '.join(self.constraints)}" if self.constraints else ""
        dishes = f" for {', '.join(self.for_dishes)}" if self.for_dishes else ""
        return (f"{self.amount:g} {self.unit} {self.ingredient}{hint}{cons}, delivery "
                f"{self.date}{dishes}.")


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def extract_needs(llm: LLM, image: bytes, mime: str, guests: int, profile: BuyerProfile,
                  today: date | None = None) -> tuple[list[Need], str]:
    if not getattr(llm, "vision", False):
        raise LLMError(f"{getattr(llm, 'name', 'this model')} cannot read images; "
                       "use TTA_LLM_PROVIDER=gemini (or an OpenAI vision model)")
    from datetime import datetime

    from ..clock import current_time
    from ..config import TZ
    today = today or current_time(datetime.now(TZ)).date()
    prompt = MENU_PROMPT.format(guests=guests, today=today.isoformat(),
                                default=profile.delivery_default(today).isoformat(),
                                kitchen=profile.context(today))
    resp = llm.chat([{"role": "user", "content": prompt, "images": [(image, mime)]}],
                    json_output=True)
    data = _parse_json(resp.text)
    needs = []
    for n in data.get("needs", []):
        try:
            needs.append(Need(ingredient=str(n["ingredient"]), amount=float(n["amount"]),
                              unit=str(n.get("unit") or "KG").upper(), date=str(n["date"]),
                              search_hint_fi=str(n.get("search_hint_fi") or ""),
                              for_dishes=n.get("for_dishes") or None,
                              constraints=n.get("constraints") or None))
        except (KeyError, TypeError, ValueError):
            continue
    return merge_needs(needs), str(data.get("notes") or "")


def merge_needs(needs: list[Need]) -> list[Need]:
    """One need per (search hint or ingredient, unit, date): amounts add up, dishes and
    constraints are united. Avoids two orders for the same product on the same day."""
    merged: dict[tuple[str, str, str], Need] = {}
    for n in needs:
        key = ((n.search_hint_fi or n.ingredient).strip().lower(), n.unit, n.date)
        if key not in merged:
            merged[key] = n
            continue
        m = merged[key]
        m.amount += n.amount
        for attr in ("for_dishes", "constraints"):
            both = list(dict.fromkeys((getattr(m, attr) or []) + (getattr(n, attr) or [])))
            setattr(m, attr, both or None)
    return list(merged.values())


@dataclass
class MenuResult:
    guests: int
    needs: list[Need]
    notes: str
    results: list[AgentResult]

    def to_dict(self) -> dict[str, Any]:
        return {"guests": self.guests, "needs": [asdict(n) for n in self.needs],
                "notes": self.notes, "results": [r.to_dict() for r in self.results]}


def run_menu(image_path: str | Path, guests: int, profile: BuyerProfile, *,
             llm: LLM, agent: BuyerAgent | None = None, max_needs: int = 8,
             today: date | None = None) -> MenuResult:
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    needs, notes = extract_needs(llm, path.read_bytes(), mime, guests, profile, today)
    agent = agent or BuyerAgent(profile, llm, today=today)
    results = [agent.run(n.as_request()) for n in needs[:max_needs]]
    return MenuResult(guests, needs, notes, results)
