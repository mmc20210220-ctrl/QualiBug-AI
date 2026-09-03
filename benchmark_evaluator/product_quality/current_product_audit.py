from __future__ import annotations

"""Capture current Requirement/Test Intelligence outputs on frozen enterprise samples.

This is evaluation infrastructure, not product logic. All selected product outputs and
semantic snapshots are captured before external review anchors are loaded. Review
expectations never enter ingestion, enterprise understanding, Requirement Intelligence,
Test Intelligence, cross-product linkage, or Test Design.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from ai_test_asset_center.enterprise_knowledge_center._crud import (
    ingest_enterprise_knowledge_files,
)
from ai_test_asset_center.enterprise_knowledge_center.composition import (
    build_enterprise_business_knowledge_asset,
)
from ai_test_asset_center.product_intelligence_linkage import (
    compose_requirement_test_linkage,
)
from benchmark_evaluator.product_quality.semantic_funnel import (
    build_semantic_capture,
    semantic_funnel_for_anchor,
)
from products.requirement_intelligence import analyze_knowledge_asset
from products.test_intelligence import analyze_test_intelligence

AUDIT_SCHEMA = "qualibug.product-quality-audit-capture.v1"
REVIEW_SCHEMA = "qualibug.product-quality-review-worksheet.v1"
AUDIT_QUALITY_CLAIM = (
    "CURRENT_PRODUCT_CAPTURE_WITH_EXTERNAL_HUMAN_REVIEW_NOT_SELF_SCORED_MODEL_QUALITY"
)

SAMPLE_SPECS: dict[str, tuple[tuple[str, str], ...]] = {
    "object_source_conflict": (
        ("benchmark/multi_source_object_conflict/PRD_LEGACY.md", "prd"),
        ("benchmark/multi_source_object_conflict/PRD_CURRENT.md", "prd"),
    ),
    "benchmark_mall": (
        ("projects/benchmark_mall/input/PRD.md", "prd"),
        ("projects/benchmark_mall/input/API_SPEC.md", "api_document"),
        ("projects/benchmark_mall/input/BUSINESS_RULES.md", "business_rules"),
        ("projects/benchmark_mall/input/DB_SCHEMA.md", "database_schema"),
        ("projects/benchmark_mall/input/USER_ROLES.md", "roles"),
        ("projects/benchmark_mall/input/TEST_ACCOUNTS.md", "config"),
        ("projects/benchmark_mall/input/HISTORICAL_BUGS.md", "historical_bugs"),
    ),
    "warehouse_e": (
        ("projects/warehouse_e/input/BUSINESS_RULES.md", "business_rules"),
        ("projects/warehouse_e/input/DATA_DICTIONARY.md", "data_dictionary"),
        ("projects/warehouse_e/input/TEST_ACCOUNTS.md", "config"),
        ("projects/warehouse_e/input/openapi.yaml", "openapi"),
    ),
}


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _copy_sources(
    repo_root: Path,
    work_root: Path,
    source_specs: tuple[tuple[str, str], ...],
) -> tuple[list[Path], dict[str, str], list[dict[str, str]]]:
    paths: list[Path] = []
    hints: dict[str, str] = {}
    snapshot: list[dict[str, str]] = []
    for relative, source_type in source_specs:
        source = (repo_root / relative).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"audit_source_missing:{relative}")
        target = (work_root / relative).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        paths.append(target)
        hints[str(target)] = source_type
        snapshot.append(
            {
                "path": relative,
                "source_type": source_type,
                "sha256": _sha256(source),
            }
        )
    return paths, hints, snapshot


def _evidence_quotes(row: dict[str, Any]) -> list[str]:
    evidence = row.get("evidence")
    if not isinstance(evidence, list):
        return []
    return [
        str(item.get("quote") or "").strip()
        for item in evidence
        if isinstance(item, dict) and str(item.get("quote") or "").strip()
    ]


def _candidate_ids_for_anchor(
    anchor: dict[str, Any],
    requirement_analysis: dict[str, Any],
    test_analysis: dict[str, Any],
) -> dict[str, list[str]]:
    """Narrow review candidates by exact source text only; never score semantics."""

    quote = str(anchor.get("exact_quote") or "").strip()
    if not quote:
        return {
            "requirement_finding_ids": [],
            "test_obligation_ids": [],
            "test_design_ids": [],
        }

    findings = [
        dict(item)
        for item in requirement_analysis.get("findings", [])
        if isinstance(item, dict)
    ]
    obligations = [
        dict(item)
        for item in test_analysis.get("obligations", [])
        if isinstance(item, dict)
    ]
    designs = [
        dict(item)
        for item in test_analysis.get("test_designs", [])
        if isinstance(item, dict)
    ]

    def contains_exact_source_text(item: dict[str, Any]) -> bool:
        return any(quote in text for text in _evidence_quotes(item))

    return {
        "requirement_finding_ids": sorted(
            str(item.get("finding_id") or "")
            for item in findings
            if str(item.get("finding_id") or "") and contains_exact_source_text(item)
        ),
        "test_obligation_ids": sorted(
            str(item.get("obligation_id") or "")
            for item in obligations
            if str(item.get("obligation_id") or "") and contains_exact_source_text(item)
        ),
        "test_design_ids": sorted(
            str(item.get("design_id") or "")
            for item in designs
            if str(item.get("design_id") or "") and contains_exact_source_text(item)
        ),
    }


def _load_review_anchors(repo_root: Path) -> dict[str, Any]:
    path = (
        repo_root
        / "benchmark_evaluator"
        / "product_quality"
        / "fixtures"
        / "review_anchors.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _review_worksheet(
    sample_id: str,
    anchors_payload: dict[str, Any],
    requirement_analysis: dict[str, Any],
    test_analysis: dict[str, Any],
    semantic_capture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sample_anchors = [
        dict(item)
        for item in anchors_payload.get("anchors", [])
        if isinstance(item, dict) and str(item.get("sample_id") or "") == sample_id
    ]
    rows: list[dict[str, Any]] = []
    for anchor in sample_anchors:
        candidate_output_ids = _candidate_ids_for_anchor(
            anchor, requirement_analysis, test_analysis
        )
        rows.append(
            {
                **anchor,
                "candidate_output_ids": candidate_output_ids,
                "semantic_funnel": semantic_funnel_for_anchor(
                    anchor,
                    semantic_capture or {},
                    candidate_output_ids,
                ),
                "human_verdict": "PENDING_REVIEW",
                "human_notes": "",
            }
        )
    return {
        "schema": REVIEW_SCHEMA,
        "sample_id": sample_id,
        "review_anchor_count": len(rows),
        "review_status": "PENDING_HUMAN_REVIEW",
        "rows": rows,
    }


def _capture_product_sample(
    repo_root: Path,
    output_root: Path,
    sample_id: str,
    source_specs: tuple[tuple[str, str], ...],
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Run product capture and semantic snapshot with no review anchors loaded."""

    with tempfile.TemporaryDirectory(prefix=f"qualibug-audit-{sample_id}-") as temporary:
        work_root = Path(temporary).resolve()
        source_paths, hints, source_snapshot = _copy_sources(
            repo_root, work_root, source_specs
        )
        ingestion = ingest_enterprise_knowledge_files(
            sample_id,
            source_paths,
            root=work_root,
            actor={"name": "product_quality_audit", "role": "qa_lead"},
            source_type_hints=hints,
        )
        if not bool(ingestion.get("ok")) or str(
            ingestion.get("transaction_status") or ""
        ) != "COMMITTED":
            return (
                {
                    "sample_id": sample_id,
                    "status": "BLOCKED",
                    "reason_code": "SOURCE_INGESTION_NOT_COMMITTED",
                    "source_snapshot": source_snapshot,
                    "ingestion_receipt": ingestion,
                },
                None,
                None,
                None,
            )

        asset = build_enterprise_business_knowledge_asset(
            sample_id,
            work_root,
            {"probe_limit": 0},
        )

        # Capture the semantic funnel input before product review truth exists.
        semantic_capture = build_semantic_capture(asset)
        requirement_analysis = analyze_knowledge_asset(asset)
        test_analysis = compose_requirement_test_linkage(
            requirement_analysis,
            analyze_test_intelligence(asset),
        )

        sample_output = output_root / sample_id
        _json_write(sample_output / "semantic_capture.json", semantic_capture)
        _json_write(sample_output / "requirement_analysis.json", requirement_analysis)
        _json_write(sample_output / "test_intelligence_analysis.json", test_analysis)

        requirement_summary = requirement_analysis.get("summary") or {}
        test_summary = test_analysis.get("summary") or {}
        result = {
            "sample_id": sample_id,
            "status": "CAPTURED",
            "measurement_status": "PENDING_HUMAN_REVIEW",
            "source_snapshot": source_snapshot,
            "business_fact_count": semantic_capture.get("fact_count", 0),
            "business_behavior_count": semantic_capture.get("behavior_count", 0),
            "requirement_readiness": (
                requirement_analysis.get("readiness") or {}
            ).get("status"),
            "requirement_finding_count": requirement_summary.get("finding_count", 0),
            "test_obligation_count": test_summary.get("obligation_count", 0),
            "test_design_count": test_summary.get("test_design_count", 0),
            "linked_requirement_finding_count": test_summary.get(
                "linked_requirement_finding_count", 0
            ),
        }
        return result, requirement_analysis, test_analysis, semantic_capture


