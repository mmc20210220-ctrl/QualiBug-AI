"""Kind-to-evidence and observer-dispatch contracts.

Four capabilities were compiled and executed while being structurally incapable
of returning a verdict. These tests derive produced keys and observer dispatch
coverage from source plus the validated runtime registration entry point, so a
new kind or observer cannot silently compile without evidence production.
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
from ai_test_asset_center.observer_contracts_base import (
    OBSERVER_REGISTRY,
    registered_observer_ids,
)


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
    """Observer ids with a static branch or a validated registered handler."""
    source = OBSERVER_SOURCE.read_text(encoding="utf-8")
    marker = "def observe_experiment_requirements"
    assert marker in source, "observe_experiment_requirements not found"
    body = source[source.index(marker):]
    dispatched = set(re.findall(r'observer_id == "([a-z_0-9]+)"', body))
    for group in re.findall(r"observer_id in \{([^}]*)\}", body):
        dispatched.update(re.findall(r'"([a-z_0-9]+)"', group))
    dispatched.update(registered_observer_ids())
    return dispatched


def test_produced_observation_keys_are_discoverable() -> None:
    produced = _produced_observation_keys()
    assert len(produced) > 40, f"key extraction looks broken: {sorted(produced)}"
    assert {"before_state", "after_state", "execution_steps"} <= produced


@pytest.mark.parametrize("key", sorted(UNPRODUCED_OBSERVATION_KEYS))
def test_recorded_unproduced_keys_are_still_unproduced(key: str) -> None:
    """When a producer lands, delete the stale unproduced-key declaration."""
    produced = _produced_observation_keys()
    assert key not in produced, (
        f"{key!r} is now produced by an observer; remove it from "
        f"UNPRODUCED_OBSERVATION_KEYS and, if present, from "
        f"UNPRODUCIBLE_ASSERTION_KINDS so the kind can compile again"
    )


def test_every_required_observation_key_is_accounted_for() -> None:
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
    for kind in UNPRODUCIBLE_ASSERTION_KINDS:
        assert unproducible_assertion_evidence(kind)
    for family_name, evaluator_name in KIND_ALIASES.items():
        if evaluator_name in UNPRODUCIBLE_ASSERTION_KINDS:
            assert unproducible_assertion_evidence(family_name), (
                f"{family_name!r} aliases to blocked kind {evaluator_name!r} but is "
                "not itself blocked"
            )


def test_satisfiable_kinds_are_not_blocked() -> None:
    for kind in (
        "http_status",
        "state_transition",
        "postcondition",
        "conservation",
        "owner_tenant_visibility",
        "field_delta",
        "idempotency_effect",
    ):
        assert unproducible_assertion_evidence(kind) == "", kind


def test_concurrency_is_not_blocked() -> None:
    assert unproducible_assertion_evidence("concurrency") == ""
    assert unproducible_assertion_evidence("concurrency_final_invariant") == ""


def test_block_reason_codes_are_registered() -> None:
    for code in (
        "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
        "BLOCKED_BINDING_LOCATION_NOT_MATERIALIZABLE",
    ):
        assert code in BLOCK_REASONS


def test_every_implemented_observer_has_a_dispatch_path() -> None:
    """Built-ins need a branch; registered observers need a validated handler."""
    dispatched = _dispatched_observer_ids()
    declared = {
        observer_id
        for observer_id, contract in OBSERVER_REGISTRY.items()
        if isinstance(contract, dict) and contract.get("implemented") is True
    }
    missing = sorted(declared - dispatched)
    assert not missing, (
        "observers declared implemented=True with neither a static dispatch branch "
        f"nor a registered handler: {missing}"
    )


def test_write_observer_is_not_registered_as_an_observation_surface() -> None:
    assert "write_observer" not in OBSERVER_REGISTRY


def test_every_static_or_registered_dispatch_is_declared() -> None:
    dispatched = _dispatched_observer_ids()
    undeclared = sorted(dispatched - set(OBSERVER_REGISTRY))
    assert not undeclared, (
        f"dispatch paths for observers absent from OBSERVER_REGISTRY: {undeclared}"
    )


def test_unproducible_kinds_remain_supported_where_evaluators_exist() -> None:
    for kind in ("cardinality", "cross_surface_consistency"):
        assert kind in SUPPORTED_KINDS
