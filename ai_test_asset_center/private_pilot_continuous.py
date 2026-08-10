"""Continuous discovery scan loop and durable state helpers."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import db_persistence as db_persist
from .private_pilot_scan_coordinator import (
    ScanLeaseBusy,
    active_scan_owner,
    project_scan_lease,
)
from .scan_stage_progress import read_scan_stage_progress


def _read_json_artifact(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8") or "null")
    except Exception as exc:
        raise ValueError(f"failed to read JSON artifact: {path}: {exc}") from exc


def _read_json_object(
    path: Path,
    *,
    missing: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


_CONTINUOUS_STATE_FILE = "continuous_discovery_state.json"
_continuous_threads: dict[tuple[str, str], dict[str, Any]] = {}
_continuous_threads_lock = threading.RLock()


def _thread_key(root: Path, project: str) -> tuple[str, str]:
    return str(root.resolve()), project


def _public_scan_owner(owner: dict[str, Any] | None) -> dict[str, Any]:
    """Project safe scan status projection.

    Lease token, pid/thread ids, tenant id and raw actor data are internal
    coordination details and must never cross the customer HTTP boundary.
    """

    source = owner if isinstance(owner, dict) else {}
    if not source:
        return {}
    return {
        "schema": "qualibug.project-scan-live-status.v1",
        "project_id": str(source.get("project_id") or ""),
        "mode": str(source.get("mode") or "scan"),
        "started_at_utc": str(source.get("started_at_utc") or ""),
    }


def _stop_requested(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return True
    event = entry.get("stop_event")
    if isinstance(event, threading.Event):
        return event.is_set()
    return bool(entry.get("stop"))


def _finding_key(finding: dict[str, Any]) -> tuple[str, str, str]:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    return (
        str(finding.get("title") or finding.get("description") or "").strip().lower()[:240],
        str(
            finding.get("_api_method")
            or finding.get("method")
            or evidence.get("method")
            or ""
        ).upper(),
        str(
            finding.get("_api_path")
            or finding.get("path")
            or evidence.get("path")
            or ""
        ).strip(),
    )


def _result_findings(result: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    for value in (
        result.get("real_findings"),
        result.get("bug_scores"),
        result.get("db_findings"),
        result.get("e2e_findings"),
        result.get("deep_findings"),
        result.get("ui_findings"),
    ):
        if isinstance(value, list):
            candidates.extend(value)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        key = _finding_key(raw)
        if not any(key) or key in seen:
            continue
        seen.add(key)
        rows.append(dict(raw))
    return rows


def _continuous_scan_loop(
    root: Path,
    project: str,
    tenant_id: str,
    interval_s: int,
) -> None:
    """Run serialized scan rounds until convergence, stop or safety limit."""

    from .__main__ import scan as scan_fn
    from .private_pilot_scan_context_contract import (
        CONTINUOUS_CAMPAIGN_CONTEXTS,
        SCAN_CAMPAIGN_CONTEXT,
        continuous_context_key,
    )

    key = _thread_key(root, project)
    no_new_rounds = 0
    completed_rounds = 0
    converge_rounds = 3
    converge_coverage = 0.7
    max_rounds = 20

    try:
        while completed_rounds < max_rounds:
            with _continuous_threads_lock:
                entry = _continuous_threads.get(key)
            if _stop_requested(entry):
                break
            try:
                with project_scan_lease(
                    root,
                    project,
                    mode="continuous_scan",
                    tenant_id=tenant_id,
                    actor=(entry or {}).get("actor")
                    if isinstance((entry or {}).get("actor"), dict)
                    else {},
                ):
                    phase = "scan"
                    campaign_context = CONTINUOUS_CAMPAIGN_CONTEXTS.get(
                        continuous_context_key(root, project)
                    )
                    token = SCAN_CAMPAIGN_CONTEXT.set(campaign_context or None)
                    try:
                        result = scan_fn(
                            project,
                            root,
                            save_report=True,
                            campaign_context=dict(campaign_context)
                            if isinstance(campaign_context, dict)
                            else None,
                        )
                    finally:
                        SCAN_CAMPAIGN_CONTEXT.reset(token)
                    if not isinstance(result, dict):
                        raise TypeError("continuous scan result must be an object")
                    if (
                        result.get("success") is False
                        or result.get("error")
                        or not str(result.get("scan_id") or "").strip()
                    ):
                        raise RuntimeError(
                            str(result.get("error") or "continuous scan produced no result")
                        )

                    phase = "cumulative_merge"
                    findings = _result_findings(result)
                    enriched = dict(result)
                    enriched["findings"] = findings
                    enriched["report_binding"] = {
                        "status": "result_native",
                        "reason": "continuous loop does not read shared report files",
                    }
                    scan_id = db_persist.save_scan(
                        root,
                        tenant_id,
                        project,
                        enriched,
                    )
                    merge_result = db_persist.merge_findings_cumulative(
                        root,
                        tenant_id,
                        project,
                        scan_id,
                        findings,
                    )
                    new_count = int(merge_result.get("new") or 0)

                    phase = "state_update"
                    _update_continuous_state(root, project, result)
                    completed_rounds += 1
                    no_new_rounds = no_new_rounds + 1 if new_count == 0 else 0
                    coverage = float(result.get("coverage") or 0)
                    converged = (
                        no_new_rounds >= converge_rounds
                        and coverage >= converge_coverage
                    )
                    with _continuous_threads_lock:
                        live = _continuous_threads.get(key)
                        if live is not None:
                            live.update(
                                {
                                    "round": completed_rounds,
                                    "last_new": new_count,
                                    "no_new_rounds": no_new_rounds,
                                    "status": "converged" if converged else "running",
                                }
                            )
                    if converged:
                        _mark_continuous_converged(
                            root,
                            project,
                            reason=(
                                f"连续{converge_rounds}轮无新发现且覆盖率≥"
                                f"{converge_coverage:.0%}"
                            ),
                        )
                        break
            except ScanLeaseBusy as exc:
                _mark_continuous_waiting(root, project, exc.owner)
            except Exception as exc:
                _record_continuous_failure(
                    root,
                    project,
                    round_num=completed_rounds + 1,
                    phase=locals().get("phase", "scan"),
                    error=exc,
                )
                with _continuous_threads_lock:
                    live = _continuous_threads.get(key)
                    if live is not None:
                        live.update(
                            {
                                "status": "failed",
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                        )
                # Fail fast: the failure is persisted above and the thread entry
                # is marked failed, but the original exception must still
                # propagate to the caller instead of being swallowed.  The
                # finally block below still performs the thread cleanup.
                raise

            deadline = time.monotonic() + max(1, int(interval_s))
            while time.monotonic() < deadline:
                with _continuous_threads_lock:
                    live = _continuous_threads.get(key)
                if _stop_requested(live):
                    break
                time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))
        else:
            _mark_continuous_max_rounds(
                root,
                project,
                max_rounds=max_rounds,
            )
    finally:
        with _continuous_threads_lock:
            entry = _continuous_threads.get(key)
            if entry is not None:
                entry["finished_at"] = time.time()
                entry["alive"] = False
                if _stop_requested(entry) and entry.get("status") not in {
                    "failed",
                    "converged",
                }:
                    entry["status"] = "stopped"
            _continuous_threads.pop(key, None)


def _continuous_state_path(root: Path, project: str) -> Path:
    return (
        root
        / "platform_workspace"
        / project
        / "defect_discovery"
        / _CONTINUOUS_STATE_FILE
    )


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
    try:
        state = _read_json_object(state_file)
    except ValueError:
        # The state file is already corrupt.  Never overwrite the failure
        # scene; the failure receipt above remains the durable record and the
        # original exception keeps propagating to the caller.
        return
    state["status"] = "failed"
    state["converged"] = False
    state.pop("converge_reason", None)
    state.pop("termination", None)
    state["last_failure"] = failure
    _write_json_object_atomic(state_file, state)


def _mark_continuous_waiting(
    root: Path,
    project: str,
    active_owner: dict[str, Any],
) -> None:
    state_file = _continuous_state_path(root, project)
    state = _read_json_object(state_file)
    state["status"] = "waiting_for_project_scan"
    state["converged"] = False
    state["active_scan"] = dict(active_owner or {})
    state["last_wait_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
    )
    _write_json_object_atomic(state_file, state)


def _mark_continuous_converged(root: Path, project: str, reason: str) -> None:
    state_file = _continuous_state_path(root, project)
    state = _read_json_object(state_file)
    state["status"] = "converged"
    state["converged"] = True
    state["converge_reason"] = reason
    state.pop("last_failure", None)
    state.pop("termination", None)
    state.pop("active_scan", None)
    _write_json_object_atomic(state_file, state)


def _mark_continuous_max_rounds(
    root: Path,
    project: str,
    *,
    max_rounds: int,
) -> None:
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


def _update_continuous_state(
    root: Path,
    project: str,
    scan_result: dict[str, Any],
) -> None:
    state_file = _continuous_state_path(root, project)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state = _read_json_object(state_file)
    runs = state.get("runs", [])
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError(
            f"continuous discovery runs must be a list of objects: {state_file}"
        )
    runs.append(
        {
            "scan_id": str(scan_result.get("scan_id") or ""),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "findings": scan_result.get("total_findings", 0),
            "coverage": scan_result.get("coverage", 0),
            "grade": scan_result.get("grade", "C"),
            "duration_ms": scan_result.get("total_ms", 0),
        }
    )
    runs = runs[-50:]
    state.update(
        {
            "runs": runs,
            "status": "scanning",
            "converged": False,
            "last_scan": runs[-1]["timestamp"],
            "total_runs": len(runs),
        }
    )
    state.pop("converge_reason", None)
    state.pop("termination", None)
    state.pop("last_failure", None)
    state.pop("active_scan", None)
    _write_json_object_atomic(state_file, state)


def _get_continuous_state(root: Path, project: str) -> dict[str, Any]:
    state_file = _continuous_state_path(root, project)
    state = _read_json_object(state_file)
    runs = state.get("runs", [])
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError(
            f"continuous discovery runs must be a list of objects: {state_file}"
        )
    last_run = runs[-1] if runs else {}
    status = str(state.get("status") or "idle")
    messages = {
        "idle": "尚未运行持续检测。",
        "scanning": "持续检测进行中。",
        "waiting_for_project_scan": "其他项目检测任务正在运行，持续检测等待租约。",
        "stopping": "持续检测正在停止。",
        "stopped": "持续检测已停止。",
        "converged": "持续检测覆盖已收敛，系统自动暂停。",
        "max_rounds_reached": "持续检测达到安全轮次上限，未判定收敛。",
        "failed": "持续检测失败，请查看 last_failure。",
    }
    message = messages.get(status, "持续检测状态未知。")
    last_failure = state.get("last_failure") if isinstance(state.get("last_failure"), dict) else {}
    termination = state.get("termination") if isinstance(state.get("termination"), dict) else {}
    if status == "failed" and last_failure:
        message = (
            f"持续检测失败（{last_failure.get('phase')} · {last_failure.get('error_type')}）："
            f"{last_failure.get('error')}。请查看 last_failure。"
        )
    elif status == "max_rounds_reached" and termination.get("round"):
        message = f"持续检测达到安全轮次上限（{termination.get('round')} 轮），未判定收敛。"

    live_owner = active_scan_owner(root, project)
    stored_owner = state.get("active_scan") if isinstance(state.get("active_scan"), dict) else {}
    visible_owner = _public_scan_owner(live_owner or stored_owner)
    elapsed_seconds = 0
    if live_owner:
        try:
            elapsed_seconds = max(
                0,
                int(time.time() - float(live_owner.get("started_unix") or time.time())),
            )
        except (TypeError, ValueError):
            elapsed_seconds = 0
    stage_progress = read_scan_stage_progress(root, project) if live_owner else {}

    return {
        "status": status,
        "converged": bool(state.get("converged")),
        "runs": runs[-10:],
        "total_runs": int(state.get("total_runs") or len(runs)),
        "last_scan": state.get("last_scan", ""),
        "last_findings": last_run.get("findings", 0),
        "last_coverage": last_run.get("coverage", 0),
        "last_failure": last_failure,
        "termination": termination,
        "active_scan": visible_owner,
        "active_scan_live": bool(live_owner),
        "active_scan_elapsed_seconds": elapsed_seconds,
        "scan_stage_progress": stage_progress,
        "message": message,
    }


__all__ = [
    "_CONTINUOUS_STATE_FILE",
    "_continuous_scan_loop",
    "_continuous_state_path",
    "_continuous_threads",
    "_continuous_threads_lock",
    "_get_continuous_state",
    "_mark_continuous_converged",
    "_mark_continuous_max_rounds",
    "_record_continuous_failure",
    "_update_continuous_state",
]
