"""CLI for evaluator-only enterprise-understanding measurement."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmark_evaluator.scored_run_comparison import ComparisonError, _read_artifact

from .business_object_types import (
    BusinessObjectGroundTruthValidationError,
    load_business_object_ground_truth,
)
from .fact_slot_document import validate_business_fact_slot_document
from .fact_slot_ground_truth import FactSlotGroundTruthValidationError
from .ground_truth import GroundTruthValidationError, load_ground_truth
from .runner import run_benchmark


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark_evaluator.enterprise_understanding",
        description=(
            "Compare evaluator-side human Ground Truth with one immutable product enterprise "
            "understanding asset. No Ground Truth enters product runtime."
        ),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--ground-truth", required=True, help="evaluator-side Ground Truth JSON")
    parser.add_argument(
        "--business-object-ground-truth",
        help="optional evaluator-side closed-world business-object type Ground Truth JSON",
    )
    parser.add_argument("--asset", required=True, help="immutable product knowledge asset JSON")
    parser.add_argument("--output", required=True, help="evaluator output directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        ground_truth = validate_business_fact_slot_document(
            load_ground_truth(args.ground_truth)
        )
        if str(ground_truth.get("project_id") or "").strip() != args.project.strip():
            raise GroundTruthValidationError(
                "--project must equal ground_truth.project_id; evaluator scope cannot silently switch"
            )
        business_object_ground_truth = (
            load_business_object_ground_truth(args.business_object_ground_truth)
            if args.business_object_ground_truth
            else None
        )
        if business_object_ground_truth and (
            str(business_object_ground_truth.get("project_id") or "").strip()
            != args.project.strip()
        ):
            raise BusinessObjectGroundTruthValidationError(
                "--project must equal business-object Ground Truth project_id"
            )
        asset = _read_artifact(Path(args.asset))
        if not isinstance(asset, dict):
            raise GroundTruthValidationError("product asset root must be a JSON object")
        result = run_benchmark(
            ground_truth,
            asset,
            business_object_ground_truth=business_object_ground_truth,
            output_dir=args.output,
        )
    except (
        GroundTruthValidationError,
        BusinessObjectGroundTruthValidationError,
        FactSlotGroundTruthValidationError,
        ComparisonError,
        OSError,
    ) as exc:
        sys.stderr.write(f"enterprise-understanding benchmark input error: {exc}\n")
        return 2
    summary = {
        "project_id": result.get("project_id"),
        "status": result.get("status"),
        "ground_truth_fingerprint": result.get("ground_truth_fingerprint"),
        "product_asset_fingerprint": result.get("product_asset_fingerprint"),
        "next_repair_target": result.get("next_repair_target"),
        "next_business_object_repair_target": result.get(
            "next_business_object_repair_target"
        ),
        "next_ingestion_repair_target": result.get("next_ingestion_repair_target"),
        "business_object_type_measurement": result.get(
            "business_object_type_measurement"
        ),
        "workflow_receipt": result.get("workflow_receipt"),
        "output_files": result.get("output_files") or {},
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
