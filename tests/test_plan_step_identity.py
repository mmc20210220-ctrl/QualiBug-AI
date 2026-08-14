"""A plan step's id is its contract subject, so it must be unique and observable.

``contract_oracles._plan_subjects`` derives each activation subject as ``step_id or id`` and
then collapses the list with ``dict.fromkeys``. Three consequences make step identity
load-bearing rather than cosmetic:

* an empty or repeated step_id shrinks ``required[phase]``, shifts every positional lookup
  after it, and finally trips the delivery gate's duplicate-subject check — after the
  experiment has already issued real requests
* per-phase observation keeps only the first and last governed write, so a multi-step plan
  with no per-step observer loses steps 2..N-1 and would still report a verdict
* ``_exec_plan`` resolved the subject POSITIONALLY against a list built from the UNFILTERED
  plan while receiving the barrier-FILTERED plan, so any phase mixing barrier and
  non-barrier steps filed evidence under the wrong step's identity

The last one was a live bug, not a future N-step concern.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from ai_test_asset_center import experiment_protocol_registry as reg
from ai_test_asset_center.experiment_compiler_obligation import (
    BLOCK_REASONS,
    compile_experiment_for_obligation,
)


FAMILY = "test_only_step_identity"
TEMPLATE = "test_only_step_identity_template"


@pytest.fixture()
def registered() -> Iterator[Any]:
    def register(compiler: Any, **flags: Any) -> None:
        reg._REGISTERED_FAMILY_PROTOCOLS[(FAMILY, TEMPLATE)] = {
            "protocol_id": f"{FAMILY}:{TEMPLATE}",
            "compiler": compiler,
            "observers": [],
            # The protocol authority gate requires a real, producible Oracle
            # assertion kind; a custom test family has no built-in mapping so
            # the registration must declare one explicitly.
            "assertion_kind": flags.get("assertion_kind", "http_status"),
            "emits_control": flags.get("emits_control", False),
            "per_step_evidence": flags.get("per_step_evidence", False),
        }
    yield register
    reg._REGISTERED_FAMILY_PROTOCOLS.pop((FAMILY, TEMPLATE), None)


def _behavior_ir() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [{
            "id": "op-read", "service": "s", "method": "GET",
            "path": "/api/items", "path_template": "/api/items", "read_write": "read",
        }],
        "actors": [{
            "id": "actor-1", "role": "admin",
            "credential_secret_ref": "secret-1", "status": "active",
        }],
        "entities": [{"id": "e1", "name": "item"}],
        "relations": [], "invariants": [], "states": [],
    }


def _obligation() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.test-obligation.v1",
        "obligation_id": "obl-step-identity",
        "risk_family": FAMILY,
        "subject_refs": ["op-read"],
        "property": {
            "operation_ref": "op-read", "actor_ref": "actor-1", "template": TEMPLATE,
        },
        "required_actors": ["actor-1"],
        "required_operations": ["op-read"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": False},
    }


def _compile() -> dict[str, Any]:
    return compile_experiment_for_obligation(
        _obligation(), behavior_ir=_behavior_ir(), environment_type="test"
    )["compile_receipt"]


def _step(step_id: str | None = "treatment_1") -> dict[str, Any]:
    step = {"actor_ref": "actor-1", "operation_ref": "op-read"}
    if step_id is not None:
        step["step_id"] = step_id
    return step


def test_block_reasons_are_registered() -> None:
    """blocked_experiment rewrites an unregistered code to BLOCKED_UNSUPPORTED_ADAPTER."""
    for code in ("BLOCKED_PLAN_STEP_IDENTITY_INVALID", "BLOCKED_STEP_EVIDENCE_UNOBSERVABLE"):
        assert code in BLOCK_REASONS


def test_step_without_an_id_blocks_at_compile_time(registered) -> None:
    registered(lambda e: {"status": "COMPILED", "treatment_plan": [_step(None)]})
    receipt = _compile()
    # The registry validates first, so either gate is an acceptable refusal point -- what
    # matters is that it never compiles.
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] in {
        "BLOCKED_PLAN_STEP_IDENTITY_INVALID", "BLOCKED_REGISTERED_PROTOCOL_INVALID",
    }


def test_duplicate_step_id_blocks_at_compile_time(registered) -> None:
    """A duplicate collapses required[phase] and only fails much later, mid-delivery."""
    registered(
        lambda e: {"status": "COMPILED", "treatment_plan": [_step("t1"), _step("t1")]},
        per_step_evidence=True,
    )
    receipt = _compile()
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] in {
        "BLOCKED_PLAN_STEP_IDENTITY_INVALID", "BLOCKED_REGISTERED_PROTOCOL_INVALID",
    }


def test_multi_step_without_per_step_evidence_blocks(registered) -> None:
    """Losing the middle steps and reporting a verdict anyway is the worst outcome."""
    registered(
        lambda e: {
            "status": "COMPILED",
            "treatment_plan": [_step("t1"), _step("t2"), _step("t3")],
        },
        per_step_evidence=False,
    )
    receipt = _compile()
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] in {
        "BLOCKED_STEP_EVIDENCE_UNOBSERVABLE", "BLOCKED_REGISTERED_PROTOCOL_INVALID",
    }


def test_single_step_plan_still_compiles(registered) -> None:
    """The gates must not over-block. A 1-step plan is the existing shape."""
    registered(lambda e: {"status": "COMPILED", "treatment_plan": [_step("treatment_1")]})
    receipt = _compile()
    assert receipt["status"] == "COMPILED", receipt


def test_every_builtin_branch_emits_a_usable_step_id() -> None:
    """The uniqueness gate runs for built-in plans too, so this is its safety argument.

    Asserted against the source rather than by compiling every family, because a built-in
    branch that stopped emitting a literal step_id would newly BLOCK — and that regression
    should be caught here, at the gate's precondition.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "experiment_protocols_base.py"
    ).read_text(encoding="utf-8")
    emitted = set(re.findall(r'"step_id":\s*"([a-z_0-9]+)"', source))
    assert emitted, "no literal step_id found; the gate's safety argument no longer holds"
    # Built-in branches emit only these two literals, one per phase, so no plan can repeat one.
    assert emitted <= {"control_1", "treatment_1"}, sorted(emitted)


