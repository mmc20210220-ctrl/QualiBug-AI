"""Real multi-source object distinctness/review baseline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any

from ai_test_asset_center.enterprise_knowledge_center._crud import ingest_enterprise_knowledge_files
from ai_test_asset_center.enterprise_knowledge_center._utils import _paths
from ai_test_asset_center.enterprise_knowledge_center.composition import build_enterprise_business_knowledge_asset

from .business_object_baseline import BusinessObjectBaselineError, BusinessObjectBaselineSpec, verify_frozen_source_snapshot
from .capture_product_asset import capture_finalized_product_asset
from .object_distinctness_review import evaluate_object_distinctness_review, load_object_distinctness_ground_truth

PROJECT_ID = "object_distinctness_review"
BASELINE_SCHEMA = "qualibug.object-distinctness-review-baseline.v1"
SOURCE_SPECS = (
    ("benchmark/multi_source_object_distinctness/CLIENT_PROFILE_PRD.md", "prd"),
    ("benchmark/multi_source_object_distinctness/CUSTOMER_ACCOUNT_PRD.md", "prd"),
    ("benchmark/multi_source_object_distinctness/SUPPLIER_ACCOUNT_PRD.md", "prd"),
)
SPEC = BusinessObjectBaselineSpec(
    project_id=PROJECT_ID,
    baseline_schema=BASELINE_SCHEMA,
    source_specs=SOURCE_SPECS,
    actor_name="object_distinctness_review_baseline",
    error_prefix="object_distinctness_review",
    source_snapshot_schema="qualibug.object-distinctness-review-snapshot-verification.v1",
    source_drift_reason_code="OBJECT_DISTINCTNESS_REVIEW_SOURCE_SNAPSHOT_DRIFT",
)


class ObjectDistinctnessBaselineError(BusinessObjectBaselineError):
    pass


def _clean(root: Path, output_dir: Path) -> None:
    paths = _paths(PROJECT_ID, root)
    for path in (paths["workspace"].parents[1], paths["output"].parents[1], output_dir):
        if path.exists():
            shutil.rmtree(path)


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def run_object_distinctness_baseline(*, root: str | Path, output_dir: str | Path | None = None) -> dict[str, Any]:
    resolved_root = Path(root).resolve()
    baseline_dir = Path(output_dir).resolve() if output_dir else resolved_root / "evaluator_outputs" / PROJECT_ID / "object_distinctness_baseline"
    snapshot_path = baseline_dir / "final_asset.json"
    summary_path = baseline_dir / "baseline_summary.json"
    truth_path = Path(__file__).resolve().parent / "fixtures" / PROJECT_ID / "structural_review_ground_truth.json"
    _clean(resolved_root, baseline_dir)

    sources: list[Path] = []
    hints: dict[str, str] = {}
    for relative, source_type in SOURCE_SPECS:
        path = (resolved_root / relative).resolve()
        if not path.is_file():
            raise ObjectDistinctnessBaselineError(f"object_distinctness_source_missing:{relative}")
        sources.append(path)
        hints[str(path)] = source_type
    ingestion = ingest_enterprise_knowledge_files(
        PROJECT_ID,
        sources,
        root=resolved_root,
        actor={"name": SPEC.actor_name, "role": "qa_lead"},
        source_type_hints=hints,
    )
    if not ingestion.get("ok") or ingestion.get("transaction_status") != "COMMITTED":
        raise ObjectDistinctnessBaselineError("object_distinctness_ingestion_not_committed")

    asset = build_enterprise_business_knowledge_asset(PROJECT_ID, resolved_root, {"probe_limit": 0})
    capture = capture_finalized_product_asset(project_id=PROJECT_ID, root=resolved_root, output_path=snapshot_path)

    # External truth is loaded only after product persistence and immutable capture.
    truth = load_object_distinctness_ground_truth(truth_path)
    source_verification = verify_frozen_source_snapshot(resolved_root, truth, SPEC)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    measurement = evaluate_object_distinctness_review(truth, snapshot)
    metrics = measurement.get("metrics") or {}
    status = "PASS" if (
        source_verification.get("status") == "PASS"
        and measurement.get("status") == "MEASURED"
        and metrics.get("review_pair_precision") == 1.0
        and metrics.get("review_pair_recall") == 1.0
        and metrics.get("review_pair_f1") == 1.0
        and metrics.get("lifecycle_contradiction_veto_coverage") == 1.0
        and metrics.get("automatic_entity_union_count") == 0
    ) else "BLOCKED"
    model = asset.get("enterprise_understanding_model") or {}
    summary = {
        "schema": BASELINE_SCHEMA,
        "project_id": PROJECT_ID,
        "status": status,
        "reason_code": measurement.get("reason_code") or "",
        "structural_review_measurement_status": measurement.get("status"),
        "structural_review_metrics": metrics,
        "false_positive_pairs": measurement.get("false_positive_pairs") or [],
        "false_negative_pairs": measurement.get("false_negative_pairs") or [],
        "suppressed_missing": measurement.get("suppressed_missing") or [],
        "suppressed_unexpected": measurement.get("suppressed_unexpected") or [],
        "identity_structural_evidence": model.get("identity_structural_evidence") or {},
        "behavior_binding_receipt": model.get("business_object_behavior_binding_receipt") or {},
        "source_snapshot_verification": source_verification,
        "ingestion_receipt": ingestion,
        "capture_receipt": capture,
        "product_asset_snapshot": str(snapshot_path),
        "ground_truth_loaded_after_product_capture": True,
        "ground_truth_entered_product_runtime": False,
        "automatic_similarity_merge_allowed": False,
        "automatic_entity_union_allowed": False,
        "model_writeback_allowed": False,
    }
    _write(summary_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and evaluate the multi-source object distinctness review baseline.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        summary = run_object_distinctness_baseline(root=args.root, output_dir=args.output)
    except (ObjectDistinctnessBaselineError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"schema": BASELINE_SCHEMA, "project_id": PROJECT_ID, "status": "BLOCKED", "reason_code": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BASELINE_SCHEMA", "PROJECT_ID", "SOURCE_SPECS", "SPEC", "ObjectDistinctnessBaselineError", "run_object_distinctness_baseline"]