def capture_current_product_audit(
    *,
    root: str | Path,
    output_dir: str | Path | None = None,
    samples: list[str] | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    selected = samples or list(SAMPLE_SPECS)
    unknown = sorted(set(selected) - set(SAMPLE_SPECS))
    if unknown:
        raise ValueError("unknown_audit_samples:" + ",".join(unknown))

    output_root = (
        Path(output_dir).resolve()
        if output_dir is not None
        else repo_root / "evaluator_outputs" / "product_quality" / "current"
    )

    # Phase 1: capture every product/semantic output with no review truth loaded.
    captures = [
        _capture_product_sample(
            repo_root,
            output_root,
            sample_id,
            SAMPLE_SPECS[sample_id],
        )
        for sample_id in selected
    ]

    # Phase 2: only after every selected capture, load independent human anchors.
    anchors_payload = _load_review_anchors(repo_root)
    sample_results: list[dict[str, Any]] = []
    for result, requirement_analysis, test_analysis, semantic_capture in captures:
        sample_id = str(result.get("sample_id") or "")
        if (
            requirement_analysis is not None
            and test_analysis is not None
            and semantic_capture is not None
        ):
            worksheet = _review_worksheet(
                sample_id,
                anchors_payload,
                requirement_analysis,
                test_analysis,
                semantic_capture,
            )
            _json_write(
                output_root / sample_id / "review_worksheet.json",
                worksheet,
            )
            result = {**result, "review_anchor_count": worksheet["review_anchor_count"]}
        sample_results.append(result)

    status = (
        "CAPTURED"
        if all(item.get("status") == "CAPTURED" for item in sample_results)
        else "BLOCKED"
    )
    summary = {
        "schema": AUDIT_SCHEMA,
        "status": status,
        "quality_claim": AUDIT_QUALITY_CLAIM,
        "product_feature_freeze": True,
        "human_scoring_required": True,
        "self_scored_model_quality": False,
        "review_truth_loaded_after_all_product_capture": True,
        "semantic_funnel_diagnostic_only": True,
        "sample_count": len(sample_results),
        "samples": sample_results,
    }
    _json_write(output_root / "audit_summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture current Requirement/Test Intelligence outputs for human "
            "product-quality review."
        )
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    parser.add_argument("--sample", action="append", choices=sorted(SAMPLE_SPECS))
    args = parser.parse_args(argv)
    try:
        summary = capture_current_product_audit(
            root=args.root,
            output_dir=args.output,
            samples=args.sample,
        )
    except (OSError, ValueError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "schema": AUDIT_SCHEMA,
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
    return 0 if summary.get("status") == "CAPTURED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
