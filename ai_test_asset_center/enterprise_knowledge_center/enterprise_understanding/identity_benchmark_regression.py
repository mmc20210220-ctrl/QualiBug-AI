"""Versioned regression authority for enterprise identity measurement.

Only snapshots produced from the same blind annotation manifest and the same external
Ground Truth fingerprint are comparable. A changed annotation universe is never called
an algorithm regression. All error rows retain exact source-occurrence evidence; this
module never infers a cause from label similarity.
"""
from __future__ import annotations

from typing import Any

from .schema import as_dict, as_list, stable_id, text

HISTORY_SCHEMA = "qualibug.enterprise-identity-benchmark-history.v1"
SNAPSHOT_SCHEMA = "qualibug.enterprise-identity-benchmark-snapshot.v1"
REGRESSION_GATE_SCHEMA = "qualibug.enterprise-identity-regression-gate.v1"
ERROR_QUEUE_SCHEMA = "qualibug.enterprise-identity-error-queue.v1"

_RATE_REGRESSION_DEFINITIONS = (
    ("maximum_pairwise_precision_drop", "pairwise_precision", "DROP"),
    ("maximum_pairwise_recall_drop", "pairwise_recall", "DROP"),
    ("maximum_pairwise_f1_drop", "pairwise_f1", "DROP"),
    ("maximum_exact_cluster_match_rate_drop", "exact_cluster_match_rate", "DROP"),
    ("maximum_overmerge_rate_increase", "overmerge_rate", "INCREASE"),
    ("maximum_undermerge_rate_increase", "undermerge_rate", "INCREASE"),
    (
        "maximum_identity_error_unknown_coverage_drop",
        "identity_error_unknown_coverage_rate",
        "DROP",
    ),
)
_COUNT_REGRESSION_DEFINITIONS = (
    (
        "maximum_silent_identity_error_increase",
        "silent_identity_error_count",
        "INCREASE",
    ),
)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _manifest_id(asset: dict[str, Any]) -> str:
    return text(
        as_dict(asset.get("enterprise_identity_annotation_manifest")).get(
            "manifest_id"
        )
    )


def _ground_truth_fingerprint(asset: dict[str, Any]) -> str:
    receipt = as_dict(asset.get("enterprise_identity_benchmark_repository_receipt"))
    return text(receipt.get("ground_truth_fingerprint"))


def _quality_policy_fingerprint(asset: dict[str, Any]) -> str:
    receipt = as_dict(asset.get("enterprise_identity_benchmark_repository_receipt"))
    return text(receipt.get("quality_policy_fingerprint"))


def _pair_ref(row: dict[str, Any], side: str) -> str:
    return text(as_dict(row.get(side)).get("mention_ref"))


def identity_error_id(row: dict[str, Any]) -> str:
    left = _pair_ref(row, "left")
    right = _pair_ref(row, "right")
    return stable_id(
        "enterprise_identity_error",
        text(row.get("error_type")),
        sorted([left, right]),
    )


