"""Evaluator-private join of GT bugs onto SPEC first-loss stages.

Product ledgers remain ground-truth free. This module may load hidden GT only
inside the evaluator boundary and must never be imported by discovery runtime.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOIN_SCHEMA = "qualibug.evaluator-fact-first-loss-ledger.v1"

# Map legacy stage-loss matrix labels → SPEC §9.2 stages.
_STAGE_LOSS_TO_SPEC = {
    "hypothesis_generation": "HYPOTHESIS_NOT_GENERATED",
    "endpoint_binding": "OPERATION_BINDING_BLOCKED",
    "trace_observability": "EFFECT_NOT_OBSERVED",
    "selection": "FACT_NOT_SELECTED",
    "execution": "EXECUTION_BLOCKED",
    "oracle_evaluation": "ORACLE_INDETERMINATE",
    "oracle_resolution": "ORACLE_INDETERMINATE",
    "delivery_gate": "DELIVERY_FILTERED",
    "delivered": "TRUE_POSITIVE",
    "diagnostic_ambiguity": "EVALUATOR_NOT_MATCHED",
}

_SPEC_STAGES = frozenset(_STAGE_LOSS_TO_SPEC.values()) | {
    "SOURCE_NOT_INGESTED",
    "FACT_NOT_EXTRACTED",
    "FACT_CONFLICTED",
    "OBLIGATION_NOT_GENERATED",
    "ABSTRACT_EXPERIMENT_NOT_COMPILED",
    "MATERIALIZATION_BLOCKED",
    "FIXTURE_BLOCKED",
    "ACTOR_BLOCKED",
    "PRECONDITION_BLOCKED",
    "OBSERVER_BLOCKED",
    "CLEANUP_BLOCKED",
    "EXECUTION_FAILED",
    "FINDING_FILTERED",
    "FALSE_POSITIVE",
    "NO_LOSS_OBSERVED",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def map_stage_loss_to_spec(stage: str) -> str:
    text = _text(stage)
    if text in _SPEC_STAGES:
        return text
    mapped = _STAGE_LOSS_TO_SPEC.get(text)
    if mapped:
        return mapped
    return "EVALUATOR_NOT_MATCHED"


def build_evaluator_fact_first_loss_ledger(
    *,
    stage_loss_matrix: dict[str, Any] | None,
    product_fact_first_loss_ledger: dict[str, Any] | None = None,
    matched_bug_ids: list[str] | None = None,
    false_positive_count: int | None = None,
    campaign_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Join stage-loss diagnostics into SPEC first-loss rows for every GT bug.

    ``product_fact_first_loss_ledger`` is optional enrichment only; GT identity
    always comes from the evaluator-private stage-loss matrix.
    """

    matrix = _dict(stage_loss_matrix)
    product = _dict(product_fact_first_loss_ledger)
    delivered = {_text(item) for item in _list(matched_bug_ids) if _text(item)}
    product_by_stage = Counter(
        _text(row.get("first_loss_stage"))
        for row in _list(product.get("items"))
        if isinstance(row, dict)
    )

    rows: list[dict[str, Any]] = []
    for bug in _list(matrix.get("bugs")):
        if not isinstance(bug, dict):
            continue
        bug_id = _text(bug.get("bug_id") or bug.get("id"))
        if not bug_id:
            continue
        legacy_stage = _text(bug.get("first_loss_stage"))
        spec_stage = map_stage_loss_to_spec(legacy_stage)
        if bug_id in delivered:
            spec_stage = "TRUE_POSITIVE"
        rows.append(
            {
                "ground_truth_ref": bug_id,
                "fact_ref": "",
                "hypothesis_ref": "",
                "obligation_ref": "",
                "experiment_ref": "",
                "execution_ref": "",
                "observation_refs": [],
                "oracle_ref": "",
                "finding_ref": "",
                "legacy_stage_loss": legacy_stage,
                "first_loss_stage": spec_stage,
                "first_loss_reason": (
                    "matched_formal_deliverable"
                    if spec_stage == "TRUE_POSITIVE"
                    else legacy_stage or spec_stage
                ),
                "blocker_owner": (
                    "evaluator"
                    if spec_stage in {"TRUE_POSITIVE", "FALSE_POSITIVE", "EVALUATOR_NOT_MATCHED"}
                    else "discovery_mainline"
                ),
                "diagnostic_status": _text(bug.get("diagnostic_status")),
                "match_basis": _text(bug.get("match_basis")),
                "candidate_ids": list(_list(bug.get("candidate_ids")))[:8],
                "trace_ids": list(_list(bug.get("trace_ids")))[:8],
            }
        )

    stage_counts = dict(Counter(_text(row.get("first_loss_stage")) for row in rows))
    issues: list[str] = []
    expected = int(matrix.get("ground_truth_bug_count") or len(rows))
    if expected != len(rows):
        issues.append(
            f"gt_row_count_mismatch:expected={expected};observed={len(rows)}"
        )
    if any(not _text(row.get("first_loss_stage")) for row in rows):
        issues.append("missing_first_loss_stage")
    if sum(stage_counts.values()) != len(rows):
        issues.append("stage_count_not_conserved")

    payload = {
        "schema_version": JOIN_SCHEMA,
        "campaign_id": _text(campaign_id),
        "run_id": _text(run_id),
        "ground_truth_bug_count": len(rows),
        "row_count": len(rows),
        "stage_counts": stage_counts,
        "product_fact_stage_counts": dict(product_by_stage),
        "false_positive_count": (
            int(false_positive_count)
            if false_positive_count is not None
            else None
        ),
        "conservation": {
            "status": "PASS" if not issues else "FAILED",
            "issues": issues,
            "every_gt_bug_has_first_loss_stage": all(
                bool(_text(row.get("first_loss_stage"))) for row in rows
            ),
        },
        "ground_truth_joined": True,
        "scoring_contract": "diagnostic_only_never_changes_tp_fp_fn",
        "items": rows,
        "created_at": _now_iso(),
    }
    fingerprint_material = json.dumps(
        {
            "items": [
                {
                    "ground_truth_ref": row.get("ground_truth_ref"),
                    "first_loss_stage": row.get("first_loss_stage"),
                }
                for row in rows
            ]
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["ledger_fingerprint"] = hashlib.sha256(
        fingerprint_material.encode("utf-8")
    ).hexdigest()[:32]
    return payload


def render_evaluator_first_loss_summary(ledger: dict[str, Any]) -> str:
    lines = [
        "# Evaluator Fact First-loss Summary",
        "",
        f"- schema_version: {ledger.get('schema_version')}",
        f"- campaign_id: {ledger.get('campaign_id') or 'NOT_BOUND'}",
        f"- run_id: {ledger.get('run_id') or 'NOT_BOUND'}",
        f"- ground_truth_bug_count: {ledger.get('ground_truth_bug_count')}",
        f"- row_count: {ledger.get('row_count')}",
        f"- ledger_fingerprint: {ledger.get('ledger_fingerprint')}",
        "",
        "## Stage counts",
    ]
    for key, value in sorted(dict(_dict(ledger.get("stage_counts"))).items()):
        lines.append(f"- {key}: {value}")
    conservation = _dict(ledger.get("conservation"))
    lines.extend(
        [
            "",
            "## Conservation",
            f"- status: {conservation.get('status')}",
            f"- every_gt_bug_has_first_loss_stage: "
            f"{conservation.get('every_gt_bug_has_first_loss_stage')}",
        ]
    )
    for issue in _list(conservation.get("issues")):
        lines.append(f"- issue: {issue}")
    lines.append("")
    return "\n".join(lines)


def write_evaluator_first_loss_files(
    ledger: dict[str, Any],
    output_dir: Path | str,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "first_loss_ledger.json"
    md_path = target / "first_loss_summary.md"
    json_path.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_evaluator_first_loss_summary(ledger), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


__all__ = [
    "JOIN_SCHEMA",
    "map_stage_loss_to_spec",
    "build_evaluator_fact_first_loss_ledger",
    "render_evaluator_first_loss_summary",
    "write_evaluator_first_loss_files",
]
