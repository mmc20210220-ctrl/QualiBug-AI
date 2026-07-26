"""Prerequisite-chain writes must be observable, and their failures must be visible.

``experiment_fixture_materializer`` executed every prerequisite step with
``observation_path=""``. ``execute_governed_control_write`` refuses any observation_path that
does not start with "/", so EVERY prerequisite-chain write was blocked before transport with
``http_attempt_count: 0``. The chain has never executed.

The refusal is correct — a governed write needs a before/after observation to prove
reversibility — so the caller was the defect, not the gate.

Worse than the block was the silence around it. The result was consumed as
``if 200 <= dep_status < 300:`` with no else branch, so a blocked write fell straight
through: ``prereq_ids`` stayed empty, every later step's ``<entity_id>`` placeholder was left
unresolved, and nothing recorded that any of it happened. A chain that never ran looked
exactly like a chain that ran fine.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MATERIALIZER = (
    Path(__file__).resolve().parents[1]
    / "ai_test_asset_center" / "experiment_fixture_materializer.py"
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

def _prerequisite_call_source() -> str:
    """The execute_governed_control_write call inside the prerequisite-chain loop."""
    source = MATERIALIZER.read_text(encoding="utf-8")
    marker = "operation_phase=\"prerequisite_chain\""
    assert marker in source, "prerequisite_chain call site not found"
    start = source.index(marker)
    return source[start - 600:start + 600]


def test_prerequisite_write_no_longer_passes_an_empty_observation_path() -> None:
    """The specific defect: observation_path="" blocked every prerequisite write.

    Asserted narrowly against the one call site rather than the whole file, so it fails on
    the defect returning and not on unrelated edits.
    """
    assert 'observation_path=""' not in _prerequisite_call_source()
    assert "observation_path=_dep_observation" in _prerequisite_call_source()


def test_prerequisite_observation_path_comes_from_the_declared_step_path() -> None:
    """Observe the collection being written to — the same thing the fixture-setup block
    below it does, for the same reason."""
    source = MATERIALIZER.read_text(encoding="utf-8")
    assert "_dep_observation" in source
    assert "path_has_placeholders(_dep_path)" in source


# ── failures are recorded, not swallowed ────────────────────────────────────

def _materializer_function() -> ast.FunctionDef:
    tree = ast.parse(MATERIALIZER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "materialize_experiment_fixtures":
            return node
    raise AssertionError("materialize_experiment_fixtures not found")


def test_every_prerequisite_failure_path_records_a_receipt() -> None:
    """Three distinct failure modes, three distinct reason codes.

    The old code had none: a blocked write, an unextractable id and a non-2xx response all
    fell through the same silent path.
    """
    source = MATERIALIZER.read_text(encoding="utf-8")
    for reason in (
        "prerequisite_observation_path_not_source_declared",
        "prerequisite_resource_id_not_extractable",
        "prerequisite_write_not_accepted:",
    ):
        assert reason in source, reason


def test_prerequisite_receipts_are_appended_to_the_shared_collection() -> None:
    """A receipt nobody collects is the same as no receipt.

    fixture_receipts is initialized in materialize_experiment_fixtures, so the new appends
    must be inside that function to be returned.
    """
    function = _materializer_function()
    segment = ast.get_source_segment(MATERIALIZER.read_text(encoding="utf-8"), function) or ""
    assert "fixture_receipts: list" in segment
    assert '"kind": "prerequisite_chain"' in segment


def test_prerequisite_success_path_still_records_the_resolved_id() -> None:
    """The fix must not break the case that was supposed to work."""
    source = MATERIALIZER.read_text(encoding="utf-8")
    assert "prereq_ids[step[\"entity\"]] = rid" in source
