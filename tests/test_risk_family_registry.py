"""Risk family registry: openness, losslessness, and cross-registry agreement.

The bug-type list is open by contract (AGENTS.md, Enterprise Business
Comprehension Contract). These tests pin the three properties that make that real:

1. Every resolution target is compilable, so a resolved family can never reach
   obligation_source_adapter's by-family maps without an entry.
2. Resolution is lossless — the declared family and the reason for any narrowing
   survive into the obligation, so a breadth gap is countable rather than
   invisible.
3. bug_ontology_registry (12 families / 88 subtypes) and this registry agree.
   They silently diverged before: tenant_isolation had no entry, so one of the
   product's headline defect classes compiled as a generic validation check.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ai_test_asset_center.bug_ontology_registry import RISK_FAMILIES as ONTOLOGY_FAMILIES
from ai_test_asset_center.test_obligation import (
    CANONICAL_RISK_FAMILIES,
    CAPABILITY_GAP_FAMILIES,
    PROMOTION_CANDIDATE_FAMILIES,
    RISK_FAMILIES,
    RISK_FAMILY_ALIASES,
    RISK_FAMILY_CAPABILITY_GAP_REASON,
    RISK_FAMILY_UNREGISTERED_REASON,
    make_obligation,
    register_risk_family,
    registered_risk_families,
    registry_self_check,
    resolve_risk_family,
)


TEST_OBLIGATION_SOURCE = (
    Path(__file__).resolve().parents[1] / "ai_test_asset_center" / "test_obligation.py"
)


def test_no_registry_constant_is_defined_twice() -> None:
    """A shadowed module-level map is a silently divergent second taxonomy.

    This module exists to collapse several divergent family maps into one authority,
    so a duplicated definition here is the same defect it was written to remove --
    and it is invisible, because the later assignment simply wins.
    ``PROMOTION_CANDIDATE_FAMILIES`` really was defined twice during this refactor.
    """
    tree = ast.parse(TEST_OBLIGATION_SOURCE.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if name.isupper() or name.startswith("_") and name.upper() == name:
                if name in seen:
                    duplicates.append(f"{name} (lines {seen[name]} and {node.lineno})")
                else:
                    seen[name] = node.lineno
    assert not duplicates, (
        "module-level constants assigned more than once in test_obligation.py; the "
        f"later assignment silently shadows the earlier one: {duplicates}"
    )


def test_every_resolution_target_is_canonical() -> None:
    registry_self_check()


def test_risk_families_alias_stays_canonical() -> None:
    """RISK_FAMILIES is the backward-compatible name for the canonical tuple."""
    assert RISK_FAMILIES == CANONICAL_RISK_FAMILIES


def test_every_ontology_family_resolves() -> None:
    """No bug_ontology_registry family may fall through as unregistered.

    A family that falls through resolves to "validation" with
    RISK_FAMILY_NOT_REGISTERED, which is exactly the invisible collapse this
    registry exists to prevent.
    """
    unresolved = {
        family: resolve_risk_family(family)
        for family in ONTOLOGY_FAMILIES
        if not resolve_risk_family(family)["registered"]
    }
    assert not unresolved, (
        "bug_ontology_registry families with no entry in the risk family registry: "
        f"{sorted(unresolved)}"
    )


def test_ontology_families_resolve_to_compilable_families() -> None:
    for family in ONTOLOGY_FAMILIES:
        assert resolve_risk_family(family)["canonical"] in CANONICAL_RISK_FAMILIES


def test_tenant_isolation_resolves_to_isolation_not_validation() -> None:
    """Regression pin for the concrete defect this registry was built to fix.

    tenant_isolation is the bug_ontology_registry family id for cross-tenant
    access. It was absent from the old alias map, so it fell through to
    "validation" and compiled as a status-code check that could not detect an
    isolation defect at all.
    """
    resolved = resolve_risk_family("tenant_isolation")
    assert resolved["canonical"] == "isolation"
    assert resolved["registered"] is True


def test_declared_family_is_preserved_when_narrowed() -> None:
    obligation = make_obligation(
        risk_family="tenant_isolation",
        subject_refs=["op_orders_get"],
        property_spec={"template": "owner_viewer_isolation"},
    )
    assert obligation["risk_family"] == "isolation"
    assert obligation["declared_risk_family"] == "tenant_isolation"
    assert obligation["risk_family_resolution"]["reason_code"]


def test_unknown_family_is_flagged_not_silently_rewritten() -> None:
    """An unrecognized family must stay visible.

    It still compiles under a canonical family so the obligation is not lost, but
    the declared name and a reason code are recorded, which is what makes a
    missing capability countable instead of indistinguishable from a real
    validation obligation.
    """
    obligation = make_obligation(
        risk_family="some_unmodelled_defect_class",
        subject_refs=["op_x"],
        property_spec={"k": 1},
    )
    resolution = obligation["risk_family_resolution"]
    assert obligation["declared_risk_family"] == "some_unmodelled_defect_class"
    assert resolution["registered"] is False
    assert resolution["reason_code"] == RISK_FAMILY_UNREGISTERED_REASON
    assert obligation["risk_family"] in CANONICAL_RISK_FAMILIES


def test_capability_gap_family_is_distinguishable_from_a_plain_alias() -> None:
    """audit_trail has no assertion kind and no observer — a real four-link gap.

    It must not be reported with the same reason code as a semantically correct
    alias, or the gap list cannot be derived from run data.
    """
    for family in CAPABILITY_GAP_FAMILIES:
        resolved = resolve_risk_family(family)
        assert resolved["reason_code"] == RISK_FAMILY_CAPABILITY_GAP_REASON
        assert resolved["registered"] is True


def test_promotion_candidates_and_aliases_do_not_overlap() -> None:
    """One name, one resolution path — overlapping maps would make order matter."""
    maps = {
        "aliases": set(RISK_FAMILY_ALIASES),
        "promotion_candidates": set(PROMOTION_CANDIDATE_FAMILIES),
        "capability_gaps": set(CAPABILITY_GAP_FAMILIES),
        "canonical": set(CANONICAL_RISK_FAMILIES),
    }
    names = list(maps)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            overlap = maps[left] & maps[right]
            assert not overlap, f"{left} and {right} both define: {sorted(overlap)}"


def test_registration_entry_point_accepts_a_new_family() -> None:
    """The open entry point: a new bug class needs no core-code edit."""
    resolved = register_risk_family("supply_chain_traceability", canonical="state")
    assert resolved == "state"
    assert resolve_risk_family("supply_chain_traceability")["canonical"] == "state"
    assert "supply_chain_traceability" in registered_risk_families()
    registry_self_check()


def test_registration_rejects_a_non_compilable_canonical() -> None:
    """Fail fast rather than register a family that would KeyError at compile time."""
    with pytest.raises(ValueError):
        register_risk_family("some_family", canonical="not_a_real_family")


def test_registration_requires_a_name() -> None:
    with pytest.raises(ValueError):
        register_risk_family("")
