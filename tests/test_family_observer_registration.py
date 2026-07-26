"""Risk families must be declared against observers that exist, and be registrable.

Two properties, both learned from real defects:

1. Every family's declared observer set must compile. ``_OBSERVERS_BY_FAMILY`` named
   "resource_visibility", "clock" and "privacy_surface", none of which were ever in
   OBSERVER_REGISTRY, so ``compile_observer_requirements`` returned
   BLOCKED_MISSING_OBSERVER for the visibility, temporal and privacy families -- 3 of
   the 10 canonical families dead on that path, from three typos nothing checked.

2. A new bug class must be addable WITHOUT editing core code. The bug-type list is open
   by contract, so a taxonomy needing five hand-maintained maps edited per addition is
   a structural ceiling wearing a registry's clothes. ``register_risk_family`` in
   descriptor mode writes the downstream maps and validates all four links up front,
   because each deferred failure has a known bad shape here: an unregistered observer
   blocks, a missing map entry raises KeyError mid-compile, and an assertion kind whose
   evidence nothing produces executes and then dies as a permanent INDETERMINATE.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center import obligation_source_adapter as adapter
from ai_test_asset_center.experiment_compiler_obligation import _FAMILY_ASSERTION_KIND
from ai_test_asset_center.observer_contracts_base import (
    OBSERVER_REGISTRY,
    compile_observer_requirements,
)
from ai_test_asset_center.test_obligation import (
    CANONICAL_RISK_FAMILIES,
    canonical_risk_families,
    make_obligation,
    register_risk_family,
    registry_self_check,
    resolve_risk_family,
)


@pytest.mark.parametrize("family", sorted(adapter._OBSERVERS_BY_FAMILY))
def test_every_family_observer_set_compiles(family: str) -> None:
    observers = list(adapter._OBSERVERS_BY_FAMILY[family])
    compiled, reason_code, detail = compile_observer_requirements(
        observers, risk_family=family, available_adapters={"http_api"}
    )
    assert not reason_code, (
        f"risk family {family!r} declares observers {observers} but compilation "
        f"returned {reason_code}:{detail} -- every obligation in this family would be "
        "blocked on that path"
    )
    assert compiled


@pytest.mark.parametrize("family", sorted(CANONICAL_RISK_FAMILIES))
def test_every_canonical_family_has_all_three_map_entries(family: str) -> None:
    """A canonical family missing a by-family entry raises KeyError mid-compile."""
    assert family in adapter._RELATION_TYPES_BY_FAMILY
    assert family in adapter._TEMPLATE_BY_FAMILY
    assert family in adapter._OBSERVERS_BY_FAMILY


@pytest.mark.parametrize("family", sorted(CANONICAL_RISK_FAMILIES))
def test_every_canonical_family_has_an_assertion_kind(family: str) -> None:
    """Without one the compiler falls back to a bare http_status check."""
    assert family in _FAMILY_ASSERTION_KIND


def test_declared_observers_are_registered_and_implemented() -> None:
    """implemented=False or absent both mean the family cannot observe anything."""
    broken: list[str] = []
    for family, observers in adapter._OBSERVERS_BY_FAMILY.items():
        for observer_id in observers:
            contract = OBSERVER_REGISTRY.get(observer_id)
            if not isinstance(contract, dict) or contract.get("implemented") is not True:
                broken.append(f"{family} -> {observer_id}")
    assert not broken, f"families declaring unusable observers: {sorted(broken)}"


def test_descriptor_registration_needs_no_core_code_edit() -> None:
    """The open entry point: one call makes a new bug class fully compilable."""
    family = "test_only_supply_chain_traceability"
    registered = register_risk_family(
        family,
        relation_types={"produces", "observes"},
        protocol_template="state_transition",
        observers=["http_response", "before_state", "after_state"],
        assertion_kind="state_transition",
    )
    assert registered == family
    assert family in canonical_risk_families()

    # All downstream maps written, so compilation cannot KeyError.
    assert adapter._RELATION_TYPES_BY_FAMILY[family] == {"produces", "observes"}
    assert adapter._TEMPLATE_BY_FAMILY[family] == "state_transition"
    assert adapter._OBSERVERS_BY_FAMILY[family] == [
        "http_response", "before_state", "after_state",
    ]
    assert _FAMILY_ASSERTION_KIND[family] == "state_transition"

    # Resolves as first-class: no narrowing, so no reason code.
    resolved = resolve_risk_family(family)
    assert resolved["canonical"] == family
    assert resolved["reason_code"] == ""

    obligation = make_obligation(
        risk_family=family, subject_refs=["op-x"], property_spec={"k": 1}
    )
    assert obligation["risk_family"] == family
    assert obligation["declared_risk_family"] == family
    registry_self_check()


@pytest.mark.parametrize(
    "label,kwargs",
    [
        (
            "unregistered observer",
            {"relation_types": {"produces"}, "protocol_template": "t",
             "observers": ["no_such_observer_exists"]},
        ),
        (
            "no relation types",
            {"relation_types": set(), "protocol_template": "t",
             "observers": ["http_response"]},
        ),
        (
            "no protocol template",
            {"relation_types": {"produces"}, "protocol_template": "",
             "observers": ["http_response"]},
        ),
        (
            "no observers",
            {"relation_types": {"produces"}, "protocol_template": "t", "observers": []},
        ),
        (
            "assertion kind whose evidence nothing produces",
            {"relation_types": {"produces"}, "protocol_template": "t",
             "observers": ["http_response"],
             "assertion_kind": "cross_surface_consistency"},
        ),
    ],
)
def test_incomplete_descriptor_is_rejected_at_registration(label: str, kwargs: dict) -> None:
    """Fail at registration, not deep inside a later compile.

    Registering a family that cannot compile is worse than refusing it: the failure
    surfaces far from its cause, and in the observer case it surfaces as a per-obligation
    block that looks like a data problem rather than a configuration one.
    """
    with pytest.raises(ValueError):
        register_risk_family(f"test_only_reject_{label.replace(' ', '_')}", **kwargs)


def test_alias_and_descriptor_modes_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError):
        register_risk_family(
            "test_only_both_modes",
            canonical="state",
            relation_types={"produces"},
            protocol_template="t",
            observers=["http_response"],
        )


def test_alias_mode_still_requires_a_canonical_target() -> None:
    with pytest.raises(ValueError):
        register_risk_family("test_only_bad_alias", canonical="not_a_canonical_family")
