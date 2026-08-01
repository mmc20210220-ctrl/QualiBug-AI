"""Real multi-source business-object authority conflict baseline.

The product is first built with two active PRDs that assign the same display label
to different canonical objects.  The unresolved build must fail closed.  The
baseline then records one explicit SELECT_FACT decision through the existing
operator authority ledger and rebuilds before loading external Ground Truth.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_authority_decision import (
    ACTION_SELECT_FACT,
    record_operator_authority_decision,
)
from ai_test_asset_center.enterprise_knowledge_center._crud import (
    ingest_enterprise_knowledge_files,
)
from ai_test_asset_center.enterprise_knowledge_center._utils import _paths
from ai_test_asset_center.enterprise_knowledge_center.composition import (
    build_enterprise_business_knowledge_asset,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding._object_source_conflicts import (
    OBJECT_DECLARATION_ALIAS_CONFLICT,
)

from .business_object_baseline import (
    BusinessObjectBaselineError,
    BusinessObjectBaselineSpec,
    verify_frozen_source_snapshot,
)
from .business_object_types import (
    evaluate_business_object_types,
    load_business_object_ground_truth,
)
from .capture_product_asset import capture_finalized_product_asset

PROJECT_ID = "object_source_conflict"
BASELINE_SCHEMA = "qualibug.object-source-conflict-business-object-baseline.v1"
SOURCE_SPECS = (
    ("benchmark/multi_source_object_conflict/PRD_LEGACY.md", "prd"),
    ("benchmark/multi_source_object_conflict/PRD_CURRENT.md", "prd"),
)
SPEC = BusinessObjectBaselineSpec(
    project_id=PROJECT_ID,
    baseline_schema=BASELINE_SCHEMA,
    source_specs=SOURCE_SPECS,
    actor_name="object_source_conflict_baseline",
    error_prefix="object_source_conflict",
    source_snapshot_schema="qualibug.object-source-conflict-snapshot-verification.v1",
    source_drift_reason_code="OBJECT_SOURCE_CONFLICT_SOURCE_SNAPSHOT_DRIFT",
)


class ObjectSourceConflictBaselineError(BusinessObjectBaselineError):
    """The source-authority conflict baseline could not close safely."""


def _recognition(asset: dict[str, Any]) -> dict[str, Any]:
    direct = asset.get("business_object_recognition")
    if isinstance(direct, dict):
        return direct
    model = asset.get("enterprise_understanding_model")
    if isinstance(model, dict) and isinstance(model.get("business_object_recognition"), dict):
        return dict(model["business_object_recognition"])
    return {}


def _object_conflicts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in asset.get("cross_document_conflicts") or []
        if isinstance(row, dict)
        and str(row.get("kind") or "") == OBJECT_DECLARATION_ALIAS_CONFLICT
    ]


def _clean_project(root: Path, output_dir: Path) -> None:
    paths = _paths(PROJECT_ID, root)
    workspace_root = paths["workspace"].parents[1]
    output_root = paths["output"].parents[1]
    for path in (workspace_root, output_root, output_dir):
        if path.exists():
            shutil.rmtree(path)


def _source_paths(root: Path) -> tuple[list[Path], dict[str, str]]:
    paths: list[Path] = []
    hints: dict[str, str] = {}
    for relative, source_type in SOURCE_SPECS:
        path = (root / relative).resolve()
        if not path.is_file():
            raise ObjectSourceConflictBaselineError(
                f"object_source_conflict_source_missing:{relative}"
            )
        paths.append(path)
        hints[str(path)] = source_type
    return paths, hints


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def run_object_source_conflict_baseline(
    *,
    root: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    resolved_root = Path(root).resolve()
    baseline_dir = (
        Path(output_dir).resolve()
        if output_dir is not None
        else resolved_root
        / "evaluator_outputs"
        / PROJECT_ID
        / "business_object_baseline"
    )
    unresolved_snapshot = baseline_dir / "unresolved_asset.json"
    final_snapshot = baseline_dir / "final_asset.json"
    summary_path = baseline_dir / "baseline_summary.json"
    ground_truth_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / PROJECT_ID
        / "business_object_ground_truth.json"
    )

    _clean_project(resolved_root, baseline_dir)
    source_paths, hints = _source_paths(resolved_root)
    ingestion_receipt = ingest_enterprise_knowledge_files(
        PROJECT_ID,
        source_paths,
        root=resolved_root,
        actor={"name": SPEC.actor_name, "role": "qa_lead"},
        source_type_hints=hints,
    )
    if not bool(ingestion_receipt.get("ok")) or str(
        ingestion_receipt.get("transaction_status") or ""
    ) != "COMMITTED":
        raise ObjectSourceConflictBaselineError(
            "object_source_conflict_ingestion_not_committed"
        )

    unresolved_asset = build_enterprise_business_knowledge_asset(
        PROJECT_ID, resolved_root, {"probe_limit": 0}
    )
    unresolved_recognition = _recognition(unresolved_asset)
    conflicts = _object_conflicts(unresolved_asset)
    if len(conflicts) != 1:
        raise ObjectSourceConflictBaselineError(
            f"object_source_conflict_expected_one_conflict:{len(conflicts)}"
        )
    conflict = conflicts[0]
    if str(conflict.get("status") or "") != "UNRESOLVED":
        raise ObjectSourceConflictBaselineError(
            "object_source_conflict_initial_conflict_not_unresolved"
        )
    gate = unresolved_recognition.get("gate") or {}
    if str(gate.get("status") or "") != (
        "BLOCKED_BUSINESS_OBJECT_SOURCE_AUTHORITY_CONFLICT"
    ) or bool(gate.get("entry_allowed")):
        raise ObjectSourceConflictBaselineError(
            "object_source_conflict_initial_object_gate_not_blocked"
        )
    initial_labels = set(unresolved_recognition.get("accepted_labels") or [])
    if initial_labels != {"Contract", "合同"}:
        raise ObjectSourceConflictBaselineError(
            "object_source_conflict_unresolved_declarations_leaked:"
            + ",".join(sorted(initial_labels))
        )
    capture_finalized_product_asset(
        project_id=PROJECT_ID,
        root=resolved_root,
        output_path=unresolved_snapshot,
    )

    current_participant = next(
        (
            row
            for row in conflict.get("object_declaration_participants") or []
            if isinstance(row, dict)
            and "PRD_CURRENT.md" in str(row.get("source_locator") or "")
        ),
        None,
    )
    if not isinstance(current_participant, dict):
        raise ObjectSourceConflictBaselineError(
            "object_source_conflict_current_participant_missing"
        )
    decision_receipt = record_operator_authority_decision(
        PROJECT_ID,
        conflict_id=str(conflict.get("conflict_id") or ""),
        action=ACTION_SELECT_FACT,
        selected_fact_id=str(current_participant.get("fact_id") or ""),
        actor={"name": "business_owner", "role": "product_owner"},
        root=resolved_root,
        rationale=(
            "The current approved PRD is the explicit authority for the 客户 "
            "business-object declaration."
        ),
        document_version="CURRENT_APPROVED_V2",
        rebuild=True,
    )
    if not bool(decision_receipt.get("ok")):
        raise ObjectSourceConflictBaselineError(
            "object_source_conflict_operator_decision_failed"
        )

    final_asset = build_enterprise_business_knowledge_asset(
        PROJECT_ID, resolved_root, {"probe_limit": 0}
    )
    final_recognition = _recognition(final_asset)
    final_conflicts = _object_conflicts(final_asset)
    if len(final_conflicts) != 1 or str(final_conflicts[0].get("status") or "") != "RESOLVED":
        raise ObjectSourceConflictBaselineError(
            "object_source_conflict_not_resolved_after_select"
        )
    selected = final_conflicts[0].get("authority_decision") or {}
    if str(selected.get("selected_fact_id") or "") != str(
        current_participant.get("fact_id") or ""
    ):
        raise ObjectSourceConflictBaselineError(
            "object_source_conflict_selected_fact_drift"
        )
    final_labels = set(final_recognition.get("accepted_labels") or [])
    expected_final = {"CustomerAccount", "客户", "Contract", "合同"}
    if final_labels != expected_final or "CustomerProfile" in final_labels:
        raise ObjectSourceConflictBaselineError(
            "object_source_conflict_final_object_projection_wrong:"
            + ",".join(sorted(final_labels))
        )
    capture_receipt = capture_finalized_product_asset(
        project_id=PROJECT_ID,
        root=resolved_root,
        output_path=final_snapshot,
    )

    # External truth is intentionally loaded only after the final product capture.
    ground_truth = load_business_object_ground_truth(ground_truth_path)
    snapshot_verification = verify_frozen_source_snapshot(
        resolved_root, ground_truth, SPEC
    )
    snapshot = json.loads(final_snapshot.read_text(encoding="utf-8"))
    measurement = evaluate_business_object_types(ground_truth, snapshot)
    measurement_status = str(measurement.get("status") or "NOT_MEASURED")
    status = (
        "PASS"
        if snapshot_verification.get("status") == "PASS"
        and measurement_status == "MEASURED"
        else "BLOCKED"
    )
    summary = {
        "schema": BASELINE_SCHEMA,
        "project_id": PROJECT_ID,
        "status": status,
        "reason_code": str(measurement.get("reason_code") or ""),
        "initial_conflict_status": conflict.get("status"),
        "initial_object_gate_status": gate.get("status"),
        "initial_accepted_labels": sorted(initial_labels),
        "operator_decision": decision_receipt.get("decision"),
        "operator_audit_receipt": decision_receipt.get("audit_receipt"),
        "resolved_conflict": final_conflicts[0],
        "final_object_gate_status": (final_recognition.get("gate") or {}).get(
            "status"
        ),
        "final_accepted_labels": sorted(final_labels),
        "business_object_measurement_status": measurement_status,
        "business_object_metrics": measurement.get("metrics") or {},
        "false_positive_objects": measurement.get("false_positive_objects") or [],
        "false_negative_objects": measurement.get("false_negative_objects") or [],
        "source_snapshot_verification": snapshot_verification,
        "ingestion_receipt": ingestion_receipt,
        "capture_receipt": capture_receipt,
        "unresolved_product_asset_snapshot": str(unresolved_snapshot),
        "product_asset_snapshot": str(final_snapshot),
        "ground_truth_loaded_after_product_capture": True,
        "ground_truth_entered_product_runtime": False,
        "existing_operator_authority_ledger_reused": True,
        "parallel_authority_ledger_created": False,
        "automatic_winner_selected": False,
        "model_writeback_allowed": False,
    }
    _write_json(summary_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build unresolved and operator-resolved multi-source object authority "
            "baselines."
        )
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        summary = run_object_source_conflict_baseline(
            root=args.root,
            output_dir=args.output,
        )
    except (ObjectSourceConflictBaselineError, OSError, ValueError, TypeError) as exc:
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
    "ObjectSourceConflictBaselineError",
    "run_object_source_conflict_baseline",
]
