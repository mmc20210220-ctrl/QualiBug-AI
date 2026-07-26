"""Kind-to-evidence and observer-dispatch contracts.

Four capabilities were compiled and executed while being structurally incapable of
returning a verdict. That is worse than a missing feature: it spends real requests
against a customer system, consumes budget, and reports a shape that looks like
coverage.

* Three assertion kinds read observation keys nothing writes. Verified by enumerating
  every ``observations["..."] =`` assignment in the package (58 distinct keys):
  ``collection``, ``invariant_held`` and ``surfaces_agree`` are written by nothing.
  Empirically, ``cross_surface_consistency`` produced 78 receipts across the stored
  artifacts, every one INDETERMINATE with CROSS_SURFACE_EVIDENCE_MISSING.
* ``temporal_date_boundary`` is compiled as an assertion kind by
  experiment_protocols_base but exists in no SUPPORTED_KINDS set, so it can only land
  as a harness error.
* ``write_observer`` was registered ``implemented=True`` with no dispatch branch, so it
  passed compile_observer_requirements and then returned UNSUPPORTED at runtime.

These tests derive the produced-key set and the dispatch set from source, so a new
kind or observer added without a producer fails here rather than silently in
production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_test_asset_center.assertion_dsl_base import (
    KIND_ALIASES,
    KIND_REQUIRED_OBSERVATION_KEYS,
    SUPPORTED_KINDS,
    UNPRODUCED_OBSERVATION_KEYS,
    UNPRODUCIBLE_ASSERTION_KINDS,
    unproducible_assertion_evidence,
)
from ai_test_asset_center.experiment_compiler_obligation import BLOCK_REASONS
from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY


PACKAGE = Path(__file__).resolve().parents[1] / "ai_test_asset_center"
OBSERVER_SOURCE = PACKAGE / "observer_contracts_base.py"


def _produced_observation_keys() -> set[str]:
    """Every key assigned into an observations dict anywhere in the package."""
    produced: set[str] = set()
    pattern = re.compile(r'observations\[\s*"([a-z_0-9]+)"\s*\]\s*=')
    for path in PACKAGE.glob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        produced.update(pattern.findall(source))
    return produced


def _dispatched_observer_ids() -> set[str]:
    """Observer ids with a real branch in observe_experiment_requirements."""
    source = OBSERVER_SOURCE.read_text(encoding="utf-8")
    marker = "def observe_experiment_requirements"
    assert marker in source, "observe_experiment_requirements not found"
    body = source[source.index(marker):]
    dispatched = set(re.findall(r'observer_id == "([a-z_0-9]+)"', body))
    for group in re.findall(r"observer_id in \{([^}]*)\}", body):
        dispatched.update(re.findall(r'"([a-z_0-9]+)"', group))
    return dispatched


def test_produced_observation_keys_are_discoverable() -> None:
    produced = _produced_observation_keys()
    assert len(produced) > 40, f"key extraction looks broken: {sorted(produced)}"
    # Anchors that must always be produced.
    assert {"before_state", "after_state", "execution_steps"} <= produced


@pytest.mark.parametrize("key", sorted(UNPRODUCED_OBSERVATION_KEYS))
def test_recorded_unproduced_keys_are_still_unproduced(key: str) -> None:
    """When a producer lands, delete the entry -- do not leave it stale.

    A stale entry would keep blocking (or keep flagging) a capability that actually
    works, which is the mirror image of the defect this file exists for.
    """
    produced = _produced_observation_keys()
    assert key not in produced, (
        f"{key!r} is now produced by an observer; remove it from "
        f"UNPRODUCED_OBSERVATION_KEYS and, if present, from "
        f"UNPRODUCIBLE_ASSERTION_KINDS so the kind can compile again"
    )


def test_every_required_observation_key_is_accounted_for() -> None:
    """A kind's required key is either produced, or explicitly recorded as not."""
    produced = _produced_observation_keys()
    unaccounted: list[str] = []
    for kind, keys in KIND_REQUIRED_OBSERVATION_KEYS.items():
        for key in keys:
            if key not in produced and key not in UNPRODUCED_OBSERVATION_KEYS:
                unaccounted.append(f"{kind} requires {key}")
    assert not unaccounted, (
        "assertion kinds requiring observation keys that nothing produces and that are "
        f"not recorded in UNPRODUCED_OBSERVATION_KEYS: {unaccounted}"
    )


