"""The fourth and last capability registry: experiment protocols.

``compile_family_protocol`` was a hardcoded ``if family == ...`` chain over six families plus
a terminal fallback, and EVERY branch emits at most one control step and exactly one
treatment step. So a multi-step business process could not be expressed at all, and a family
added through ``register_risk_family`` inherited the generic single-step fallback whether or
not that shape suited it.

The key is (family, template), and it needs no new plumbing: the built-in chain already
dispatches on ``template`` for ``permitted_operation_invocation`` ahead of the family chain,
and ``register_risk_family`` already writes ``_TEMPLATE_BY_FAMILY`` which ``_planning_property``
applies with ``setdefault`` — so an obligation selects a protocol by DECLARING its template.
Nothing is inferred.

The load-bearing test in this file is the last group: on a registry MISS every built-in
branch must behave exactly as before. The registry is additive; if that property broke, the
whole approach would be unsafe.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from ai_test_asset_center import experiment_protocol_registry as reg
from ai_test_asset_center.experiment_compiler_obligation import BLOCK_REASONS
from ai_test_asset_center.experiment_protocol_registry import (
    BUILTIN_FAMILY_TEMPLATES,
    ProtocolRegistryError,
    register_family_protocol,
    registered_family_protocols,
    resolve_family_protocol,
)
from ai_test_asset_center.experiment_protocols import compile_family_protocol


TEST_FAMILY = "test_only_process_integrity"
TEST_TEMPLATE = "test_only_ordered_multi_step"


@pytest.fixture()
def cleanup() -> Iterator[list[tuple[str, str]]]:
    registered: list[tuple[str, str]] = []
    yield registered
    for key in registered:
        reg._REGISTERED_FAMILY_PROTOCOLS.pop(key, None)
    reg._REGISTERED_FAMILY_PROTOCOLS.pop((TEST_FAMILY, TEST_TEMPLATE), None)


def _step(step_id: str) -> dict[str, Any]:
    return {"step_id": step_id, "actor_ref": "actor-1", "operation_ref": "op-x"}


def _multi_step_compiler(steps: int = 3) -> Any:
    def compiler(_envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [_step(f"treatment_{i}") for i in range(1, steps + 1)],
            "assertion": {"kind": "http_status"},
        }
    return compiler


def _invoke(family: str, template: str) -> dict[str, Any]:
    return compile_family_protocol(
        risk_family=family,
        operation={"id": "op-x", "method": "POST", "path": "/api/x"},
        operation_ref="op-x",
        control_actor_ref="",
        treatment_actor_ref="actor-1",
        property_spec={"template": template},
        behavior_ir={},
    )


# ── registration validation ─────────────────────────────────────────────────

def test_multi_step_protocol_registers_and_compiles(cleanup) -> None:
    """The capability that did not exist: more than one treatment step."""
    protocol_id = register_family_protocol(
        TEST_FAMILY, TEST_TEMPLATE,
        compiler=_multi_step_compiler(3), per_step_evidence=True,
    )
    cleanup.append((TEST_FAMILY, TEST_TEMPLATE))

    assert protocol_id == f"{TEST_FAMILY}:{TEST_TEMPLATE}"
    assert protocol_id in registered_family_protocols()

    result = _invoke(TEST_FAMILY, TEST_TEMPLATE)
    assert result["status"] == "COMPILED"
    assert len(result["treatment_plan"]) == 3
    assert result["_registry_protocol_id"] == protocol_id
    assert result["per_step_evidence"] is True


@pytest.mark.parametrize("family", sorted(BUILTIN_FAMILY_TEMPLATES))
def test_builtin_family_template_pair_cannot_be_taken_over(family: str) -> None:
    """Shadowing the template a family compiles with today would change every obligation in it."""
    with pytest.raises(ProtocolRegistryError) as excinfo:
        register_family_protocol(
            family, BUILTIN_FAMILY_TEMPLATES[family], compiler=_multi_step_compiler(1)
        )
    assert "built-in pair" in str(excinfo.value)


def test_builtin_template_name_cannot_be_shadowed() -> None:
    """permitted_operation_invocation is dispatched ahead of the family chain."""
    with pytest.raises(ProtocolRegistryError):
        register_family_protocol(
            "any_family", "permitted_operation_invocation", compiler=_multi_step_compiler(1)
        )


@pytest.mark.parametrize(
    "family,template,kwargs,label",
    [
        ("", "t", {}, "no family"),
        ("f", "", {}, "no template"),
        ("f", "t", {"compiler": "not callable"}, "non-callable compiler"),
        ("f", "t", {"observers": ["no_such_observer"]}, "unregistered observer"),
        ("f", "t", {"assertion_kind": "cross_surface_consistency"}, "dead assertion kind"),
    ],
)
def test_unusable_registration_is_refused(family: str, template: str, kwargs: dict, label: str) -> None:
    """Fail at registration, not at compile or run time.

    The same lesson the other registries record: write_observer shipped as a registry entry
    with no dispatch branch, so it compiled, spent real target requests, and only then
    reported UNSUPPORTED.
    """
    kwargs.setdefault("compiler", _multi_step_compiler(1))
    with pytest.raises(ProtocolRegistryError):
        register_family_protocol(family, template, **kwargs)


def test_a_new_template_for_a_builtin_family_is_allowed(cleanup) -> None:
    """Extending a built-in family is fine; only its existing pair is protected.

    Such a protocol is reachable only by an obligation that explicitly declares that
    template, because _planning_property applies the family default with setdefault.
    """
    protocol_id = register_family_protocol(
        "state", "test_only_multi_step_state", compiler=_multi_step_compiler(2),
        per_step_evidence=True,
    )
    cleanup.append(("state", "test_only_multi_step_state"))
    assert resolve_family_protocol("state", "test_only_multi_step_state") is not None
    assert protocol_id == "state:test_only_multi_step_state"


# ── result validation ───────────────────────────────────────────────────────

def _register_raw(compiler: Any, **registration: Any) -> None:
    reg._REGISTERED_FAMILY_PROTOCOLS[(TEST_FAMILY, TEST_TEMPLATE)] = {
        "protocol_id": f"{TEST_FAMILY}:{TEST_TEMPLATE}",
        "compiler": compiler,
        "observers": registration.get("observers", []),
        "assertion_kind": registration.get("assertion_kind", ""),
        "emits_control": registration.get("emits_control", False),
        "per_step_evidence": registration.get("per_step_evidence", False),
    }


@pytest.mark.parametrize(
    "compiler,label",
    [
        (lambda e: (_ for _ in ()).throw(RuntimeError("boom")), "compiler raises"),
        (lambda e: "not a dict", "returns a non-dict"),
        (lambda e: {"status": "COMPILED", "treatment_plan": []}, "no treatment step"),
        (lambda e: {"status": "COMPILED", "treatment_plan": [{"actor_ref": "a", "operation_ref": "o"}]},
         "step without step_id"),
        (lambda e: {"status": "COMPILED", "treatment_plan": [
            {"step_id": "t1", "actor_ref": "a", "operation_ref": "o"},
            {"step_id": "t1", "actor_ref": "a", "operation_ref": "o"}]},
         "duplicate step_id"),
        (lambda e: {"status": "COMPILED", "treatment_plan": [{"step_id": "t1", "actor_ref": "a"}]},
         "step without operation_ref"),
        (lambda e: {"status": "COMPILED", "treatment_plan": [{"step_id": "t1", "operation_ref": "o"}]},
         "step without actor_ref"),
        (lambda e: {"status": "WHATEVER"}, "status neither COMPILED nor BLOCKED"),
        (lambda e: {"status": "BLOCKED"}, "BLOCKED without a reason_code"),
    ],
)
def test_invalid_result_becomes_a_visible_block(cleanup, compiler: Any, label: str) -> None:
    """An exception must never escape into the compile loop.

    The loop compiles a whole batch; one bad registration aborting it would take unrelated
    obligations down with it.
    """
    _register_raw(compiler)
    cleanup.append((TEST_FAMILY, TEST_TEMPLATE))

    result = _invoke(TEST_FAMILY, TEST_TEMPLATE)
    assert result["status"] == "BLOCKED", label
    assert result["reason_code"] == "BLOCKED_REGISTERED_PROTOCOL_INVALID"
    assert result["detail"]


def test_multi_step_without_per_step_evidence_is_refused(cleanup) -> None:
    """A plan that would lose its middle steps must not execute and report a verdict."""
    _register_raw(_multi_step_compiler(3), per_step_evidence=False)
    cleanup.append((TEST_FAMILY, TEST_TEMPLATE))

    result = _invoke(TEST_FAMILY, TEST_TEMPLATE)
    assert result["status"] == "BLOCKED"
    assert "per_step_evidence" in result["detail"]


def test_control_leg_must_match_the_declaration(cleanup) -> None:
    """The delivery gate exempts an EMPTY control leg from operation-symmetry checks.

    So a protocol emitting a control leg while declaring emits_control=False would slip past
    a comparison it should have been subject to.
    """
    def with_control(_envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "COMPILED",
            "control_plan": [_step("control_1")],
            "treatment_plan": [_step("treatment_1")],
        }
    _register_raw(with_control, emits_control=False)
    cleanup.append((TEST_FAMILY, TEST_TEMPLATE))

    result = _invoke(TEST_FAMILY, TEST_TEMPLATE)
    assert result["status"] == "BLOCKED"
    assert "emits_control" in result["detail"]


def test_a_registered_protocol_may_refuse(cleanup) -> None:
    """BLOCKED from a registered compiler is fail-closed, not an error."""
    _register_raw(lambda e: {
        "status": "BLOCKED", "reason_code": "BLOCKED_MISSING_BINDING", "detail": "declared_gap",
    })
    cleanup.append((TEST_FAMILY, TEST_TEMPLATE))

    result = _invoke(TEST_FAMILY, TEST_TEMPLATE)
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert result["detail"] == "declared_gap"


def test_block_reason_is_registered() -> None:
    """blocked_experiment rewrites any unregistered code to BLOCKED_UNSUPPORTED_ADAPTER.

    Which would make a registration problem indistinguishable from a target adapter problem.
    """
    assert "BLOCKED_REGISTERED_PROTOCOL_INVALID" in BLOCK_REASONS


# ── the load-bearing compatibility property ─────────────────────────────────

def test_registry_miss_leaves_the_builtin_state_protocol_unchanged() -> None:
    result = compile_family_protocol(
        risk_family="state",
        operation={"id": "op-x", "method": "POST", "path": "/api/x"},
        operation_ref="op-x",
        control_actor_ref="control-actor",
        treatment_actor_ref="treatment-actor",
        property_spec={"template": "state_transition", "from_state": "A", "to_state": "B"},
        behavior_ir={},
    )
    assert result["status"] == "COMPILED"
    assert result["assertion"]["kind"] == "state_transition"
    assert [row["step_id"] for row in result["treatment_plan"]] == ["treatment_1"]
    assert "_registry_protocol_id" not in result


def test_registry_miss_leaves_the_builtin_privacy_field_protocol_unchanged() -> None:
    result = compile_family_protocol(
        risk_family="privacy",
        operation={"id": "op-x", "method": "GET", "path": "/api/x"},
        operation_ref="op-x",
        control_actor_ref="",
        treatment_actor_ref="actor-1",
        property_spec={
            "template": "source_declared_privacy",
            "privacy_test_mode": "field_policy",
            "privacy_policy": "absent",
            "field_tokens": ["ssn"],
        },
        behavior_ir={},
    )
    assert result["status"] == "COMPILED"
    assert result["assertion"]["kind"] == "privacy_field_policy"
    assert "_registry_protocol_id" not in result


@pytest.mark.parametrize("family", sorted(BUILTIN_FAMILY_TEMPLATES))
def test_no_builtin_family_is_marked_as_registered(family: str) -> None:
    """The marker gates a compiler branch, so it must never appear on a built-in result."""
    result = compile_family_protocol(
        risk_family=family,
        operation={"id": "op-x", "method": "POST", "path": "/api/x"},
        operation_ref="op-x",
        control_actor_ref="control-actor",
        treatment_actor_ref="treatment-actor",
        property_spec={"template": BUILTIN_FAMILY_TEMPLATES[family]},
        behavior_ir={},
    )
    assert "_registry_protocol_id" not in result


def test_no_test_only_protocol_leaks() -> None:
    leaked = [p for p in registered_family_protocols() if "test_only" in p]
    assert not leaked, f"test-only protocols left registered: {leaked}"
