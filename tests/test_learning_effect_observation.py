"""Tests for learning-effect observation (executed-set diff across rounds).

The effect of learning is a change in what gets executed and delivered, not
internal counts. These tests drive the round-diff and aggregation logic with
two synthetic trace ledgers of the same campaign.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.learning_effect_observation import (
    build_learning_effect_report,
    diff_rounds,
    load_round_ledgers,
    round_effect_snapshot,
    write_learning_effect_report,
)


def _ledger(
    *,
    run_id: str,
    campaign_id: str,
    created_at: str,
    attempts: list[dict],
    delivery_ids: list[str] | None = None,
    canonical_ids: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "qualibug.discovery-trace-ledger.v3",
        "created_at_utc": created_at,
        "run_id": run_id,
        "campaign_id": campaign_id,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "terminal_status_counts": {},
        "delivery_occurrence_finding_ids": delivery_ids or [],
        "canonical_defect_ids": canonical_ids or [],
    }


def _attempt(obligation_id: str, terminal_status: str, reason_code: str = "") -> dict:
    return {
        "obligation_id": obligation_id,
        "executed_obligation_id": obligation_id,
        "terminal_status": terminal_status,
        "reason_code": reason_code,
    }


def _write_ledger(root: Path, project: str, ledger: dict) -> Path:
    target_dir = (
        root / "platform_outputs" / project
        / "discovery_evolution" / "trace_ledgers" / "target-1"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{ledger['run_id']}.trace-ledger.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    return path


def test_round_effect_snapshot_classifies_attempts() -> None:
    ledger = _ledger(
        run_id="RUN_a",
        campaign_id="CMP_x",
        created_at="2026-08-01T00:00:00Z",
        attempts=[
            _attempt("obl_1", "DELIVERABLE"),
            _attempt("obl_2", "PASS"),
            _attempt("obl_3", "BLOCKED", "BLOCKED_MISSING_BINDING"),
            _attempt("obl_4", "BLOCKED", "BLOCKED_MISSING_BINDING"),
            _attempt("obl_5", "DEFERRED"),
        ],
        delivery_ids=["finding_1"],
        canonical_ids=["cdef_1"],
    )
    snapshot = round_effect_snapshot(ledger)
    assert snapshot["executed_obligation_ids"] == ["obl_1", "obl_2"]
    assert snapshot["blocked_obligation_ids"] == ["obl_3", "obl_4"]
    assert snapshot["blocked_reason_counts"] == {"BLOCKED_MISSING_BINDING": 2}
    assert snapshot["delivery_occurrence_finding_ids"] == ["finding_1"]
    assert snapshot["canonical_defect_ids"] == ["cdef_1"]


def test_diff_rounds_reports_executed_and_blocked_deltas() -> None:
    prev = _ledger(
        run_id="RUN_a",
        campaign_id="CMP_x",
        created_at="2026-08-01T00:00:00Z",
        attempts=[
            _attempt("obl_1", "DELIVERABLE"),
            _attempt("obl_2", "BLOCKED", "BLOCKED_MISSING_BINDING"),
            _attempt("obl_3", "BLOCKED", "BLOCKED_MISSING_BINDING"),
        ],
        delivery_ids=["finding_1"],
    )
    next_round = _ledger(
        run_id="RUN_b",
        campaign_id="CMP_x",
        created_at="2026-08-02T00:00:00Z",
        attempts=[
            _attempt("obl_1", "DELIVERABLE"),
            _attempt("obl_2", "DELIVERABLE"),   # unblocked: executed set grew
            _attempt("obl_3", "BLOCKED", "BLOCKED_MISSING_BINDING"),
            _attempt("obl_4", "DELIVERABLE"),   # new execution
        ],
        delivery_ids=["finding_1", "finding_2"],
        canonical_ids=["cdef_1"],
    )
    diff = diff_rounds(prev, next_round)
    assert diff["campaign_id"] == "CMP_x"
    assert diff["prev_run_id"] == "RUN_a"
    assert diff["next_run_id"] == "RUN_b"
    assert diff["executed_obligations"]["delta"] == 2
    assert diff["executed_obligations"]["added"] == ["obl_2", "obl_4"]
    assert diff["blocked_obligations"]["delta"] == -1
    assert diff["blocked_reason_delta"] == {"BLOCKED_MISSING_BINDING": -1}
    assert diff["delivery_occurrence_findings"]["added"] == ["finding_2"]
    assert diff["canonical_defects"]["added"] == ["cdef_1"]


def test_write_report_persists_diffs(tmp_path: Path) -> None:
    prev = _ledger(
        run_id="RUN_a",
        campaign_id="CMP_x",
        created_at="2026-08-01T00:00:00Z",
        attempts=[_attempt("obl_1", "BLOCKED", "BLOCKED_MISSING_BINDING")],
    )
    next_round = _ledger(
        run_id="RUN_b",
        campaign_id="CMP_x",
        created_at="2026-08-02T00:00:00Z",
        attempts=[_attempt("obl_1", "DELIVERABLE")],
    )
    _write_ledger(tmp_path, "proj_effect", prev)
    _write_ledger(tmp_path, "proj_effect", next_round)

    report = write_learning_effect_report("proj_effect", tmp_path)
    assert report["status"] == "OK"
    assert report["campaign_count"] == 1
    assert report["round_count"] == 2
    assert len(report["campaigns"][0]["round_diffs"]) == 1
    diff = report["campaigns"][0]["round_diffs"][0]
    assert diff["executed_obligations"]["delta"] == 1
    assert diff["blocked_total"]["delta"] == -1

    out_dir = tmp_path / "platform_outputs" / "proj_effect" / "learning_effect"
    assert (out_dir / "learning_effect_report.json").exists()
    assert (out_dir / "round_diff_RUN_a_RUN_b.json").exists()


def test_report_empty_when_no_rounds(tmp_path: Path) -> None:
    report = build_learning_effect_report("proj_empty", tmp_path)
    assert report["status"] == "NO_ROUNDS"
    assert report["campaigns"] == []


def test_load_round_ledgers_sorted_by_time(tmp_path: Path) -> None:
    later = _ledger(
        run_id="RUN_b", campaign_id="CMP_x",
        created_at="2026-08-02T00:00:00Z",
        attempts=[],
    )
    earlier = _ledger(
        run_id="RUN_a", campaign_id="CMP_x",
        created_at="2026-08-01T00:00:00Z",
        attempts=[],
    )
    _write_ledger(tmp_path, "proj_sort", later)
    _write_ledger(tmp_path, "proj_sort", earlier)
    ledgers = load_round_ledgers("proj_sort", tmp_path)
    assert [l["run_id"] for l in ledgers] == ["RUN_a", "RUN_b"]