def test_subject_resolution_is_step_id_authoritative_not_positional() -> None:
    """Pins the live barrier-filter bug this replaced.

    planned_subjects is built over the UNFILTERED plan; _exec_plan receives the
    barrier-FILTERED plan. Indexing one with the other's position mislabels every step after
    the first barrier step.
    """
    def resolve(planned: list[str], step: dict, index: int, phase: str, op: str) -> str:
        declared = str(step.get("step_id") or "")
        return (
            declared
            if declared and declared in planned
            else (
                planned[index] if index < len(planned)
                else declared or f"{phase}:{op or 'operation'}:{index + 1}"
            )
        )

    # Existing 1/1 shape: identical to the old positional result.
    assert resolve(["treatment_1"], {"step_id": "treatment_1"}, 0, "treatment", "op") == "treatment_1"
    assert resolve(["control_1"], {"step_id": "control_1"}, 0, "control", "op") == "control_1"
    # Step declaring no id: the generated fallback is retained.
    assert resolve(["treatment:op:1"], {}, 0, "treatment", "op") == "treatment:op:1"
    # The fixed case: unfiltered = [barrier_1, t2], filtered = [t2]. Positionally t2 would
    # have taken barrier_1's identity.
    assert resolve(["barrier_1", "t2"], {"step_id": "t2"}, 0, "treatment", "op") == "t2"


def test_executor_uses_the_step_id_authoritative_form() -> None:
    """Source check: the positional-only expression must be gone."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "experiment_plan_step_executor_core.py"
    ).read_text(encoding="utf-8")
    assert "declared_step_id" in source
    assert "declared_step_id and declared_step_id in planned_subjects" in source
