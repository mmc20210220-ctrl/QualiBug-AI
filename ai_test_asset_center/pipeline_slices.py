"""Behavior slice lifecycle management.
Extracted from v12_pipeline.py.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ABS_MAX_SLICE_BUDGET = 200
_ABS_MAX_ROUND_LIMIT = 48


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _behavior_slice_settings() -> dict[str, int]:
    try:
        from .policy_wiring import get_policy_value
        budget = get_policy_value("execution", "max_behavior_slices_per_round", 15)
        round_number = get_policy_value("execution", "incremental_discovery_round", 1)
        round_limit = get_policy_value("execution", "incremental_discovery_round_limit", 8)
    except Exception:
        budget, round_number, round_limit = 15, 1, 8
    return {
        "slice_budget": _as_int(os.environ.get("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", budget), 15, 1, _ABS_MAX_SLICE_BUDGET),
        "round_number": _as_int(os.environ.get("QUALIBUG_DISCOVERY_ROUND", round_number), 1, 1, 24),
        "round_limit": _as_int(os.environ.get("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", round_limit), 8, 1, _ABS_MAX_ROUND_LIMIT),
    }


def _auto_scale_slice_budget(pool_size: int) -> int:
    import math
    if pool_size <= 0:
        return 15
    return max(15, min(_ABS_MAX_SLICE_BUDGET, math.ceil(pool_size / 2)))


def _auto_scale_round_limit(pool_size: int, budget: int) -> int:
    if pool_size <= 0 or budget <= 0:
        return 8
    rounds = (pool_size + budget - 1) // budget
    return max(1, min(_ABS_MAX_ROUND_LIMIT, rounds))


def _slice_ledger_path(root: Path, project: str) -> Path:
    return root / "platform_workspace" / str(project) / "defect_discovery" / "v12_behavior_slice_ledger.json"


def _load_persisted_slice_history(root: Path, project: str) -> list[dict[str, Any]] | None:
    path = _slice_ledger_path(root, project)
    if not path.exists():
        return None
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("entries") or data.get("history")
    except Exception:
        pass
    return None


def _persist_slice_ledger(root: Path, project: str, ledger: dict[str, Any]) -> None:
    path = _slice_ledger_path(root, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _slice_history(history: list[dict[str, Any]] | None) -> tuple[set[str], set[str]]:
    attempted: set[str] = set()
    confirmed: set[str] = set()
    if not history:
        return attempted, confirmed
    for entry in history:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("slice_id") or "").strip()
        if not sid:
            continue
        attempted.add(sid)
        if str(entry.get("status") or "").lower() in {"confirmed", "deliverable"}:
            confirmed.add(sid)
    return attempted, confirmed
