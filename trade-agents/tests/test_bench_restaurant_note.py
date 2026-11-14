"""Local supplied-photo specification integrity, not model-quality evaluation."""
from __future__ import annotations

import hashlib
import json

import pytest

from trade_agents.bench.menu_eval import MENU_DIR, load_menu_cases

DIRECTORY = MENU_DIR / "restaurant-note-v1"


def specification():
    return json.loads((DIRECTORY / "specifications" /
                       "01-restaurant-note-clarification.json").read_text())


def test_photo_bytes_pinned_and_publication_rights_not_assumed():
    spec = specification()
    data = (DIRECTORY / spec["asset"]["path"]).read_bytes()
    assert data.startswith(b"\xff\xd8\xff")
    assert hashlib.sha256(data).hexdigest() == spec["asset"]["sha256"]
    provenance = spec["provenance"]
    assert provenance["independently_verified"] is False
    assert provenance["public_redistribution_license"] == "unspecified"
    assert "internal repository authorized" in provenance["publication_status"]


def test_incomplete_photo_is_clarification_spec_never_executable_procurement_pass():
    spec = specification()
    assert spec["execution_status"] == "capability_gated_not_executable"
    gate = spec["capability_gate"]
    assert gate["included_in_executable_suite"] is False and gate["score"] is None
    assert len(gate["missing_capabilities"]) == 4
    oracle = spec["exact_oracle"]
    assert oracle["required_status"] == "needs_clarification"
    assert oracle["orders_created"] == oracle["signed_purchase_authorizations_in_photo"] == 0
    # The current loader refuses an empty top-level fixture suite rather than
    # silently treating a nested operational specification as an evaluated case.
    assert not list(DIRECTORY.glob("*.json"))
    with pytest.raises(ValueError, match="unique IDs"):
        load_menu_cases(DIRECTORY)


def test_uncertain_handwriting_is_not_promoted_to_exact_oracle_facts():
    spec = specification()
    reference = spec["visual_reference"]
    assert len(reference["clear_text"]) == 6
    assert len(reference["uncertain_text"]) == 3
    assert all("required_handling" in row for row in reference["uncertain_text"])
    assert all("amount" not in row and "date" not in row for row in reference["clear_text"])
    assert any("Numeric quantity" in fact for fact in reference["missing_facts"])
    assert any("calendar" in fact for fact in reference["missing_facts"])
    assert "Valio Aimo only" in spec["supplier_scope"]

pytestmark = pytest.mark.skipif(
    not DIRECTORY.exists(),
    reason="Private fixture assets are intentionally excluded from the public snapshot",
)
