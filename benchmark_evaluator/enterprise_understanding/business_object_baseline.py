"""Reusable evaluator orchestration for real business-object baselines.

The product phase always reuses canonical ingestion and composition. Ground Truth
is loaded only after the finalized asset has been persisted and captured.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .business_object_types import (
    evaluate_business_object_types,
    load_business_object_ground_truth,
)
from .capture_product_asset import capture_finalized_product_asset


class BusinessObjectBaselineError(RuntimeError):
    """A real business-object baseline could not be built or measured safely."""


@dataclass(frozen=True)
class BusinessObjectBaselineSpec:
    project_id: str
    baseline_schema: str
    source_specs: tuple[tuple[str, str], ...]
    actor_name: str
    error_prefix: str
    source_snapshot_schema: str
    source_drift_reason_code: str


def git_blob_sha(blob: bytes) -> str:
    # Source files are text and git normalizes CRLF to LF before storing the
    # blob (autocrlf). A Windows checkout therefore reads back CRLF bytes whose
    # raw hash differs from the canonical git blob sha recorded in the frozen
    # ground-truth snapshot. Normalize line endings so the verification compares
    # the canonical git identity, not the checkout's working-tree bytes.
    normalized = blob.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    header = f"blob {len(normalized)}\0".encode("utf-8")
    return hashlib.sha1(header + normalized).hexdigest()


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _default_ingestor(
    project_id: str,
    file_paths: list[Path],
    *,
    root: Path,
    actor: dict[str, str],
    source_type_hints: dict[str, str],
) -> dict[str, Any]:
    from ai_test_asset_center.enterprise_knowledge_center._crud import (
        ingest_enterprise_knowledge_files,
    )

    return ingest_enterprise_knowledge_files(
        project_id,
        file_paths,
        root=root,
        actor=actor,
        source_type_hints=source_type_hints,
    )


def _default_builder(
    project_id: str,
    root: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        build_enterprise_business_knowledge_asset,
    )

    return build_enterprise_business_knowledge_asset(project_id, root, options)


def _source_files(
    root: Path,
    spec: BusinessObjectBaselineSpec,
) -> tuple[list[Path], dict[str, str]]:
    paths: list[Path] = []
    hints: dict[str, str] = {}
    missing: list[str] = []
    for relative, source_type in spec.source_specs:
        path = (root / relative).resolve()
        if not path.is_file():
            missing.append(relative)
            continue
        paths.append(path)
        hints[str(path)] = source_type
    if missing:
        raise BusinessObjectBaselineError(
            f"{spec.error_prefix}_public_sources_missing:"
            + ",".join(sorted(missing))
        )
    return paths, hints


def verify_frozen_source_snapshot(
    root: Path,
    business_object_ground_truth: dict[str, Any],
    spec: BusinessObjectBaselineSpec,
) -> dict[str, Any]:
    declared = {
        str(row.get("path") or "").strip(): str(row.get("blob_sha") or "").strip()
        for row in business_object_ground_truth.get("source_snapshot") or []
        if isinstance(row, dict)
        and str(row.get("path") or "").strip()
        and str(row.get("blob_sha") or "").strip()
    }
    required = {relative for relative, _source_type in spec.source_specs}
    undeclared = sorted(required - set(declared))
    if undeclared:
        raise BusinessObjectBaselineError(
            f"{spec.error_prefix}_object_ground_truth_source_snapshot_incomplete:"
            + ",".join(undeclared)
        )

    rows: list[dict[str, Any]] = []
    drift: list[dict[str, str]] = []
    for relative, _source_type in spec.source_specs:
        path = (root / relative).resolve()
        if not path.is_file():
            raise BusinessObjectBaselineError(
                f"{spec.error_prefix}_public_source_missing_after_capture:{relative}"
            )
        actual = git_blob_sha(path.read_bytes())
        expected = declared[relative]
        row = {
            "path": relative,
            "expected_blob_sha": expected,
            "actual_blob_sha": actual,
            "status": "MATCH" if actual == expected else "DRIFTED",
        }
        rows.append(row)
        if actual != expected:
            drift.append(
                {
                    "path": relative,
                    "expected_blob_sha": expected,
                    "actual_blob_sha": actual,
                }
            )
    return {
        "schema": spec.source_snapshot_schema,
        "status": "BLOCKED" if drift else "PASS",
        "source_count": len(rows),
        "sources": rows,
        "drift": drift,
        "ground_truth_generated_from_product_output": False,
    }


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def run_business_object_baseline(
    spec: BusinessObjectBaselineSpec,
    *,
    root: str | Path,
    output_dir: str | Path | None = None,
    ground_truth_path: str | Path | None = None,
    ingestor: Callable[..., dict[str, Any]] | None = None,
    builder: Callable[[str, Path, dict[str, Any]], dict[str, Any]] | None = None,
    capturer: Callable[..., dict[str, Any]] | None = None,
    business_object_ground_truth_loader: Callable[[str | Path], dict[str, Any]] | None = None,
    evaluator: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        raise BusinessObjectBaselineError(f"product_root_missing:{resolved_root}")

    baseline_dir = (
        Path(output_dir).resolve()
        if output_dir is not None
        else (
            resolved_root
            / "evaluator_outputs"
            / spec.project_id
            / "business_object_baseline"
        ).resolve()
    )
    snapshot_path = baseline_dir / "final_asset.json"
    summary_path = baseline_dir / "baseline_summary.json"
    object_ground_truth_path = (
        Path(ground_truth_path).resolve()
        if ground_truth_path is not None
        else (
            Path(__file__).resolve().parent
            / "fixtures"
            / spec.project_id
            / "business_object_ground_truth.json"
        )
    )

    source_paths, source_type_hints = _source_files(resolved_root, spec)
    ingest = ingestor or _default_ingestor
    build = builder or _default_builder
    capture = capturer or capture_finalized_product_asset
    load_object_truth = (
        business_object_ground_truth_loader or load_business_object_ground_truth
    )
    score = evaluator or evaluate_business_object_types

    ingestion_receipt = ingest(
        spec.project_id,
        source_paths,
        root=resolved_root,
        actor={"name": spec.actor_name, "role": "qa_lead"},
        source_type_hints=source_type_hints,
    )
    if not isinstance(ingestion_receipt, dict) or not bool(
        ingestion_receipt.get("ok")
    ):
        raise BusinessObjectBaselineError(
            f"{spec.error_prefix}_source_ingestion_blocked:"
            + json.dumps(
                (ingestion_receipt or {}).get("errors") or [],
                ensure_ascii=False,
                sort_keys=True,
            )[:1500]
        )
    if str(ingestion_receipt.get("transaction_status") or "") != "COMMITTED":
        raise BusinessObjectBaselineError(
            f"{spec.error_prefix}_source_ingestion_not_committed:"
            + str(ingestion_receipt.get("transaction_status") or "UNKNOWN")
        )

    built_asset = build(spec.project_id, resolved_root, {"probe_limit": 0})
    if not isinstance(built_asset, dict) or not built_asset:
        raise BusinessObjectBaselineError(
            f"{spec.error_prefix}_composition_returned_empty_asset"
        )
    model = built_asset.get("enterprise_understanding_model")
    if not isinstance(model, dict) or not model:
        raise BusinessObjectBaselineError(
            f"{spec.error_prefix}_composition_missing_enterprise_understanding_model"
        )

    capture_receipt = capture(
        project_id=spec.project_id,
        root=resolved_root,
        output_path=snapshot_path,
    )
    if not snapshot_path.is_file():
        raise BusinessObjectBaselineError(
            f"{spec.error_prefix}_final_asset_snapshot_missing:{snapshot_path}"
        )

    object_truth = load_object_truth(object_ground_truth_path)
    snapshot_verification = verify_frozen_source_snapshot(
        resolved_root, object_truth, spec
    )
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    if snapshot_verification.get("status") != "PASS":
        summary = {
            "schema": spec.baseline_schema,
            "project_id": spec.project_id,
            "status": "BLOCKED",
            "reason_code": spec.source_drift_reason_code,
            "source_snapshot_verification": snapshot_verification,
            "ingestion_receipt": _json_clone(ingestion_receipt),
            "capture_receipt": _json_clone(capture_receipt),
            "product_asset_snapshot": str(snapshot_path),
            "ground_truth_loaded_after_product_capture": True,
            "ground_truth_entered_product_runtime": False,
            "model_writeback_allowed": False,
        }
        _write_summary(summary_path, summary)
        return summary

    measurement = score(object_truth, snapshot)
    if not isinstance(measurement, dict):
        raise BusinessObjectBaselineError(
            f"{spec.error_prefix}_business_object_evaluator_returned_invalid_result"
        )
    measurement_status = str(measurement.get("status") or "NOT_MEASURED")
    summary = {
        "schema": spec.baseline_schema,
        "project_id": spec.project_id,
        "status": "PASS" if measurement_status == "MEASURED" else "BLOCKED",
        "reason_code": str(measurement.get("reason_code") or ""),
        "business_object_measurement_status": measurement_status,
        "business_object_metrics": _json_clone(measurement.get("metrics") or {}),
        "false_positive_objects": _json_clone(
            measurement.get("false_positive_objects") or []
        ),
        "false_negative_objects": _json_clone(
            measurement.get("false_negative_objects") or []
        ),
        "source_snapshot_verification": snapshot_verification,
        "ingestion_receipt": _json_clone(ingestion_receipt),
        "capture_receipt": _json_clone(capture_receipt),
        "asset_id": snapshot.get("asset_id"),
        "model_id": (snapshot.get("enterprise_understanding_model") or {}).get(
            "model_id"
        ),
        "product_asset_snapshot": str(snapshot_path),
        "ground_truth_loaded_after_product_capture": True,
        "ground_truth_entered_product_runtime": False,
        "ground_truth_passed_to_ingestion": False,
        "ground_truth_passed_to_composition": False,
        "ground_truth_passed_to_capture": False,
        "product_builder_reused": True,
        "parallel_product_builder_created": False,
        "model_writeback_allowed": False,
    }
    _write_summary(summary_path, summary)
    return summary


__all__ = [
    "BusinessObjectBaselineError",
    "BusinessObjectBaselineSpec",
    "git_blob_sha",
    "run_business_object_baseline",
    "verify_frozen_source_snapshot",
]
