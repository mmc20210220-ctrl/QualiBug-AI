"""Build and measure the first real TicketSLA business-object baseline.

This evaluator-side command closes the public-source-to-measurement workflow without creating a
second product builder:

public TicketSLA files
  -> canonical enterprise knowledge ingestion
  -> explicit product composition root
  -> persisted finalized asset
  -> immutable evaluator snapshot
  -> evaluator-only Ground Truth loading and scoring

Ground Truth is deliberately loaded only after the product asset has been persisted and captured.
It is never passed to ingestion, composition, persistence, or product recognition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .business_object_types import (
    evaluate_business_object_types,
    load_business_object_ground_truth,
)
from .capture_product_asset import capture_finalized_product_asset

PROJECT_ID = "ticketsla_d"
BASELINE_SCHEMA = "qualibug.ticketsla-business-object-baseline.v1"
SOURCE_SPECS = (
    ("projects/ticketsla_d/input/BUSINESS_RULES.md", "business_rules"),
    ("projects/ticketsla_d/input/TEST_ACCOUNTS.md", "config"),
    ("projects/ticketsla_d/input/openapi.yaml", "openapi"),
)


class TicketSLAObjectBaselineError(RuntimeError):
    """The real TicketSLA baseline could not be built or measured safely."""


def _git_blob_sha(blob: bytes) -> str:
    header = f"blob {len(blob)}\0".encode("utf-8")
    return hashlib.sha1(header + blob).hexdigest()


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


def _source_files(root: Path) -> tuple[list[Path], dict[str, str]]:
    paths: list[Path] = []
    hints: dict[str, str] = {}
    missing: list[str] = []
    for relative, source_type in SOURCE_SPECS:
        path = (root / relative).resolve()
        if not path.is_file():
            missing.append(relative)
            continue
        paths.append(path)
        hints[str(path)] = source_type
    if missing:
        raise TicketSLAObjectBaselineError(
            "ticketsla_public_sources_missing:" + ",".join(sorted(missing))
        )
    return paths, hints


def verify_frozen_source_snapshot(
    root: Path,
    business_object_ground_truth: dict[str, Any],
) -> dict[str, Any]:
    """Verify the local public files still equal the evaluator's frozen Git blobs."""

    declared = {
        str(row.get("path") or "").strip(): str(row.get("blob_sha") or "").strip()
        for row in business_object_ground_truth.get("source_snapshot") or []
        if isinstance(row, dict)
        and str(row.get("path") or "").strip()
        and str(row.get("blob_sha") or "").strip()
    }
    required = {relative for relative, _source_type in SOURCE_SPECS}
    undeclared = sorted(required - set(declared))
    if undeclared:
        raise TicketSLAObjectBaselineError(
            "ticketsla_object_ground_truth_source_snapshot_incomplete:"
            + ",".join(undeclared)
        )

    rows: list[dict[str, Any]] = []
    drift: list[dict[str, str]] = []
    for relative, _source_type in SOURCE_SPECS:
        path = (root / relative).resolve()
        if not path.is_file():
            raise TicketSLAObjectBaselineError(
                f"ticketsla_public_source_missing_after_capture:{relative}"
            )
        actual = _git_blob_sha(path.read_bytes())
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
        "schema": "qualibug.ticketsla-source-snapshot-verification.v1",
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


