"""Behavior slice lifecycle management.

SSOT extracted from ``v12_pipeline``; the compatibility module re-exports via
``from .pipeline_slices import *``.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

# Absolute safety clamps — auto-scaler and env overrides stay bounded.
_ABS_MAX_SLICE_BUDGET = 1200
_ABS_MAX_ROUND_LIMIT = 48


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _behavior_slice_settings() -> dict[str, int]:
    try:
        from .policy_wiring import get_policy_value
        budget = get_policy_value("execution", "max_behavior_slices_per_round", 15)
        round_number = get_policy_value("execution", "incremental_discovery_round", 1)
        round_limit = get_policy_value("execution", "incremental_discovery_round_limit", 8)
    except Exception:
        budget, round_number, round_limit = 15, 1, 8

    # Per-round budget remains a starting value and is auto-scaled to the
    # discovered compiled pool later in planning. Automatic continuation uses
    # the absolute safety ceiling rather than the historical policy default of
    # eight rounds: the runtime already exits immediately when the pending queue
    # is empty and has independent no-progress / repeated-plan / repeated-error
    # guards for retry-only loops, so a small default round count only creates a
    # hidden Recall ceiling for large systems. An explicit environment value is
    # still a hard operator override and may intentionally lower the ceiling.
    explicit_round_limit = str(
        os.environ.get("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT") or ""
    ).strip()
    resolved_round_limit = (
        _as_int(explicit_round_limit, 8, 1, _ABS_MAX_ROUND_LIMIT)
        if explicit_round_limit
        else _ABS_MAX_ROUND_LIMIT
    )
    return {
        "slice_budget": _as_int(os.environ.get("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", budget), 15, 1, _ABS_MAX_SLICE_BUDGET),
        "round_number": _as_int(os.environ.get("QUALIBUG_DISCOVERY_ROUND", round_number), 1, 1, 24),
        "round_limit": resolved_round_limit,
    }


def _auto_scale_slice_budget(pool_size: int) -> int:
    """Per-round slice budget that follows the business system's scale.

    Small systems (few source-bound slices) keep the lean historical floor of 15.
    Large enterprises (hundreds of slices from state graph + analyzers + LLM
    reasoner) automatically get a proportionally larger budget so the candidate
    pool is actually consumed instead of starving at 15/round — no env tuning.
    Target: drain the pool in ~2 rounds, bounded by _ABS_MAX_SLICE_BUDGET.
    """
    import math

    if pool_size <= 0:
        return 15
    return max(15, min(_ABS_MAX_SLICE_BUDGET, pool_size))


def _auto_scale_round_limit(pool_size: int, budget: int) -> int:
    """Automatic round count sized to actually drain ``pool_size`` at ``budget``/round.

    Kept for legacy schedulers that know the complete pool size at scheduling
    time. The mainline campaign now uses ``_ABS_MAX_ROUND_LIMIT`` as its
    automatic ceiling and exits as soon as its lossless pending queue drains;
    explicit environment configuration remains the only hard lower override.
    """
    if pool_size <= 0 or budget <= 0:
        return 8
    needed = math.ceil(pool_size / budget) + 1
    return max(8, min(_ABS_MAX_ROUND_LIMIT, needed))


def _slice_ledger_path(root: Path, project: str) -> Path:
    return root / "platform_workspace" / str(project) / "defect_discovery" / "v12_behavior_slice_ledger.json"


def _load_persisted_slice_history(
    root: Path,
    project: str,
    source_snapshot_hash: str = "",
    source_hash: str = "",
) -> list[dict[str, Any]]:
    path = _slice_ledger_path(root, project)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    expected_snapshot = str(source_snapshot_hash or "").strip()
    expected_source_hash = str(source_hash or "").strip()
    persisted_snapshot = str(payload.get("source_snapshot_hash") or "").strip()
    persisted_source_hash = str(payload.get("source_hash") or "").strip()
    snapshot_matches = bool(expected_snapshot) and persisted_snapshot == expected_snapshot
    source_hash_matches = bool(expected_source_hash) and persisted_source_hash == expected_source_hash
    if expected_snapshot or expected_source_hash:
        if not snapshot_matches and not source_hash_matches:
            return []
    return [{"behavior_slice_ledger": payload}]


def _derive_slice_status(
    attempted_ids: list[str] | set[str] | tuple[str, ...],
    confirmed_ids: list[str] | set[str] | tuple[str, ...],
    campaign_status: str,
) -> dict[str, str]:
    """主链 4: turn raw campaign progress into an explicit per-task status map so
    the task list surfaced to the API/frontend carries pending/running/passed/blocked
    instead of only opaque id sets.

    - attempted & confirmed        -> passed
    - attempted & not confirmed     -> running (or blocked when the campaign is blocked)
    - not attempted (planned)       -> omitted from the map, implicitly "pending"
    """
    confirmed_set = set()
    for value in confirmed_ids:
        if value is None:
            continue
        s = str(value).strip()
        if s:
            confirmed_set.add(s)
    status: dict[str, str] = {}
    blocked = str(campaign_status or "").strip() == "blocked"
    for value in attempted_ids:
        if value is None:
            continue
        sid = str(value).strip()
        if not sid:
            continue
        if sid in confirmed_set:
            status[sid] = "passed"
        else:
            status[sid] = "blocked" if blocked else "running"
    return status


def _persist_slice_ledger(root: Path, project: str, ledger: dict[str, Any]) -> None:
    path = _slice_ledger_path(root, project)
    attempted = [str(value) for value in ledger.get("attempted_slice_ids", []) if str(value)]
    confirmed = [str(value) for value in ledger.get("confirmed_slice_ids", []) if str(value)]
    # 主链 4: derive an explicit per-task status map from the campaign progress
    # so the task list surfaced to the API/frontend carries pending/running/
    # passed/blocked instead of only opaque id sets.
    slice_status = _derive_slice_status(attempted, confirmed, ledger.get("campaign_status") or "")
    safe = {
        "campaign_id": str(ledger.get("campaign_id") or ""),
        "campaign_status": str(ledger.get("campaign_status") or ""),
        "scope_id": str(ledger.get("scope_id") or ""),
        "source_snapshot_hash": str(ledger.get("source_snapshot_hash") or ""),
        "source_id": str(ledger.get("source_id") or ""),
        "source_hash": str(ledger.get("source_hash") or ""),
        "project": str(project),
        "round": int(ledger.get("round") or 0),
        "round_limit": int(ledger.get("round_limit") or 0),
        "slice_budget": int(ledger.get("slice_budget") or 0),
        "selection_mode": str(ledger.get("selection_mode") or ""),
        "selected_slice_ids": [str(value) for value in ledger.get("selected_slice_ids", []) if str(value)],
        "attempted_slice_ids": attempted,
        "confirmed_slice_ids": confirmed,
        "slice_status": slice_status,
        "next_round": ledger.get("next_round"),
        "stop_reason": str(ledger.get("stop_reason") or ""),
        "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    from .artifact_redactor import write_json_redacted

    write_json_redacted(path, safe)
