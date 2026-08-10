"""Finalize scan-stage telemetry from the completed authoritative scan result.

The planning/execution path publishes real ACTIVE boundaries while the scan is
running. Some late phases are still owned by the monolithic scan coordinator.
This post-hook therefore publishes completion-only receipts for those phases
from their already-produced authoritative result objects; it never invents a
start time, percentage, or duration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .scan_stage_progress import fail_active_scan_stage, mark_scan_stage

HOOK_NAME = "scan_stage_finalization"


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def install_scan_stage_finalization_hook() -> None:
    """Register completion-only stage projection idempotently."""
    from .scan_post_hooks import register_scan_post_hook

    register_scan_post_hook(HOOK_NAME, _finalize_scan_stage_progress)


def _finalize_scan_stage_progress(
    result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result

    if result.get("success") is False or _text(result.get("error")):
        fail_active_scan_stage(
            root,
            project,
            detail=(
                _text(result.get("error"))
                or _text(result.get("failure_stage"))
                or "scan_failed_before_stage_finalization"
            )[:240],
        )
        return result

    evidence = _record(result.get("evidence_bundle"))
    evidence_status = _text(evidence.get("status")).lower()
    execution_status = _text(result.get("execution_status")).lower()
    if evidence:
        if evidence_status in {"failed", "error", "invalid"}:
            evidence_stage_status = "failed"
        elif evidence_status in {"blocked", "not_created"} and execution_status == "blocked":
            evidence_stage_status = "blocked"
        else:
            evidence_stage_status = "completed"
        mark_scan_stage(
            root,
            project,
            "evidence_collection",
            evidence_stage_status,
            detail=f"evidence_bundle={evidence_status or 'reported'}"[:240],
        )

    test_data_plan = _record(result.get("test_data_plan"))
    test_data_status = _text(test_data_plan.get("status")).lower()
    if test_data_plan:
        if test_data_status in {"failed", "error", "invalid"}:
            test_data_stage_status = "failed"
        elif test_data_status.startswith("blocked"):
            test_data_stage_status = "blocked"
        else:
            test_data_stage_status = "completed"
        mark_scan_stage(
            root,
            project,
            "test_data_assessment",
            test_data_stage_status,
            detail=(
                f"test_data_plan={test_data_status or 'reported'}"
                f" strategy={_text(test_data_plan.get('strategy')) or 'unspecified'}"
            )[:240],
        )

    release_gate = _record(result.get("release_gate"))
    release_status = _text(release_gate.get("status")).lower()
    release_verdict = _text(release_gate.get("verdict")).lower()
    if release_gate:
        delivery_stage_status = (
            "failed"
            if release_status in {"failed", "error", "invalid"}
            else "completed"
        )
        report_state = "persisted" if _text(result.get("report_path")) else "not_persisted"
        mark_scan_stage(
            root,
            project,
            "delivery_finalization",
            delivery_stage_status,
            detail=(
                f"release_gate={release_status or 'reported'}"
                f" verdict={release_verdict or 'unspecified'}"
                f" report={report_state}"
            )[:240],
        )

    return result


__all__ = [
    "HOOK_NAME",
    "install_scan_stage_finalization_hook",
]
