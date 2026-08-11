"""Public evaluation-contract facade with truthful seeded-benchmark semantics.

The long-lived contract implementation lives in
``_discovery_evaluation_contract_mechanics``.  This facade narrows one scoring
boundary only: a seeded-defect ground truth can measure recall / benchmark
match rate, but it cannot classify formally delivered runtime defects that are
absent from the frozen GT as false positives.

Clean targets remain the separate false-positive authority.  Until a measured
precision authority exists, seeded precision/F1 and commercial policy promotion
that depends on them fail closed as ``NOT_MEASURED`` rather than fabricating a
zero or treating GT-unmatched runtime defects as errors.
"""
from __future__ import annotations

from typing import Any

from . import _discovery_evaluation_contract_mechanics as _core
from ._discovery_evaluation_contract_mechanics import *  # noqa: F401,F403

_NOT_MEASURED = "NOT_MEASURED"

_original_aggregate_evaluation_receipts = _core.aggregate_evaluation_receipts
_original_policy_metrics_from_evaluation_reports = (
    _core.policy_metrics_from_evaluation_reports
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _aggregate_seeded(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate seeded targets without inventing false-positive authority."""

    measured = [
        item
        for item in receipts
        if item.get("expectation") == "seeded_defects"
        and item.get("measurement_status") == "MEASURED"
    ]
    tp = sum(
        int(_dict(item.get("metrics")).get("true_positives") or 0)
        for item in measured
    )
    fn = sum(
        int(_dict(item.get("metrics")).get("false_negatives") or 0)
        for item in measured
    )
    evaluated = sum(
        int(_dict(item.get("metrics")).get("canonical_defects_evaluated") or 0)
        for item in measured
    )
    gt_unmatched_runtime = sum(
        int(
            _dict(item.get("metrics")).get(
                "ground_truth_unmatched_runtime_defect_count"
            )
            or 0
        )
        for item in measured
    )
    macro_recall = _core._mean(
        float(_dict(item.get("metrics")).get("recall"))
        for item in measured
        if isinstance(_dict(item.get("metrics")).get("recall"), (int, float))
    )
    return {
        "target_count": len(receipts),
        "measured_seeded_target_count": len(measured),
        "true_positives": tp,
        "false_positives": None,
        "false_positive_measurement_status": _NOT_MEASURED,
        "false_negatives": fn,
        "micro_recall": _core._ratio(tp, tp + fn),
        "micro_precision": None,
        "micro_f1": None,
        "macro_recall": macro_recall,
        "macro_precision": None,
        "precision_measurement_status": _NOT_MEASURED,
        "f1_measurement_status": _NOT_MEASURED,
        "canonical_defects_evaluated": evaluated,
        "ground_truth_unmatched_runtime_defect_count": gt_unmatched_runtime,
        "benchmark_match_rate": _core._ratio(tp, evaluated),
        "scoring_contract": "qualibug.commercial-benchmark-scoring.v2",
    }


def _seeded_precision_measured(report: dict[str, Any]) -> bool:
    for split in ("held_in", "held_out"):
        row = _dict(report.get(split))
        if int(row.get("measured_seeded_target_count") or 0) <= 0:
            continue
        if row.get("micro_precision") is None:
            return False
    return True


def aggregate_evaluation_receipts(
    manifest: Any,
    receipts: list[dict[str, Any]],
    *,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Build the signed report and fail closed for commercial promotion."""

    report = _original_aggregate_evaluation_receipts(
        manifest,
        receipts,
        receipt_signing_key=receipt_signing_key,
    )
    if _seeded_precision_measured(report):
        return report

    # The original aggregate is already signed. Any semantic amendment must be
    # resealed rather than mutating authenticated material in place.
    unsigned = {
        key: value
        for key, value in report.items()
        if key
        not in {
            _core.REPORT_FINGERPRINT_FIELD,
            _core.REPORT_AUTHENTICATION_FIELD,
        }
    }
    unsigned["commercial_promotion_evidence_ready"] = False
    unsigned["commercial_promotion_not_ready_reason"] = (
        "seeded_precision_not_measured"
    )
    unsigned["seeded_precision_measurement_status"] = _NOT_MEASURED
    try:
        return _core.seal_evaluator_artifact(
            unsigned,
            signing_key=receipt_signing_key,
            domain=_core.REPORT_SCHEMA,
            fingerprint_field=_core.REPORT_FINGERPRINT_FIELD,
            authentication_field=_core.REPORT_AUTHENTICATION_FIELD,
        )
    except _core.EvaluatorReceiptAuthError as exc:
        raise _core.EvaluationContractError(
            f"evaluation report authentication failed: {exc}"
        ) from exc


def policy_metrics_from_evaluation_reports(
    replay_report: dict[str, Any],
    shadow_report: dict[str, Any],
    *,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Never coerce unmeasured seeded precision into numeric policy evidence."""

    if not _seeded_precision_measured(replay_report) or not _seeded_precision_measured(
        shadow_report
    ):
        raise _core.EvaluationContractError(
            "seeded_precision_not_measured:policy_promotion_requires_a_valid_"
            "precision_authority"
        )
    return _original_policy_metrics_from_evaluation_reports(
        replay_report,
        shadow_report,
        receipt_signing_key=receipt_signing_key,
    )


# Core functions resolve module globals at call time. Install the governed
# aggregate into the mechanics module so internal report rebuild/authentication
# paths use exactly the same semantics as public callers.
_core._aggregate_seeded = _aggregate_seeded
_core.aggregate_evaluation_receipts = aggregate_evaluation_receipts
_core.policy_metrics_from_evaluation_reports = policy_metrics_from_evaluation_reports

__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name != "_core"
)
