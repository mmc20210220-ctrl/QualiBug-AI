"""Evaluator-only comparison for two externally scored discovery runs.

This module consumes three artifacts per run:

* the product result (for receipt-backed loss/cost/safety funnels);
* the evaluation submission (for immutable run/target/ground-truth identities);
* the external target scorer result (the only authority for TP/FP/FN).

It never reads a hidden answer key and is not imported by the product runtime. A comparison is
blocked unless the two submissions prove the same target/ground-truth identity. Metrics are
computed only from external scorer fields; product findings can never label themselves true or
false.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

COMPARISON_SCHEMA = "qualibug.externally-scored-run-comparison.v1"
SNAPSHOT_SCHEMA = "qualibug.externally-scored-run-snapshot.v1"
_SCORE_ALIASES = {
    "ground_truth_total": (
        "ground_truth_total",
        "known_bug_total",
        "total_ground_truth",
    ),
    "reported_total": (
        "reported_total",
        "finding_total",
        "submitted_total",
    ),
    "matched_total": (
        "matched_total",
        "true_positive",
        "true_positives",
        "tp",
    ),
    "missing_total": (
        "missing_total",
        "false_negative",
        "false_negatives",
        "fn",
    ),
    "estimated_false_positives": (
        "estimated_false_positives",
        "false_positive",
        "false_positives",
        "fp",
    ),
    "coverage_rate": (
        "coverage_rate",
        "recall",
    ),
    "match_strategy": (
        "match_strategy",
        "matching_strategy",
    ),
}
_IDENTITY_FIELDS = (
    "target_fingerprint",
    "target_snapshot_fingerprint",
    "ground_truth_policy_fingerprint",
    "ground_truth_fingerprint",
    "ground_truth_identity",
    "dataset_fingerprint",
    "target_id",
    "environment_id",
    "environment_ref",
    "source_snapshot_hash",
)


class ComparisonError(ValueError):
    """A supplied evaluator artifact is malformed or internally inconsistent."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    """Yield every mapping key/value recursively; lists keep no positional identity."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _first_recursive(value: Any, names: Iterable[str]) -> Any:
    wanted = {str(name) for name in names}
    for key, child in _walk(value):
        if key in wanted and child not in (None, "", [], {}):
            return child
    return None


def _all_recursive(value: Any, name: str) -> list[Any]:
    return [child for key, child in _walk(value) if key == name]


