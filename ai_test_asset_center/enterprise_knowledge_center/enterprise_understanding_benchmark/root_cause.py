"""Earliest visible root-cause analysis for enterprise-understanding misses."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

ROOT_CAUSE_SCHEMA = "qualibug.enterprise-understanding-miss-root-cause.v1"
ROOT_CAUSE_PRIORITY = (
    "SOURCE_NOT_PARSED",
    "FACT_NOT_EXTRACTED",
    "OBJECT_NOT_RESOLVED",
    "ACTOR_NOT_RESOLVED",
    "OPERATION_NOT_RESOLVED",
    "CONDITION_NOT_PARSED",
    "STATE_TRANSITION_MISSING",
    "RULE_NOT_COMPILED",
    "BEHAVIOR_NOT_CONFIRMED",
    "IMPLEMENTATION_NOT_BOUND",
    "SCENARIO_NOT_GENERATED",
    "ORACLE_NOT_AVAILABLE",
    "EXECUTION_NOT_REACHED",
)
_PRIORITY = {name: index for index, name in enumerate(ROOT_CAUSE_PRIORITY)}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _model(asset: dict[str, Any]) -> dict[str, Any]:
    model = asset.get("enterprise_understanding_model")
    return model if isinstance(model, dict) else asset


def _source_tokens_from_evidence(value: Any) -> set[str]:
    result: set[str] = set()
    for row in _rows(value):
        for field in ("source_id", "source_locator", "asset_ref", "filename"):
            item = _text(row.get(field))
            if item:
                result.add(item)
    return result


def _asset_source_tokens(asset: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for key in ("sources", "source_manifest", "knowledge_sources"):
        for row in _rows(asset.get(key)):
            for field in ("source_id", "id", "filename", "path", "source_locator"):
                value = _text(row.get(field))
                if value:
                    result.add(value)
    structures = asset.get("document_structure_assets")
    if isinstance(structures, dict):
        for row in _rows(structures.get("items")):
            for field in ("source_id", "filename"):
                value = _text(row.get(field))
                if value:
                    result.add(value)
    return result


def _fact_source_tokens(asset: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    model = _model(asset)
    containers = [asset, model]
    for container in containers:
        for key in (
            "business_facts",
            "accepted_business_facts",
            "enterprise_business_facts",
            "rules",
            "business_behaviors",
        ):
            for row in _rows(container.get(key)):
                result.update(_source_tokens_from_evidence(row.get("evidence")))
                for span in _rows(row.get("source_spans")):
                    for field in ("source_id", "source_locator", "locator"):
                        value = _text(span.get(field))
                        if value:
                            result.add(value)
                source_id = _text(row.get("source_id"))
                if source_id:
                    result.add(source_id)
    comprehension = asset.get("chinese_business_comprehension")
    if isinstance(comprehension, dict):
        for key in ("accepted_facts", "candidate_facts", "facts"):
            for row in _rows(comprehension.get(key)):
                result.update(_source_tokens_from_evidence(row.get("evidence")))
                source_id = _text(row.get("source_id"))
                if source_id:
                    result.add(source_id)
    return result


def _ground_truth_index(ground_truth: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in ground_truth.values():
        for row in _rows(value):
            identity = _text(row.get("ground_truth_id"))
            if identity:
                result[identity] = row
    return result


def _alignment_index(alignment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("ground_truth_id")): row
        for row in _rows(alignment.get("alignments"))
        if _text(row.get("ground_truth_id"))
    }


def _source_stage_cause(
    ground_truth: dict[str, Any], source_tokens: set[str], fact_tokens: set[str]
) -> str:
    expected = {
        _text(value)
        for field in ("source_refs", "source_locators")
        for value in (
            ground_truth.get(field) if isinstance(ground_truth.get(field), list) else []
        )
        if _text(value)
    }
    if expected and not expected & source_tokens:
        return "SOURCE_NOT_PARSED"
    if expected and not expected & fact_tokens:
        return "FACT_NOT_EXTRACTED"
    return ""


def _semantic_cause(alignment: dict[str, Any]) -> str:
    collection = _text(alignment.get("collection"))
    status = _text(alignment.get("alignment_status"))
    details = alignment.get("details") if isinstance(alignment.get("details"), dict) else {}
    missing_slots = set(details.get("missing_or_wrong_slots", []))
    if collection == "business_objects":
        return "OBJECT_NOT_RESOLVED"
    if collection == "actors":
        return "ACTOR_NOT_RESOLVED"
    if collection == "operations":
        return "OBJECT_NOT_RESOLVED" if status == "WRONG_BINDING" else "OPERATION_NOT_RESOLVED"
    if collection == "object_relations":
        return "OBJECT_NOT_RESOLVED" if status == "WRONG_BINDING" else "FACT_NOT_EXTRACTED"
    if collection in {"lifecycles", "state_transitions"}:
        return "STATE_TRANSITION_MISSING"
    if collection in {"business_rules", "business_behaviors"}:
        if status == "WRONG_BINDING":
            return "OBJECT_NOT_RESOLVED"
        if "actor_refs" in missing_slots:
            return "ACTOR_NOT_RESOLVED"
        if "preconditions" in missing_slots or "permission_decision" in missing_slots:
            return "CONDITION_NOT_PARSED"
        if "state_effects" in missing_slots:
            return "STATE_TRANSITION_MISSING"
        if details.get("candidate_not_confirmed"):
            return "BEHAVIOR_NOT_CONFIRMED"
        return "RULE_NOT_COMPILED" if collection == "business_rules" else "BEHAVIOR_NOT_CONFIRMED"
    if collection == "conflicts":
        return "RULE_NOT_COMPILED"
    if collection == "expected_unknowns":
        return "BEHAVIOR_NOT_CONFIRMED"
    return "FACT_NOT_EXTRACTED"


def _earliest(causes: list[str]) -> str:
    filtered = [cause for cause in causes if cause]
    return min(filtered, key=lambda value: _PRIORITY.get(value, 999)) if filtered else ""


def analyse_miss_root_causes(
    ground_truth: dict[str, Any], asset: dict[str, Any], alignment: dict[str, Any]
) -> dict[str, Any]:
    ground_truth_by_id = _ground_truth_index(ground_truth)
    alignment_by_id = _alignment_index(alignment)
    source_tokens = _asset_source_tokens(asset)
    fact_tokens = _fact_source_tokens(asset)
    misses: list[dict[str, Any]] = []
    cause_by_ground_truth_id: dict[str, str] = {}

    for identity, row in alignment_by_id.items():
        status = _text(row.get("alignment_status"))
        if status in {"EXACT_MATCH", "UNKNOWN_CORRECTLY_EXPOSED"}:
            continue
        expected = ground_truth_by_id.get(identity, {})
        source_cause = _source_stage_cause(expected, source_tokens, fact_tokens)
        semantic_cause = _semantic_cause(row)
        root_cause = _earliest([source_cause, semantic_cause])
        cause_by_ground_truth_id[identity] = root_cause
        misses.append(
            {
                "ground_truth_id": identity,
                "collection": row.get("collection"),
                "criticality": row.get("criticality"),
                "alignment_status": status,
                "root_cause": root_cause,
                "source_stage_cause": source_cause,
                "semantic_stage_cause": semantic_cause,
                "candidate_id": row.get("candidate_id"),
                "details": row.get("details") or {},
            }
        )

    bug_rows: list[dict[str, Any]] = []
    for dependency in _rows(ground_truth.get("bug_dependencies")):
        required_ids = [
            _text(value)
            for value in dependency.get("required_ground_truth_ids", [])
            if _text(value)
        ]
        dependency_causes = [
            cause_by_ground_truth_id.get(identity, "")
            for identity in required_ids
            if cause_by_ground_truth_id.get(identity)
        ]
        bug_rows.append(
            {
                "bug_id": dependency.get("bug_id"),
                "required_ground_truth_ids": required_ids,
                "missing_dependency_ids": [
                    identity for identity in required_ids if identity in cause_by_ground_truth_id
                ],
                "dependency_root_causes": sorted(set(dependency_causes), key=lambda value: _PRIORITY.get(value, 999)),
                "earliest_root_cause": _earliest(dependency_causes),
                "understanding_chain_complete": not bool(dependency_causes),
            }
        )

    distribution = Counter(row.get("root_cause") for row in misses if row.get("root_cause"))
    weighted_distribution: dict[str, float] = defaultdict(float)
    weights = {"P0": 5.0, "P1": 3.0, "P2": 1.0, "P3": 0.5}
    for row in misses:
        weighted_distribution[_text(row.get("root_cause"))] += weights.get(
            _text(row.get("criticality") or "P2").upper(), 1.0
        )
    ranked = sorted(
        (
            {
                "root_cause": cause,
                "miss_count": count,
                "criticality_weighted_impact": round(weighted_distribution[cause], 2),
            }
            for cause, count in distribution.items()
        ),
        key=lambda row: (-row["criticality_weighted_impact"], _PRIORITY.get(row["root_cause"], 999)),
    )
    return {
        "schema": ROOT_CAUSE_SCHEMA,
        "project_id": ground_truth.get("project_id"),
        "misses": misses,
        "root_cause_distribution": dict(distribution),
        "ranked_root_causes": ranked,
        "highest_impact_root_cause": ranked[0]["root_cause"] if ranked else "",
        "bug_dependency_root_causes": bug_rows,
        "repair_policy": "FIX_THE_EARLIEST_EXISTING_MAINLINE_MODULE_NOT_A_DOWNSTREAM_PATCH",
        "model_writeback_allowed": False,
    }


__all__ = [
    "ROOT_CAUSE_SCHEMA",
    "ROOT_CAUSE_PRIORITY",
    "analyse_miss_root_causes",
]
