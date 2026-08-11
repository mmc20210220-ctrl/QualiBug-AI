"""Commercial benchmark scoring semantics.

Hidden ground truth measures *benchmark coverage*.  It cannot prove that a
customer-deliverable runtime defect absent from the frozen GT is a false
positive.  This module keeps the existing evaluator's one-to-one matching
algorithm, then corrects only the public metric semantics:

- GT match -> benchmark true positive / recall evidence;
- GT miss -> benchmark false negative;
- runtime defect not represented in GT -> retained real defect, not FP;
- precision / FPR / F1 -> NOT_MEASURED because no true-negative / false-positive
  authority exists in a seeded-defect-only dataset;
- TP / delivered-runtime-defects is exposed as ``benchmark_match_rate`` rather
  than being mislabeled precision.

The installer is intentionally evaluator-local.  Discovery code never imports
GT and never learns benchmark vocabulary.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

SCHEMA_VERSION = "qualibug.commercial-benchmark-scoring.v2"
NOT_MEASURED = "NOT_MEASURED"


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def apply_commercial_scoring_contract(
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Correct seeded-benchmark metric semantics without changing matching."""

    if not isinstance(metrics, dict) or metrics.get("benchmark_active") is not True:
        return metrics

    result = dict(metrics)
    true_positives = _int(result.get("true_positives"))
    false_negatives = _int(result.get("false_negatives"))
    evaluated = _int(
        result.get("canonical_defects_evaluated")
        or result.get("scan_findings_total")
    )
    unmatched_runtime = len(
        [
            value
            for value in (result.get("canonical_unmatched") or [])
            if str(value or "").strip()
        ]
    )

    # Preserve the useful count under its truthful name.  These rows are real,
    # formally delivered runtime defects whose only known property is that the
    # frozen benchmark does not contain a matching GT entry.
    result["ground_truth_unmatched_runtime_defect_count"] = unmatched_runtime
    result["benchmark_match_rate"] = (
        round(true_positives / evaluated, 4) if evaluated else 0.0
    )

    # A seeded-defect benchmark contains positive labels, not a complete set of
    # all defects and clean surfaces.  It therefore has no authority to classify
    # an unmatched runtime defect as false positive or to compute precision/FPR.
    result["false_positives"] = None
    result["false_positive_measurement_status"] = NOT_MEASURED
    result["precision"] = NOT_MEASURED
    result["precision_measurement_status"] = NOT_MEASURED
    result["false_positive_rate"] = NOT_MEASURED
    result["f1_score"] = NOT_MEASURED

    # Five explicit states make downstream reporting mechanically honest.
    result["benchmark_scoring_states"] = {
        "GT_MATCHED_RUNTIME_DEFECT": true_positives,
        "GT_MISSED_DEFECT": false_negatives,
        "GT_UNMATCHED_RUNTIME_DEFECT": unmatched_runtime,
        "FALSE_POSITIVE": NOT_MEASURED,
        "TRUE_NEGATIVE": NOT_MEASURED,
    }
    result["scoring_contract"] = SCHEMA_VERSION
    result["scoring_note"] = (
        "GT-unmatched customer-deliverable defects are retained as runtime "
        "defects outside the frozen benchmark; they are not false positives."
    )
    return result


def install_benchmark_compute_contract() -> None:
    """Install the semantic projection once around benchmark_compute."""

    from . import benchmark_compute as module

    current: Callable[..., dict[str, Any]] = module.compute_benchmark
    if getattr(current, "_qualibug_commercial_scoring_contract", False):
        return

    @wraps(current)
    def governed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return apply_commercial_scoring_contract(current(*args, **kwargs))

    governed._qualibug_commercial_scoring_contract = True  # type: ignore[attr-defined]
    governed._qualibug_original_compute_benchmark = current  # type: ignore[attr-defined]
    module.compute_benchmark = governed


__all__ = [
    "NOT_MEASURED",
    "SCHEMA_VERSION",
    "apply_commercial_scoring_contract",
    "install_benchmark_compute_contract",
]
