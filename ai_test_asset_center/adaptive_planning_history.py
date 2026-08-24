"""Receipt-backed adaptive-planning budget and execution history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


PLANNING_HISTORY_SCHEMA = "qualibug.adaptive-planning-history.v1"
PLANNING_BUDGET_SCHEMA = "qualibug.adaptive-planning-budget.v1"


class AdaptivePlanningHistoryError(ValueError):
    """Planning history is malformed or belongs to another policy identity."""


# Planning history reads only the receipt sub-field of the previous
# scan_result; a multi-hundred-MB scan_result must never be fully loaded
# just to reach it (measured MemoryError at 787MB). Skip above this size.
_MAX_PLANNING_HISTORY_LOAD_BYTES = 256 << 20  # 256MB

def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _positive_budget(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AdaptivePlanningHistoryError("planning_budget_invalid")
    return value


def _policy_identity(value: Any) -> dict[str, str]:
    row = _dict(value)
    identity = {
        "policy_id": _text(row.get("policy_id")),
        "policy_version": _text(row.get("policy_version")),
        "strategy_fingerprint": _text(row.get("strategy_fingerprint")),
    }
    missing = [key for key, item in identity.items() if not item]
    if missing:
        raise AdaptivePlanningHistoryError(
            "planning_history_policy_identity_missing:" + ",".join(missing)
        )
    fingerprint = identity["strategy_fingerprint"]
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef"
        for character in fingerprint.lower()
    ):
        raise AdaptivePlanningHistoryError(
            "planning_history_strategy_fingerprint_invalid"
        )
    return identity


def build_planning_budget_receipt(configured_budget: int) -> dict[str, Any]:
    """Bind the operator budget without silently increasing it."""
    budget = _positive_budget(configured_budget)
    payload: dict[str, Any] = {
        "schema_version": PLANNING_BUDGET_SCHEMA,
        "configured_budget": budget,
        "effective_budget": budget,
        "consumed_budget": 0,
        "remaining_budget": budget,
        "stop_condition": "NOT_STARTED",
    }
    payload["receipt_fingerprint"] = _fingerprint(payload)
    return payload


def finalize_planning_budget_receipt(
    receipt: dict[str, Any],
    *,
    consumed_budget: int,
    stop_condition: str,
) -> dict[str, Any]:
    value = dict(_dict(receipt))
    if value.get("schema_version") != PLANNING_BUDGET_SCHEMA:
        raise AdaptivePlanningHistoryError("planning_budget_schema_invalid")
    claimed = _text(value.pop("receipt_fingerprint", ""))
    if not claimed or claimed != _fingerprint(value):
        raise AdaptivePlanningHistoryError("planning_budget_fingerprint_mismatch")
    budget = _positive_budget(value.get("effective_budget"))
    if (
        isinstance(consumed_budget, bool)
        or not isinstance(consumed_budget, int)
        or not 0 <= consumed_budget <= budget
    ):
        raise AdaptivePlanningHistoryError("planning_budget_consumption_invalid")
    terminal = _text(stop_condition)
    if not terminal:
        raise AdaptivePlanningHistoryError("planning_budget_stop_condition_missing")
    value.update({
        "consumed_budget": consumed_budget,
        "remaining_budget": budget - consumed_budget,
        "stop_condition": terminal,
    })
    value["receipt_fingerprint"] = _fingerprint(value)
    return value


def build_planning_history_receipt(
    *,
    policy_identity: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize observed compile/execution conversion without inventing yield."""
    identity = _policy_identity(policy_identity)
    family_counts: dict[str, dict[str, int]] = {}
    for raw_attempt in _list(attempts):
        attempt = _dict(raw_attempt)
        family = _text(attempt.get("risk_family"))
        if not family:
            raise AdaptivePlanningHistoryError(
                "planning_history_attempt_family_missing"
            )
        counts = family_counts.setdefault(
            family,
            {"attempted": 0, "compiled": 0, "executed": 0},
        )
        counts["attempted"] += 1
        stages = {
            _text(_dict(stage).get("stage")): _text(
                _dict(stage).get("status")
            ).upper()
            for stage in _list(attempt.get("stages"))
            if isinstance(stage, dict)
        }
        if stages.get("compile") == "COMPILED":
            counts["compiled"] += 1
        if stages.get("execution") == "EXECUTED":
            counts["executed"] += 1
    metrics = {
        family: {
            **counts,
            "compile_rate": round(counts["compiled"] / counts["attempted"], 6),
            "execution_rate": round(counts["executed"] / counts["attempted"], 6),
            "formal_yield_status": "NOT_MEASURED",
            "cost_status": "NOT_MEASURED",
        }
        for family, counts in sorted(family_counts.items())
    }
    payload: dict[str, Any] = {
        "schema_version": PLANNING_HISTORY_SCHEMA,
        "policy_identity": identity,
        "history_status": "OBSERVED_EXECUTION_ONLY",
        "formal_yield_status": "NOT_MEASURED",
        "cost_status": "NOT_MEASURED",
        "attempt_count": sum(row["attempted"] for row in family_counts.values()),
        "family_metrics": metrics,
    }
    payload["receipt_id"] = "planning_history_" + _fingerprint(payload)[:20]
    payload["receipt_fingerprint"] = _fingerprint(payload)
    return payload