def _read_artifact(path: Path | str) -> Any:
    target = Path(path)
    text = target.read_text(encoding="utf-8").strip()
    if not text:
        raise ComparisonError(f"artifact_empty:{target}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # The benchmark scorer historically printed ``key: value`` lines. Preserve only
        # scalar keys; never attempt to parse arbitrary prose into hidden-answer content.
        parsed: dict[str, Any] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, raw = line.split(":", 1)
            normalized = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")
            if not normalized:
                continue
            value_text = raw.strip()
            if re.fullmatch(r"-?\d+", value_text):
                value: Any = int(value_text)
            elif re.fullmatch(r"-?(?:\d+\.\d+|\d+)(?:e[+-]?\d+)?", value_text, re.I):
                value = float(value_text)
            elif value_text.lower() in {"true", "false"}:
                value = value_text.lower() == "true"
            else:
                value = value_text
            parsed[normalized] = value
        if not parsed:
            raise ComparisonError(f"artifact_not_json_or_scalar_receipt:{target}")
        return parsed


def _number(value: Any, field: str, *, integer: bool = False) -> float | int:
    if isinstance(value, bool) or value is None:
        raise ComparisonError(f"score_{field}_missing_or_invalid")
    try:
        converted = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ComparisonError(f"score_{field}_not_numeric") from exc
    if converted < 0 or not math.isfinite(float(converted)):
        raise ComparisonError(f"score_{field}_negative_or_nonfinite")
    return converted


def _score_field(score: Any, field: str) -> Any:
    return _first_recursive(score, _SCORE_ALIASES[field])


def normalize_external_score(score: Any) -> dict[str, Any]:
    """Normalize the target scorer receipt and derive metrics from TP/FP/FN only."""
    ground_truth_total = int(_number(
        _score_field(score, "ground_truth_total"),
        "ground_truth_total",
        integer=True,
    ))
    reported_total = int(_number(
        _score_field(score, "reported_total"),
        "reported_total",
        integer=True,
    ))
    matched_total = int(_number(
        _score_field(score, "matched_total"),
        "matched_total",
        integer=True,
    ))
    missing_raw = _score_field(score, "missing_total")
    missing_total = (
        ground_truth_total - matched_total
        if missing_raw is None
        else int(_number(missing_raw, "missing_total", integer=True))
    )
    false_positive_raw = _score_field(score, "estimated_false_positives")
    false_positives = (
        max(0, reported_total - matched_total)
        if false_positive_raw is None
        else int(_number(
            false_positive_raw,
            "estimated_false_positives",
            integer=True,
        ))
    )
    if matched_total > ground_truth_total:
        raise ComparisonError("score_matched_total_exceeds_ground_truth")
    if missing_total != ground_truth_total - matched_total:
        raise ComparisonError("score_missing_total_identity_mismatch")
    if matched_total > reported_total:
        raise ComparisonError("score_matched_total_exceeds_reported")
    if false_positives < reported_total - matched_total:
        raise ComparisonError("score_false_positive_count_below_unmatched_reports")

    precision = (
        matched_total / (matched_total + false_positives)
        if matched_total + false_positives
        else 0.0
    )
    recall = matched_total / ground_truth_total if ground_truth_total else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    supplied_coverage = _score_field(score, "coverage_rate")
    if supplied_coverage is not None:
        supplied = float(_number(supplied_coverage, "coverage_rate"))
        # Permit percentage-form scorer output but normalize it to [0,1].
        if supplied > 1 and supplied <= 100:
            supplied /= 100.0
        if supplied > 1 or not math.isclose(supplied, recall, abs_tol=1e-6):
            raise ComparisonError("score_coverage_rate_disagrees_with_tp_fn")

    match_strategy = _text(_score_field(score, "match_strategy"))
    identity = {
        field: _text(_first_recursive(score, (field,)))
        for field in _IDENTITY_FIELDS
        if _text(_first_recursive(score, (field,)))
    }
    return {
        "ground_truth_total": ground_truth_total,
        "reported_total": reported_total,
        "true_positives": matched_total,
        "false_positives": false_positives,
        "false_negatives": missing_total,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "match_strategy": match_strategy,
        "score_identity": identity,
        "metric_authority": "external_target_scorer",
        "raw_score_fingerprint": _fingerprint(score),
    }


def _submission_identity(submission: Any) -> dict[str, str]:
    identity: dict[str, str] = {}
    for field in _IDENTITY_FIELDS:
        values = [_text(value) for value in _all_recursive(submission, field) if _text(value)]
        unique = list(dict.fromkeys(values))
        if len(unique) > 1:
            # Some artifacts can contain both requested and approved environment ids under
            # the same generic name. Conflicting immutable fingerprints are never accepted.
            if "fingerprint" in field or field in {"source_snapshot_hash", "target_id"}:
                raise ComparisonError(f"submission_{field}_conflicting")
        if unique:
            identity[field] = unique[0]
    for field in (
        "run_id",
        "campaign_id",
        "policy_id",
        "policy_version",
        "evaluation_mode",
        "measurement_status",
    ):
        value = _text(_first_recursive(submission, (field,)))
        if value:
            identity[field] = value
    return identity


def _terminal_counts(result: Any) -> dict[str, int]:
    funnel = _dict(_first_recursive(result, ("discovery_loss_funnel",)))
    values = _dict(funnel.get("terminal_reason_counts"))
    if not values:
        values = _dict(_first_recursive(result, ("terminal_reason_counts",)))
    output: dict[str, int] = {}
    for key, value in values.items():
        try:
            output[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return dict(sorted(output.items()))


def _family_counts(result: Any) -> dict[str, int]:
    candidates = (
        _first_recursive(result, ("by_family",)),
        _first_recursive(result, ("risk_family_counts",)),
        _first_recursive(result, ("obligation_family_counts",)),
    )
    for candidate in candidates:
        if isinstance(candidate, dict):
            output: dict[str, int] = {}
            for key, value in candidate.items():
                try:
                    output[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
            if output:
                return dict(sorted(output.items()))
    return {}


def _observer_counts(result: Any) -> dict[str, int]:
    funnel = _dict(_first_recursive(result, ("discovery_loss_funnel",)))
    for field in ("observer_status_counts", "observer_reason_counts"):
        value = _dict(funnel.get(field))
        if value:
            return {
                str(key): int(raw)
                for key, raw in sorted(value.items())
                if isinstance(raw, (int, float)) and not isinstance(raw, bool)
            }
    return {}


def _operational_metrics(result: Any) -> dict[str, Any]:
    fields = (
        "selected_count",
        "scheduled_count",
        "executed_count",
        "blocked_count",
        "harness_failure_count",
        "cleanup_failures",
        "observed_http_request_count",
        "production_http_requests",
        "accepted_write_count",
        "canonical_defect_count",
        "evidence_graph_count",
    )
    output: dict[str, Any] = {}
    for field in fields:
        value = _first_recursive(result, (field,))
        if isinstance(value, bool) or value is None:
            continue
        try:
            output[field] = int(value)
        except (TypeError, ValueError):
            continue
    return output


def build_scored_run_snapshot(
    *,
    label: str,
    product_result: Any,
    evaluation_submission: Any,
    external_score: Any,
) -> dict[str, Any]:
    score = normalize_external_score(external_score)
    identity = _submission_identity(evaluation_submission)
    for field, value in _dict(score.get("score_identity")).items():
        existing = _text(identity.get(field))
        if existing and existing != _text(value):
            raise ComparisonError(f"score_submission_{field}_mismatch")
        identity.setdefault(field, _text(value))
    measurement_status = _text(identity.get("measurement_status")).upper()
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        "label": _text(label) or "run",
        "identity": identity,
        "measurement_status_before_external_score": measurement_status or "UNKNOWN",
        "external_score": score,
        "loss_funnel": {
            "terminal_reason_counts": _terminal_counts(product_result),
            "obligation_family_counts": _family_counts(product_result),
            "observer_counts": _observer_counts(product_result),
        },
        "operational_metrics": _operational_metrics(product_result),
        "safety": {
            "production_http_requests": int(
                _operational_metrics(product_result).get("production_http_requests", 0)
            ),
            "cleanup_failures": int(
                _operational_metrics(product_result).get("cleanup_failures", 0)
            ),
        },
        "quality_metric_authority": "external_target_scorer_only",
    }
    snapshot["snapshot_fingerprint"] = _fingerprint(snapshot)
    return snapshot


def _shared_identity(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, str], list[str]]:
    left = _dict(baseline.get("identity"))
    right = _dict(candidate.get("identity"))
    shared: dict[str, str] = {}
    mismatches: list[str] = []
    for field in _IDENTITY_FIELDS:
        left_value = _text(left.get(field))
        right_value = _text(right.get(field))
        if left_value and right_value:
            if left_value == right_value:
                shared[field] = left_value
            else:
                mismatches.append(field)
    return shared, mismatches


def _count_delta(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    keys = sorted(set(left) | set(right))
    return {key: int(right.get(key, 0)) - int(left.get(key, 0)) for key in keys}


def _numeric_delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(set(left) | set(right))
    output: dict[str, Any] = {}
    for key in keys:
        before = left.get(key)
        after = right.get(key)
        if isinstance(before, bool) or isinstance(after, bool):
            continue
        if isinstance(before, (int, float)) or isinstance(after, (int, float)):
            output[key] = (after or 0) - (before or 0)
    return output


def compare_scored_runs(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Compare two snapshots, fail-closed on target/ground-truth identity."""
    if baseline.get("schema_version") != SNAPSHOT_SCHEMA:
        raise ComparisonError("baseline_snapshot_schema_invalid")
    if candidate.get("schema_version") != SNAPSHOT_SCHEMA:
        raise ComparisonError("candidate_snapshot_schema_invalid")
    shared, mismatches = _shared_identity(baseline, candidate)
    gt_left = int(_dict(baseline.get("external_score")).get("ground_truth_total") or 0)
    gt_right = int(_dict(candidate.get("external_score")).get("ground_truth_total") or 0)
    if gt_left != gt_right:
        mismatches.append("ground_truth_total")

    strong_identity_fields = {
        field
        for field in shared
        if "fingerprint" in field or field in {"ground_truth_identity", "dataset_fingerprint"}
    }
    status = "COMPARABLE"
    reason_code = ""
    if mismatches:
        status = "BLOCKED_IDENTITY_MISMATCH"
        reason_code = "EXTERNAL_SCORE_TARGET_IDENTITY_MISMATCH"
    elif not strong_identity_fields:
        status = "BLOCKED_IDENTITY_UNPROVEN"
        reason_code = "EXTERNAL_SCORE_GROUND_TRUTH_FINGERPRINT_MISSING"

    left_score = _dict(baseline.get("external_score"))
    right_score = _dict(candidate.get("external_score"))
    comparison: dict[str, Any] = {
        "schema_version": COMPARISON_SCHEMA,
        "status": status,
        "reason_code": reason_code,
        "baseline_label": _text(baseline.get("label")),
        "candidate_label": _text(candidate.get("label")),
        "shared_identity": shared,
        "identity_mismatches": sorted(set(mismatches)),
        "quality_metric_authority": "external_target_scorer_only",
        "hidden_answer_key_consumed_by_comparator": False,
        "baseline_snapshot_fingerprint": _text(baseline.get("snapshot_fingerprint")),
        "candidate_snapshot_fingerprint": _text(candidate.get("snapshot_fingerprint")),
    }
    if status != "COMPARABLE":
        comparison["quality_delta"] = None
        comparison["loss_funnel_delta"] = None
        comparison["operational_delta"] = None
        comparison["comparison_fingerprint"] = _fingerprint(comparison)
        return comparison

    quality_fields = (
        "reported_total",
        "true_positives",
        "false_positives",
        "false_negatives",
        "precision",
        "recall",
        "f1",
    )
    comparison["quality_delta"] = {
        field: right_score[field] - left_score[field]
        for field in quality_fields
    }
    left_funnel = _dict(baseline.get("loss_funnel"))
    right_funnel = _dict(candidate.get("loss_funnel"))
    comparison["loss_funnel_delta"] = {
        "terminal_reason_counts": _count_delta(
            _dict(left_funnel.get("terminal_reason_counts")),
            _dict(right_funnel.get("terminal_reason_counts")),
        ),
        "obligation_family_counts": _count_delta(
            _dict(left_funnel.get("obligation_family_counts")),
            _dict(right_funnel.get("obligation_family_counts")),
        ),
        "observer_counts": _count_delta(
            _dict(left_funnel.get("observer_counts")),
            _dict(right_funnel.get("observer_counts")),
        ),
    }
    comparison["operational_delta"] = _numeric_delta(
        _dict(baseline.get("operational_metrics")),
        _dict(candidate.get("operational_metrics")),
    )
    comparison["safety_regression"] = bool(
        int(_dict(candidate.get("safety")).get("production_http_requests") or 0) > 0
        or int(_dict(candidate.get("safety")).get("cleanup_failures") or 0)
        > int(_dict(baseline.get("safety")).get("cleanup_failures") or 0)
    )
    comparison["improvement_summary"] = {
        "new_true_positives": comparison["quality_delta"]["true_positives"],
        "false_positive_change": comparison["quality_delta"]["false_positives"],
        "false_negative_change": comparison["quality_delta"]["false_negatives"],
        "recall_change": comparison["quality_delta"]["recall"],
        "precision_change": comparison["quality_delta"]["precision"],
        "f1_change": comparison["quality_delta"]["f1"],
    }
    comparison["comparison_fingerprint"] = _fingerprint(comparison)
    return comparison


def render_markdown(comparison: dict[str, Any]) -> str:
    status = _text(comparison.get("status"))
    lines = [
        "# Externally Scored Funnel Comparison",
        "",
        f"- Status: `{status}`",
        f"- Baseline: `{_text(comparison.get('baseline_label'))}`",
        f"- Candidate: `{_text(comparison.get('candidate_label'))}`",
        "- Quality authority: external target scorer only",
        "- Hidden answer key consumed by comparator: false",
    ]
    reason = _text(comparison.get("reason_code"))
    if reason:
        lines.append(f"- Reason: `{reason}`")
    if status != "COMPARABLE":
        mismatches = ", ".join(_list(comparison.get("identity_mismatches"))) or "none"
        lines.extend(["", f"Identity mismatches: {mismatches}"])
        return "\n".join(lines) + "\n"

    summary = _dict(comparison.get("improvement_summary"))
    lines.extend([
        "",
        "## Quality delta",
        "",
        "| Metric | Candidate - Baseline |",
        "|---|---:|",
    ])
    for field in (
        "new_true_positives",
        "false_positive_change",
        "false_negative_change",
        "recall_change",
        "precision_change",
        "f1_change",
    ):
        value = summary.get(field, 0)
        rendered = f"{value:.6f}" if isinstance(value, float) else str(value)
        lines.append(f"| {field} | {rendered} |")
    lines.extend([
        "",
        f"Safety regression: `{bool(comparison.get('safety_regression'))}`",
        "",
        "## Top blocker changes",
        "",
        "| Reason | Delta |",
        "|---|---:|",
    ])
    reasons = _dict(_dict(comparison.get("loss_funnel_delta")).get("terminal_reason_counts"))
    for reason_code, delta in sorted(
        reasons.items(),
        key=lambda item: (-abs(int(item[1])), item[0]),
    )[:15]:
        lines.append(f"| `{reason_code}` | {int(delta):+d} |")
    return "\n".join(lines) + "\n"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two externally scored QualiBug funnel runs.",
    )
    for prefix in ("baseline", "candidate"):
        parser.add_argument(f"--{prefix}-result", required=True)
        parser.add_argument(f"--{prefix}-submission", required=True)
        parser.add_argument(f"--{prefix}-score", required=True)
        parser.add_argument(f"--{prefix}-label", default=prefix)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-markdown", default="")
    args = parser.parse_args(argv)

    baseline = build_scored_run_snapshot(
        label=args.baseline_label,
        product_result=_read_artifact(args.baseline_result),
        evaluation_submission=_read_artifact(args.baseline_submission),
        external_score=_read_artifact(args.baseline_score),
    )
    candidate = build_scored_run_snapshot(
        label=args.candidate_label,
        product_result=_read_artifact(args.candidate_result),
        evaluation_submission=_read_artifact(args.candidate_submission),
        external_score=_read_artifact(args.candidate_score),
    )
    comparison = compare_scored_runs(baseline, candidate)
    _write_json(Path(args.output_json), comparison)
    if args.output_markdown:
        target = Path(args.output_markdown)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(comparison), encoding="utf-8")
    print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if comparison["status"] == "COMPARABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
