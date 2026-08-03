"""Project implicit business rules into the existing rule-library mainline.

This stage does not ask a model to invent rules and does not create a parallel IR.
It derives conservative candidates from already-ingested formal constraints,
accepted typed business facts and independent cross-source facts, validates them
through ``_candidate_validation``, and promotes only accepted candidates into
``rule_library``. The existing Behavior IR compiler then turns those rows into
invariants and the existing obligation/experiment/oracle chain remains the sole
execution authority.

The projection is intentionally idempotent. The enterprise cognition boundary can
run twice when a structure-first compiler upgrades the same fact ledger; every run
replaces prior derived rules, relations, risks, oracles and gaps from the current
fact authority instead of accumulating stale projection artifacts.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Iterable

from ._candidate_validation import (
    promote_validated_candidates,
    validate_and_promote_candidates,
)
from .implicit_rule_fact_entailment import (
    derive_rule_candidates_from_business_facts,
    uncovered_rule_candidate_spans,
)

SCHEMA_VERSION = "qualibug.implicit-rule-projection.v1"
_INVALID_SOURCE_IDS = frozenset({"", "unknown", "unspecified", "*"})
_DERIVATION = "implicit_rule_entailment"
_IMPLICIT_GAP_KINDS = frozenset(
    {
        "IMPLICIT_RULE_AUTHORITY_INSUFFICIENT",
        "IMPLICIT_RULE_CONFLICTED",
        "IMPLICIT_RULE_SOURCE_SPAN_UNCOVERED",
    }
)


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


def _norm(value: Any) -> str:
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", _text(value)).lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", raw).strip("_")


def _singular(value: Any) -> str:
    token = _norm(value)
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        return token[:-1]
    return token


def _source_ref(
    source_id: Any,
    *,
    locator: Any = "",
    kind: str,
    fact_ref: Any = "",
) -> dict[str, Any]:
    resolved_locator = _text(locator)
    if not resolved_locator:
        # A schema/permission fact carries no document locator when it was
        # entailed rather than quoted. The fact's own identity (fact_ref) is
        # still a grounded locator inside the evidence chain — using it keeps
        # the reference projectable by the canonical defect registry instead
        # of silently ungrounded. Never invent a locator beyond that.
        resolved_locator = _text(fact_ref)
    return {
        "source_id": _text(source_id),
        "source_locator": resolved_locator,
        "kind": kind,
        "fact_ref": _text(fact_ref),
    }


def _source_ids(refs: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            _text(row.get("source_id"))
            for row in refs
            if isinstance(row, dict)
            and _text(row.get("source_id")).lower() not in _INVALID_SOURCE_IDS
        }
    )


def _rule_candidate(
    *,
    logical_form: str,
    statement: str,
    source_refs: list[dict[str, Any]],
    supporting_fact_refs: list[str],
    source_authority: str,
    derivation_basis: list[str],
    antecedents: list[dict[str, Any]],
    consequent: dict[str, Any],
    subject_refs: list[str] | None = None,
    actor_refs: list[str] | None = None,
    operation_refs: list[str] | None = None,
    table_refs: list[str] | None = None,
    field_refs: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    exceptions: list[Any] | None = None,
    risk_type: str = "data_integrity",
    severity: str = "P1",
    observation_requirements: list[str] | None = None,
    counterexample_plan: dict[str, Any] | None = None,
    contradicting_fact_refs: list[str] | None = None,
    confidence: float = 0.9,
    scope_status: str = "RESOLVED",
    exception_status: str = "NOT_APPLICABLE",
) -> dict[str, Any]:
    sources = _source_ids(source_refs)
    candidate_id = _stable_id(
        "rulecand",
        logical_form,
        statement,
        supporting_fact_refs,
        sources,
    )
    return {
        "candidate_id": candidate_id,
        "kind": "rule",
        "name": statement,
        "statement": statement,
        "logical_form": logical_form,
        "antecedents": antecedents,
        "consequent": consequent,
        "subject_refs": list(subject_refs or []),
        "actor_refs": list(actor_refs or []),
        "operation_refs": list(operation_refs or []),
        "table_refs": list(table_refs or []),
        "field_refs": list(field_refs or []),
        "scope": dict(scope or {}),
        "exceptions": list(exceptions or []),
        "derivation_basis": list(derivation_basis),
        "supporting_fact_refs": sorted(
            {_text(value) for value in supporting_fact_refs if _text(value)}
        ),
        "contradicting_fact_refs": sorted(
            {
                _text(value)
                for value in (contradicting_fact_refs or [])
                if _text(value)
            }
        ),
        "source_refs": source_refs,
        "supporting_source_ids": sources,
        "supporting_evidence": source_refs,
        "source_authority": source_authority,
        "falsifiability": "EVALUABLE",
        "binding_readiness": "READY_FOR_IR_BINDING",
        "scope_status": scope_status,
        "exception_status": exception_status,
        "counterexample_plan": dict(counterexample_plan or {}),
        "observation_requirements": list(observation_requirements or []),
        "risk_type": risk_type,
        "severity": severity,
        "confidence": confidence,
        "status": "CANDIDATE",
    }


def _constraint_text(row: dict[str, Any]) -> str:
    values: list[str] = []
    for field in (
        "constraint",
        "constraints",
        "validation",
        "rules",
        "description",
        "comment",
        "check",
    ):
        value = row.get(field)
        if isinstance(value, list):
            values.extend(_text(item) for item in value if _text(item))
        elif _text(value):
            values.append(_text(value))
    return " ".join(values).lower()


def _field_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _list(asset.get("field_dictionary")):
        if not isinstance(row, dict):
            continue
        field = _text(row.get("field") or row.get("name"))
        table = _text(row.get("table") or row.get("entity"))
        if not field:
            continue
        key = f"{_norm(table)}:{_norm(field)}:{_text(row.get('source_id'))}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(dict(row))
    for table in _list(asset.get("data_tables")):
        if not isinstance(table, dict):
            continue
        table_name = _text(table.get("name"))
        table_id = _text(table.get("table_id"))
        source_id = _text(table.get("source_id"))
        identities = {_norm(value) for value in _list(table.get("identity_fields"))}
        for column in _list(table.get("columns")):
            row = dict(column) if isinstance(column, dict) else {"field": column}
            field = _text(row.get("field") or row.get("name"))
            if not field:
                continue
            row.setdefault("table", table_name)
            row.setdefault("table_id", table_id)
            row.setdefault("source_id", source_id)
            if _norm(field) in identities:
                row.setdefault("unique", True)
                row.setdefault("identity", True)
            key = f"{_norm(table_name)}:{_norm(field)}:{source_id}"
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def _schema_rule_candidates(asset: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in _field_rows(asset):
        field = _text(row.get("field") or row.get("name"))
        table = _text(row.get("table") or row.get("entity") or "record")
        table_ref = _text(row.get("table_id")) or f"table:{_norm(table)}"
        field_ref = _text(row.get("field_id")) or f"field:{_norm(table)}.{_norm(field)}"
        source_id = _text(row.get("source_id"))
        locator = _text(row.get("source_locator") or row.get("locator"))
        fact_ref = field_ref
        constraints = _constraint_text(row)
        refs = [
            _source_ref(
                source_id,
                locator=locator,
                kind="formal_schema_constraint",
                fact_ref=fact_ref,
            )
        ]
        base = {
            "source_refs": refs,
            "supporting_fact_refs": [fact_ref],
            "source_authority": "formal_constraint",
            "derivation_basis": ["schema_entailment"],
            "subject_refs": [table_ref],
            "table_refs": [table_ref],
            "field_refs": [field_ref],
        }

        required = (
            row.get("required") is True
            or row.get("nullable") is False
            or "not null" in constraints
            or "required" in constraints
            or "必填" in constraints
            or "不能为空" in constraints
        )
        if required:
            candidates.append(
                _rule_candidate(
                    logical_form="REQUIRED_FIELD",
                    statement=f"{table}.{field} 必须有值",
                    antecedents=[
                        {"entity_ref": table_ref, "predicate": "record_exists"}
                    ],
                    consequent={"field_ref": field_ref, "operator": "not_null"},
                    observation_requirements=["field_value"],
                    counterexample_plan={
                        "mutate": "omit_or_null",
                        "observe": field_ref,
                    },
                    risk_type="validation",
                    **base,
                )
            )

        unique = (
            row.get("unique") is True
            or row.get("primary_key") is True
            or row.get("identity") is True
            or " unique" in f" {constraints}"
            or "primary key" in constraints
            or "唯一" in constraints
            or "主键" in constraints
        )
        if unique:
            candidates.append(
                _rule_candidate(
                    logical_form="UNIQUENESS",
                    statement=f"{table}.{field} 的值必须唯一",
                    antecedents=[
                        {"entity_ref": table_ref, "predicate": "two_records_exist"}
                    ],
                    consequent={"field_ref": field_ref, "operator": "unique"},
                    observation_requirements=["collection_values"],
                    counterexample_plan={
                        "mutate": "duplicate_value",
                        "observe": field_ref,
                    },
                    risk_type="data_integrity",
                    **base,
                )
            )

        enum_values = _list(row.get("enum") or _dict(row.get("schema")).get("enum"))
        if enum_values:
            candidates.append(
                _rule_candidate(
                    logical_form="DOMAIN_MEMBERSHIP",
                    statement=f"{table}.{field} 只能取声明枚举值",
                    antecedents=[
                        {"entity_ref": table_ref, "predicate": "record_exists"}
                    ],
                    consequent={
                        "field_ref": field_ref,
                        "operator": "in",
                        "values": enum_values,
                    },
                    observation_requirements=["field_value"],
                    counterexample_plan={
                        "mutate": "outside_enum",
                        "observe": field_ref,
                    },
                    risk_type="validation",
                    **base,
                )
            )

        minimum = row.get("minimum")
        maximum = row.get("maximum")
        if minimum is not None or maximum is not None:
            consequent: dict[str, Any] = {
                "field_ref": field_ref,
                "operator": "range",
            }
            if minimum is not None:
                consequent["minimum"] = minimum
            if maximum is not None:
                consequent["maximum"] = maximum
            candidates.append(
                _rule_candidate(
                    logical_form="VALUE_BOUND",
                    statement=f"{table}.{field} 必须满足声明的数值边界",
                    antecedents=[
                        {"entity_ref": table_ref, "predicate": "record_exists"}
                    ],
                    consequent=consequent,
                    observation_requirements=["field_value"],
                    counterexample_plan={
                        "mutate": "outside_declared_range",
                        "observe": field_ref,
                    },
                    risk_type="validation",
                    **base,
                )
            )

        foreign = row.get("foreign_key") or row.get("references") or row.get("ref_table")
        if foreign:
            target_ref = (
                _text(foreign)
                if not isinstance(foreign, dict)
                else _text(
                    foreign.get("table")
                    or foreign.get("target")
                    or foreign.get("ref")
                )
            )
            if target_ref:
                candidates.append(
                    _rule_candidate(
                        logical_form="REFERENTIAL_INTEGRITY",
                        statement=f"{table}.{field} 必须引用存在的 {target_ref} 记录",
                        antecedents=[
                            {"entity_ref": table_ref, "field_ref": field_ref}
                        ],
                        consequent={
                            "target_ref": target_ref,
                            "operator": "reference_exists",
                        },
                        subject_refs=[table_ref, target_ref],
                        observation_requirements=[
                            "foreign_key_value",
                            "referenced_record",
                        ],
                        counterexample_plan={
                            "mutate": "unknown_reference",
                            "observe": target_ref,
                        },
                        risk_type="data_integrity",
                        source_refs=refs,
                        supporting_fact_refs=[fact_ref],
                        source_authority="formal_constraint",
                        derivation_basis=["schema_entailment"],
                        table_refs=[table_ref, target_ref],
                        field_refs=[field_ref],
                    )
                )
    return candidates


def _request_schema(operation: dict[str, Any]) -> dict[str, Any]:
    schema = _dict(operation.get("request_schema") or operation.get("requestBody"))
    content = _dict(schema.get("content"))
    if content:
        media = _dict(content.get("application/json"))
        return _dict(media.get("schema")) or media
    return schema


def _api_schema_rule_candidates(asset: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for operation in _list(asset.get("interfaces")):
        if not isinstance(operation, dict):
            continue
        schema = _request_schema(operation)
        operation_ref = _text(
            operation.get("interface_id") or operation.get("operation_id")
        )
        if not schema or not operation_ref:
            continue
        source_id = _text(operation.get("source_id"))
        locator = _text(operation.get("source_locator") or operation.get("path"))
        method = _text(operation.get("method")).upper()
        path = _text(operation.get("path"))
        properties = _dict(schema.get("properties"))
        required = {
            _text(value) for value in _list(schema.get("required")) if _text(value)
        }
        for field, spec_value in properties.items():
            spec = _dict(spec_value)
            field_ref = f"operation_field:{operation_ref}:{field}"
            refs = [
                _source_ref(
                    source_id,
                    locator=locator,
                    kind="api_schema_constraint",
                    fact_ref=field_ref,
                )
            ]
            base = {
                "source_refs": refs,
                "supporting_fact_refs": [field_ref],
                "source_authority": "api_schema_constraint",
                "derivation_basis": ["schema_entailment"],
                "subject_refs": [operation_ref],
                "operation_refs": [operation_ref],
                "field_refs": [field_ref],
            }
            if field in required:
                candidates.append(
                    _rule_candidate(
                        logical_form="REQUIRED_FIELD",
                        statement=f"{method} {path} 请求字段 {field} 必须有值",
                        antecedents=[
                            {
                                "operation_ref": operation_ref,
                                "predicate": "request_sent",
                            }
                        ],
                        consequent={
                            "field_ref": field_ref,
                            "operator": "not_null",
                        },
                        observation_requirements=["http_response"],
                        counterexample_plan={
                            "mutate": "omit_or_null",
                            "observe": "http_response",
                        },
                        risk_type="validation",
                        **base,
                    )
                )
            enum_values = _list(spec.get("enum"))
            if enum_values:
                candidates.append(
                    _rule_candidate(
                        logical_form="DOMAIN_MEMBERSHIP",
                        statement=(
                            f"{method} {path} 请求字段 {field} 只能取声明枚举值"
                        ),
                        antecedents=[
                            {
                                "operation_ref": operation_ref,
                                "predicate": "request_sent",
                            }
                        ],
                        consequent={
                            "field_ref": field_ref,
                            "operator": "in",
                            "values": enum_values,
                        },
                        observation_requirements=["http_response"],
                        counterexample_plan={
                            "mutate": "outside_enum",
                            "observe": "http_response",
                        },
                        risk_type="validation",
                        **base,
                    )
                )
            if spec.get("minimum") is not None or spec.get("maximum") is not None:
                bound: dict[str, Any] = {
                    "field_ref": field_ref,
                    "operator": "range",
                }
                if spec.get("minimum") is not None:
                    bound["minimum"] = spec.get("minimum")
                if spec.get("maximum") is not None:
                    bound["maximum"] = spec.get("maximum")
                candidates.append(
                    _rule_candidate(
                        logical_form="VALUE_BOUND",
                        statement=f"{method} {path} 请求字段 {field} 必须满足声明边界",
                        antecedents=[
                            {
                                "operation_ref": operation_ref,
                                "predicate": "request_sent",
                            }
                        ],
                        consequent=bound,
                        observation_requirements=["http_response"],
                        counterexample_plan={
                            "mutate": "outside_declared_range",
                            "observe": "http_response",
                        },
                        risk_type="validation",
                        **base,
                    )
                )
    return candidates


_SCOPE_FIELD_MARKERS = {
    "own": ("owner_id", "owner", "created_by", "creator_id", "user_id"),
    "tenant": ("tenant_id", "tenant", "org_id", "organization_id", "company_id"),
    "own_tenant": ("tenant_id", "org_id", "organization_id"),
    "organization": ("org_id", "organization_id", "company_id"),
    "org": ("org_id", "organization_id", "company_id"),
    "department": ("department_id", "dept_id"),
    "region": ("region_id", "area_id"),
    "warehouse": ("warehouse_id",),
}


def _table_index(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for table in _list(asset.get("data_tables")):
        if not isinstance(table, dict):
            continue
        name = _text(table.get("name"))
        if name:
            result[_singular(name)] = table
    return result


def _operation_resource_tokens(operation: dict[str, Any]) -> set[str]:
    values = [_text(operation.get("resource")), _text(operation.get("path"))]
    tokens: set[str] = set()
    for value in values:
        for token in re.split(r"[/{}:_\-]+", value):
            normalized = _singular(token)
            if normalized and normalized not in {
                "api",
                "v1",
                "v2",
                "v3",
                "admin",
            }:
                tokens.add(normalized)
    return tokens


def _permission_rule_candidates(asset: dict[str, Any]) -> list[dict[str, Any]]:
    table_index = _table_index(asset)
    field_rows = _field_rows(asset)
    candidates: list[dict[str, Any]] = []
    for permission in _list(asset.get("permission_matrix")):
        if not isinstance(permission, dict):
            continue
        role = _text(
            permission.get("role")
            or permission.get("actor")
            or permission.get("principal")
        )
        resource = _text(permission.get("resource"))
        scope_key = _norm(permission.get("scope"))
        if not role or not resource or scope_key not in _SCOPE_FIELD_MARKERS:
            continue
        actions = [
            _norm(value)
            for value in _list(permission.get("actions"))
            if _norm(value)
        ]
        if not actions and _text(permission.get("action")):
            actions = [_norm(permission.get("action"))]
        if not actions:
            continue
        resource_key = _singular(resource)
        table = table_index.get(resource_key)
        if table is None:
            table = next(
                (
                    row
                    for key, row in table_index.items()
                    if key and (key in resource_key or resource_key in key)
                ),
                None,
            )
        if not isinstance(table, dict):
            continue
        table_name = _text(table.get("name"))
        table_ref = _text(table.get("table_id")) or f"table:{_norm(table_name)}"
        scope_markers = _SCOPE_FIELD_MARKERS[scope_key]
        scope_field = next(
            (
                row
                for row in field_rows
                if _singular(row.get("table") or row.get("entity"))
                == _singular(table_name)
                and any(
                    marker == _norm(row.get("field") or row.get("name"))
                    for marker in scope_markers
                )
            ),
            None,
        )
        if not isinstance(scope_field, dict):
            continue
        field_name = _text(scope_field.get("field") or scope_field.get("name"))
        field_ref = _text(scope_field.get("field_id")) or (
            f"field:{_norm(table_name)}.{_norm(field_name)}"
        )
        operation_pairs: list[tuple[dict[str, Any], str]] = []
        for operation in _list(asset.get("interfaces")):
            if not isinstance(operation, dict):
                continue
            operation_ref = _text(
                operation.get("interface_id") or operation.get("operation_id")
            )
            if not operation_ref:
                continue
            if resource_key not in _operation_resource_tokens(operation):
                continue
            operation_text = _norm(
                operation.get("path") or operation.get("summary")
            )
            if not any(action in operation_text for action in actions):
                continue
            operation_pairs.append((operation, operation_ref))
        if not operation_pairs:
            continue
        operation_refs = [operation_ref for _, operation_ref in operation_pairs]
        permission_ref = _text(permission.get("permission_id")) or _stable_id(
            "permission", role, resource, actions, scope_key
        )
        source_refs = [
            _source_ref(
                permission.get("source_id"),
                locator=permission.get("source_locator"),
                kind="permission_scope",
                fact_ref=permission_ref,
            ),
            _source_ref(
                scope_field.get("source_id"),
                locator=scope_field.get("source_locator"),
                kind="scope_field",
                fact_ref=field_ref,
            ),
            *[
                _source_ref(
                    operation.get("source_id"),
                    locator=(
                        operation.get("source_locator") or operation.get("path")
                    ),
                    kind="operation_contract",
                    fact_ref=operation_ref,
                )
                for operation, operation_ref in operation_pairs
            ],
        ]
        conflict_refs = _matching_conflict_refs(asset, role, resource, field_name)
        candidates.append(
            _rule_candidate(
                logical_form="PERMISSION_BOUNDARY",
                statement=(
                    f"角色 {role} 对 {table_name} 执行 {','.join(actions)} 时，"
                    f"数据范围必须受 {field_name} 的 {scope_key} 约束"
                ),
                antecedents=[
                    {"actor_ref": role, "operation_refs": operation_refs},
                    {"entity_ref": table_ref, "field_ref": field_ref},
                ],
                consequent={
                    "operator": "within_actor_scope",
                    "field_ref": field_ref,
                    "scope": scope_key,
                },
                source_refs=source_refs,
                supporting_fact_refs=[permission_ref, field_ref, *operation_refs],
                source_authority="multi_source_entailment",
                derivation_basis=["multi_source_entailment", "relation_closure"],
                subject_refs=[table_ref],
                actor_refs=[role],
                operation_refs=operation_refs,
                table_refs=[table_ref],
                field_refs=[field_ref],
                scope={"kind": scope_key, "field_ref": field_ref},
                observation_requirements=[
                    "actor_identity",
                    "resource_identity",
                    "scope_field",
                ],
                counterexample_plan={
                    "control": "within_scope_resource",
                    "treatment": "outside_scope_resource",
                    "observe": "access_decision_and_resource_identity",
                },
                contradicting_fact_refs=conflict_refs,
                risk_type=(
                    "isolation" if "tenant" in scope_key else "authorization"
                ),
                severity="P1",
                confidence=0.86,
            )
        )
    return candidates


def _matching_conflict_refs(asset: dict[str, Any], *needles: Any) -> list[str]:
    normalized = {_norm(value) for value in needles if _norm(value)}
    refs: list[str] = []
    for row in _list(asset.get("cross_document_conflicts")):
        if not isinstance(row, dict):
            continue
        conflict_text = _norm(
            " ".join(
                _text(row.get(field))
                for field in (
                    "entity",
                    "reason",
                    "detail",
                    "statement",
                    "conflict_type",
                )
            )
        )
        if normalized and any(
            value and value in conflict_text for value in normalized
        ):
            refs.append(
                _text(row.get("conflict_id") or row.get("id"))
                or _stable_id("conflict", conflict_text)
            )
    return sorted(set(refs))


def _partition_existing_rules(
    rules: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep source rules, quarantine industry priors and replace old projections."""
    retained: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if _text(rule.get("derivation")) == _DERIVATION:
            continue
        if _text(rule.get("source_id")) != "industry_inference" and _text(
            rule.get("source_type")
        ) != "derived_inference":
            retained.append(rule)
            continue
        statement = _text(rule.get("statement") or rule.get("expected"))
        if not statement:
            continue
        rule_id = _text(rule.get("rule_id")) or _stable_id(
            "industry_rule", statement
        )
        candidates.append(
            _rule_candidate(
                logical_form="PRIOR_HYPOTHESIS",
                statement=statement,
                source_refs=[
                    _source_ref(
                        "industry_inference",
                        locator=rule.get("source_locator"),
                        kind="industry_prior",
                        fact_ref=rule_id,
                    )
                ],
                supporting_fact_refs=[rule_id],
                source_authority="industry_prior",
                derivation_basis=["industry_prior"],
                antecedents=list(
                    _dict(rule.get("structured_expression")).get("antecedents")
                    or []
                ),
                consequent=dict(
                    _dict(rule.get("structured_expression")).get("consequent")
                    or {}
                ),
                subject_refs=list(rule.get("subject_refs") or []),
                operation_refs=list(rule.get("operation_refs") or []),
                risk_type=_text(rule.get("risk_type") or "business_logic"),
                severity=_text(rule.get("severity") or "P2"),
                observation_requirements=[
                    "independent_customer_source_confirmation"
                ],
                counterexample_plan={
                    "action": (
                        "seek_independent_customer_source_or_runtime_contract"
                    )
                },
                confidence=min(0.45, float(rule.get("confidence") or 0.4)),
            )
        )
    return retained, candidates


