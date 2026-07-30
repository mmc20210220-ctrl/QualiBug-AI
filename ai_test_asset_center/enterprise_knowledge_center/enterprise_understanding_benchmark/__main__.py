"""CLI for the measurement-only enterprise understanding benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .ground_truth import GroundTruthValidationError, load_ground_truth
from .runner import run_benchmark


def _load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GroundTruthValidationError(f"asset file not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise GroundTruthValidationError(f"asset is not valid JSON: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise GroundTruthValidationError("asset JSON root must be an object")
    return value


def _load_persisted_asset(project: str) -> dict[str, Any]:
    from .. import load_enterprise_business_knowledge_asset

    value = load_enterprise_business_knowledge_asset(project)
    if not isinstance(value, dict):
        return {}
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=(
            "python -m ai_test_asset_center.enterprise_knowledge_center."
            "enterprise_understanding_benchmark"
        ),
        description=(
            "Compare human-authored Ground Truth with the existing enterprise understanding "
            "asset. The benchmark never writes back to the model."
        ),
    )
    parser.add_argument("--project", required=True, help="existing QualiBug project id")
    parser.add_argument("--ground-truth", required=True, help="Ground Truth JSON path")
    parser.add_argument(
        "--asset",
        help=(
            "optional enterprise knowledge asset JSON path; when omitted, load the existing "
            "persisted project asset"
        ),
    )
    parser.add_argument("--output", required=True, help="benchmark output directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        ground_truth = load_ground_truth(args.ground_truth)
        if str(ground_truth.get("project_id") or "").strip() != args.project.strip():
            raise GroundTruthValidationError(
                "--project must equal ground_truth.project_id; benchmark scope cannot be silently switched"
            )
        asset = _load_json(args.asset) if args.asset else _load_persisted_asset(args.project)
        result = run_benchmark(ground_truth, asset, output_dir=args.output)
    except GroundTruthValidationError as exc:
        sys.stderr.write(f"benchmark input error: {exc}\n")
        return 2
    summary = {
        "project_id": result.get("project_id"),
        "status": result.get("status"),
        "ground_truth_status": result.get("ground_truth_status"),
        "next_repair_target": result.get("next_repair_target"),
        "output_files": result.get("output_files") or {},
        "model_writeback_allowed": False,
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