def _error_rows(benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field in ("false_positive_pairs", "false_negative_pairs"):
        for raw in as_list(benchmark.get(field)):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            error_id = identity_error_id(row)
            if not error_id or not _pair_ref(row, "left") or not _pair_ref(row, "right"):
                continue
            row["error_id"] = error_id
            row["source_evidence_only"] = True
            row["name_similarity_root_cause_inferred"] = False
            rows.append(row)
    return sorted(rows, key=lambda row: text(row.get("error_id")))


def _history_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    history = as_dict(asset.get("enterprise_identity_benchmark_history"))
    return [
        dict(row)
        for row in as_list(history.get("snapshots"))
        if isinstance(row, dict)
        and text(row.get("schema")) == SNAPSHOT_SCHEMA
    ]


def latest_comparable_snapshot(
    asset: dict[str, Any],
) -> dict[str, Any]:
    manifest_id = _manifest_id(asset)
    truth_fingerprint = _ground_truth_fingerprint(asset)
    if not manifest_id or not truth_fingerprint:
        return {}
    comparable = [
        row
        for row in _history_rows(asset)
        if text(row.get("measurement_status")) == "MEASURED"
        and text(row.get("manifest_id")) == manifest_id
        and text(row.get("ground_truth_fingerprint")) == truth_fingerprint
    ]
    return dict(comparable[-1]) if comparable else {}


def _regression_delta(
    *,
    current: float,
    baseline: float,
    direction: str,
) -> float:
    if direction == "DROP":
        return max(0.0, baseline - current)
    return max(0.0, current - baseline)


def evaluate_identity_benchmark_regression(
    asset: dict[str, Any],
    benchmark: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    enforce = bool(policy.get("enforce_regression"))
    if text(benchmark.get("status")) != "MEASURED":
        return {
            "schema": REGRESSION_GATE_SCHEMA,
            "status": "NOT_MEASURED",
            "entry_allowed": not enforce,
            "enforced": enforce,
            "comparable": False,
            "blocking_reasons": (
                ["IDENTITY_REGRESSION_CURRENT_BENCHMARK_NOT_MEASURED"]
                if enforce
                else []
            ),
        }

    baseline = latest_comparable_snapshot(asset)
    if not baseline:
        return {
            "schema": REGRESSION_GATE_SCHEMA,
            "status": "NOT_COMPARABLE",
            "reason_code": "IDENTITY_REGRESSION_COMPARABLE_BASELINE_MISSING",
            "entry_allowed": True,
            "enforced": enforce,
            "comparable": False,
            "manifest_id": _manifest_id(asset),
            "ground_truth_fingerprint": _ground_truth_fingerprint(asset),
            "blocking_reasons": [],
            "annotation_change_is_not_regression": True,
        }

    current_metrics = as_dict(benchmark.get("metrics"))
    baseline_metrics = as_dict(baseline.get("metrics"))
    deltas: dict[str, float] = {}
    for _threshold, metric, _direction in (
        *_RATE_REGRESSION_DEFINITIONS,
        *_COUNT_REGRESSION_DEFINITIONS,
    ):
        current = _number(current_metrics.get(metric))
        prior = _number(baseline_metrics.get(metric))
        if current is not None and prior is not None:
            deltas[metric] = round(current - prior, 6)

    thresholds = as_dict(policy.get("regression_thresholds"))
    if not thresholds:
        return {
            "schema": REGRESSION_GATE_SCHEMA,
            "status": "NOT_CONFIGURED",
            "entry_allowed": True,
            "enforced": enforce,
            "comparable": True,
            "baseline_snapshot_id": baseline.get("snapshot_id"),
            "metric_deltas": deltas,
            "blocking_reasons": [],
        }

    checks: list[dict[str, Any]] = []
    invalid: list[str] = []
    for threshold_key, metric, direction in (
        *_RATE_REGRESSION_DEFINITIONS,
        *_COUNT_REGRESSION_DEFINITIONS,
    ):
        if threshold_key not in thresholds:
            continue
        threshold = _number(thresholds.get(threshold_key))
        current = _number(current_metrics.get(metric))
        prior = _number(baseline_metrics.get(metric))
        is_count = threshold_key.startswith("maximum_silent_identity_error")
        if (
            threshold is None
            or current is None
            or prior is None
            or threshold < 0
            or (not is_count and threshold > 1.0)
        ):
            invalid.append(threshold_key)
            continue
        regression = _regression_delta(
            current=current,
            baseline=prior,
            direction=direction,
        )
        checks.append(
            {
                "threshold": threshold_key,
                "metric": metric,
                "direction": direction,
                "maximum_allowed_regression": threshold,
                "baseline": prior,
                "current": current,
                "observed_regression": round(regression, 6),
                "passed": regression <= threshold,
            }
        )

    if invalid or not checks:
        return {
            "schema": REGRESSION_GATE_SCHEMA,
            "status": "INVALID_IDENTITY_REGRESSION_POLICY",
            "entry_allowed": not enforce,
            "enforced": enforce,
            "comparable": True,
            "baseline_snapshot_id": baseline.get("snapshot_id"),
            "metric_deltas": deltas,
            "invalid_thresholds": sorted(invalid),
            "blocking_reasons": (
                ["IDENTITY_REGRESSION_THRESHOLDS_MISSING"]
                if not checks and not invalid
                else ["IDENTITY_REGRESSION_THRESHOLD_INVALID"]
            ),
            "checks": checks,
        }

    failed = [row for row in checks if not bool(row.get("passed"))]
    return {
        "schema": REGRESSION_GATE_SCHEMA,
        "status": "PASS" if not failed else "BLOCKED_IDENTITY_REGRESSION",
        "entry_allowed": not enforce or not failed,
        "enforced": enforce,
        "comparable": True,
        "baseline_snapshot_id": baseline.get("snapshot_id"),
        "manifest_id": _manifest_id(asset),
        "ground_truth_fingerprint": _ground_truth_fingerprint(asset),
        "metric_deltas": deltas,
        "checks": checks,
        "failed_check_count": len(failed),
        "passed_check_count": len(checks) - len(failed),
        "blocking_reasons": [
            f"{row['metric']} regression {row['observed_regression']} > "
            f"{row['maximum_allowed_regression']}"
            for row in failed
        ],
    }


def combine_identity_quality_gates(
    absolute_gate: dict[str, Any],
    regression_gate: dict[str, Any],
) -> dict[str, Any]:
    absolute = dict(as_dict(absolute_gate))
    regression = dict(as_dict(regression_gate))
    absolute_blocked = bool(absolute.get("enforced")) and not bool(
        absolute.get("entry_allowed", True)
    )
    regression_blocked = bool(regression.get("enforced")) and not bool(
        regression.get("entry_allowed", True)
    )
    blocking_reasons = [
        *[text(value) for value in as_list(absolute.get("blocking_reasons")) if text(value)],
        *[text(value) for value in as_list(regression.get("blocking_reasons")) if text(value)],
    ]
    if absolute_blocked:
        status = text(absolute.get("status")) or "BLOCKED_IDENTITY_QUALITY_THRESHOLD"
    elif regression_blocked:
        status = "BLOCKED_IDENTITY_REGRESSION"
    elif text(absolute.get("status")) == "PASS" and text(regression.get("status")) in {
        "PASS",
        "NOT_CONFIGURED",
        "NOT_COMPARABLE",
    }:
        status = "PASS"
    else:
        status = text(absolute.get("status")) or text(regression.get("status")) or "NOT_CONFIGURED"
    return {
        **absolute,
        "status": status,
        "entry_allowed": not absolute_blocked and not regression_blocked,
        "enforced": bool(absolute.get("enforced")) or bool(regression.get("enforced")),
        "blocking_reasons": blocking_reasons,
        "absolute_gate": absolute,
        "regression_gate": regression,
        "combined_static_and_regression_authority": True,
    }


def project_identity_benchmark_regression(
    asset: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    benchmark = dict(as_dict(result.get("benchmark")))
    policy = as_dict(asset.get("enterprise_identity_quality_policy"))
    regression = evaluate_identity_benchmark_regression(asset, benchmark, policy)
    combined = combine_identity_quality_gates(
        as_dict(benchmark.get("quality_gate")),
        regression,
    )
    benchmark["regression"] = regression
    benchmark["quality_gate"] = combined
    result["benchmark"] = benchmark
    asset["enterprise_identity_benchmark"] = benchmark

    gate = dict(as_dict(result.get("gate")))
    gate["quality_gate"] = combined
    gate["identity_regression_gate"] = regression
    if bool(combined.get("enforced")) and not bool(combined.get("entry_allowed", True)):
        gate.update(
            {
                "status": "BLOCKED_ENTERPRISE_IDENTITY_QUALITY_GATE",
                "entry_allowed": False,
                "business_understanding_allowed": False,
                "required_operator_action": (
                    "restore identity quality or resolve exact occurrence-level errors "
                    "before formal business understanding"
                ),
            }
        )
    result["gate"] = gate
    asset["enterprise_identity_gate"] = gate
    asset["enterprise_identity_resolution"] = result

    summary = dict(as_dict(asset.get("summary")))
    summary["enterprise_identity_regression_gate_status"] = regression.get("status")
    summary["enterprise_identity_regression_comparable"] = bool(
        regression.get("comparable")
    )
    asset["summary"] = summary
    return result


def build_identity_error_queue(
    benchmark: dict[str, Any],
    baseline_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = _error_rows(benchmark)
    baseline = [
        dict(row)
        for row in as_list(as_dict(baseline_snapshot).get("errors"))
        if isinstance(row, dict) and text(row.get("error_id"))
    ]
    baseline_by_id = {text(row.get("error_id")): row for row in baseline}
    current_by_id = {text(row.get("error_id")): row for row in current}
    active = [
        {
            **row,
            "lifecycle_status": (
                "PERSISTING" if text(row.get("error_id")) in baseline_by_id else "NEW"
            ),
        }
        for row in current
    ]
    resolved = [
        {**row, "lifecycle_status": "RESOLVED"}
        for error_id, row in baseline_by_id.items()
        if error_id not in current_by_id
    ]
    return {
        "schema": ERROR_QUEUE_SCHEMA,
        "status": "READY" if text(benchmark.get("status")) == "MEASURED" else "NOT_MEASURED",
        "active_errors": active,
        "resolved_errors": resolved,
        "active_error_count": len(active),
        "new_error_count": sum(1 for row in active if row["lifecycle_status"] == "NEW"),
        "persisting_error_count": sum(
            1 for row in active if row["lifecycle_status"] == "PERSISTING"
        ),
        "resolved_error_count": len(resolved),
        "source_occurrence_evidence_only": True,
        "fuzzy_or_llm_root_cause_inference_used": False,
    }


def build_identity_benchmark_snapshot(
    asset: dict[str, Any],
    *,
    trigger: str,
    actor: dict[str, Any],
    recorded_at_utc: str,
) -> dict[str, Any]:
    benchmark = as_dict(asset.get("enterprise_identity_benchmark"))
    if text(benchmark.get("status")) != "MEASURED":
        return {}
    manifest_id = _manifest_id(asset)
    truth_fingerprint = _ground_truth_fingerprint(asset)
    if not manifest_id or not truth_fingerprint:
        return {}
    errors = _error_rows(benchmark)
    result_fingerprint = stable_id(
        "enterprise_identity_benchmark_result",
        benchmark.get("benchmark_id"),
        as_dict(benchmark.get("metrics")),
        [row.get("error_id") for row in errors],
    )
    return {
        "schema": SNAPSHOT_SCHEMA,
        "snapshot_id": stable_id(
            "enterprise_identity_benchmark_snapshot",
            recorded_at_utc,
            trigger,
            result_fingerprint,
        ),
        "recorded_at_utc": recorded_at_utc,
        "trigger": text(trigger) or "MANUAL_REMEASURE",
        "actor": {
            "name": text(actor.get("name") or actor.get("username")),
            "role": text(actor.get("role")),
            "tenant_id": text(actor.get("tenant_id") or actor.get("tenant")),
        },
        "manifest_id": manifest_id,
        "ground_truth_fingerprint": truth_fingerprint,
        "quality_policy_fingerprint": _quality_policy_fingerprint(asset),
        "benchmark_id": benchmark.get("benchmark_id"),
        "measurement_status": benchmark.get("status"),
        "result_fingerprint": result_fingerprint,
        "metrics": dict(as_dict(benchmark.get("metrics"))),
        "quality_gate": dict(as_dict(benchmark.get("quality_gate"))),
        "regression": dict(as_dict(benchmark.get("regression"))),
        "errors": errors,
        "error_count": len(errors),
        "external_ground_truth_only": True,
        "annotation_universe_frozen_by_manifest": True,
    }


__all__ = [
    "ERROR_QUEUE_SCHEMA",
    "HISTORY_SCHEMA",
    "REGRESSION_GATE_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "build_identity_benchmark_snapshot",
    "build_identity_error_queue",
    "combine_identity_quality_gates",
    "evaluate_identity_benchmark_regression",
    "identity_error_id",
    "latest_comparable_snapshot",
    "project_identity_benchmark_regression",
]