def historical_yield_from_receipt(
    receipt: dict[str, Any],
    *,
    expected_policy_identity: dict[str, Any],
) -> dict[str, float]:
    """Validate exact identity and expose only actually measured fields."""
    value = dict(_dict(receipt))
    if value.get("schema_version") != PLANNING_HISTORY_SCHEMA:
        raise AdaptivePlanningHistoryError("planning_history_schema_invalid")
    claimed = _text(value.pop("receipt_fingerprint", ""))
    if not claimed or claimed != _fingerprint(value):
        raise AdaptivePlanningHistoryError(
            "planning_history_fingerprint_mismatch"
        )
    observed_identity = _policy_identity(value.get("policy_identity"))
    expected_identity = _policy_identity(expected_policy_identity)
    for field, expected in expected_identity.items():
        if observed_identity[field] != expected:
            raise AdaptivePlanningHistoryError(
                f"planning_history_policy_identity_mismatch:{field}"
            )
    history: dict[str, float] = {}
    for family, raw_metrics in _dict(value.get("family_metrics")).items():
        family_name = _text(family)
        metrics = _dict(raw_metrics)
        if not family_name:
            raise AdaptivePlanningHistoryError(
                "planning_history_family_identity_missing"
            )
        for source, target in (
            ("compile_rate", "compile"),
            ("execution_rate", "exec"),
        ):
            raw = metrics.get(source)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise AdaptivePlanningHistoryError(
                    f"planning_history_{source}_invalid:{family_name}"
                )
            rate = float(raw)
            if not 0.0 <= rate <= 1.0:
                raise AdaptivePlanningHistoryError(
                    f"planning_history_{source}_invalid:{family_name}"
                )
            history[f"{target}:{family_name}"] = rate
    return history


def select_matching_historical_yield(
    receipt: dict[str, Any],
    *,
    expected_policy_identity: dict[str, Any],
) -> tuple[dict[str, float], str]:
    """Treat a valid receipt for another policy as cold start, not evidence."""
    try:
        history = historical_yield_from_receipt(
            receipt,
            expected_policy_identity=expected_policy_identity,
        )
    except AdaptivePlanningHistoryError as exc:
        if str(exc).startswith("planning_history_policy_identity_mismatch:"):
            return {}, "POLICY_IDENTITY_MISMATCH"
        raise
    return history, "MATCHED"


def load_prior_planning_history_receipt(
    root: Path,
    project: str,
) -> dict[str, Any]:
    """Load the prior product receipt; absence is an explicit cold start."""
    output_root = (Path(root) / "platform_outputs").resolve()
    path = (output_root / _text(project) / "scan_result.json").resolve()
    if output_root != path and output_root not in path.parents:
        raise AdaptivePlanningHistoryError("planning_history_project_invalid")
    if not path.is_file():
        return {}
    try:
        size = path.stat().st_size
    except OSError:
        return {}
    if size > _MAX_PLANNING_HISTORY_LOAD_BYTES:
        from .scan_result_store import is_sharded_scan_result

        if not is_sharded_scan_result(path):
            # 旧单文件超大产物：规划历史只需要 receipt 子字段，全量 json.loads 会
            # 先于 receipt 耗尽内存。跳过并给出可见原因（fail-open 冷启动语义）。
            # 分片 store 索引很小，不受该限制（keys 流式只取所需子字段）。
            return {
                "status": "SKIPPED",
                "reason_code": "scan_result_too_large",
                "bytes": size,
                "limit_bytes": _MAX_PLANNING_HISTORY_LOAD_BYTES,
            }
    try:
        from .scan_result_store import load_scan_result

        value = load_scan_result(
            path, keys=["v12.adaptive_planning_history_receipt"]
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise AdaptivePlanningHistoryError(
            f"planning_history_scan_result_invalid:{type(exc).__name__}"
        ) from exc
    except ValueError as exc:
        # A torn/incomplete store here is the CURRENT run's own in-progress
        # shards (written before this load) or an aborted prior run — adaptive
        # planning history is best-effort, so a cold start is the correct
        # fail-open. The strict torn-store refusal stays in `load_scan_result`
        # for forensic/analysis loads of COMPLETED runs, which call it
        # directly and must not analyze half-written data.
        import logging

        logging.getLogger(__name__).warning(
            "[planning-history] prior store unavailable (%s); cold start",
            exc,
        )
        return {}
    if not isinstance(value, dict):
        raise AdaptivePlanningHistoryError(
            "planning_history_scan_result_not_object"
        )
    runtime = _dict(value.get("v12")) or value
    receipt = runtime.get("adaptive_planning_history_receipt")
    if receipt is None:
        return {}
    if not isinstance(receipt, dict):
        raise AdaptivePlanningHistoryError(
            "planning_history_receipt_not_object"
        )
    return dict(receipt)
