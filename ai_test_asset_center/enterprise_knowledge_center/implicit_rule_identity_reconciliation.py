"""Reconcile typed implicit-rule promotions with one durable rule identity.

Canonical parser source IDs are content-version identities. Source occurrences provide
stable evidence references, but the number or location of duplicate occurrences is not a
business-rule identity. When both project scope and occurrence evidence are available,
this stage derives one authority ID from ``project_id`` plus normalized typed semantics.
Occurrence refs remain auditable evidence and never participate in the ID digest.

The stage reuses the existing candidate validator/promoter, rule library, authoritative
rule-to-interface linker, risk library and oracle library. It creates no parallel rule IR,
source registry, validator, promoter or linker.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ._candidate_validation import promote_validated_candidates
from ._linking import _authoritative_rule_to_interface_edges

SCHEMA_VERSION = "qualibug.implicit-rule-identity-reconciliation.v4"
_DERIVATION = "implicit_rule_entailment"
_PROJECT_IDENTITY_AUTHORITY = (
    "PROJECT_SCOPED_TYPED_SEMANTICS_WITH_OCCURRENCE_EVIDENCE"
)
_COMPATIBILITY_IDENTITY_AUTHORITY = (
    "SOURCE_OCCURRENCE_REF_TYPED_SEMANTICS"
)
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


def _unique_text(values: Any) -> list[str]:
    return sorted({_text(value) for value in _list(values) if _text(value)})


def _unique_dicts(values: Any) -> list[dict[str, Any]]:
    by_value: dict[str, dict[str, Any]] = {}
    for value in _list(values):
        if isinstance(value, dict):
            by_value.setdefault(_canonical(value), copy.deepcopy(value))
    return [by_value[key] for key in sorted(by_value)]


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
            *[
                _text(item)
                for item in _list(candidate.get("supporting_source_ids"))
            ],
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
) -> tuple[str, tuple[str, ...], str, str]:
    """Return durable authority ID, current evidence refs, authority and project.

    The occurrence-ref set is intentionally excluded from the project-scoped digest.
    Adding or removing a duplicate online location therefore updates evidence without
    changing the business-rule identity. A compatibility digest is retained only for
    direct legacy/unit assets that provide source refs but no project scope.
    """

    source_rule_id = _text(source_rule.get("rule_id"))
    stable_refs = _stable_source_refs(asset, candidate, source_rule)
    project_id = _text(asset.get("project_id"))
    semantics = _typed_semantic_identity(candidate)
    if project_id and stable_refs:
        payload = {
            "project_id": project_id,
            "typed_semantics": semantics,
        }
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:24]
        return (
            f"implicit_rule:{digest}",
            stable_refs,
            _PROJECT_IDENTITY_AUTHORITY,
            project_id,
        )
    if stable_refs:
        payload = {
            "stable_source_refs": list(stable_refs),
            "typed_semantics": semantics,
        }
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:24]
        return (
            f"implicit_rule:{digest}",
            stable_refs,
            _COMPATIBILITY_IDENTITY_AUTHORITY,
            "",
        )
    return source_rule_id, (), "SOURCE_RULE_ID_FALLBACK", project_id


def _merge_source_and_typed_rule(
    source_rule: dict[str, Any],
    typed_rule: dict[str, Any],
    candidate: dict[str, Any],
    *,
    authority_rule_id: str,
    stable_source_refs: tuple[str, ...],
    identity_authority: str,
    identity_project_id: str,
) -> dict[str, Any]:
    target = _dict(candidate.get("authority_upgrade_target"))
    source_rule_id = _text(target.get("rule_id"))
    source_origin = _source_rule_origin(source_rule)
    merged = {**copy.deepcopy(source_rule), **copy.deepcopy(typed_rule)}
    merged["rule_id"] = authority_rule_id
    merged["derivation"] = _DERIVATION
    merged["source_rule_origin"] = source_origin
    merged["source_rule_origins"] = [source_origin]
    merged["source_rule_id"] = source_rule_id
    merged["source_rule_ids"] = [source_rule_id]
    merged["stable_source_refs"] = list(stable_source_refs)
    merged["rule_identity_project_id"] = identity_project_id
    merged["rule_identity_authority"] = identity_authority
    merged["occurrence_ref_set_participates_in_rule_id"] = False
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
        "rule_identity_project_id": identity_project_id,
        "rule_identity_authority": identity_authority,
        "occurrence_ref_set_participates_in_rule_id": False,
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
        "rule_identity_project_id": identity_project_id,
        "rule_identity_authority": identity_authority,
        "occurrence_ref_set_participates_in_rule_id": False,
        "typed_runtime_semantics_authoritative": True,
    }
    merged["semantic_contract"] = semantic_contract
    return merged


def _merge_same_authority_rule(
    current: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """Fuse independent declarations that resolve to the same project rule."""

    merged = {**copy.deepcopy(current), **copy.deepcopy(incoming)}
    merged["rule_id"] = current["rule_id"]
    merged["stable_source_refs"] = _unique_text(
        [
            *current.get("stable_source_refs", []),
            *incoming.get("stable_source_refs", []),
        ]
    )
    merged["source_rule_ids"] = _unique_text(
        [
            *current.get("source_rule_ids", []),
            current.get("source_rule_id"),
            *incoming.get("source_rule_ids", []),
            incoming.get("source_rule_id"),
        ]
    )
    merged["source_rule_origins"] = _unique_dicts(
        [
            *current.get("source_rule_origins", []),
            current.get("source_rule_origin"),
            *incoming.get("source_rule_origins", []),
            incoming.get("source_rule_origin"),
        ]
    )
    for field in (
        "source_ids",
        "supporting_fact_refs",
        "contradicting_fact_refs",
        "subject_refs",
        "actor_refs",
        "operation_refs",
        "table_refs",
        "field_refs",
        "derivation_basis",
        "observation_requirements",
    ):
        merged[field] = _unique_text(
            [*current.get(field, []), *incoming.get(field, [])]
        )
    merged["source_refs"] = _unique_dicts(
        [*current.get("source_refs", []), *incoming.get("source_refs", [])]
    )
    receipt = _dict(merged.get("authority_upgrade_receipt"))
    receipt["source_rule_ids"] = list(merged["source_rule_ids"])
    receipt["stable_source_refs"] = list(merged["stable_source_refs"])
    receipt["merged_source_rule_count"] = len(merged["source_rule_ids"])
    receipt["multiple_occurrences_create_parallel_rules"] = False
    merged["authority_upgrade_receipt"] = receipt
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
    """Refresh the single relationship authority before cognition is frozen.

    Source-declared Chinese rules and promoted implicit rules share the same
    ``rule_to_interface`` linker. Previously only implicit rows were refreshed here;
    Chinese-rule edges were added later by downstream projection, after Identity and
    Implementation Binding had already consumed the graph. Refreshing all current
    rules closes that ordering gap while risk/oracle replacement remains limited to
    implicit rules.
    """
    all_rules = [
        dict(row)
        for row in _list(asset.get("rule_library"))
        if isinstance(row, dict) and _text(row.get("rule_id"))
    ]
    relationships, all_operation_refs = _merge_relationships(asset, all_rules)
    asset["relationships"] = relationships
    asset["rule_library"] = _dedupe_by_id(all_rules, "rule_id")

    implicit_rules = [
        row for row in all_rules if _text(row.get("derivation")) == _DERIVATION
    ]
    implicit_operation_refs = {
        _text(rule.get("rule_id")): list(
            all_operation_refs.get(_text(rule.get("rule_id"))) or []
        )
        for rule in implicit_rules
    }
    _replace_risks_and_oracles(
        asset, implicit_rules, implicit_operation_refs
    )
    return sum(
        bool(all_operation_refs.get(_text(rule.get("rule_id"))))
        for rule in all_rules
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
        (
            authority_id,
            stable_refs,
            identity_authority,
            identity_project_id,
        ) = _authority_rule_identity(asset, candidate, source_rule)
        source_target_ids.add(source_rule_id)
        merged = _merge_source_and_typed_rule(
            source_rule,
            typed_rule,
            candidate,
            authority_rule_id=authority_id,
            stable_source_refs=stable_refs,
            identity_authority=identity_authority,
            identity_project_id=identity_project_id,
        )
        if authority_id in merged_by_authority_id:
            merged_by_authority_id[authority_id] = _merge_same_authority_rule(
                merged_by_authority_id[authority_id], merged
            )
        else:
            merged_by_authority_id[authority_id] = merged

    project_scoped_identity_count = sum(
        _text(rule.get("rule_identity_authority")) == _PROJECT_IDENTITY_AUTHORITY
        for rule in merged_by_authority_id.values()
    )
    compatibility_identity_count = sum(
        _text(rule.get("rule_identity_authority"))
        == _COMPATIBILITY_IDENTITY_AUTHORITY
        for rule in merged_by_authority_id.values()
    )
    stable_identity_count = (
        project_scoped_identity_count + compatibility_identity_count
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
            if source_rule_id in _unique_text(
                [
                    rule.get("source_rule_id"),
                    *rule.get("source_rule_ids", []),
                ]
            )
        ]
        if len(matching) == 1:
            row["promoted_rule_id"] = matching[0]["rule_id"]
            row["identity_reconciliation_status"] = "MERGED_IN_PLACE"
            row["stable_source_refs"] = list(
                matching[0].get("stable_source_refs") or []
            )
            row["rule_identity_authority"] = matching[0].get(
                "rule_identity_authority"
            )
            row["occurrence_ref_set_participates_in_rule_id"] = False

    gate = _dict(asset.get("implicit_rule_projection_gate"))
    gate["source_rule_semantic_upgrade_count"] = len(merged_by_authority_id)
    gate["stable_rule_identity_count"] = stable_identity_count
    gate["project_scoped_rule_identity_count"] = project_scoped_identity_count
    gate["compatibility_rule_identity_count"] = compatibility_identity_count
    gate["exact_binding_refreshed_rule_count"] = (
        exact_binding_refreshed_rule_count
    )
    gate["occurrence_ref_set_participates_in_rule_id"] = False
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
    duplicate_source_rule_count = max(
        0, len(source_target_ids) - len(merged_by_authority_id)
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
        "project_scoped_rule_identity_count": project_scoped_identity_count,
        "compatibility_rule_identity_count": compatibility_identity_count,
        "duplicate_source_rule_count": duplicate_source_rule_count,
        "missing_targets": missing_targets,
        "exact_binding_refreshed_rule_count": exact_binding_refreshed_rule_count,
        "source_rule_identity_retained_as_origin": True,
        "typed_semantics_replaced_prose_projection": bool(
            merged_by_authority_id
        ),
        "parallel_rule_row_created": False,
        "multiple_occurrences_create_parallel_rules": False,
        "occurrence_ref_set_participates_in_rule_id": False,
        "candidate_validation_authority_reused": True,
        "candidate_promotion_authority_reused": True,
        "source_identity_authority": (
            "PROJECT_ID_PLUS_TYPED_SEMANTICS_WITH_SOURCE_OCCURRENCE_EVIDENCE"
        ),
        "relationship_authority_reused": (
            "_authoritative_rule_to_interface_edges"
        ),
    }
    summary = _dict(asset.get("summary"))
    summary["implicit_rule_source_semantic_upgrade_count"] = len(
        merged_by_authority_id
    )
    summary["implicit_rule_stable_identity_count"] = stable_identity_count
    summary["implicit_rule_project_scoped_identity_count"] = (
        project_scoped_identity_count
    )
    summary["implicit_rule_duplicate_source_rule_count"] = (
        duplicate_source_rule_count
    )
    summary["implicit_rule_exact_binding_refreshed_rule_count"] = (
        exact_binding_refreshed_rule_count
    )
    summary["implicit_rule_identity_reconciliation_status"] = status
    asset["summary"] = summary
    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "implicit_rule_source_identity_reconciled_before_behavior_ir": True,
            "implicit_rule_authority_id_uses_project_and_typed_semantics": True,
            "implicit_rule_occurrence_refs_are_evidence_not_identity": True,
            "implicit_rule_occurrence_ref_set_changes_preserve_rule_id": True,
            "implicit_rule_content_version_source_id_is_not_durable_rule_identity": True,
            "implicit_rule_source_rule_id_is_retained_as_origin": True,
            "implicit_rule_semantic_upgrade_creates_parallel_rule": False,
            "implicit_rule_multiple_occurrences_create_parallel_rules": False,
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
