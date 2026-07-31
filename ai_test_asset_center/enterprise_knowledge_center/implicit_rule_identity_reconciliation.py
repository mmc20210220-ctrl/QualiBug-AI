"""Reconcile typed implicit-rule promotions with one durable rule identity.

Canonical parser source IDs are content-version identities. Source occurrences already
carry stable ``source_ref`` values across those versions. This stage combines the existing
source-rule row with typed semantics and, when occurrence identity is available, derives
one durable rule authority ID from stable source refs plus normalized typed semantics.
It does not create a second source registry, validator, promoter or linker.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ._candidate_validation import promote_validated_candidates
from ._linking import _authoritative_rule_to_interface_edges

SCHEMA_VERSION = "qualibug.implicit-rule-identity-reconciliation.v3"
_DERIVATION = "implicit_rule_entailment"
_IDENTITY_IGNORED_KEYS = frozenset(
    {
        "raw",
        "quote",
        "verbatim_quote",
        "source_locator",
        "locator",
        "document_block_id",
        "quote_hash",
        "fact_ref",
        "candidate_id",
        "rule_id",
        "promotion_receipt_id",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(
        _canonical(part)
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


def _source_ref_index(asset: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    inventories = [
        *_list(asset.get("source_inventory")),
        *_list(asset.get("canonical_source_inventory")),
    ]
    for raw in inventories:
        if not isinstance(raw, dict):
            continue
        identities = {
            _text(raw.get("source_id")),
            _text(raw.get("canonical_source_id")),
        }
        refs = {
            _text(value)
            for value in [
                *_list(raw.get("source_refs")),
                raw.get("source_ref"),
                raw.get("external_ref"),
                raw.get("source_origin_ref"),
            ]
            if _text(value)
        }
        if not refs:
            continue
        normalized = tuple(sorted(refs))
        for identity in identities:
            if identity:
                result[identity] = normalized
    return result


def _candidate_source_ids(candidate: dict[str, Any]) -> set[str]:
    return {
        value
        for value in [
            *[_text(item) for item in _list(candidate.get("supporting_source_ids"))],
            *[
                _text(item.get("source_id"))
                for item in _list(candidate.get("source_refs"))
                if isinstance(item, dict)
            ],
        ]
        if value
    }


def _stable_source_refs(
    asset: dict[str, Any], candidate: dict[str, Any], source_rule: dict[str, Any]
) -> tuple[str, ...]:
    index = _source_ref_index(asset)
    source_ids = {
        _text(source_rule.get("source_id")),
        *[_text(value) for value in _list(source_rule.get("source_ids"))],
        *_candidate_source_ids(candidate),
    }
    refs = {
        ref
        for source_id in source_ids
        if source_id
        for ref in index.get(source_id, ())
        if ref
    }
    return tuple(sorted(refs))


def _identity_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _text(key): _identity_value(raw)
            for key, raw in sorted(value.items(), key=lambda item: _text(item[0]))
            if _text(key) not in _IDENTITY_IGNORED_KEYS
            and raw not in (None, "", [], {})
        }
    if isinstance(value, list):
        normalized = [_identity_value(item) for item in value]
        return sorted(normalized, key=_canonical)
    if isinstance(value, tuple):
        return _identity_value(list(value))
    return value


def _typed_semantic_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    structured = _dict(candidate.get("structured_expression"))
    consequent = _dict(candidate.get("consequent")) or _dict(
        structured.get("consequent")
    )
    antecedents = _list(candidate.get("antecedents")) or _list(
        structured.get("antecedents")
    )
    return _identity_value(
        {
            "logical_form": _text(
                candidate.get("logical_form") or structured.get("logical_form")
            ).upper(),
            "antecedents": antecedents,
            "consequent": consequent,
            "subject_refs": list(candidate.get("subject_refs") or []),
            "actor_refs": list(candidate.get("actor_refs") or []),
            "scope": dict(candidate.get("scope") or structured.get("scope") or {}),
            "exceptions": list(
                candidate.get("exceptions") or structured.get("exceptions") or []
            ),
        }
    )


def _authority_rule_identity(
    asset: dict[str, Any], candidate: dict[str, Any], source_rule: dict[str, Any]
) -> tuple[str, tuple[str, ...], str]:
    source_rule_id = _text(source_rule.get("rule_id"))
    stable_refs = _stable_source_refs(asset, candidate, source_rule)
    if not stable_refs:
        return source_rule_id, (), "SOURCE_RULE_ID_FALLBACK"
    payload = {
        "stable_source_refs": list(stable_refs),
        "typed_semantics": _typed_semantic_identity(candidate),
    }
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:24]
    return f"implicit_rule:{digest}", stable_refs, "SOURCE_OCCURRENCE_REF_TYPED_SEMANTICS"


def _merge_source_and_typed_rule(
    source_rule: dict[str, Any],
    typed_rule: dict[str, Any],
    candidate: dict[str, Any],
    *,
    authority_rule_id: str,
    stable_source_refs: tuple[str, ...],
    identity_authority: str,
) -> dict[str, Any]:
    target = _dict(candidate.get("authority_upgrade_target"))
    source_rule_id = _text(target.get("rule_id"))
    merged = {**copy.deepcopy(source_rule), **copy.deepcopy(typed_rule)}
    merged["rule_id"] = authority_rule_id
    merged["derivation"] = _DERIVATION
    merged["source_rule_origin"] = _source_rule_origin(source_rule)
    merged["source_rule_id"] = source_rule_id
    merged["stable_source_refs"] = list(stable_source_refs)
    merged["rule_identity_authority"] = identity_authority
    merged["authority_upgrade_target"] = {
        **copy.deepcopy(target),
        "source_rule_id": source_rule_id,
        "authority_rule_id": authority_rule_id,
    }
    merged["authority_upgrade_receipt"] = {
        "schema": SCHEMA_VERSION,
        "status": "MERGED_IN_PLACE",
        "source_rule_id": source_rule_id,
        "authority_rule_id": authority_rule_id,
        "candidate_id": candidate.get("candidate_id"),
        "source_statement_relation": target.get("source_statement_relation"),
        "stable_source_refs": list(stable_source_refs),
        "rule_identity_authority": identity_authority,
        "source_rule_identity_retained_as_origin": True,
        "typed_semantics_replaced_prose_projection": True,
        "parallel_rule_row_created": False,
        "candidate_validation_authority_reused": True,
        "candidate_promotion_authority_reused": True,
    }
    semantic_contract = _dict(merged.get("semantic_contract"))
    semantic_contract["source_rule_identity_reconciliation"] = {
        "status": "MERGED_IN_PLACE",
        "source_rule_id": source_rule_id,
        "authority_rule_id": authority_rule_id,
        "stable_source_refs": list(stable_source_refs),
        "rule_identity_authority": identity_authority,
        "typed_runtime_semantics_authoritative": True,
    }
    merged["semantic_contract"] = semantic_contract
    return merged


def _merge_relationships(
    asset: dict[str, Any],
    rules: list[dict[str, Any]],
    *,
    replaced_rule_ids: set[str] | None = None,
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
    replaced = set(replaced_rule_ids or set())
    existing = [
        dict(row)
        for row in _list(asset.get("relationships"))
        if isinstance(row, dict)
        and _text(row.get("from") or row.get("from_ref")) not in replaced
    ]
    return _dedupe_by_id([*existing, *exact_edges], "edge_id"), operation_refs


def _replace_risks_and_oracles(
    asset: dict[str, Any],
    rules: list[dict[str, Any]],
    operation_refs: dict[str, list[str]],
    *,
    replaced_rule_ids: set[str] | None = None,
) -> None:
    rule_ids = {
        _text(rule.get("rule_id"))
        for rule in rules
        if _text(rule.get("rule_id"))
    }
    affected = rule_ids | set(replaced_rule_ids or set())
    risks = [
        dict(row)
        for row in _list(asset.get("risk_domains"))
        if isinstance(row, dict)
        and _text(row.get("source_rule_id")) not in affected
        and _text(row.get("risk_id")) not in {
            f"risk:{rule_id}" for rule_id in affected
        }
    ]
    oracles = [
        dict(row)
        for row in _list(asset.get("oracle_library"))
        if isinstance(row, dict)
        and _text(row.get("rule_id")) not in affected
        and _text(row.get("oracle_id")) not in {
            f"oracle:{rule_id}" for rule_id in affected
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
    return sum(
        bool(operation_refs.get(_text(rule.get("rule_id")))) for rule in rules
    )


def reconcile_implicit_rule_identities(asset: dict[str, Any]) -> dict[str, Any]:
    """Merge typed upgrades, stabilize authority IDs and refresh exact bindings."""

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
    merged_by_authority_id: dict[str, dict[str, Any]] = {}
    source_target_ids: set[str] = set()
    missing_targets: list[dict[str, Any]] = []
    stable_identity_count = 0
    for typed_rule in promoted:
        candidate = candidate_by_id.get(_text(typed_rule.get("candidate_id"))) or {}
        target = _dict(candidate.get("authority_upgrade_target"))
        source_rule_id = _text(target.get("rule_id"))
        source_rule = by_id.get(source_rule_id)
        if not source_rule_id or source_rule is None:
            missing_targets.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "target_rule_id": source_rule_id,
                    "reason_code": "SOURCE_RULE_UPGRADE_TARGET_MISSING",
                }
            )
            continue
        authority_id, stable_refs, identity_authority = _authority_rule_identity(
            asset, candidate, source_rule
        )
        if identity_authority == "SOURCE_OCCURRENCE_REF_TYPED_SEMANTICS":
            stable_identity_count += 1
        source_target_ids.add(source_rule_id)
        merged_by_authority_id[authority_id] = _merge_source_and_typed_rule(
            source_rule,
            typed_rule,
            candidate,
            authority_rule_id=authority_id,
            stable_source_refs=stable_refs,
            identity_authority=identity_authority,
        )

    if merged_by_authority_id:
        authority_ids = set(merged_by_authority_id)
        reconciled_rules = [
            rule
            for rule in rules
            if _text(rule.get("rule_id")) not in source_target_ids | authority_ids
        ]
        reconciled_rules.extend(merged_by_authority_id.values())
        asset["rule_library"] = _dedupe_by_id(reconciled_rules, "rule_id")
        merged_rules = list(merged_by_authority_id.values())
        relationships, operation_refs = _merge_relationships(
            asset,
            merged_rules,
            replaced_rule_ids=source_target_ids | authority_ids,
        )
        asset["relationships"] = relationships
        _replace_risks_and_oracles(
            asset,
            merged_rules,
            operation_refs,
            replaced_rule_ids=source_target_ids | authority_ids,
        )

    exact_binding_refreshed_rule_count = _refresh_current_implicit_artifacts(asset)

    receipt = _dict(asset.get("implicit_rule_candidate_validation_receipt"))
    for row in _list(receipt.get("validated")):
        if not isinstance(row, dict):
            continue
        target = _dict(row.get("authority_upgrade_target"))
        source_rule_id = _text(target.get("rule_id"))
        matching = [
            rule
            for rule in merged_by_authority_id.values()
            if _text(_dict(rule.get("authority_upgrade_target")).get("source_rule_id"))
            == source_rule_id
        ]
        if len(matching) == 1:
            row["promoted_rule_id"] = matching[0]["rule_id"]
            row["identity_reconciliation_status"] = "MERGED_IN_PLACE"
            row["stable_source_refs"] = list(
                matching[0].get("stable_source_refs") or []
            )
    gate = _dict(asset.get("implicit_rule_projection_gate"))
    gate["source_rule_semantic_upgrade_count"] = len(merged_by_authority_id)
    gate["stable_rule_identity_count"] = stable_identity_count
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
        if merged_by_authority_id
        else "NO_TYPED_SOURCE_RULE_UPGRADES"
    )
    asset["implicit_rule_identity_reconciliation_receipt"] = {
        "schema": SCHEMA_VERSION,
        "status": status,
        "candidate_count": len(candidates),
        "promoted_candidate_count": len(promoted),
        "merged_rule_count": len(merged_by_authority_id),
        "merged_rule_ids": sorted(merged_by_authority_id),
        "replaced_source_rule_ids": sorted(source_target_ids),
        "stable_rule_identity_count": stable_identity_count,
        "missing_targets": missing_targets,
        "exact_binding_refreshed_rule_count": exact_binding_refreshed_rule_count,
        "source_rule_identity_retained_as_origin": True,
        "typed_semantics_replaced_prose_projection": bool(
            merged_by_authority_id
        ),
        "parallel_rule_row_created": False,
        "candidate_validation_authority_reused": True,
        "candidate_promotion_authority_reused": True,
        "source_identity_authority": "SOURCE_OCCURRENCE_REGISTRY_SOURCE_REFS",
        "relationship_authority_reused": (
            "_authoritative_rule_to_interface_edges"
        ),
    }
    summary = _dict(asset.get("summary"))
    summary["implicit_rule_source_semantic_upgrade_count"] = len(
        merged_by_authority_id
    )
    summary["implicit_rule_stable_identity_count"] = stable_identity_count
    summary["implicit_rule_exact_binding_refreshed_rule_count"] = (
        exact_binding_refreshed_rule_count
    )
    summary["implicit_rule_identity_reconciliation_status"] = status
    asset["summary"] = summary
    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "implicit_rule_source_identity_reconciled_before_behavior_ir": True,
            "implicit_rule_authority_id_uses_stable_source_ref_when_available": True,
            "implicit_rule_content_version_source_id_is_not_durable_rule_identity": True,
            "implicit_rule_source_rule_id_is_retained_as_origin": True,
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