def _prior_proposal_candidates(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(asset.get("implicit_rule_candidates"))
        if isinstance(row, dict)
        and _text(row.get("kind")) == "rule"
        and _text(row.get("logical_form")) == "PRIOR_HYPOTHESIS"
    ]


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_id = _text(candidate.get("candidate_id"))
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        result.append(candidate)
    return result


def _dedupe_by_id(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _text(row.get(field))
        if not identity:
            identity = _stable_id(field, row)
            row = {**row, field: identity}
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _project_relationships(
    existing: list[dict[str, Any]], accepted_rules: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in existing
        if isinstance(row, dict) and _text(row.get("derivation")) != _DERIVATION
    ]
    for rule in accepted_rules:
        rule_id = _text(rule.get("rule_id"))
        for table_ref in _list(rule.get("table_refs")):
            if _text(table_ref):
                rows.append(
                    {
                        "edge_id": _stable_id(
                            "edge", rule_id, "rule_to_table", table_ref
                        ),
                        "from": rule_id,
                        "to": _text(table_ref),
                        "relation": "rule_to_table",
                        "confidence": 1.0,
                        "status": "accepted",
                        "derivation": _DERIVATION,
                        "evidence": {
                            "candidate_id": rule.get("candidate_id"),
                            "supporting_fact_refs": rule.get(
                                "supporting_fact_refs"
                            ),
                        },
                    }
                )
        for operation_ref in _list(rule.get("operation_refs")):
            if _text(operation_ref):
                rows.append(
                    {
                        "edge_id": _stable_id(
                            "edge",
                            rule_id,
                            "rule_to_interface",
                            operation_ref,
                        ),
                        "from": rule_id,
                        "to": _text(operation_ref),
                        "relation": "rule_to_interface",
                        "confidence": 1.0,
                        "status": "accepted",
                        "derivation": _DERIVATION,
                        "evidence": {
                            "candidate_id": rule.get("candidate_id"),
                            "supporting_fact_refs": rule.get(
                                "supporting_fact_refs"
                            ),
                        },
                    }
                )
    return _dedupe_by_id(rows, "edge_id")


def _project_risks_and_oracles(
    asset: dict[str, Any], accepted_rules: list[dict[str, Any]]
) -> None:
    risks = [
        dict(row)
        for row in _list(asset.get("risk_domains"))
        if isinstance(row, dict) and _text(row.get("derivation")) != _DERIVATION
    ]
    oracles = [
        dict(row)
        for row in _list(asset.get("oracle_library"))
        if isinstance(row, dict) and _text(row.get("derivation")) != _DERIVATION
    ]
    for rule in accepted_rules:
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
                "linked_interfaces": list(rule.get("operation_refs") or []),
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


def enrich_asset_with_implicit_rule_projection(asset: dict[str, Any]) -> dict[str, Any]:
    """Derive, validate and promote implicit rules in the existing asset graph."""
    existing_rules = [
        dict(row)
        for row in _list(asset.get("rule_library"))
        if isinstance(row, dict)
    ]
    retained_rules, industry_candidates = _partition_existing_rules(existing_rules)
    typed_fact_candidates = derive_rule_candidates_from_business_facts(asset)
    uncovered_spans = uncovered_rule_candidate_spans(asset)
    critical_uncovered_spans = [row for row in uncovered_spans if row.get("critical")]
    candidates = _dedupe_candidates(
        [
            *_schema_rule_candidates(asset),
            *_api_schema_rule_candidates(asset),
            *_permission_rule_candidates(asset),
            *typed_fact_candidates,
            *industry_candidates,
            *_prior_proposal_candidates(asset),
        ]
    )
    receipt = validate_and_promote_candidates(
        candidates,
        interfaces=[
            row
            for row in _list(asset.get("interfaces"))
            if isinstance(row, dict)
        ],
        tables=[
            row
            for row in _list(asset.get("data_tables"))
            if isinstance(row, dict)
        ],
        rules=retained_rules,
        state_machines=[
            row
            for row in _list(asset.get("state_machines"))
            if isinstance(row, dict)
        ],
        validation_context={"asset": asset, "projection_schema": SCHEMA_VERSION},
    )
    receipt_id = _stable_id(
        "implicit_rule_receipt",
        [row.get("candidate_id") for row in candidates],
        len(receipt.validated),
        len(receipt.pending),
        len(receipt.conflicted),
        len(uncovered_spans),
    )
    for row in receipt.validated:
        row["promotion_receipt_id"] = receipt_id
    accepted_rules = promote_validated_candidates(receipt.validated, kind="rule")
    for rule in accepted_rules:
        rule["promotion_receipt_id"] = receipt_id
        operation_refs = list(rule.get("operation_refs") or [])
        rule["downstream_binding_status"] = (
            "READY_AUTHORITATIVE_OPERATION_BOUND"
            if operation_refs
            else "READY_BEHAVIOR_IR_BINDING_REQUIRED"
        )

    asset["rule_library"] = _dedupe_by_id(
        [*retained_rules, *accepted_rules], "rule_id"
    )
    asset["relationships"] = _project_relationships(
        [
            row
            for row in _list(asset.get("relationships"))
            if isinstance(row, dict)
        ],
        accepted_rules,
    )
    _project_risks_and_oracles(asset, accepted_rules)

    gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) not in _IMPLICIT_GAP_KINDS
    ]
    for row in receipt.pending:
        gaps.append(
            {
                "kind": "IMPLICIT_RULE_AUTHORITY_INSUFFICIENT",
                "gap_type": "implicit_rule_candidate_pending",
                "candidate_id": row.get("candidate_id"),
                "logical_form": row.get("logical_form"),
                "statement": row.get("statement"),
                "pending_reason": row.get("reason")
                or row.get("pending_reason"),
                "pending_gates": list(row.get("pending_gates") or []),
                "source_ids": _candidate_source_ids_for_gap(row),
                "operator_action": (
                    "provide independent source evidence or an approved runtime contract"
                ),
            }
        )
    for row in receipt.conflicted:
        gaps.append(
            {
                "kind": "IMPLICIT_RULE_CONFLICTED",
                "gap_type": "implicit_rule_counterevidence_present",
                "candidate_id": row.get("candidate_id"),
                "logical_form": row.get("logical_form"),
                "statement": row.get("statement"),
                "conflict_reason": row.get("reason")
                or row.get("conflict_reason"),
                "conflict_sources": list(row.get("conflict_sources") or []),
                "operator_action": "resolve source authority conflict before execution",
            }
        )
    for row in uncovered_spans:
        gaps.append(
            {
                "kind": "IMPLICIT_RULE_SOURCE_SPAN_UNCOVERED",
                "gap_type": "source_span_has_rule_signal_but_no_compiled_fact",
                **row,
                "operator_action": (
                    "extend the existing structure-first fact compiler for this source-backed span"
                ),
            }
        )
    asset["coverage_gaps"] = gaps

    counts = Counter(
        _text(row.get("logical_form")) or "UNKNOWN" for row in candidates
    )
    validation = receipt.to_dict()
    asset["implicit_rule_candidates"] = candidates
    asset["implicit_rule_candidate_validation_receipt"] = {
        **validation,
        "receipt_id": receipt_id,
    }
    status = (
        "BLOCKED_CONFLICTED"
        if receipt.conflicted
        else "BLOCKED_SOURCE_SPAN_COVERAGE"
        if critical_uncovered_spans
        else "PARTIAL_PENDING_AUTHORITY"
        if receipt.pending or uncovered_spans
        else "PASS"
    )
    asset["implicit_rule_projection_gate"] = {
        "schema": SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "status": status,
        "entry_allowed": not bool(receipt.conflicted or critical_uncovered_spans),
        "candidate_count": len(candidates),
        "accepted_rule_count": len(accepted_rules),
        "typed_fact_candidate_count": len(typed_fact_candidates),
        "pending_rule_count": len(receipt.pending),
        "conflicted_rule_count": len(receipt.conflicted),
        "rejected_rule_count": len(receipt.rejected),
        "uncovered_rule_span_count": len(uncovered_spans),
        "critical_uncovered_rule_span_count": len(critical_uncovered_spans),
        "logical_form_distribution": dict(sorted(counts.items())),
        "industry_prior_direct_authority_allowed": False,
        "runtime_convention_direct_authority_allowed": False,
        "pending_rule_execution_allowed": False,
        "behavior_ir_authority": "existing_rule_library_to_invariant_compiler",
        "parallel_rule_ir_created": False,
        "reprojection_is_idempotent": True,
        "span_coverage_authority": "business_fact_candidate_ledger",
    }
    summary = dict(asset.get("summary") or {})
    summary.update(
        {
            "implicit_rule_candidate_count": len(candidates),
            "implicit_rule_accepted_count": len(accepted_rules),
            "implicit_rule_typed_fact_candidate_count": len(typed_fact_candidates),
            "implicit_rule_pending_count": len(receipt.pending),
            "implicit_rule_conflicted_count": len(receipt.conflicted),
            "implicit_rule_uncovered_span_count": len(uncovered_spans),
            "implicit_rule_projection_status": status,
        }
    )
    asset["summary"] = summary
    governance = dict(asset.get("governance") or {})
    governance.update(
        {
            "implicit_rules_use_existing_candidate_validation_authority": True,
            "implicit_rules_enter_existing_rule_library": True,
            "implicit_rules_create_parallel_behavior_ir": False,
            "implicit_rule_projection_is_idempotent": True,
            "implicit_rule_authority_requires_explicit_source_identity": True,
            "implicit_rule_span_coverage_uses_existing_candidate_ledger": True,
            "implicit_rule_typed_facts_use_existing_business_fact_ledger": True,
            "industry_prior_may_propose_but_not_authorize_rule": True,
            "runtime_convention_may_propose_but_not_authorize_rule": True,
            "rule_authority_uses_hard_gates_not_average_confidence": True,
        }
    )
    asset["governance"] = governance
    return asset


def _candidate_source_ids_for_gap(candidate: dict[str, Any]) -> list[str]:
    refs = [
        row
        for row in _list(candidate.get("source_refs"))
        if isinstance(row, dict)
    ]
    return _source_ids(refs)


__all__ = [
    "SCHEMA_VERSION",
    "enrich_asset_with_implicit_rule_projection",
]
