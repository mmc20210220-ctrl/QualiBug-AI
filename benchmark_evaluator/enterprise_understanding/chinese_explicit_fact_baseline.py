"""Run the frozen Chinese explicit-business-fact baseline through the product mainline.

The source corpus and Ground Truth are evaluator fixtures. Product construction runs in
an isolated child process through the existing source-backed workflow and never receives
Ground Truth. Slot measurement is repeated only after that workflow has completed, using
the existing evaluator function, so the baseline can persist first-loss diagnostics
without creating another extraction or scoring authority.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from benchmark_evaluator.scored_run_comparison import _fingerprint

from .build_product_snapshot import _git_blob_sha
from .fact_slot_document import validate_business_fact_slot_document
from .fact_slots import evaluate_business_fact_slots
from .ground_truth import load_ground_truth
from .run_source_backed_workflow import run_source_backed_understanding_workflow

PROJECT_ID = "chinese_explicit_fact_baseline_v1"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / PROJECT_ID
SOURCE_RELATIVE_PATH = (
    "benchmark_evaluator/enterprise_understanding/fixtures/"
    f"{PROJECT_ID}/source_rules.md"
)
GROUND_TRUTH_PATH = FIXTURE_ROOT / "ground_truth.json"
TARGETS = {
    "fact_recall": 0.95,
    "slot_exact_accuracy": 0.92,
    "p0_exact_fact_recall": 0.95,
    "source_locator_exact_accuracy": 0.98,
    "accepted_fact_precision": 0.98,
}
_CRITICALITY_WEIGHT = {"P0": 4.0, "P1": 3.0, "P2": 2.0, "P3": 1.0}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_source_manifest(product_root: Path, output_dir: Path) -> Path:
    source = product_root / SOURCE_RELATIVE_PATH
    if not source.is_file():
        raise FileNotFoundError(f"frozen baseline source missing: {source}")
    manifest = {
        "schema": "qualibug.enterprise-understanding-source-manifest.v1",
        "project_id": PROJECT_ID,
        "sources": [
            {
                "path": SOURCE_RELATIVE_PATH,
                "source_type": "business_rules",
                "blob_sha": _git_blob_sha(source.read_bytes()),
            }
        ],
        "product_phase_may_load_ground_truth": False,
        "fixture_source_is_frozen": True,
        "manifest_generated_from_frozen_source_bytes": True,
    }
    path = output_dir / "source_manifest.json"
    _write_json(path, manifest)
    return path


def _first_loss_for_alignment(row: dict[str, Any]) -> str:
    status = str(row.get("alignment_status") or "").strip().upper()
    if status == "EXACT":
        return "NONE"
    if status == "MISSING":
        return "EXTRACTION_OR_ATOMIZATION"
    if status == "AMBIGUOUS":
        return "IDENTITY_OR_DEDUPLICATION"

    slots = row.get("slot_alignments")
    slot_rows = slots if isinstance(slots, dict) else {}
    failed = {
        str(field)
        for field, detail in slot_rows.items()
        if isinstance(detail, dict)
        and str(detail.get("status") or "").upper() in {"MISSING", "WRONG"}
    }
    priority = (
        ({"source_locators"}, "EVIDENCE_ADDRESS_ALIGNMENT"),
        ({"actor_refs", "object_refs"}, "IDENTITY_BINDING"),
        ({"condition_frame", "exception_scope"}, "CONDITION_EXCEPTION_COMPILATION"),
        (
            {
                "state_effects",
                "data_effects",
                "postconditions",
                "compensation",
            },
            "EFFECT_ATOMIZATION",
        ),
        (
            {
                "quantity_constraints",
                "time_window_constraints",
                "formula_constraints",
            },
            "CONSTRAINT_COMPILATION",
        ),
        ({"fact_type", "modality"}, "FACT_TYPING"),
    )
    for fields, loss in priority:
        if failed.intersection(fields):
            return loss
    return "SLOT_PROJECTION"


def _first_loss_analysis(measurement: dict[str, Any]) -> dict[str, Any]:
    distribution: Counter[str] = Counter()
    weighted: defaultdict[str, float] = defaultdict(float)
    rows: list[dict[str, Any]] = []
    for alignment in measurement.get("alignments") or []:
        if not isinstance(alignment, dict):
            continue
        loss = _first_loss_for_alignment(alignment)
        criticality = str(alignment.get("criticality") or "P2").upper()
        weight = _CRITICALITY_WEIGHT.get(criticality, 2.0)
        if loss != "NONE":
            distribution[loss] += 1
            weighted[loss] += weight
        rows.append(
            {
                "ground_truth_id": alignment.get("ground_truth_id"),
                "criticality": criticality,
                "alignment_status": alignment.get("alignment_status"),
                "first_loss_stage": loss,
                "candidate_id": alignment.get("candidate_id"),
                "candidate_ids": alignment.get("candidate_ids") or [],
                "slot_alignments": alignment.get("slot_alignments") or {},
            }
        )

    for false_fact in measurement.get("false_accepted_facts") or []:
        if not isinstance(false_fact, dict):
            continue
        stage = "FACT_DISCOVERY_FALSE_ACCEPTANCE"
        distribution[stage] += 1
        weighted[stage] += _CRITICALITY_WEIGHT["P1"]
        rows.append(
            {
                "ground_truth_id": "",
                "criticality": "P1",
                "alignment_status": "FALSE_ACCEPTED",
                "first_loss_stage": stage,
                "candidate_id": false_fact.get("candidate_id"),
                "candidate_ids": [],
                "slot_alignments": {},
                "false_accepted_fact": false_fact,
            }
        )

    ranking = sorted(
        (
            {
                "first_loss_stage": stage,
                "miss_count": distribution[stage],
                "criticality_weighted_impact": impact,
            }
            for stage, impact in weighted.items()
        ),
        key=lambda row: (
            -float(row["criticality_weighted_impact"]),
            -int(row["miss_count"]),
            str(row["first_loss_stage"]),
        ),
    )
    return {
        "schema": "qualibug.chinese-explicit-fact-first-loss-analysis.v1",
        "highest_impact_first_loss": (
            ranking[0]["first_loss_stage"] if ranking else ""
        ),
        "distribution": dict(distribution),
        "ranking": ranking,
        "alignments": rows,
        "repair_policy": (
            "FIX_ONLY_THE_HIGHEST_IMPACT_EXISTING_MAINLINE_STAGE;_DO_NOT_PATCH_DOWNSTREAM"
        ),
    }


def _threshold_status(metrics: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    checks: dict[str, Any] = {}
    passed = True
    for metric, target in TARGETS.items():
        value = metrics.get(metric)
        measurable = isinstance(value, (int, float))
        meets = measurable and float(value) >= target
        checks[metric] = {
            "value": value,
            "target": target,
            "measurable": measurable,
            "meets_target": meets,
        }
        passed = passed and meets
    return ("PASS" if passed else "BELOW_TARGET"), checks


def _quality_result_status(measurement_status: Any, threshold_status: Any) -> str:
    measured = str(measurement_status or "").strip().upper()
    threshold = str(threshold_status or "").strip().upper()
    if measured != "PASS":
        return "NOT_MEASURED"
    return "PASS" if threshold == "PASS" else "BELOW_TARGET"


def _baseline_exit_code(result: dict[str, Any]) -> int:
    status = str(result.get("status") or "").strip().upper()
    if status == "PASS":
        return 0
    if status == "BELOW_TARGET":
        return 3
    return 2


def run_chinese_explicit_fact_baseline(
    *,
    product_root: str | Path,
    workspace_root: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    product = Path(product_root).resolve()
    workspace = Path(workspace_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = _write_source_manifest(product, output)
    ground_truth = (
        product
        / GROUND_TRUTH_PATH.relative_to(Path(__file__).parent.parent.parent)
    ).resolve()
    if not ground_truth.is_file():
        ground_truth = GROUND_TRUTH_PATH.resolve()

    workflow = run_source_backed_understanding_workflow(
        project_id=PROJECT_ID,
        product_root=product,
        workspace_root=workspace,
        source_manifest_path=manifest,
        ground_truth_path=ground_truth,
        output_dir=output,
    )
    asset_path = output / "final_enterprise_understanding_asset.json"
    if not workflow.get("ground_truth_loaded_after_product_phase") or not asset_path.is_file():
        summary = {
            "schema": "qualibug.chinese-explicit-fact-baseline-result.v1",
            "project_id": PROJECT_ID,
            "status": "BLOCKED",
            "workflow_status": workflow.get("status"),
            "reason_code": workflow.get("reason_code"),
            "measurement_status": "NOT_MEASURED",
            "threshold_status": "NOT_MEASURED",
            "highest_impact_first_loss": "PRODUCT_OR_INGESTION_PHASE",
            "workflow_receipt": workflow,
            "ground_truth_entered_product_runtime": False,
        }
        _write_json(output / "chinese_explicit_fact_baseline_summary.json", summary)
        return summary

    validated_ground_truth = validate_business_fact_slot_document(
        load_ground_truth(ground_truth)
    )
    product_asset = _read_json(asset_path)
    measurement = evaluate_business_fact_slots(validated_ground_truth, product_asset)
    measurement_path = output / "evaluation" / "business_fact_slot_measurement.json"
    _write_json(measurement_path, measurement)
    metrics = (
        measurement.get("metrics")
        if isinstance(measurement.get("metrics"), dict)
        else {}
    )
    first_loss = _first_loss_analysis(measurement)
    _write_json(
        output / "evaluation" / "explicit_fact_first_loss_analysis.json",
        first_loss,
    )
    threshold_status, threshold_checks = _threshold_status(metrics)
    measurement_status = measurement.get("status")
    summary = {
        "schema": "qualibug.chinese-explicit-fact-baseline-result.v1",
        "project_id": PROJECT_ID,
        "status": _quality_result_status(measurement_status, threshold_status),
        "workflow_status": workflow.get("status"),
        "measurement_status": measurement_status,
        "threshold_status": threshold_status,
        "targets": TARGETS,
        "threshold_checks": threshold_checks,
        "metrics": metrics,
        "highest_impact_first_loss": first_loss.get("highest_impact_first_loss"),
        "first_loss_distribution": first_loss.get("distribution") or {},
        "ground_truth_fingerprint": _fingerprint(validated_ground_truth),
        "source_manifest_fingerprint": _fingerprint(_read_json(manifest)),
        "product_asset_fingerprint": _fingerprint(product_asset),
        "ground_truth_entered_product_runtime": False,
        "product_model_can_self_label_true_or_false": False,
        "automatic_winner_used": False,
        "fuzzy_or_llm_alignment_used": False,
        "quality_thresholds_are_process_exit_authority": True,
        "output_paths": {
            "workflow_receipt": str(output / "source_backed_workflow_receipt.json"),
            "product_asset": str(asset_path),
            "fact_slot_measurement": str(measurement_path),
            "first_loss_analysis": str(
                output / "evaluation" / "explicit_fact_first_loss_analysis.json"
            ),
        },
    }
    _write_json(output / "chinese_explicit_fact_baseline_summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen Chinese explicit-business-fact baseline."
    )
    default_product_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--product-root", default=str(default_product_root))
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = run_chinese_explicit_fact_baseline(
        product_root=args.product_root,
        workspace_root=args.workspace_root,
        output_dir=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return _baseline_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PROJECT_ID",
    "TARGETS",
    "run_chinese_explicit_fact_baseline",
]
