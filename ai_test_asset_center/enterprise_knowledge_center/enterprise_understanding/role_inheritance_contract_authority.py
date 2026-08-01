"""Source-backed role inheritance projected onto the existing permission matrix.

Role inheritance is not inferred from role names, seniority words, account order or
organizational proximity.  Only an explicit structured declaration or an explicit source
statement creates a hierarchy edge.  The authority expands effective permissions without
widening scope, inheriting temporary delegation, or binding credentials across accounts.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable


CONTRACT_SCHEMA = "qualibug.role-inheritance-contract.v1"
RECEIPT_SCHEMA = "qualibug.role-inheritance-receipt.v1"

_SCOPE_ALIASES = {
    "": "unspecified",
    "unspecified": "unspecified",
    "all": "all",
    "global": "all",
    "all_tenants": "all_tenants",
    "跨租户": "all_tenants",
    "全租户": "all_tenants",
    "所有租户": "all_tenants",
    "own": "own",
    "self": "own",
    "own_tenant": "own_tenant",
    "本租户": "own_tenant",
    "当前租户": "own_tenant",
    "同一租户": "own_tenant",
    "own_organization": "own_organization",
    "本组织": "own_organization",
    "当前组织": "own_organization",
    "own_department": "own_department",
    "本部门": "own_department",
    "当前部门": "own_department",
    "own_warehouse": "own_warehouse",
    "本仓库": "own_warehouse",
    "当前仓库": "own_warehouse",
    "own_project": "own_project",
    "本项目": "own_project",
    "当前项目": "own_project",
    "own_region": "own_region",
    "本区域": "own_region",
    "当前区域": "own_region",
}
_ALL_SCOPES = frozenset({"all", "all_tenants", "global"})
_CONDITION_MARKERS = re.compile(
    r"(?:如果|若|当|仅当|除非|金额|额度|时间|日期|工作日|小时|天内|before|after|if\b|when\b|unless\b)",
    re.I,
)
_INHERITANCE_MARKER = re.compile(
    r"(?:继承|承接|包含|拥有).{0,12}(?:权限|permission)|inherits?.{0,32}permissions?",
    re.I,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _norm(value: Any) -> str:
    return re.sub(r"[\s，,。；;：:（）()【】\[\]“”\"'、]+", "", _text(value)).casefold()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _canonical_scope(value: Any) -> str:
    raw = _text(value)
    normalized = _norm(raw)
    for alias, canonical in _SCOPE_ALIASES.items():
        if normalized == _norm(alias):
            return canonical
    return raw or "unspecified"


def _scope_from_statement(statement: str) -> tuple[str, bool]:
    normalized = _norm(statement)
    matches = [
        canonical
        for alias, canonical in _SCOPE_ALIASES.items()
        if alias and _norm(alias) in normalized
    ]
    distinct = sorted(set(matches))
    if len(distinct) > 1:
        return "", True
    if distinct:
        return distinct[0], False
    has_unbound_condition = bool(_CONDITION_MARKERS.search(statement))
    if re.search(r"在[^，。；;]{1,24}(?:内|范围内)", statement) and not distinct:
        has_unbound_condition = True
    return "unspecified", has_unbound_condition


def _known_roles(asset: dict[str, Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for row in [*_list(asset.get("roles")), *_list(asset.get("permission_matrix"))]:
        if not isinstance(row, dict):
            continue
        role = _text(row.get("role") or row.get("name") or row.get("actor"))
        if role:
            roles.setdefault(_norm(role), role)
    return roles


def _source_statements(source: dict[str, Any]) -> Iterable[tuple[str, str]]:
    text = _text(source.get("text") or source.get("content"))
    filename = _text(source.get("filename") or source.get("name") or "source")
    if not text:
        return []
    statements: list[tuple[str, str]] = []
    for match in re.finditer(r"[^。；;\n]+[。；;]?", text):
        statement = match.group(0).strip().rstrip("。；;")
        if statement:
            statements.append(
                (statement, f"{filename}#chars={match.start()}-{match.end()}")
            )
    return statements


def _structured_contracts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for index, raw in enumerate(_list(asset.get("roles"))):
        if not isinstance(raw, dict):
            continue
        child = _text(raw.get("role") or raw.get("name"))
        parents = [
            raw.get("inherits_from"),
            raw.get("parent_role"),
            raw.get("base_role"),
            *_list(raw.get("inherited_roles")),
            *_list(raw.get("parent_roles")),
        ]
        for parent_value in parents:
            parent = _text(parent_value)
            if not child or not parent:
                continue
            scope = _canonical_scope(
                raw.get("inheritance_scope") or raw.get("scope") or "unspecified"
            )
            source_id = _text(raw.get("source_id")) or "roles"
            locator = _text(raw.get("source_locator")) or f"roles[{index}]"
            contracts.append(
                {
                    "schema_version": CONTRACT_SCHEMA,
                    "contract_id": _stable_id(
                        "role_inheritance", child, parent, scope, source_id, locator
                    ),
                    "inheriting_role": child,
                    "inherited_role": parent,
                    "scope": scope,
                    "status": "RESOLVED" if child != parent else "UNRESOLVED",
                    "reason_code": "" if child != parent else "ROLE_INHERITANCE_SELF_CYCLE",
                    "source_backed": True,
                    "source_id": source_id,
                    "source_locator": locator,
                    "derivation": "structured_role_inheritance",
                    "automatic_inference_allowed": False,
                }
            )
    return contracts


def _maximal_role_mentions(segment: str, roles: list[str]) -> list[str]:
    spans: dict[str, list[tuple[int, int]]] = {
        role: [
            (match.start(), match.end())
            for match in re.finditer(re.escape(role), segment)
        ]
        for role in roles
    }
    selected: list[str] = []
    for role in roles:
        role_spans = spans.get(role) or []
        if not role_spans:
            continue
        fully_nested = all(
            any(
                other != role
                and len(other) > len(role)
                and left >= other_left
                and right <= other_right
                for other in roles
                for other_left, other_right in spans.get(other, [])
            )
            for left, right in role_spans
        )
        if not fully_nested:
            selected.append(role)
    return sorted(set(selected), key=lambda value: (-len(value), value))


def _text_contracts(
    asset: dict[str, Any], sources: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    known = _known_roles(asset)
    roles = sorted(known.values(), key=lambda value: (-len(value), value))
    contracts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for raw_source in sources:
        if not isinstance(raw_source, dict):
            continue
        source_id = _text(raw_source.get("source_id")) or "source"
        for statement, locator in _source_statements(raw_source):
            if not _INHERITANCE_MARKER.search(statement):
                continue
            candidates: list[tuple[str, str]] = []
            marker = re.search(r"继承|承接|包含|拥有|inherits?", statement, re.I)
            if marker:
                left = statement[: marker.start()]
                right = statement[marker.end() :]
                children = _maximal_role_mentions(left, roles)
                parents = _maximal_role_mentions(right, roles)
                candidates = sorted(
                    {
                        (child, parent)
                        for child in children
                        for parent in parents
                        if child != parent
                    }
                )
            if len(candidates) != 1:
                gaps.append(
                    {
                        "kind": "role_inheritance_coordinate_unresolved",
                        "gap_type": "role_inheritance_coordinate_unresolved",
                        "source_id": source_id,
                        "source_locator": locator,
                        "statement_hash": hashlib.sha256(
                            statement.encode("utf-8")
                        ).hexdigest(),
                        "candidate_role_pairs": [list(row) for row in candidates],
                    }
                )
                continue
            child, parent = candidates[0]
            scope, condition_unbound = _scope_from_statement(statement)
            status = "UNRESOLVED" if condition_unbound else "RESOLVED"
            reason = (
                "ROLE_INHERITANCE_CONDITION_UNBOUND" if condition_unbound else ""
            )
            contracts.append(
                {
                    "schema_version": CONTRACT_SCHEMA,
                    "contract_id": _stable_id(
                        "role_inheritance", child, parent, scope, source_id, locator
                    ),
                    "inheriting_role": child,
                    "inherited_role": parent,
                    "scope": scope,
                    "status": status,
                    "reason_code": reason,
                    "source_backed": True,
                    "source_id": source_id,
                    "source_locator": locator,
                    "statement_hash": hashlib.sha256(
                        statement.encode("utf-8")
                    ).hexdigest(),
                    "derivation": "explicit_source_role_inheritance",
                    "automatic_inference_allowed": False,
                }
            )
    return contracts, gaps


def materialize_role_inheritance_contracts(
    asset: dict[str, Any], sources: Iterable[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Collect explicit hierarchy declarations without deriving permissions yet."""
    existing = [
        dict(row)
        for row in _list(asset.get("role_inheritance_contracts"))
        if isinstance(row, dict)
    ]
    contracts = [*existing, *_structured_contracts(asset)]
    text_gaps: list[dict[str, Any]] = []
    if sources is not None:
        source_contracts, text_gaps = _text_contracts(asset, list(sources))
        contracts.extend(source_contracts)
    by_id = {
        _text(row.get("contract_id")): row
        for row in contracts
        if _text(row.get("contract_id"))
    }
    asset["role_inheritance_contracts"] = sorted(
        by_id.values(), key=lambda row: _text(row.get("contract_id"))
    )
    gaps = [
        dict(row) for row in _list(asset.get("coverage_gaps")) if isinstance(row, dict)
    ]
    gap_keys = {
        (
            _text(row.get("gap_type") or row.get("kind")),
            _text(row.get("source_id")),
            _text(row.get("source_locator")),
        )
        for row in gaps
    }
    for gap in text_gaps:
        key = (
            _text(gap.get("gap_type")),
            _text(gap.get("source_id")),
            _text(gap.get("source_locator")),
        )
        if key not in gap_keys:
            gaps.append(gap)
            gap_keys.add(key)
    asset["coverage_gaps"] = gaps
    return asset


__all__ = [
    "CONTRACT_SCHEMA",
    "RECEIPT_SCHEMA",
    "materialize_role_inheritance_contracts",
]