def test_unproducible_kinds_are_blocked_before_and_after_alias_resolution() -> None:
    """A protocol may emit the family name or the evaluator name; both must block.

    _FAMILY_ASSERTION_KIND emits "consistency" while the dead kind is registered as
    "cross_surface_consistency". Matching only one of the two would let the family
    name straight through.
    """
    for kind in UNPRODUCIBLE_ASSERTION_KINDS:
        assert unproducible_assertion_evidence(kind)
    for family_name, evaluator_name in KIND_ALIASES.items():
        if evaluator_name in UNPRODUCIBLE_ASSERTION_KINDS:
            assert unproducible_assertion_evidence(family_name), (
                f"{family_name!r} aliases to blocked kind {evaluator_name!r} but is "
                "not itself blocked"
            )


def test_satisfiable_kinds_are_not_blocked() -> None:
    """Over-blocking silently removes coverage, so pin the working kinds."""
    for kind in ("http_status", "state_transition", "postcondition", "conservation",
                 "owner_tenant_visibility", "field_delta", "idempotency_effect"):
        assert unproducible_assertion_evidence(kind) == "", kind


def test_concurrency_is_not_blocked() -> None:
    """Concurrency keeps compiling on purpose.

    invariant_held is unproduced like the others, but the barrier protocol and the
    barrier_timeline / final_state observers are real and exercised. Blocking would
    discard working concurrency evidence in order to suppress a missing verdict, and
    that verdict is already visible as a BLOCKED terminal with reason
    ASSERTION_INDETERMINATE.
    """
    assert unproducible_assertion_evidence("concurrency") == ""
    assert unproducible_assertion_evidence("concurrency_final_invariant") == ""


def test_block_reason_codes_are_registered() -> None:
    for code in (
        "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
        "BLOCKED_BINDING_LOCATION_NOT_MATERIALIZABLE",
    ):
        assert code in BLOCK_REASONS


def test_every_implemented_observer_has_a_dispatch_branch() -> None:
    """implemented=True with no branch compiles and then returns UNSUPPORTED.

    compile_observer_requirements only checks ``implemented is True``, so a registry
    entry without a handler passes compilation and dies at observation time. This is
    exactly how write_observer shipped.
    """
    dispatched = _dispatched_observer_ids()
    declared = {
        observer_id
        for observer_id, contract in OBSERVER_REGISTRY.items()
        if isinstance(contract, dict) and contract.get("implemented") is True
    }
    missing = sorted(declared - dispatched)
    assert not missing, (
        "observers declared implemented=True in OBSERVER_REGISTRY with no branch in "
        f"observe_experiment_requirements: {missing}"
    )


def test_write_observer_is_not_registered_as_an_observation_surface() -> None:
    """It is a governance callback parameter name, never an observer id."""
    assert "write_observer" not in OBSERVER_REGISTRY


def test_every_dispatched_observer_is_declared() -> None:
    """A handler with no registry entry can never be required, so it is dead code."""
    dispatched = _dispatched_observer_ids()
    undeclared = sorted(dispatched - set(OBSERVER_REGISTRY))
    assert not undeclared, (
        f"dispatch branches for observers absent from OBSERVER_REGISTRY: {undeclared}"
    )


def test_unproducible_kinds_remain_in_supported_kinds_where_they_have_evaluators() -> None:
    """Blocking happens at compile time; the evaluator stays intact.

    Removing them from SUPPORTED_KINDS would change a fail-closed
    ``*_EVIDENCE_MISSING`` INDETERMINATE into an ``unsupported_assertion_kind`` raise
    for any historical artifact being replayed.
    """
    for kind in ("cardinality", "cross_surface_consistency"):
        assert kind in SUPPORTED_KINDS
