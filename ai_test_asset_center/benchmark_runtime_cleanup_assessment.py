from __future__ import annotations

"""Receipt-backed benchmark cleanup assessment before any target reset.

A full database reset can make a target clean, but it cannot prove that QualiBug's
own per-experiment cleanup ran. This module reads the completed product scan and
separates those two facts:

* runtime cleanup execution is derived only from exact adapter binding audits,
  adapter cleanup receipts, and environment-restoration evidence;
* physical database residue remains NOT_MEASURED unless an independent database
  snapshot receipt is supplied by an evaluator.

The assessment is intentionally target-agnostic. It never reads hidden ground
truth and never queries project-specific tables.
"""

import hashlib
import json
from pathlib import Path
from typing import Any


ASSESSMENT_SCHEMA = "qualibug.benchmark-runtime-cleanup-assessment.v1"
BINDING_SCHEMA = "qualibug.declared-adapter-cleanup-runtime-binding.v1"

VERDICT_NOT_MEASURED = "NOT_MEASURED"
VERDICT_NOT_EXERCISED = "NOT_EXERCISED"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_CLEAN = "CLEAN"

MEASUREMENT_MEASURED = "MEASURED"
MEASUREMENT_NOT_MEASURED = "NOT_MEASURED"


class BenchmarkCleanupAssessmentError(RuntimeError):
    """The stored product result cannot be interpreted safely."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _text(
            row.get("experiment_id")
            or row.get("execution_id")
            or row.get("obligation_id")
        )
        key = identity or _stable_hash(row)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _result_roots(document: dict[str, Any]) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    for value in (
        document,
        document.get("full_result"),
        document.get("result"),
        document.get("scan_result"),
    ):
        if isinstance(value, dict) and value not in roots:
            roots.append(value)
    return roots


def _experiment_results(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in _result_roots(document):
        v12 = _dict(root.get("v12"))
        for owner in (root, v12):
            execution = _dict(owner.get("experiment_execution"))
            rows.extend(
                dict(row)
                for row in _list(execution.get("results"))
                if isinstance(row, dict)
            )
            phases = _dict(owner.get("phases"))
            phase_execution = _dict(phases.get("execution"))
            rows.extend(
                dict(row)
                for row in _list(phase_execution.get("results"))
                if isinstance(row, dict)
            )
            rows.extend(
                dict(row)
                for row in _list(owner.get("experiment_results"))
                if isinstance(row, dict)
            )
    return _dedupe_rows(rows)


def _runtime_row(raw: dict[str, Any]) -> dict[str, Any]:
    nested = raw.get("result")
    if isinstance(nested, dict) and (
        isinstance(nested.get("observations"), dict)
        or nested.get("experiment_id")
        or nested.get("execution_id")
    ):
        return nested
    return raw


def _observations(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("observations")
    return value if isinstance(value, dict) else {}


def _binding_required(binding: dict[str, Any]) -> bool:
    return bool(
        binding.get("required") is True
        or _safe_int(binding.get("declared_operation_count")) > 0
        or _list(binding.get("declared_operation_refs"))
    )


def _binding_counts(binding: dict[str, Any]) -> tuple[int, int]:
    bound = _safe_int(binding.get("bound_count"))
    if not bound:
        bound = len([row for row in _list(binding.get("bound")) if isinstance(row, dict)])
    unbound = _safe_int(binding.get("unbound_count"))
    if not unbound:
        unbound = len(
            [row for row in _list(binding.get("unbound")) if isinstance(row, dict)]
        )
    return bound, unbound


def _receipt_rows(observations: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = observations.get(key)
    if isinstance(value, dict):
        return [value]
    return [dict(row) for row in _list(value) if isinstance(row, dict)]


def _execution_counters_valid(receipt: dict[str, Any]) -> bool:
    fields = [
        key
        for key in ("rows_deleted", "rows_updated")
        if key in receipt
    ]
    if not fields:
        return False
    for field in fields:
        value = receipt.get(field)
        if isinstance(value, bool):
            return False
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return False
        if parsed < 0:
            return False
    return True


def _valid_cleaned_adapter_receipt(receipt: dict[str, Any]) -> bool:
    mode = _text(receipt.get("mode"))
    return bool(
        _text(receipt.get("receipt_id"))
        and _text(receipt.get("adapter")) == "db_sql"
        and _text(receipt.get("status")).upper() == "CLEANED"
        and _text(receipt.get("table"))
        and _text(receipt.get("identity_column"))
        and _text(receipt.get("ownership_basis"))
        and mode in {"row_delete", "adapter_row_delete", "field_restore"}
        and _execution_counters_valid(receipt)
    )


def _environment_restoration_proven(
    row: dict[str, Any],
    observations: dict[str, Any],
) -> bool:
    if row.get("environment_restored") is True:
        return True
    if observations.get("environment_restored") is True:
        return True
    receipt = _dict(
        observations.get("environment_restoration_receipt")
        or row.get("environment_restoration_receipt")
    )
    payload = _dict(receipt.get("payload"))
    if (
        receipt.get("environment_restored") is True
        or receipt.get("restored") is True
        or payload.get("environment_restored") is True
        or payload.get("restored") is True
    ):
        return bool(_text(receipt.get("receipt_id")) or _text(receipt.get("schema_version")))
    status = _text(receipt.get("status") or payload.get("status")).upper()
    return bool(
        status in {"RESTORED", "COMPLETED", "VERIFIED", "EQUIVALENT"}
        and (_text(receipt.get("receipt_id")) or _text(receipt.get("schema_version")))
    )


def _experiment_summary(raw: dict[str, Any]) -> dict[str, Any] | None:
    row = _runtime_row(raw)
    observations = _observations(row)
    binding = _dict(observations.get("declared_adapter_cleanup_runtime_binding"))
    if not binding:
        return None
    required = _binding_required(binding)
    bound_count, unbound_count = _binding_counts(binding)
    adapter_receipts = _receipt_rows(observations, "adapter_cleanup_receipts")
    database_receipts = _receipt_rows(observations, "database_cleanup_receipts")
    cleaned = [row for row in adapter_receipts if _valid_cleaned_adapter_receipt(row)]
    failed = [
        row
        for row in adapter_receipts
        if _text(row.get("status")).upper() == "FAILED"
    ]
    invalid = [
        row
        for row in adapter_receipts
        if row not in cleaned and row not in failed
    ]
    restoration_proven = _environment_restoration_proven(row, observations)
    experiment_id = _text(
        row.get("experiment_id")
        or raw.get("experiment_id")
        or row.get("execution_id")
        or row.get("obligation_id")
    )
    return {
        "experiment_id": experiment_id,
        "binding_schema_valid": _text(binding.get("schema_version")) == BINDING_SCHEMA,
        "binding_required": required,
        "binding_complete": binding.get("complete") is True,
        "bound_count": bound_count,
        "unbound_count": unbound_count,
        "missing_runtime_operation_refs": sorted(
            {
                _text(value)
                for value in _list(binding.get("missing_runtime_operation_refs"))
                if _text(value)
            }
        ),
        "adapter_cleanup_receipt_count": len(adapter_receipts),
        "adapter_cleanup_cleaned_count": len(cleaned),
        "adapter_cleanup_failed_count": len(failed),
        "adapter_cleanup_invalid_count": len(invalid),
        "database_cleanup_receipt_count": len(database_receipts),
        "environment_restoration_proven": restoration_proven,
        "cleanup_status": _text(observations.get("cleanup_status")),
        "cleanup_reason": _text(observations.get("cleanup_reason")),
    }


def _not_measured(
    *,
    project: str,
    source_path: str,
    reason_code: str,
    detail: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": ASSESSMENT_SCHEMA,
        "project": _text(project),
        "scan_result_path": _text(source_path),
        "scan_result_sha256": "",
        "measurement_status": MEASUREMENT_NOT_MEASURED,
        "verdict": VERDICT_NOT_MEASURED,
        "reason_codes": [_text(reason_code) or "RUNTIME_CLEANUP_NOT_MEASURED"],
        "detail": _text(detail),
        "experiment_result_count": 0,
        "adapter_binding_required_experiment_count": 0,
        "adapter_binding_bound_experiment_count": 0,
        "adapter_binding_unbound_experiment_count": 0,
        "adapter_binding_bound_count": 0,
        "adapter_binding_unbound_count": 0,
        "adapter_cleanup_receipt_count": 0,
        "adapter_cleanup_cleaned_count": 0,
        "adapter_cleanup_failed_count": 0,
        "adapter_cleanup_invalid_count": 0,
        "database_cleanup_receipt_count": 0,
        "environment_restoration_proven_experiment_count": 0,
        "assessed_experiments": [],
        "physical_residue_measurement_status": MEASUREMENT_NOT_MEASURED,
        "physical_residue_reason": "independent_database_snapshot_receipt_required",
        "target_reset_excluded_from_runtime_cleanup_proof": True,
    }
    payload["assessment_hash"] = _stable_hash(payload)
    payload["receipt_id"] = "brca_" + payload["assessment_hash"][:24]
    return payload


def assess_runtime_cleanup_document(
    document: dict[str, Any],
    *,
    project: str,
    source_path: str = "",
    source_sha256: str = "",
) -> dict[str, Any]:
    """Assess one completed scan document without reading target state."""
    if not isinstance(document, dict):
        return _not_measured(
            project=project,
            source_path=source_path,
            reason_code="SCAN_RESULT_NOT_OBJECT",
        )
    result_rows = _experiment_results(document)
    summaries = [
        summary
        for summary in (_experiment_summary(row) for row in result_rows)
        if isinstance(summary, dict)
    ]
    required = [row for row in summaries if row["binding_required"]]
    bound = [row for row in required if row["bound_count"] > 0]
    unbound = [
        row
        for row in required
        if row["unbound_count"] > 0
        or row["binding_complete"] is not True
        or row["binding_schema_valid"] is not True
    ]

    reason_codes: list[str] = []
    if not required:
        verdict = VERDICT_NOT_EXERCISED
        reason_codes.append("ADAPTER_CLEANUP_NOT_DECLARED_OR_REACHED")
    elif not bound:
        verdict = VERDICT_NOT_EXERCISED
        reason_codes.append("ADAPTER_CLEANUP_RUNTIME_NOT_REACHED")
        if unbound:
            reason_codes.append("ADAPTER_BINDING_INCOMPLETE")
    else:
        if unbound:
            reason_codes.append("ADAPTER_BINDING_INCOMPLETE")
        if any(row["adapter_cleanup_receipt_count"] < row["bound_count"] for row in bound):
            reason_codes.append("ADAPTER_CLEANUP_RECEIPT_MISSING")
        if any(row["adapter_cleanup_failed_count"] > 0 for row in bound):
            reason_codes.append("ADAPTER_CLEANUP_RECEIPT_FAILED")
        if any(row["adapter_cleanup_invalid_count"] > 0 for row in bound):
            reason_codes.append("ADAPTER_CLEANUP_RECEIPT_INVALID")
        if any(
            row["adapter_cleanup_cleaned_count"] < row["bound_count"]
            for row in bound
        ):
            reason_codes.append("ADAPTER_CLEANUP_BINDING_RECEIPT_IMBALANCE")
        if any(not row["environment_restoration_proven"] for row in bound):
            reason_codes.append("ENVIRONMENT_RESTORATION_NOT_PROVEN")
        verdict = VERDICT_INCOMPLETE if reason_codes else VERDICT_CLEAN

    payload: dict[str, Any] = {
        "schema_version": ASSESSMENT_SCHEMA,
        "project": _text(project),
        "scan_result_path": _text(source_path),
        "scan_result_sha256": _text(source_sha256),
        "measurement_status": MEASUREMENT_MEASURED,
        "verdict": verdict,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "detail": "",
        "experiment_result_count": len(result_rows),
        "adapter_binding_required_experiment_count": len(required),
        "adapter_binding_bound_experiment_count": len(bound),
        "adapter_binding_unbound_experiment_count": len(unbound),
        "adapter_binding_bound_count": sum(row["bound_count"] for row in required),
        "adapter_binding_unbound_count": sum(row["unbound_count"] for row in required),
        "adapter_cleanup_receipt_count": sum(
            row["adapter_cleanup_receipt_count"] for row in required
        ),
        "adapter_cleanup_cleaned_count": sum(
            row["adapter_cleanup_cleaned_count"] for row in required
        ),
        "adapter_cleanup_failed_count": sum(
            row["adapter_cleanup_failed_count"] for row in required
        ),
        "adapter_cleanup_invalid_count": sum(
            row["adapter_cleanup_invalid_count"] for row in required
        ),
        "database_cleanup_receipt_count": sum(
            row["database_cleanup_receipt_count"] for row in required
        ),
        "environment_restoration_proven_experiment_count": sum(
            1 for row in required if row["environment_restoration_proven"]
        ),
        "assessed_experiments": required,
        "physical_residue_measurement_status": MEASUREMENT_NOT_MEASURED,
        "physical_residue_reason": "independent_database_snapshot_receipt_required",
        "target_reset_excluded_from_runtime_cleanup_proof": True,
    }
    payload["assessment_hash"] = _stable_hash(payload)
    payload["receipt_id"] = "brca_" + payload["assessment_hash"][:24]
    return payload


def _candidate_scan_result_paths(root: Path, project: str) -> list[Path]:
    return [
        root / "platform_outputs" / project / "scan_result.json",
        root / "platform_workspace" / project / "scan_result.json",
        root / "platform_workspace" / project / "defect_discovery" / "scan_result.json",
    ]


def assess_benchmark_runtime_cleanup(
    *,
    root: Path,
    project: str,
    scan_result: dict[str, Any] | None = None,
    scan_result_path: Path | str | None = None,
) -> dict[str, Any]:
    """Load and assess the latest product scan before any target reset."""
    root = Path(root)
    if isinstance(scan_result, dict):
        return assess_runtime_cleanup_document(
            scan_result,
            project=project,
            source_path=_text(scan_result_path),
            source_sha256=_stable_hash(scan_result),
        )

    path = Path(scan_result_path) if scan_result_path else next(
        (candidate for candidate in _candidate_scan_result_paths(root, project) if candidate.is_file()),
        None,
    )
    if path is None or not path.is_file():
        return _not_measured(
            project=project,
            source_path=str(path or ""),
            reason_code="SCAN_RESULT_MISSING",
        )
    try:
        from .scan_result_store import load_scan_result

        document = load_scan_result(path, keys=None)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        return _not_measured(
            project=project,
            source_path=str(path),
            reason_code="SCAN_RESULT_INVALID",
            detail=f"{type(exc).__name__}:{exc}",
        )
    return assess_runtime_cleanup_document(
        document,
        project=project,
        source_path=str(path),
        source_sha256=hashlib.sha256(raw).hexdigest(),
    )


__all__ = [
    "ASSESSMENT_SCHEMA",
    "BenchmarkCleanupAssessmentError",
    "MEASUREMENT_MEASURED",
    "MEASUREMENT_NOT_MEASURED",
    "VERDICT_CLEAN",
    "VERDICT_INCOMPLETE",
    "VERDICT_NOT_EXERCISED",
    "VERDICT_NOT_MEASURED",
    "assess_benchmark_runtime_cleanup",
    "assess_runtime_cleanup_document",
]
