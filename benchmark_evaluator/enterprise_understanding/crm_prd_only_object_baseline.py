"""Real PRD-only CRM business-object baseline using shared evaluator authority."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from .business_object_baseline import (
    BusinessObjectBaselineError,
    BusinessObjectBaselineSpec,
    run_business_object_baseline,
    verify_frozen_source_snapshot as _verify_frozen_source_snapshot,
)

PROJECT_ID = "crm_prd_only"
BASELINE_SCHEMA = "qualibug.crm-prd-only-business-object-baseline.v1"
SOURCE_SPECS = (("benchmark/multi_industry/crm/PRD.md", "prd"),)
SPEC = BusinessObjectBaselineSpec(
    project_id=PROJECT_ID,
    baseline_schema=BASELINE_SCHEMA,
    source_specs=SOURCE_SPECS,
    actor_name="crm_prd_only_object_baseline",
    error_prefix="crm_prd_only",
    source_snapshot_schema="qualibug.crm-prd-only-source-snapshot-verification.v1",
    source_drift_reason_code="CRM_PRD_ONLY_SOURCE_SNAPSHOT_DRIFT",
)
CRMPrdOnlyObjectBaselineError = BusinessObjectBaselineError


def verify_frozen_source_snapshot(
    root: Path,
    business_object_ground_truth: dict[str, Any],
) -> dict[str, Any]:
    return _verify_frozen_source_snapshot(root, business_object_ground_truth, SPEC)


def run_crm_prd_only_object_baseline(
    *,
    root: str | Path,
    output_dir: str | Path | None = None,
    ingestor: Callable[..., dict[str, Any]] | None = None,
    builder: Callable[[str, Path, dict[str, Any]], dict[str, Any]] | None = None,
    capturer: Callable[..., dict[str, Any]] | None = None,
    business_object_ground_truth_loader: Callable[[str | Path], dict[str, Any]] | None = None,
    evaluator: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return run_business_object_baseline(
        SPEC,
        root=root,
        output_dir=output_dir,
        ingestor=ingestor,
        builder=builder,
        capturer=capturer,
        business_object_ground_truth_loader=business_object_ground_truth_loader,
        evaluator=evaluator,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and measure PRD-only CRM business-object recognition."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        summary = run_crm_prd_only_object_baseline(
            root=args.root,
            output_dir=args.output,
        )
    except (CRMPrdOnlyObjectBaselineError, OSError, ValueError, TypeError) as exc:
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
    "CRMPrdOnlyObjectBaselineError",
    "run_crm_prd_only_object_baseline",
    "verify_frozen_source_snapshot",
]
