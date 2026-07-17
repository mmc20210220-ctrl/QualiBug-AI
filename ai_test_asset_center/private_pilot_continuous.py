"""Continuous discovery scan loop and durable state helpers.

Extracted from ``private_pilot_service``. Symbols remain re-exported there so
handler routes and runtime patches keep working.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import db_persistence as db_persist


def _read_json_artifact(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception as exc:
        raise ValueError(f"failed to read JSON artifact: {path}: {exc}") from exc


def _read_json_object(path: Path, *, missing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return dict(missing or {})
    payload = _read_json_artifact(path)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _write_json_object_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


_CONTINUOUS_STATE_FILE = "continuous_discovery_state.json"

# In-memory tracking of active continuous-scan threads per project.
# Key: (root, project_id), Value: dict with thread + stop flag.
_continuous_threads: dict[tuple[str, str], dict[str, Any]] = {}


def _continuous_scan_loop(root: Path, project: str, tenant_id: str, interval_s: int) -> None:
    """Background loop: run scans at intervals until convergence or stop.

    Convergence = consecutive N rounds with zero new findings AND coverage
    above threshold. Once converged, the loop auto-stops and records the
    reason so the UI can show "覆盖收敛，自动暂停".
    """
    import time as _time
    from .__main__ import scan as _scan_fn

    key = (str(root), project)
    no_new_rounds = 0
    CONVERGE_ROUNDS = 3  # 连续3轮无新发现视为收敛
    CONVERGE_COVERAGE = 0.7
    MAX_ROUNDS = 20  # 安全上限，防止无限循环

    for round_num in range(1, MAX_ROUNDS + 1):
        # Check stop flag
        entry = _continuous_threads.get(key)
        if not entry or entry.get("stop"):
            break

        phase = "scan"
        try:
            from .private_pilot_scan_context_contract import (
                CONTINUOUS_CAMPAIGN_CONTEXTS,
                SCAN_CAMPAIGN_CONTEXT,
                continuous_context_key,
            )

            campaign_context = CONTINUOUS_CAMPAIGN_CONTEXTS.get(continuous_context_key(root, project))
            token = SCAN_CAMPAIGN_CONTEXT.set(campaign_context or None)
            try:
                result = _scan_fn(
                    project,
                    root,
                    save_report=True,
                    campaign_context=dict(campaign_context) if isinstance(campaign_context, dict) else None,
                )
            finally:
                SCAN_CAMPAIGN_CONTEXT.reset(token)
            if not isinstance(result, dict):
                raise TypeError("continuous scan result must be an object")

            # Cumulative merge
            phase = "cumulative_merge"
            db_persist.init_db(root)
            report_path = root / "platform_outputs" / project / "intelligence_report.json"
            report_data = _read_json_object(report_path)
            findings_value = report_data.get("real_findings") or report_data.get("bug_scores") or []
            if not isinstance(findings_value, list):
                raise ValueError(f"continuous report findings must be a list: {report_path}")
            if any(not isinstance(finding, dict) for finding in findings_value):
                raise ValueError(f"continuous report findings must contain objects: {report_path}")
            findings_list = list(findings_value)
            enriched = dict(result)
            enriched["findings"] = findings_list
            scan_id = db_persist.save_scan(root, tenant_id, project, enriched)
            merge_result = db_persist.merge_findings_cumulative(root, tenant_id, project, scan_id, findings_list)
            if not isinstance(merge_result, dict):
                raise TypeError("cumulative merge result must be an object")
            new_count = int(merge_result.get("new") or 0)

            # Update continuous state
            phase = "state_update"
            _update_continuous_state(root, project, result)

            # Convergence check
            if new_count == 0:
                no_new_rounds += 1
            else:
                no_new_rounds = 0

            phase = "convergence"
            coverage = float(result.get("coverage", 0) or 0)
            converged = no_new_rounds >= CONVERGE_ROUNDS and coverage >= CONVERGE_COVERAGE

            # Update thread entry with progress
            if key in _continuous_threads:
                _continuous_threads[key]["round"] = round_num
                _continuous_threads[key]["last_new"] = new_count
                _continuous_threads[key]["no_new_rounds"] = no_new_rounds
                if converged:
                    _continuous_threads[key]["converged"] = True
                    _continuous_threads[key]["stop"] = True
                    # Mark state as converged
                    _mark_continuous_converged(root, project, reason="连续{}轮无新发现且覆盖率≥{:.0%}".format(CONVERGE_ROUNDS, CONVERGE_COVERAGE))
                    break
        except Exception as exc:
            if key in _continuous_threads:
                _continuous_threads[key].update({
                    "status": "failed",
                    "failed_phase": phase,
                    "failed_round": round_num,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "stop": True,
                })
            _continuous_threads.pop(key, None)
            _record_continuous_failure(root, project, round_num=round_num, phase=phase, error=exc)
            raise

        # Wait for next interval (check stop flag every second)
        for _ in range(interval_s):
            entry = _continuous_threads.get(key)
            if not entry or entry.get("stop"):
                break
            _time.sleep(1)
    else:
        try:
            _mark_continuous_max_rounds(root, project, max_rounds=MAX_ROUNDS)
        finally:
            _continuous_threads.pop(key, None)
        return

    # Clean up thread entry
    _continuous_threads.pop(key, None)
def _continuous_state_path(root: Path, project: str) -> Path:
    return root / "platform_workspace" / project / "defect_discovery" / _CONTINUOUS_STATE_FILE
def _record_continuous_failure(
    root: Path,
    project: str,
    *,
    round_num: int,
    phase: str,
    error: Exception,
) -> None:
    state_file = _continuous_state_path(root, project)
    failure = {
        "project": project,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "round": round_num,
        "phase": phase,
        "error_type": type(error).__name__,
        "error": str(error),
    }
    _write_json_object_atomic(
        state_file.with_name("continuous_discovery_last_error.json"),
        failure,
    )
    state = _read_json_object(state_file)
    state["status"] = "failed"
    state["converged"] = False
    state.pop("converge_reason", None)
    state.pop("termination", None)
    state["last_failure"] = failure
    _write_json_object_atomic(state_file, state)
def _mark_continuous_converged(root: Path, project: str, reason: str) -> None:
    """Mark the continuous discovery state as converged with a reason."""
    state_file = _continuous_state_path(root, project)
    state = _read_json_object(state_file)
    state["status"] = "converged"
    state["converged"] = True
    state["converge_reason"] = reason
    state.pop("last_failure", None)
    state.pop("termination", None)
    _write_json_object_atomic(state_file, state)
def _mark_continuous_max_rounds(root: Path, project: str, *, max_rounds: int) -> None:
    state_file = _continuous_state_path(root, project)
    state = _read_json_object(state_file)
    state["status"] = "max_rounds_reached"
    state["converged"] = False
    state.pop("converge_reason", None)
    state["termination"] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reason_code": "MAX_ROUNDS_REACHED",
        "round": max_rounds,
    }
    _write_json_object_atomic(state_file, state)
def _update_continuous_state(root: Path, project: str, scan_result: dict) -> None:
    """Track continuous discovery coverage state after each auto-scan."""
    state_dir = root / "platform_workspace" / project / "defect_discovery"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = _continuous_state_path(root, project)
    state = _read_json_object(state_file)

    total_findings = scan_result.get("total_findings", 0)
    coverage = scan_result.get("coverage", 0)
    grade = scan_result.get("grade", "C")
    total_ms = scan_result.get("total_ms", 0)

    # Track scan runs
    runs = state.get("runs", [])
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError(f"continuous discovery runs must be a list of objects: {state_file}")
    runs.append({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "findings": total_findings,
        "coverage": coverage,
        "grade": grade,
        "duration_ms": total_ms,
    })
    # Keep last 50 runs
    runs = runs[-50:]

    state["runs"] = runs
    state["status"] = "scanning"
    state["converged"] = False
    state["last_scan"] = runs[-1]["timestamp"] if runs else ""
    state["total_runs"] = len(runs)
    state.pop("converge_reason", None)
    state.pop("termination", None)
    state.pop("last_failure", None)

    _write_json_object_atomic(state_file, state)
def _get_continuous_state(root: Path, project: str) -> dict[str, Any]:
    """Get the current continuous discovery state."""
    state_file = root / "platform_workspace" / project / "defect_discovery" / _CONTINUOUS_STATE_FILE
    if not state_file.exists():
        return {
            "status": "idle",
            "converged": False,
            "runs": [],
            "total_runs": 0,
            "message": "尚未运行过持续检测。上传文档后将自动开始。"
        }
    state = _read_json_object(state_file)
    runs = state.get("runs", [])
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError(f"continuous discovery runs must be a list of objects: {state_file}")
    last_failure = state.get("last_failure")
    if last_failure is not None and not isinstance(last_failure, dict):
        raise ValueError(f"continuous discovery last_failure must be an object: {state_file}")
    termination = state.get("termination")
    if termination is not None and not isinstance(termination, dict):
        raise ValueError(f"continuous discovery termination must be an object: {state_file}")
    last_run = runs[-1] if runs else {}
    status = str(state.get("status") or "idle")
    if status == "failed" and last_failure:
        message = (
            f"持续检测失败（阶段 {last_failure.get('phase') or 'unknown'}，"
            f"第 {last_failure.get('round') or 0} 轮）：{last_failure.get('error') or 'unknown error'}"
        )
    elif status == "max_rounds_reached" and termination:
        message = f"持续检测已达到 {termination.get('round') or 0} 轮安全上限，未判定为收敛。"
    elif state.get("converged"):
        message = "持续检测覆盖已收敛，系统自动暂停。上传新文档后将自动恢复。"
    elif runs:
        message = "持续检测进行中，系统检测到新的覆盖空间。"
    else:
        message = "等待首次扫描..."
    return {
        "status": status,
        "converged": state.get("converged", False),
        "runs": runs[-10:],
        "total_runs": state.get("total_runs", len(runs)),
        "last_scan": state.get("last_scan", ""),
        "last_findings": last_run.get("findings", 0),
        "last_coverage": last_run.get("coverage", 0),
        "last_failure": last_failure or {},
        "termination": termination or {},
        "message": message,
    }
