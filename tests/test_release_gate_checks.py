"""The release gate publishes its own named checks and its own verdict.

Before this, ``release_gate`` carried only verdict/status/reasons and no ``checks``,
so ``frontend/src/api/data.ts`` synthesized five checks from the finding list and
merged them over whatever the backend supplied. Two consequences:

* A run that published nothing rendered "无 P0 缺陷 ✓" — an empty defect list read as
  a clean target, which the repo's own rules forbid.
* The "DB 验证" row was driven by ``data.db_verification.confirmed``, a key the
  backend never emits, so it was permanently green.

These tests pin that the backend now owns the verdict, that an unmeasured check is
``pending`` rather than ``pass``, and that the reason-code copy cannot drift away
from the code that emits those reason codes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from ai_test_asset_center.release_gate import (
    _PIPELINE_CODE_PREFIX,
    _READINESS_CHECK_COPY,
    _readiness_check_copy,
    reconcile_release_gate_with_run_readiness,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTION = REPO_ROOT / "ai_test_asset_center" / "discovery_quality_projection.py"

READINESS_SCHEMA = "qualibug.run-delivery-readiness.v1"


def _readiness(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": READINESS_SCHEMA,
        "scope": "current_run_formal_finding_publication",
        "status": "NOT_READY",
        "release_ready": False,
        "reason_codes": [],
        "identities": {"campaign_id": "CMP_1", "run_id": "RUN_1"},
        "selected_obligation_count": 10,
        "executed_obligation_count": 10,
        "cleanup_failure_count": 0,
        "published_formal_deliverable_count": 0,
        "eligible_formal_deliverable_count": 0,
    }
    base.update(overrides)
    return base


def _emitted_reason_codes() -> set[str]:
    """Every literal reason code appended in build_run_delivery_readiness_projection."""
    source = PROJECTION.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PROJECTION))
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_run_delivery_readiness_projection":
            target = node
            break
    assert target is not None, "build_run_delivery_readiness_projection not found"

    codes: set[str] = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "append"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                value = arg.value
                if value and value == value.upper() and re.fullmatch(r"[A-Z_]+", value):
                    codes.add(value)
    return codes


def test_every_emitted_reason_code_has_named_copy() -> None:
    """Drift guard: the copy map is derived from the emitter, not from guesswork.

    An uncovered code degrades to showing the raw identifier to a customer, which is
    how the first version of this map shipped with CLEANUP_FAILURES_PRESENT and
    NO_OBLIGATION_SELECTED — names the emitter never produces.
    """
    emitted = _emitted_reason_codes()
    assert emitted, "no reason codes extracted; the AST walk is broken"

    uncovered = sorted(
        code
        for code in emitted
        if code not in _READINESS_CHECK_COPY and not code.startswith(_PIPELINE_CODE_PREFIX)
    )
    assert not uncovered, (
        "reason codes emitted by build_run_delivery_readiness_projection with no entry "
        f"in _READINESS_CHECK_COPY: {uncovered}"
    )


@pytest.mark.parametrize("status", ["DEGRADED", "BLOCKED", "STATUS_MISSING", "SOME_FUTURE_STATE"])
def test_pipeline_codes_are_named_by_prefix(status: str) -> None:
    """PIPELINE_* is f-string generated, so its tail cannot be enumerated."""
    label, detail = _readiness_check_copy(f"{_PIPELINE_CODE_PREFIX}{status}")
    assert label == "执行管线健康度"
    assert status.replace("_", " ").lower() in detail


def test_unknown_code_is_explicitly_unknown_not_silently_generic() -> None:
    label, detail = _readiness_check_copy("SOME_CODE_WITH_NO_COPY")
    assert label == "SOME_CODE_WITH_NO_COPY"
    assert "尚无产品化说明文案" in detail


def test_gate_emits_checks_and_a_three_state_verdict() -> None:
    gate = reconcile_release_gate_with_run_readiness(
        {},
        _readiness(reason_codes=["COVERAGE_GAPS_REMAIN"]),
    )
    assert gate["has_decision"] is True
    assert gate["overall_status"] in {"pass", "pending", "fail"}
    assert gate["checks"]
    assert gate["blocking_check_count"] >= 1
    assert gate["measurement_status"] == "NOT_MEASURED"
    names = [row["name"] for row in gate["checks"]]
    assert "行为空间覆盖缺口" in names
    assert "正式交付发布决定" in names


def test_blocked_publication_never_reads_as_a_clean_target() -> None:
    """The detail line must say why the list is empty, not that nothing was found."""
    gate = reconcile_release_gate_with_run_readiness(
        {},
        _readiness(
            reason_codes=["PIPELINE_DEGRADED"],
            eligible_formal_deliverable_count=9,
            published_formal_deliverable_count=0,
        ),
    )
    publication = next(
        row for row in gate["checks"] if row["code"] == "FORMAL_PUBLICATION_DECISION"
    )
    assert publication["status"] != "pass"
    assert "空缺陷列表不代表目标无缺陷" in publication["detail"]
    assert gate["overall_status"] == "fail"


def test_partial_execution_is_pending_not_pass() -> None:
    """An unmeasured obligation is not a passed obligation."""
    gate = reconcile_release_gate_with_run_readiness(
        {},
        _readiness(selected_obligation_count=452, executed_obligation_count=13),
    )
    ratio = next(row for row in gate["checks"] if row["code"] == "OBLIGATION_EXECUTION_RATIO")
    assert ratio["status"] == "pending"
    assert "13/452" in ratio["detail"]


def test_ready_run_reports_pass_only_when_nothing_is_pending() -> None:
    gate = reconcile_release_gate_with_run_readiness(
        {"verdict": "pass", "status": "ready"},
        _readiness(
            status="READY",
            release_ready=True,
            reason_codes=[],
            published_formal_deliverable_count=3,
            eligible_formal_deliverable_count=3,
        ),
    )
    assert gate["release_ready"] is True
    assert gate["blocking_check_count"] == 0
    assert gate["pending_check_count"] == 0
    assert gate["overall_status"] == "pass"


def test_existing_backend_checks_are_preserved_and_not_duplicated() -> None:
    """A check contributed elsewhere (e.g. the regression patch) must survive."""
    gate = reconcile_release_gate_with_run_readiness(
        {"checks": [{"name": "回归套件", "status": "pass", "detail": "d", "source": "regression"}]},
        _readiness(reason_codes=["COVERAGE_GAPS_REMAIN"]),
    )
    names = [row["name"] for row in gate["checks"]]
    assert names.count("回归套件") == 1
    assert "行为空间覆盖缺口" in names