def run_ticketsla_object_baseline(
    *,
    root: str | Path,
    output_dir: str | Path | None = None,
    ingestor: Callable[..., dict[str, Any]] | None = None,
    builder: Callable[[str, Path, dict[str, Any]], dict[str, Any]] | None = None,
    capturer: Callable[..., dict[str, Any]] | None = None,
    business_object_ground_truth_loader: Callable[[str | Path], dict[str, Any]] | None = None,
    evaluator: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build, freeze and score TicketSLA through existing product authorities."""

    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        raise TicketSLAObjectBaselineError(f"product_root_missing:{resolved_root}")

    baseline_dir = (
        Path(output_dir).resolve()
        if output_dir is not None
        else (
            resolved_root
            / "evaluator_outputs"
            / PROJECT_ID
            / "business_object_baseline"
        ).resolve()
    )
    snapshot_path = baseline_dir / "final_asset.json"
    summary_path = baseline_dir / "baseline_summary.json"
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / PROJECT_ID
    object_ground_truth_path = fixture_dir / "business_object_ground_truth.json"

    source_paths, source_type_hints = _source_files(resolved_root)
    ingest = ingestor or _default_ingestor
    build = builder or _default_builder
    capture = capturer or capture_finalized_product_asset
    load_object_truth = (
        business_object_ground_truth_loader or load_business_object_ground_truth
    )
    score = evaluator or evaluate_business_object_types

    # Product phase. No Ground Truth has been loaded at this point.
    ingestion_receipt = ingest(
        PROJECT_ID,
        source_paths,
        root=resolved_root,
        actor={"name": "ticketsla_object_baseline", "role": "qa_lead"},
        source_type_hints=source_type_hints,
    )
    if not isinstance(ingestion_receipt, dict) or not bool(
        ingestion_receipt.get("ok")
    ):
        raise TicketSLAObjectBaselineError(
            "ticketsla_source_ingestion_blocked:"
            + json.dumps(
                (ingestion_receipt or {}).get("errors") or [],
                ensure_ascii=False,
                sort_keys=True,
            )[:1500]
        )
    if str(ingestion_receipt.get("transaction_status") or "") != "COMMITTED":
        raise TicketSLAObjectBaselineError(
            "ticketsla_source_ingestion_not_committed:"
            + str(ingestion_receipt.get("transaction_status") or "UNKNOWN")
        )

    built_asset = build(PROJECT_ID, resolved_root, {"probe_limit": 0})
    if not isinstance(built_asset, dict) or not built_asset:
        raise TicketSLAObjectBaselineError("ticketsla_composition_returned_empty_asset")
    model = built_asset.get("enterprise_understanding_model")
    if not isinstance(model, dict) or not model:
        raise TicketSLAObjectBaselineError(
            "ticketsla_composition_missing_enterprise_understanding_model"
        )

    capture_receipt = capture(
        project_id=PROJECT_ID,
        root=resolved_root,
        output_path=snapshot_path,
    )
    if not snapshot_path.is_file():
        raise TicketSLAObjectBaselineError(
            f"ticketsla_final_asset_snapshot_missing:{snapshot_path}"
        )

    # Evaluator phase. Ground Truth is loaded only after product persistence and capture.
    object_truth = load_object_truth(object_ground_truth_path)
    snapshot_verification = verify_frozen_source_snapshot(resolved_root, object_truth)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    if snapshot_verification.get("status") != "PASS":
        summary = {
            "schema": BASELINE_SCHEMA,
            "project_id": PROJECT_ID,
            "status": "BLOCKED",
            "reason_code": "TICKETSLA_PUBLIC_SOURCE_SNAPSHOT_DRIFT",
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
        raise TicketSLAObjectBaselineError(
            "ticketsla_business_object_evaluator_returned_invalid_result"
        )
    measurement_status = str(measurement.get("status") or "NOT_MEASURED")
    status = "PASS" if measurement_status == "MEASURED" else "BLOCKED"
    reason_code = str(measurement.get("reason_code") or "")
    summary = {
        "schema": BASELINE_SCHEMA,
        "project_id": PROJECT_ID,
        "status": status,
        "reason_code": reason_code,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest TicketSLA public sources, build the finalized product asset through the "
            "composition root, freeze it, and measure business-object recognition."
        )
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        summary = run_ticketsla_object_baseline(
            root=args.root,
            output_dir=args.output,
        )
    except (TicketSLAObjectBaselineError, OSError, ValueError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "schema": BASELINE_SCHEMA,
                    "project_id": PROJECT_ID,
                    "status": "BLOCKED",
                    "reason_code": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASELINE_SCHEMA",
    "PROJECT_ID",
    "SOURCE_SPECS",
    "TicketSLAObjectBaselineError",
    "run_ticketsla_object_baseline",
    "verify_frozen_source_snapshot",
]
