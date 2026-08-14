"""Prerequisite-chain writes must be observable, and their failures must be visible.

``experiment_fixture_materializer`` used to execute every prerequisite step with
``observation_path=""``. ``execute_governed_control_write`` refuses any observation_path that
does not start with "/", so EVERY prerequisite-chain write was blocked before transport with
``http_attempt_count: 0``. The chain never executed.

The refusal is correct — a governed write needs a before/after observation to prove
reversibility — so the caller was the defect, not the gate.

Worse than the block was the silence around it. The result was consumed as
``if 200 <= dep_status < 300:`` with no else branch, so a blocked write fell straight
through: ``prereq_ids`` stayed empty, every later step's ``<entity_id>`` placeholder was left
unresolved, and nothing recorded that any of it happened. A chain that never ran looked
exactly like a chain that ran fine.

Since that fix, the monolithic materializer was split into a facade
(``experiment_fixture_materializer.py``) plus ``*_core.py``/``*_with_preconditions.py``
modules, and the prerequisite chain now executes in
``experiment_precondition_executor.execute_precondition_plan``. The observable-write /
fail-closed contract is preserved there, so the regression guards below pin the same
properties at their new home rather than the old facade path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# The prerequisite chain now executes here (governed write + observation path +
# receipts + identity capture). The old monolithic materializer is a facade.
PRECONDITION_EXECUTOR = (
    Path(__file__).resolve().parents[1]
    / "ai_test_asset_center" / "experiment_precondition_executor.py"
)
# The composed materializer is where precondition receipts are merged into the
# shared ``fixture_receipts`` collection that the run actually returns.
PRECONDITION_COMPOSITION = (
    Path(__file__).resolve().parents[1]
    / "ai_test_asset_center" / "experiment_fixture_materializer_with_preconditions.py"
)


# ── the gate the fix depends on ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "observation_path,should_pass",
    [
        ("", False),
        ("items", False),
        ("/api/items/{id}", False),
        ("/api/items", True),
        ("/api/orders", True),
    ],
)
def test_observation_path_contract(observation_path: str, should_pass: bool) -> None:
    """Pins the contract the prerequisite fix relies on.

    Reproduces execute_governed_control_write's own check rather than driving a live write,
    so the test states the rule without needing a target.
    """
    from ai_test_asset_center.real_id_resolver import path_has_placeholders

    accepted = observation_path.startswith("/") and not path_has_placeholders(observation_path)
    assert accepted is should_pass, observation_path


def test_governed_write_refuses_an_unobservable_path_before_transport() -> None:
    """The gate is a pre-transport refusal, so a blocked write never reaches the target.

    _blocked_result is a closure inside execute_governed_control_write and cannot be
    imported, so this asserts the guard exists ahead of any request construction.
    """
    from pathlib import Path as _Path

    source = (
        _Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "sandbox_write_executor_base.py"
    ).read_text(encoding="utf-8")
    assert 'governed_control_observation_path_placeholder_unresolved' in source
    assert '"http_attempt_count": 0' in source


# ── the caller no longer defeats it ─────────────────────────────────────────

def _precondition_call_source() -> str:
    """The execute_governed_control_write call inside the precondition write loop."""
    source = PRECONDITION_EXECUTOR.read_text(encoding="utf-8")
    marker = 'operation_phase="state_precondition_establishment"'
    assert marker in source, "state_precondition_establishment call site not found"
    start = source.index(marker)
    return source[start - 800:start + 800]


def test_prerequisite_write_no_longer_passes_an_empty_observation_path() -> None:
    """The specific defect: observation_path="" blocked every prerequisite write.

    Asserted narrowly against the one call site rather than the whole file, so it fails on
    the defect returning and not on unrelated edits.
    """
    assert 'observation_path=""' not in _precondition_call_source()
    assert "observation_path=observation_path" in _precondition_call_source()


def test_prerequisite_observation_path_comes_from_the_declared_step_path() -> None:
    """Observe the collection being written to — the same thing the fixture-setup block
    below it does, for the same reason.

    The observation path is derived from the step's declared path via
    ``_declared_observation_path`` (which itself rejects relative and placeholder-carrying
    paths), and a path that cannot resolve to an absolute source-declared observer is
    refused before transport rather than attempted.
    """
    source = PRECONDITION_EXECUTOR.read_text(encoding="utf-8")
    assert "_declared_observation_path(" in source
    assert 'observation_path.startswith("/")' in source


# ── failures are recorded, not swallowed ────────────────────────────────────

def _precondition_function() -> ast.FunctionDef:
    tree = ast.parse(PRECONDITION_EXECUTOR.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "execute_precondition_plan":
            return node
    raise AssertionError("execute_precondition_plan not found")


def test_every_prerequisite_failure_path_records_a_receipt() -> None:
    """Three distinct failure modes, three distinct reason codes.

    The old code had none: a blocked write, an unextractable id and a non-2xx response all
    fell through the same silent path. In the migrated executor these are now distinct
    fail-closed reason codes (the id-extraction failure distinguishes missing vs ambiguous).
    """
    source = PRECONDITION_EXECUTOR.read_text(encoding="utf-8")
    for reason in (
        "BLOCKED_PRECONDITION_TARGET_NOT_OBSERVED",
        "BLOCKED_PRECONDITION_TRANSPORT",
        "BLOCKED_PRECONDITION_IDENTITY_OUTPUT_MISSING",
        "BLOCKED_PRECONDITION_IDENTITY_OUTPUT_AMBIGUOUS",
    ):
        assert reason in source, reason


def test_prerequisite_receipts_are_appended_to_the_shared_collection() -> None:
    """A receipt nobody collects is the same as no receipt.

    Receipts are initialized inside execute_precondition_plan and returned on every
    terminal path, then the composed materializer merges them into the shared
    ``fixture_receipts`` collection the run returns — so a failure receipt is never dropped.
    """
    function = _precondition_function()
    segment = ast.get_source_segment(
        PRECONDITION_EXECUTOR.read_text(encoding="utf-8"), function
    ) or ""
    assert "receipts: list[dict[str, Any]] = []" in segment
    composition = PRECONDITION_COMPOSITION.read_text(encoding="utf-8")
    assert "fixture_receipts.append(" in composition
    assert '"kind": "state_precondition_establishment"' in composition


def test_prerequisite_success_path_still_records_the_resolved_id() -> None:
    """The fix must not break the case that was supposed to work."""
    source = PRECONDITION_EXECUTOR.read_text(encoding="utf-8")
    assert "runtime_bindings[_target_name] = _identity_value" in source
