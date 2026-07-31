"""Reconcile typed implicit-rule promotions with their existing source-rule identity.

The source parser and the typed fact compiler may describe the same rule at different
semantic depths. The parser owns the original source rule identity and exact source
relationships; the typed candidate authority owns logical form, operands, counterexample
and runtime assertion semantics. This stage runs immediately after candidate projection
and before Enterprise Understanding Model or lifecycle construction. It combines those
two views into one rule-library row and refreshes exact bindings for every current
implicit rule through the existing relationship authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ._candidate_validation import promote_validated_candidates
from ._linking import _authoritative_rule_to_interface_edges

SCHEMA_VERSION = "qualibug.implicit-rule-identity-reconciliation.v2"
_DERIVATION = "implicit_rule_entailment"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(
        json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(part, (dict, list, tuple, set))
        else _text(part)
        for part in parts
        if part not in (None, "", [], {}, ())
    )
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _dedupe_by_id(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        identity = _text(row.get(field))
        if not identity:
            identity = _stable_id(field, row)
            row[field] = identity
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _implicit_rules(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(asset.get("rule_library"))
        if isinstance(row, dict) and _text(row.get("derivation")) == _DERIVATION
    ]


def _validated_upgrade_candidates(asset: dict[str, Any]) -> list[dict[str, Any]]:
    receipt = _dict(asset.get("implicit_rule_candidate_validation_receipt"))
    result: list[dict[str, Any]] = []
    for row in _list(receipt.get("validated")):
        if not isinstance(row, dict):
            continue
        target = _dict(row.get("authority_upgrade_target"))
        if target.get("match_kind") != "SOURCE_RULE_TYPED_SEMANTIC_UPGRADE":
            continue
        if not _text(target.get("rule_id")):
            continue
        result.append(dict(row))
    return result


def _source_rule_origin(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(rule.get(key))
        for key in (
            "rule_id",
            "source_id",
            "source_type",
            "source_locator",
            "statement",
            "expected",
            "rule_type",
            "risk_type",
            "severity",
            "semantic_frame",
            "tokens",
        )
        if rule.get(key) not in (None, "", [], {})
    }


def _merge_source_and_typed_rule(
    source_rule: dict[str, Any], typed_rule: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    target = _dict(candidate.get("authority_upgrade_target"))
    target_id = _text(target.get("rule_id"))
    merged = {**copy.deepcopy(source_rule), **copy.deepcopy(typed_rule)}
    merged["rule_id"] = target_id
    merged["derivation"] = _DERIVATION
    merged["source_rule_origin"] = _source_rule_origin(source_rule)
    merged["authority_upgrade_target"] = copy.deepcopy(target)
    merged["authority_upgrade_receipt"] = {
        "schema": SCHEMA_VERSION,
        "status": "MERGED_IN_PLACE",
        "target_rule_id": target_id,
        "candidate_id": candidate.get("candidate_id"),
        "source_statement_relation": target.get("source_statement_relation"),
        "source_rule_identity_preserved": True,
        "typed_semantics_replaced_prose_projection": True,
        "parallel_rule_row_created": False,
        "candidate_validation_authority_reused": True,
        "candidate_promotion_authority_reused": True,
    }
    semantic_contract = _dict(merged.get("semantic_contract"))
    semantic_contract["source_rule_identity_reconciliation"] = {
        "status": "MERGED_IN_PLACE",
        "target_rule_id": target_id,
        "source_rule_identity_preserved": True,
        "typed_runtime_semantics_authoritative": True,
    }
    merged["semantic_contract"] = semantic_contract
    return merged


def _merge_relationships(
    asset: dict[str, Any], rules: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    interfaces = [
        dict(row)
        for row in _list(asset.get("interfaces"))
        if isinstance(row, dict)
    ]
    exact_edges = _authoritative_rule_to_interface_edges(rules, interfaces)
    operation_refs: dict[str, list[str]] = {}
    for rule in rules:
        rule_id = _text(rule.get("rule_id"))
        exact_refs = {
            _text(edge.get("to"))
            for edge in exact_edges
            if _text(edge.get("from")) == rule_id and _text(edge.get("to"))
        }
        declared_refs = {
            _text(value)
            for value in _list(rule.get("operation_refs"))
            if _text(value)
        }
        refs = sorted(exact_refs | declared_refs)
        operation_refs[rule_id] = refs
        if refs:
            rule["operation_refs"] = refs
            rule["downstream_binding_status"] = (
                "READY_AUTHORITATIVE_OPERATION_BOUND"
            )
    existing = [
        dict(row)
        for row in _list(asset.get("relationships"))
        if isinstance(row, dict)
    ]
    return _dedupe_by_id([*existing, *exact_edges], "edge_id"), operation_refs


def _replace_risks_and_oracles(
    asset: dict[str, Any], rules: list[dict[str, Any]], operation_refs: dict[str, list[str]]
) -> None:
    rule_ids = {
        _text(rule.get("rule_id"))
        for rule in rules
        if _text(rule.get("rule_id"))
    }
    risks = [
        dict(row)
        for row in _list(asset.get("risk_domains"))
        if isinstance(row, dict)
        and _text(row.get("source_rule_id")) not in rule_ids
        and _text(row.get("risk_id")) not in {
            f"risk:{rule_id}" for rule_id in rule_ids
        }
    ]
    oracles = [
        dict(row)
        for row in _list(asset.get("oracle_library"))
        if isinstance(row, dict)
        and _text(row.get("rule_id")) not in rule_ids
        and _text(row.get("oracle_id")) not in {
            f"oracle:{rule_id}" for rule_id in rule_ids
        }
    ]
    for rule in rules:
        rule_id = _text(rule.get("rule_id"))
        risk_type = _text(rule.get("risk_type") or "business_logic")
        risks.append(
            {
                "risk_id": f"risk:{rule_id}",
                "source_rule_id": rule_id,
                "source_id": rule.get("source_id"),
                "risk_type": risk_type,
                "severity": rule.get("severity") or "P1",
                "title": f"隐式规则验证：{_text(rule.get('statement'))}",
                "expected": rule.get("statement"),
                "evidence": list(rule.get("source_ids") or []),
                "derivation": _DERIVATION,
                "candidate_id": rule.get("candidate_id"),
            }
        )
        oracles.append(
            {
                "oracle_id": f"oracle:{rule_id}",
                "rule_id": rule_id,
                "family": risk_type,
                "assertion": rule.get("statement"),
                "linked_interfaces": list(operation_refs.get(rule_id) or []),
                "linked_tables": list(rule.get("table_refs") or []),
                "execution_policy": "read_only_evidence_or_governed_sandbox",
                "evidence_requirements": list(
                    rule.get("observation_requirements") or []
                ),
                "derivation": _DERIVATION,
                "candidate_id": rule.get("candidate_id"),
            }
        )
    asset["risk_domains"] = _dedupe_by_id(risks, "risk_id")
    asset["oracle_library"] = _dedupe_by_id(oracles, "oracle_id")


def _refresh_current_implicit_artifacts(asset: dict[str, Any]) -> int:
    rules = _implicit_rules(asset)
    relationships, operation_refs = _merge_relationships(asset, rules)
    asset["relationships"] = relationships
    _replace_risks_and_oracles(asset, rules, operation_refs)
    return sum(bool(operation_refs.get(_text(rule.get("rule_id")))) for rule in rules)


def reconcile_implicit_rule_identities(asset: dict[str, Any]) -> dict[str, Any]:
    """Merge typed upgrades and refresh exact bindings for current implicit rules."""

    candidates = _validated_upgrade_candidates(asset)
    promoted = promote_validated_candidates(candidates, kind="rule")
    candidate_by_id = {
        _text(row.get("candidate_id")): row
        for row in candidates
        if _text(row.get("candidate_id"))
    }
    rules = [
        dict(row)
        for row in _list(asset.get("rule_library"))
        if isinstance(row, dict)
    ]
    by_id = {
        _text(row.get("rule_id")): row
        for row in rules
        if _text(row.get("rule_id"))
    }
    merged_by_id: dict[str, dict[str, Any]] = {}
    missing_targets: list[dict[str, Any]] = []
    for typed_rule in promoted:
        candidate = candidate_by_id.get(_text(typed_rule.get("candidate_id"))) or {}
        target = _dict(candidate.get("authority_upgrade_target"))
        target_id = _text(target.get("rule_id"))
        source_rule = by_id.get(target_id)
        if not target_id or source_rule is None:
            missing_targets.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "target_rule_id": target_id,
                    "reason_code": "SOURCE_RULE_UPGRADE_TARGET_MISSING",
                }
            )
            continue
        merged_by_id[target_id] = _merge_source_and_typed_rule(
            source_rule, typed_rule, candidate
        )

    if merged_by_id:
        reconciled_rules: list[dict[str, Any]] = []
        for rule in rules:
            rule_id = _text(rule.get("rule_id"))
            reconciled_rules.append(merged_by_id.get(rule_id, rule))
        asset["rule_library"] = _dedupe_by_id(reconciled_rules, "rule_id")

    exact_binding_refreshed_rule_count = _refresh_current_implicit_artifacts(asset)

    receipt = _dict(asset.get("implicit_rule_candidate_validation_receipt"))
    for row in _list(receipt.get("validated")):
        if not isinstance(row, dict):
            continue
        target = _dict(row.get("authority_upgrade_target"))
        target_id = _text(target.get("rule_id"))
        if target_id in merged_by_id:
            row["promoted_rule_id"] = target_id
            row["identity_reconciliation_status"] = "MERGED_IN_PLACE"
    gate = _dict(asset.get("implicit_rule_projection_gate"))
    gate["source_rule_semantic_upgrade_count"] = len(merged_by_id)
    gate["exact_binding_refreshed_rule_count"] = (
        exact_binding_refreshed_rule_count
    )
    gate["source_rule_identity_reconciliation_status"] = (
        "PASS" if not missing_targets else "BLOCKED_UPGRADE_TARGET_MISSING"
    )
    if missing_targets:
        gate["status"] = "BLOCKED_UPGRADE_TARGET_MISSING"
        gate["entry_allowed"] = False
    asset["implicit_rule_projection_gate"] = gate

    status = (
        "BLOCKED"
        if missing_targets
        else "PASS"
        if merged_by_id
        else "NO_TYPED_SOURCE_RULE_UPGRADES"
    )
    asset["implicit_rule_identity_reconciliation_receipt"] = {
        "schema": SCHEMA_VERSION,
        "status": status,
        "candidate_count": len(candidates),
        "promoted_candidate_count": len(promoted),
        "merged_rule_count": len(merged_by_id),
        "merged_rule_ids": sorted(merged_by_id),
        "missing_targets": missing_targets,
        "exact_binding_refreshed_rule_count": exact_binding_refreshed_rule_count,
        "source_rule_identity_preserved": True,
        "typed_semantics_replaced_prose_projection": bool(merged_by_id),
        "parallel_rule_row_created": False,
        "candidate_validation_authority_reused": True,
        "candidate_promotion_authority_reused": True,
        "relationship_authority_reused": (
            "_authoritative_rule_to_interface_edges"
        ),
    }
    summary = _dict(asset.get("summary"))
    summary["implicit_rule_source_semantic_upgrade_count"] = len(merged_by_id)
    summary["implicit_rule_exact_binding_refreshed_rule_count"] = (
        exact_binding_refreshed_rule_count
    )
    summary["implicit_rule_identity_reconciliation_status"] = status
    asset["summary"] = summary
    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "implicit_rule_source_identity_reconciled_before_behavior_ir": True,
            "implicit_rule_semantic_upgrade_reuses_source_rule_id": True,
            "implicit_rule_semantic_upgrade_creates_parallel_rule": False,
            "implicit_rule_semantic_upgrade_reuses_candidate_promoter": True,
            "implicit_rule_exact_binding_reuses_existing_authority": True,
            "implicit_rule_exact_binding_refreshes_on_every_projection": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "SCHEMA_VERSION",
    "reconcile_implicit_rule_identities",
]
