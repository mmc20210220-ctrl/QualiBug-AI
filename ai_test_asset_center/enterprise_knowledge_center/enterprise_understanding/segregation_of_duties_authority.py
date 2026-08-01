"""Source-governed separation-of-duties contracts and permission binding.

SoD is never inferred from role names or generic workflow order. Only explicit source or
structured declarations create a contract. Resolved contracts bind to the existing effective
permission matrix and are projected as ordinary Behavior-IR invariants/rules.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

CONTRACT_SCHEMA = "qualibug.segregation-of-duties-contract.v1"
POLICY_SCHEMA = "qualibug.segregation-of-duties-policy.v1"

_EXPLICIT_MARKER = re.compile(
    r"(?:必须由不同(?:人员|人|账号|用户|主体)|不得由同一(?:人员|人|账号|用户|主体)|"
    r"不能由同一(?:人员|人|账号|用户|主体)|不可由同一(?:人员|人|账号|用户|主体)|"
    r"(?:不得|不能|不可)为同一(?:人员|人|账号|用户|主体)|"
    r"不得兼任|不能兼任|不可兼任|岗位互斥|角色互斥|职责分离|"
    r"mutually\s+exclusive|different\s+(?:users?|accounts?|principals?))",
    re.I,
)
_CONDITION_MARKER = re.compile(
    r"(?:如果|若|当|仅当|除非|金额|额度|时间|日期|工作日|小时|天内|"
    r"\bif\b|\bwhen\b|\bunless\b|before|after)", re.I,
)
_SPLIT_RE = re.compile(r"\s*(?:与|和|及|以及|、|/|and)\s*", re.I)
_SCOPE_MARKERS = (
    ("本租户", "own_tenant"), ("当前租户", "own_tenant"),
    ("本组织", "own_organization"), ("本部门", "own_department"),
    ("本项目", "own_project"), ("本仓库", "own_warehouse"),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _norm(value: Any) -> str:
    return re.sub(r"[\s，,。；;：:（）()【】\[\]“”\"'、_/.-]+", "", _text(value)).casefold()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _scope(statement: str) -> str:
    found = {canonical for marker, canonical in _SCOPE_MARKERS if marker in statement}
    return next(iter(found)) if len(found) == 1 else ("" if len(found) > 1 else "unspecified")


def _known_roles(asset: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for row in [*_list(asset.get("roles")), *_list(asset.get("permission_matrix"))]:
        if isinstance(row, dict):
            role = _text(row.get("role") or row.get("name") or row.get("actor"))
            if role:
                values.append(role)
    return sorted(set(values), key=lambda value: (-len(value), value))


def _roles_in(segment: str, roles: list[str]) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for role in roles:
        for match in re.finditer(re.escape(role), segment):
            matches.append((match.start(), match.end(), role))
    selected: list[str] = []
    for left, right, role in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(left >= chosen_left and right <= chosen_right for chosen_left, chosen_right, _ in matches if (chosen_right-chosen_left) > (right-left)):
            continue
        if role not in selected:
            selected.append(role)
    return selected


def _clean_operand(value: str) -> str:
    value = re.sub(r"(?:岗位|角色|人员|人|账号|用户|主体)$", "", value.strip())
    return value.strip(" ：:，,。；;")


def _source_statements(source: dict[str, Any]) -> Iterable[tuple[str, str]]:
    content = _text(source.get("text") or source.get("content"))
    filename = _text(source.get("filename") or source.get("name") or "source")
    for match in re.finditer(r"[^。；;\n]+[。；;]?", content):
        statement = match.group(0).strip().rstrip("。；;")
        if statement:
            yield statement, f"{filename}#chars={match.start()}-{match.end()}"


def _structured_contracts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [*_list(asset.get("segregation_of_duties_contracts")), *_list(asset.get("sod_policies"))]
    contracts: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        if _text(raw.get("schema_version")) == CONTRACT_SCHEMA and _text(raw.get("contract_id")):
            continue
        left_role = _text(raw.get("left_role") or raw.get("setup_role") or raw.get("role_a"))
        right_role = _text(raw.get("right_role") or raw.get("guarded_role") or raw.get("role_b"))
        left_action = _text(raw.get("left_action") or raw.get("setup_action") or raw.get("action_a"))
        right_action = _text(raw.get("right_action") or raw.get("guarded_action") or raw.get("action_b"))
        source_id = _text(raw.get("source_id")) or "segregation_of_duties_contracts"
        locator = _text(raw.get("source_locator")) or f"segregation_of_duties_contracts[{index}]"
        complete = bool((left_role or left_action) and (right_role or right_action))
        scope = _text(raw.get("scope")) or "unspecified"
        contracts.append({
            "schema_version": CONTRACT_SCHEMA,
            "contract_id": _stable_id("sod_contract", left_role, right_role, left_action, right_action, scope, source_id, locator),
            "left_role": left_role, "right_role": right_role,
            "left_action": left_action, "right_action": right_action,
            "resource_ref": _text(raw.get("resource_ref") or raw.get("resource") or raw.get("entity")),
            "scope": scope,
            "same_credential_forbidden": True,
            "same_resource_required": raw.get("same_resource_required") is not False,
            "status": "RESOLVED" if complete and raw.get("condition_binding_required") is not True else "UNRESOLVED",
            "reason_code": "" if complete and raw.get("condition_binding_required") is not True else "SOD_COORDINATE_OR_CONDITION_UNRESOLVED",
            "source_backed": True,
            "source_id": source_id, "source_locator": locator,
            "derivation": "structured_segregation_of_duties",
            "automatic_inference_allowed": False,
        })
    return contracts


def materialize_sod_contracts(asset: dict[str, Any], sources: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    roles = _known_roles(asset)
    contracts = [dict(row) for row in _list(asset.get("segregation_of_duties_contracts")) if isinstance(row, dict)]
    contracts.extend(_structured_contracts(asset))
    gaps: list[dict[str, Any]] = []
    for source in list(sources or []):
        if not isinstance(source, dict):
            continue
        source_id = _text(source.get("source_id")) or "source"
        for statement, locator in _source_statements(source):
            marker = _EXPLICIT_MARKER.search(statement)
            if not marker:
                continue
            prefix = statement[:marker.start()]
            parts = [part for part in _SPLIT_RE.split(prefix) if _clean_operand(part)]
            if len(parts) != 2:
                gaps.append({"kind": "sod_coordinate_unresolved", "gap_type": "sod_coordinate_unresolved", "source_id": source_id, "source_locator": locator})
                continue
            left, right = map(_clean_operand, parts)
            left_roles, right_roles = _roles_in(left, roles), _roles_in(right, roles)
            role_mode = len(left_roles) == 1 and len(right_roles) == 1
            ambiguous_roles = bool(left_roles or right_roles) and not role_mode
            scope = _scope(statement)
            unresolved = bool(_CONDITION_MARKER.search(statement) or ambiguous_roles or not scope)
            contract = {
                "schema_version": CONTRACT_SCHEMA,
                "contract_id": _stable_id("sod_contract", left, right, scope, source_id, locator),
                "left_role": left_roles[0] if role_mode else "",
                "right_role": right_roles[0] if role_mode else "",
                "left_action": "" if role_mode else left,
                "right_action": "" if role_mode else right,
                "resource_ref": "",
                "scope": scope or "unspecified",
                "same_credential_forbidden": True,
                "same_resource_required": True,
                "status": "UNRESOLVED" if unresolved else "RESOLVED",
                "reason_code": "SOD_CONDITION_OR_ROLE_COORDINATE_UNRESOLVED" if unresolved else "",
                "source_backed": True,
                "source_id": source_id, "source_locator": locator,
                "statement": statement,
                "statement_hash": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
                "derivation": "explicit_source_segregation_of_duties",
                "automatic_inference_allowed": False,
            }
            contracts.append(contract)
    by_id = {_text(row.get("contract_id")): row for row in contracts if _text(row.get("contract_id"))}
    for row in by_id.values():
        if _text(row.get("status")).upper() == "UNRESOLVED":
            gaps.append({
                "kind": "sod_contract_unresolved",
                "gap_type": "sod_contract_unresolved",
                "contract_id": _text(row.get("contract_id")),
                "reason_code": _text(row.get("reason_code")) or "SOD_CONTRACT_UNRESOLVED",
                "source_id": _text(row.get("source_id")),
                "source_locator": _text(row.get("source_locator")),
            })
    asset["segregation_of_duties_contracts"] = sorted(by_id.values(), key=lambda row: _text(row.get("contract_id")))
    existing = [dict(row) for row in _list(asset.get("coverage_gaps")) if isinstance(row, dict)]
    asset["coverage_gaps"] = [*existing, *gaps]
    return asset


def _action_tokens(value: Any) -> set[str]:
    raw = _norm(value)
    aliases = {
        "申请": {"申请", "apply", "request", "create", "submit"},
        "审批": {"审批", "审核", "approve", "review", "authorize"},
        "制单": {"制单", "开单", "create"},
        "复核": {"复核", "审核", "review", "approve"},
        "付款": {"付款", "支付", "pay", "payment"},
        "对账": {"对账", "reconcile", "settle"},
    }
    result = {raw} if raw else set()
    for key, values in aliases.items():
        normalized = {_norm(key), *(_norm(item) for item in values)}
        if raw in normalized:
            result.update(normalized)
    return result


def _row_actions(row: dict[str, Any]) -> set[str]:
    return {_norm(value) for value in [*_list(row.get("actions")), row.get("action")] if _norm(value)}


def _row_resource(row: dict[str, Any], interfaces: dict[str, dict[str, Any]]) -> set[str]:
    interface = interfaces.get(_text(row.get("interface_id")), {})
    values = [*_list(interface.get("entity_refs")), interface.get("resource"), row.get("resource_ref")]
    return {_norm(value) for value in values if _norm(value)}


def _candidate_rows(contract: dict[str, Any], side: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    role = _norm(contract.get(f"{side}_role"))
    actions = _action_tokens(contract.get(f"{side}_action"))
    candidates = []
    for row in rows:
        if _text(row.get("decision") or row.get("effect")).lower() not in {"allow", "allowed", "permit", "permitted"}:
            continue
        if role and _norm(row.get("role")) != role:
            continue
        if actions and not actions.intersection(_row_actions(row)):
            continue
        candidates.append(row)
    return candidates


def apply_sod_permission_policies(asset: dict[str, Any]) -> dict[str, Any]:
    contracts = [dict(row) for row in _list(asset.get("segregation_of_duties_contracts")) if isinstance(row, dict) and _text(row.get("status")).upper() == "RESOLVED" and row.get("source_backed") is True]
    rows = [dict(row) for row in _list(asset.get("permission_matrix")) if isinstance(row, dict)]
    interfaces = {_text(row.get("interface_id") or row.get("operation_id")): row for row in _list(asset.get("interfaces")) if isinstance(row, dict)}
    policies: list[dict[str, Any]] = []
    gaps = [dict(row) for row in _list(asset.get("coverage_gaps")) if isinstance(row, dict)]
    rules = [dict(row) for row in _list(asset.get("rule_library")) if isinstance(row, dict)]
    for contract in contracts:
        left_rows = _candidate_rows(contract, "left", rows)
        right_rows = _candidate_rows(contract, "right", rows)
        pairs: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        for left in left_rows:
            for right in right_rows:
                if _text(left.get("interface_id")) == _text(right.get("interface_id")):
                    continue
                resources = _row_resource(left, interfaces).intersection(_row_resource(right, interfaces))
                explicit_resource = _norm(contract.get("resource_ref"))
                if explicit_resource and explicit_resource not in resources:
                    continue
                if contract.get("same_resource_required") is True and len(resources) != 1:
                    continue
                resource = next(iter(resources), explicit_resource)
                pairs.append((left, right, resource))
        unique = {( _text(a.get("interface_id")), _text(b.get("interface_id")), resource): (a,b,resource) for a,b,resource in pairs}
        if len(unique) != 1:
            gaps.append({
                "kind": "sod_permission_binding_unresolved", "gap_type": "sod_permission_binding_unresolved",
                "contract_id": _text(contract.get("contract_id")),
                "left_candidate_count": len(left_rows), "right_candidate_count": len(right_rows),
                "pair_candidate_count": len(unique), "automatic_resolution_allowed": False,
            })
            continue
        left, right, resource = next(iter(unique.values()))
        policy_id = _stable_id("sod_policy", contract.get("contract_id"), left.get("permission_id"), right.get("permission_id"))
        policy = {
            "schema_version": POLICY_SCHEMA, "policy_id": policy_id,
            "contract_id": _text(contract.get("contract_id")),
            "setup_role": _text(left.get("role")), "guarded_role": _text(right.get("role")),
            "setup_operation_ref": _text(left.get("interface_id")), "guarded_operation_ref": _text(right.get("interface_id")),
            "setup_permission_id": _text(left.get("permission_id")), "guarded_permission_id": _text(right.get("permission_id")),
            "resource_ref": resource, "scope": _text(contract.get("scope") or "unspecified"),
            "same_credential_forbidden": True, "same_resource_required": True,
            "status": "RESOLVED", "source_backed": True,
            "source_id": _text(contract.get("source_id")), "source_locator": _text(contract.get("source_locator")),
        }
        policies.append(policy)
        rules.append({
            "rule_id": policy_id,
            "kind": "segregation_of_duties",
            "risk_type": "authorization",
            "operator": "different_credential_required",
            "statement": _text(contract.get("statement")) or f"{policy['setup_role']} and {policy['guarded_role']} require different credentials",
            "entity": resource or "segregation_of_duties",
            "operation_refs": [policy["setup_operation_ref"], policy["guarded_operation_ref"]],
            "operands": [policy],
            "source_id": policy["source_id"], "source_locator": policy["source_locator"],
            "confidence": 0.95,
        })
    asset["segregation_of_duties_policies"] = sorted({p["policy_id"]: p for p in policies}.values(), key=lambda row: row["policy_id"])
    asset["rule_library"] = sorted({_text(r.get("rule_id") or r.get("id")): r for r in rules if _text(r.get("rule_id") or r.get("id"))}.values(), key=lambda row: _text(row.get("rule_id") or row.get("id")))
    asset["coverage_gaps"] = gaps
    summary = _dict(asset.get("summary")); summary["segregation_of_duties_policy_count"] = len(policies); asset["summary"] = summary
    governance = _dict(asset.get("governance")); governance.update({
        "segregation_of_duties_requires_explicit_source_authority": True,
        "segregation_of_duties_role_name_inference_allowed": False,
        "segregation_of_duties_uses_effective_permission_matrix": True,
    }); asset["governance"] = governance
    return asset


__all__ = ["CONTRACT_SCHEMA", "POLICY_SCHEMA", "materialize_sod_contracts", "apply_sod_permission_policies"]
