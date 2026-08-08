# -*- coding: utf-8 -*-
"""Semantic contract binding adapter (industry-neutral).

Root cause it repairs
----------------------
Interface-documented business contracts (per-endpoint ``关键契约`` /
``业务约束`` lines inside an API document) are parsed by the knowledge
extractors as plain rules, but the authoritative rule-to-interface binding
channels only accept rules that either appear verbatim inside an interface
excerpt, mention ASCII contract fields, or share a same-source module with an
already-seeded rule. Pure-Chinese business statements (state machine, money
conservation, idempotency, sensitive-content contracts) therefore never bind
to the interface they are documented at, so no state/idempotency/conservation
obligation is ever scheduled, and the conservation family additionally dies
from a 12-term ``unchanged_sum`` equation built over every amount-typed field.

This adapter re-attaches such rules to their interfaces with exact evidence
and structures their expressions, so the existing Behavior IR builder and
obligation compiler can consume them through the untouched
``rule_to_interface`` channel. It is pure enrichment over visible source
materials: it never invents rules, operations, actors or fields, and every
emitted edge carries a named evidence channel.

Binding channels (all evidence-carrying, all source-grounded)
-------------------------------------------------------------
1. ``section_line_range`` — a rule whose ``source_locator`` is ``line:N`` and
   whose source text is the API document falls inside an endpoint section
   derived from the same document with the parser's endpoint regex. The rule
   IS the endpoint's own contract line.
2. ``verbatim_containment`` — the whitespace-collapsed rule statement is a
   substring of the interface's own excerpt/summary/description.
3. ``cjk_action_term`` — at least two 2-char CJK bigrams of the interface's
   self-declared summary appear in the rule statement (the rule references
   the action the interface documents).

Expression structuring (only when both sides resolve uniquely)
--------------------------------------------------------------
- conservation/amount rules: ``X必须等于Y`` → field_equality equation,
  ``X不得超过/不能超过/不能大于Y`` → upper_bound equation,
  ``不得为负/不能为负`` → non_negative equation.
- state precondition: ``仅 <STATE> 可…`` with an ASCII state name declared by
  the entity's state machine → operand ``{entity_ref, from_state}``.
- sensitive-content response contracts (不得返回/禁止包含/禁止泄露 …密钥/
  密码/凭据/secret/password…) → kind normalized to ``privacy`` so the
  compiler routes them to the privacy family instead of the default.

State-transition binding
------------------------
For state machine transitions (allowed and forbidden), the operation that
performs the transition is the one whose bound contract rules mention the
TO state name verbatim; a unique hit writes an ``operation_ref`` hint the IR
builder resolves by exact source identity.

All functions are pure: the asset is deep-copied before any mutation.
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any, Iterable

# Matches both "### METHOD /path", "### `METHOD /path`" and
# "#### N. `METHOD /path` — title" forms (the knowledge parser accepts the
# same header shapes; backticks are optional around the method/path pair).
_ENDPOINT_HEADER_RE = re.compile(
    r"^#{3,4}\s+(?:(\d+)\.\s+)?`?([A-Z]+)\s+(/api/[^\s`]+)`?(?:\s*—\s*[^\n]*)?$",
    re.M,
)

# Verbatim containment is endpoint-specific evidence only when the statement
# does not recur across many endpoint sections (cross-cutting annotations such
# as shared table rows are not a documented attachment to any one endpoint).
_CONTAINMENT_CAP = 6

_WHITESPACE_RE = re.compile(r"\s+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_CJK_BIGRAM_RE = re.compile(r"[\u4e00-\u9fff]")

_LINE_LOCATOR_RE = re.compile(r"line[:：]\s*(\d+)", re.I)

# Constraint modality markers (language-function words only, industry-neutral).
_EQUALITY_RE = re.compile(r"(必须等于|等于|需等于|应为)")
_UPPER_BOUND_RE = re.compile(r"(不得超过|不能超过|不得大于|不能大于|不得高于|不能高于)")
_NON_NEGATIVE_RE = re.compile(r"(不得为负|不能为负|必须为非负|必须大于等于\s*0|必须≥\s*0)")
_PRECONDITION_RE = re.compile(r"仅\s*([A-Z][A-Z0-9_]{1,40})\s*可")

# Sensitive-content response contract markers (generic security vocabulary:
# response must not disclose secret/credential material).
_SENSITIVE_RESPONSE_RE = re.compile(
    r"(不得返回|禁止包含|禁止泄露|不能返回|不得包含|不得泄露)"
)
_SENSITIVE_TERM_RE = re.compile(
    r"(密钥|密码|凭据|私钥|令牌|证书|secret|password|credential|token|apikey|api_key)",
    re.I,
)

# CJK validation term → field-name token table (mirrors the industry-neutral
# table already used by behavior_ir_core for CJK field binding; language
# categories only, no industry vocabulary).
_CJK_FIELD_TOKENS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("应付金额", "实付金额", "应付"), ("payable_amount", "payable")),
    (("支付金额", "金额"), ("amount", "price", "total", "fee", "cost")),
    (("余额",), ("balance", "wallet", "deposit")),
    (("数量",), ("quantity", "qty", "count")),
    (("总金额", "合计"), ("total_amount", "total", "subtotal")),
    (("折扣", "优惠"), ("discount", "coupon", "promotion")),
    (("退款金额",), ("refund_amount", "refund", "amount")),
)


def _prose_identity(text: str) -> str:
    """Collapse whitespace and literal escape sequences for containment."""
    value = str(text or "")
    for sequence in ("\\n", "\\r", "\\t"):
        value = value.replace(sequence, "")
    return _WHITESPACE_RE.sub("", value)


def _cjk_bigrams(text: str) -> set[str]:
    """2-char sliding CJK bigrams of a text (industry-neutral shape signal)."""
    chars = _CJK_BIGRAM_RE.findall(str(text or ""))
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _short_hash(value: Any) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _clean_statement(text: str) -> str:
    """Strip markdown/annotation noise for semantic analysis only."""
    value = re.sub(r"\*\*关键契约\*\*|关键契约|^[-*#>\s]+", "", str(text or ""))
    return value.strip("：:，。,．;； \t")


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _endpoint_sections(text: str) -> list[dict[str, Any]]:
    """Derive (method, path, [start_line, end_line)) sections from a document.

    Uses the same header shape the markdown API parser consumes, so the line
    ranges match the line numbers the rule extractor records in
    ``source_locator``.
    """
    if not str(text or "").strip():
        return []
    matches = list(_ENDPOINT_HEADER_RE.finditer(str(text)))
    if not matches:
        return []
    total_lines = str(text).count("\n") + 1
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start_line = str(text[: match.start()]).count("\n") + 1
        end_line = (
            str(text[: matches[index + 1].start()]).count("\n") + 1
            if index + 1 < len(matches)
            else total_lines + 1
        )
        sections.append({
            "method": match.group(2).upper(),
            "path": match.group(3),
            "start_line": start_line,
            "end_line": end_line,
        })
    return sections


def _interface_by_id(interfaces: Iterable[Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in _list(interfaces):
        interface_id = _text(row.get("interface_id"))
        if interface_id:
            index.setdefault(interface_id, _dict(row))
    return index


def _rule_source_line(rule: dict[str, Any]) -> int:
    for key in ("source_locator", "locator"):
        match = _LINE_LOCATOR_RE.search(_text(rule.get(key)))
        if match:
            return int(match.group(1))
    return -1


def _rule_source_id(rule: dict[str, Any]) -> str:
    return _text(rule.get("source_id"))


def _interface_text_identity(interface: dict[str, Any]) -> str:
    parts = [
        interface.get("source_excerpt"),
        interface.get("summary"),
        interface.get("title"),
        interface.get("description"),
    ]
    return _prose_identity(" ".join(_text(part) for part in parts))


def _existing_edge_keys(relationships: Iterable[Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for edge in _list(relationships):
        if not isinstance(edge, dict):
            continue
        from_ref = _text(edge.get("from") or edge.get("from_ref"))
        to_ref = _text(edge.get("to") or edge.get("to_ref"))
        if from_ref and to_ref:
            keys.add((from_ref, to_ref))
    return keys


def _edge(
    rule_id: str,
    interface_id: str,
    *,
    channel: str,
    evidence: dict[str, Any],
    confidence: float,
) -> dict[str, Any]:
    identity = {
        "rule": rule_id,
        "interface": interface_id,
        "derivation": "interface_contract_attachment",
        "channel": channel,
    }
    return {
        "edge_id": "edge:" + _short_hash(identity),
        "from": rule_id,
        "to": interface_id,
        "relation": "rule_to_interface",
        "confidence": confidence,
        "status": "accepted",
        "derivation": "interface_contract_attachment",
        "evidence_gate": "interface_contract_attachment",
        "evidence": dict(evidence),
    }


def _bind_rules_to_interfaces(
    rules: list[dict[str, Any]],
    interfaces: list[dict[str, Any]],
    api_spec_text: str,
    existing_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Emit accepted rule_to_interface edges through the three channels."""
    interface_index = _interface_by_id(interfaces)
    sections = _endpoint_sections(api_spec_text)
    section_by_rule_source: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        source_id = _rule_source_id(rule)
        if source_id and source_id not in section_by_rule_source:
            # Rules of this source came from the API document; every section
            # range applies (the line numbers were recorded against this text).
            if "markdown_api" in source_id:
                section_by_rule_source[source_id] = sections
    edges: list[dict[str, Any]] = []
    channel_counts: dict[str, int] = {}
    emitted: set[tuple[str, str]] = set()

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = _text(rule.get("rule_id"))
        statement = _text(rule.get("statement"))
        if not rule_id or not statement:
            continue
        if (rule_id, "") in existing_keys:
            continue
        statement_key = _prose_identity(statement)
        if not statement_key:
            continue
        source_id = _rule_source_id(rule)
        source_line = _rule_source_line(rule)

        # Channel 1: section line range from the same API document.
        if source_line > 0:
            for section in section_by_rule_source.get(source_id, []):
                if not (section["start_line"] <= source_line < section["end_line"]):
                    continue
                interface_id = (
                    f"markdown_api:{section['method']}:{section['path']}"
                )
                if interface_id not in interface_index:
                    continue
                key = (rule_id, interface_id)
                if key in emitted or key in existing_keys:
                    continue
                emitted.add(key)
                channel_counts["section_line_range"] = (
                    channel_counts.get("section_line_range", 0) + 1
                )
                edges.append(_edge(
                    rule_id,
                    interface_id,
                    channel="section_line_range",
                    evidence={
                        "source_id": source_id,
                        "source_line": source_line,
                        "operation_locator": (
                            f"{section['method']} {section['path']}"
                        ),
                        "statement_hash": _short_hash(statement_key),
                    },
                    confidence=0.97,
                ))

        # Channel 2: verbatim containment inside the interface's OWN source
        # section (same document). Cross-document containment is not an
        # interface attachment: a PRD sentence that happens to appear in an
        # API excerpt is not evidence the rule was documented at that endpoint.
        # A statement that fires containment across many endpoints is a
        # cross-cutting annotation (e.g. a shared "普通用户只能使用自己的 ID"
        # table row), not endpoint-specific evidence — it is skipped here and
        # its permission semantics stay with the permission-matrix channel.
        containment_hits: list[tuple[dict[str, Any], str]] = []
        if not any(
            (rule_id, interface_id) in emitted or (rule_id, interface_id) in existing_keys
            for interface_id in interface_index
        ):
            for interface_id, interface in interface_index.items():
                if _text(interface.get("source_id")) != source_id:
                    continue
                interface_text = _interface_text_identity(interface)
                if interface_text and statement_key in interface_text:
                    containment_hits.append((interface, interface_text))
            if 0 < len(containment_hits) <= _CONTAINMENT_CAP:
                for interface, interface_text in containment_hits:
                    key = (rule_id, _text(interface.get("interface_id")))
                    if key in emitted or key in existing_keys:
                        continue
                    emitted.add(key)
                    channel_counts["verbatim_containment"] = (
                        channel_counts.get("verbatim_containment", 0) + 1
                    )
                    edges.append(_edge(
                        rule_id,
                        _text(interface.get("interface_id")),
                        channel="verbatim_containment",
                        evidence={
                            "operation_locator": (
                                f"{_text(interface.get('method')).upper()} "
                                f"{_text(interface.get('path'))}"
                            ),
                            "statement_hash": _short_hash(statement_key),
                            "interface_text_hash": _short_hash(interface_text),
                        },
                        confidence=0.92,
                    ))

        # Channel 3: CJK action-term binding for rules that are not documented
        # inside any interface section (typically PRD rules). The interface's
        # own summary is its self-declared action phrase; when at least two of
        # its CJK bigrams appear in the rule statement, the rule references
        # that action. To stay conservative the candidate set must be small
        # (generic bigrams like 订单/支付/状态 shared by many endpoints are
        # then not evidence of a specific attachment).
        candidates: list[tuple[dict[str, Any], set[str]]] = []
        if not any(
            (rule_id, interface_id) in emitted or (rule_id, interface_id) in existing_keys
            for interface_id in interface_index
        ):
            rule_bigrams = _cjk_bigrams(statement)
            if len(rule_bigrams) >= 8:
                for interface_id, interface in interface_index.items():
                    summary = _text(interface.get("summary"))
                    if len(summary) < 4:
                        continue
                    overlap = rule_bigrams & _cjk_bigrams(summary)
                    if len(overlap) >= 2:
                        candidates.append((interface, overlap))
                candidates.sort(
                    key=lambda row: (
                        -len(row[1]),
                        _text(row[0].get("interface_id")),
                    )
                )
                if len(candidates) <= 3:
                    for interface, overlap in candidates:
                        interface_id = _text(interface.get("interface_id"))
                        key = (rule_id, interface_id)
                        if key in emitted or key in existing_keys:
                            continue
                        emitted.add(key)
                        channel_counts["cjk_action_term"] = (
                            channel_counts.get("cjk_action_term", 0) + 1
                        )
                        edges.append(_edge(
                            rule_id,
                            interface_id,
                            channel="cjk_action_term",
                            evidence={
                                "operation_locator": (
                                    f"{_text(interface.get('method')).upper()} "
                                    f"{_text(interface.get('path'))}"
                                ),
                                "matched_bigrams": sorted(overlap),
                                "summary": _text(interface.get("summary"))[:120],
                                "statement_hash": _short_hash(statement_key),
                            },
                            confidence=0.86,
                        ))
    return edges, channel_counts


def _field_index(
    asset: dict[str, Any],
    interfaces: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index canonical fields: name → {entity, field_id, description, table}.

    Sources: data_tables columns, field_dictionary rows, and interface request
    schema properties (with descriptions). All are visible source materials.
    """
    index: dict[str, dict[str, Any]] = {}

    def _add(entity: str, field: str, description: str, table: str) -> None:
        key = f"{_text(entity).lower()}.{_text(field).lower()}"
        if key in index:
            return
        index[key] = {
            "entity": _text(entity),
            "field": _text(field),
            "description": _text(description),
            "table": _text(table),
        }

    for table in _list(asset.get("data_tables")) + _list(asset.get("tables")):
        table_name = _text(table.get("name"))
        for column in _list(table.get("columns")):
            if isinstance(column, dict):
                _add(table_name, column.get("name"), column.get("description") or "", table_name)
            elif _text(column):
                _add(table_name, column, "", table_name)
    for field in _list(asset.get("field_dictionary")):
        _add(
            field.get("table") or field.get("entity") or "",
            field.get("field") or field.get("name") or "",
            field.get("description") or "",
            field.get("table") or "",
        )
    for interface in interfaces:
        request_schema = _dict(interface.get("request_schema"))
        props = _dict(request_schema.get("properties"))
        if not props:
            content = _dict(request_schema.get("content"))
            json_media = _dict(content.get("application/json"))
            props = _dict(_dict(json_media.get("schema")).get("properties"))
        entity = _interface_table_hint(interface)
        for field_name, prop in props.items():
            if isinstance(prop, dict):
                _add(entity, field_name, prop.get("description") or "", entity)
            else:
                _add(entity, field_name, "", entity)
    return index


def _interface_table_hint(interface: dict[str, Any]) -> str:
    """Naming-convention table hint from the interface path (evidence-carrying).

    The first path segment after ``/api/`` names the resource the operation
    operates on; entity/table rows share the same name in source materials.
    Used only to disambiguate field resolution, never to invent fields.
    """
    parts = [part for part in _text(interface.get("path") or "").split("/") if part]
    if len(parts) >= 2 and parts[0].lower() == "api" and parts[1]:
        return parts[1]
    return parts[0] if parts else ""


def _resolve_field(
    side: str,
    field_index: dict[str, dict[str, Any]],
    table_hint: str,
) -> list[dict[str, Any]]:
    """Resolve one equation side to canonical fields, conservatively.

    Priority: exact ASCII field name in the statement → field description
    verbatim containment → CJK term → field-name token mapping (with the
    interface table hint disambiguating). Returns [] when not unique.
    """
    side = _text(side)
    if not side:
        return []
    candidates: list[dict[str, Any]] = []
    exact = _text(re.sub(r"[`\"]", "", side))
    if exact and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", exact):
        candidates = [
            row for row in field_index.values()
            if _text(row["field"]).lower() == exact.lower()
        ]
    if not candidates:
        candidates = [
            row for row in field_index.values()
            if _text(row["description"])
            and _prose_identity(side) in _prose_identity(row["description"])
        ]
    if not candidates:
        for terms, tokens in _CJK_FIELD_TOKENS:
            if not any(term in side for term in terms):
                continue
            for row in field_index.values():
                field_lower = _text(row["field"]).lower()
                if any(
                    field_lower == token
                    or field_lower.endswith("_" + token)
                    or field_lower.startswith(token + "_")
                    for token in tokens
                ):
                    candidates.append(row)
            break
    if not candidates:
        return []
    # Table-hint disambiguation (interface resource naming convention).
    if table_hint:
        hinted = [
            row for row in candidates
            if _text(row["table"]).lower() == table_hint.lower()
        ]
        if len(hinted) == 1:
            return hinted
    deduped: dict[str, dict[str, Any]] = {}
    for row in candidates:
        key = f"{_text(row['entity']).lower()}.{_text(row['field']).lower()}"
        deduped.setdefault(key, row)
    if len(deduped) == 1:
        return list(deduped.values())
    return []


def _structure_conservation_equation(
    rule: dict[str, Any],
    field_index: dict[str, dict[str, Any]],
    table_hint: str,
) -> bool:
    """Structure a conservation rule's equation when both sides resolve."""
    statement = _clean_statement(rule.get("statement") or "")
    if not statement:
        return False
    operands: list[dict[str, Any]] = []
    equation: dict[str, Any] = {}

    match = _NON_NEGATIVE_RE.search(statement)
    if match:
        candidates = _resolve_field(statement, field_index, table_hint)
        if len(candidates) == 1:
            row = candidates[0]
            operands = [{
                "entity_ref": row["entity"],
                "field": row["field"],
                "expected_delta_direction": "non_negative",
            }]
            equation = {"operator": "non_negative", "terms": [_text(row["field"])]}
    else:
        equality = _EQUALITY_RE.search(statement)
        upper = _UPPER_BOUND_RE.search(statement)
        splitter = equality or upper
        if splitter:
            left = statement[: splitter.start()]
            right = statement[splitter.end():]
            if right:
                left_fields = _resolve_field(left, field_index, table_hint)
                right_fields = _resolve_field(right, field_index, table_hint)
                if len(left_fields) == 1 and len(right_fields) == 1:
                    lhs, rhs = left_fields[0], right_fields[0]
                    if (
                        f"{_text(lhs['entity']).lower()}.{_text(lhs['field']).lower()}"
                        != f"{_text(rhs['entity']).lower()}.{_text(rhs['field']).lower()}"
                    ):
                        operands = [
                            {"entity_ref": lhs["entity"], "field": lhs["field"]},
                            {"entity_ref": rhs["entity"], "field": rhs["field"]},
                        ]
                        equation = {
                            "operator": (
                                "field_equality" if equality else "upper_bound"
                            ),
                            "terms": [
                                {"entity": lhs["entity"], "field": lhs["field"]},
                                {"entity": rhs["entity"], "field": rhs["field"]},
                            ],
                        }
    if not operands or not equation:
        return False
    rule["operands"] = operands
    rule["equation"] = equation
    return True


def _structure_state_precondition(
    rule: dict[str, Any],
    asset: dict[str, Any],
) -> bool:
    """Structure ``仅 <STATE> 可…`` preconditions with declared state names."""
    statement = _clean_statement(rule.get("statement") or "")
    match = _PRECONDITION_RE.search(statement)
    if not match:
        return False
    state_name = match.group(1)
    declared = {
        _text(name if isinstance(name, str) else name.get("name")).upper()
        for machine in (
            _list(asset.get("state_machines"))
            + _list(asset.get("states"))
        )
        if isinstance(machine, dict)
        for name in _list(machine.get("states"))
        if isinstance(name, str) or (isinstance(name, dict) and name.get("name"))
    }
    if state_name.upper() not in declared:
        return False
    entity = ""
    for machine in (
        _list(asset.get("state_machines"))
        + _list(asset.get("states"))
    ):
        if isinstance(machine, dict):
            entity = _text(machine.get("entity") or machine.get("object"))
            if entity:
                break
    rule["operands"] = [{
        "entity_ref": entity,
        "from_state": state_name,
    }]
    return True


def _is_sensitive_response_contract(statement: str) -> bool:
    return bool(
        _SENSITIVE_RESPONSE_RE.search(statement)
        and _SENSITIVE_TERM_RE.search(statement)
    )


def _structure_rule_expressions(
    rules: list[dict[str, Any]],
    asset: dict[str, Any],
    interfaces: list[dict[str, Any]],
    bound_rule_ids: set[str],
) -> dict[str, int]:
    """Structure expressions of interface-bound rules (conservation/privacy)."""
    field_index = _field_index(asset, interfaces)
    counts = {
        "conservation_structured": 0,
        "state_precondition_structured": 0,
        "privacy_rekind": 0,
    }
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = _text(rule.get("rule_id"))
        if not rule_id or rule_id not in bound_rule_ids:
            continue
        if _list(rule.get("operands")) or _dict(rule.get("equation")):
            continue
        kind = _text(rule.get("kind") or rule.get("risk_type") or "").lower()
        statement = _text(rule.get("statement"))
        table_hint = ""
        bound_interface = _bound_interface_for_rule(asset, rule_id)
        if bound_interface:
            table_hint = _interface_table_hint(bound_interface)
        if any(
            token in kind
            for token in ("conserv", "data_conservation", "amount", "balance", "quantity")
        ):
            if _structure_conservation_equation(rule, field_index, table_hint):
                counts["conservation_structured"] += 1
                continue
        if any(token in kind for token in ("state_machine", "state", "business_rule")):
            if _structure_state_precondition(rule, asset):
                counts["state_precondition_structured"] += 1
        if _is_sensitive_response_contract(statement):
            rule["kind"] = "privacy"
            rule["risk_type"] = "privacy"
            counts["privacy_rekind"] += 1
    return counts


def _bound_interface_for_rule(
    asset: dict[str, Any],
    rule_id: str,
) -> dict[str, Any]:
    for edge in _list(asset.get("relationships")):
        if not isinstance(edge, dict):
            continue
        if (
            _text(edge.get("from") or edge.get("from_ref")) == rule_id
            and _text(edge.get("relation") or edge.get("relation_type")).lower()
            == "rule_to_interface"
            and _text(edge.get("status")).lower() == "accepted"
        ):
            to_ref = _text(edge.get("to") or edge.get("to_ref"))
            for interface in _list(asset.get("interfaces")):
                if _text(interface.get("interface_id")) == to_ref:
                    return interface
    return {}


def _state_names_by_entity(asset: dict[str, Any]) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    for machine in (
        _list(asset.get("state_machines"))
        + _list(asset.get("states"))
    ):
        if not isinstance(machine, dict):
            continue
        entity = _text(machine.get("entity") or machine.get("object") or "").lower()
        for state in _list(machine.get("states")):
            name = _text(state if isinstance(state, str) else state.get("name"))
            if name:
                names.setdefault(entity, set()).add(name)
    return names


def _bound_statements_by_interface(
    asset: dict[str, Any],
) -> dict[str, list[str]]:
    """Map interface_id → statements of rules bound to it (accepted edges)."""
    by_rule: dict[str, str] = {}
    for rule in _list(asset.get("rule_library")):
        if isinstance(rule, dict) and _text(rule.get("rule_id")):
            by_rule[_text(rule.get("rule_id"))] = _text(rule.get("statement"))
    result: dict[str, list[str]] = {}
    for edge in _list(asset.get("relationships")):
        if not isinstance(edge, dict):
            continue
        if (
            _text(edge.get("relation") or edge.get("relation_type")).lower()
            != "rule_to_interface"
            or _text(edge.get("status")).lower() != "accepted"
        ):
            continue
        rule_id = _text(edge.get("from") or edge.get("from_ref"))
        interface_id = _text(edge.get("to") or edge.get("to_ref"))
        statement = by_rule.get(rule_id)
        if rule_id and interface_id and statement:
            result.setdefault(interface_id, []).append(statement)
    return result


def _bind_state_transitions_to_operations(asset: dict[str, Any]) -> int:
    """Bind transitions whose TO state is mentioned by the interface's rules.

    For each transition (allowed or forbidden) of an entity state machine,
    find write interfaces whose bound contract/effect rules mention the TO
    state name verbatim; candidates in one module resolve by strongest
    evidence (most mentions, stable interface-id tie-break). A unique hit
    writes ``operation_ref`` so the IR builder resolves the operation by
    exact source identity.
    """
    bound_statements = _bound_statements_by_interface(asset)
    interface_index = _interface_by_id(_list(asset.get("interfaces")))
    _WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    # A state change is performed by the operation whose documented contract
    # declares an EFFECT on the state (变为/置为/转为/进入/必须变为…): the
    # TO-state mention must ride an effect marker. Precondition statements
    # ("仅 PAID 可发货" / "订单必须为 PAID 或 COMPLETED") name the source
    # state the entity must already be in — they are not evidence that this
    # operation moves the entity INTO that state. Transition-LIST declaration
    # lines ("禁止 `CANCELLED -> PAID`…") declare the machine, not a performer.
    _EFFECT_MARKERS = ("变为", "置为", "转为", "进入", "改成", "更新为", "变更为", "必须变为", "must_become")
    # Index: TO state (upper) → [(interface_id, mention_count)].
    to_state_interfaces: dict[str, dict[str, int]] = {}
    for interface_id, statements in bound_statements.items():
        interface = interface_index.get(interface_id)
        if not interface:
            continue
        if str(interface.get("method") or "").upper() not in _WRITE_METHODS:
            continue
        for statement in statements:
            if "->" in statement or "禁止" in statement or "状态机" in statement:
                continue
            if not any(marker in statement for marker in _EFFECT_MARKERS):
                continue
            for match in re.finditer(r"\b([A-Z][A-Z0-9_]{2,40})\b", statement):
                state_token = match.group(1).upper()
                if state_token in {
                    "HTTP", "JSON", "API", "UUID", "ID", "OK", "PAY", "GET",
                    "POST", "PUT", "PATCH", "DELETE",
                }:
                    continue
                counts = to_state_interfaces.setdefault(state_token, {})
                counts[interface_id] = counts.get(interface_id, 0) + 1
    bound_count = 0
    for machine in _list(asset.get("state_machines")):
        if not isinstance(machine, dict):
            continue
        entity = _text(machine.get("entity") or machine.get("object") or "").lower()
        for key in ("transitions", "forbidden_transitions"):
            for transition in _list(machine.get(key)):
                if not isinstance(transition, dict):
                    continue
                if _text(
                    transition.get("operation_ref")
                    or transition.get("operation_id")
                    or transition.get("operation")
                ):
                    continue
                to_name = _text(transition.get("to") or transition.get("to_state")).upper()
                if not to_name or "任" in to_name:
                    continue
                candidates = dict(to_state_interfaces.get(to_name, {}))
                if not candidates:
                    continue
                # Same-module candidates are the same business operation family
                # (e.g. pay vs pay-by-balance); resolve by strongest evidence
                # with a stable tie-break instead of dropping the transition.
                modules = {
                    _path_module_prefix(_text(interface_index[cand].get("path")))
                    for cand in candidates
                    if cand in interface_index
                }
                if len(modules) != 1:
                    continue
                best_count = max(candidates.values())
                best_ids = [
                    cand for cand in candidates
                    if candidates[cand] == best_count
                ]
                interface_id = min(
                    best_ids,
                    key=lambda cand: _text(interface_index[cand].get("path")),
                )
                transition["operation_ref"] = interface_id
                transition["bound_operation_evidence"] = {
                    "to_state": to_name,
                    "interface_id": interface_id,
                    "entity": entity,
                    "derivation": "to_state_contract_mention",
                    "candidate_interface_ids": sorted(candidates),
                }
                bound_count += 1
    return bound_count


def _path_module_prefix(path: str) -> str:
    parts = [part for part in str(path or "").split("/") if part]
    if len(parts) >= 2 and parts[0].lower() == "api":
        return "/" + "/".join(parts[:2])
    return parts[0] if parts else ""


SCHEMA_VERSION = "qualibug.semantic-contract-binding.v1"


def apply_semantic_contract_binding(
    asset: dict[str, Any] | None,
    *,
    api_spec_text: str = "",
) -> dict[str, Any]:
    """Enrich an asset with interface-contract rule binding and structuring.

    Pure function: returns a new asset dict; the input is never mutated.
    """
    source = deepcopy(dict(asset or {}))
    interfaces = [
        row for row in _list(source.get("interfaces")) if isinstance(row, dict)
    ]
    rules = [row for row in _list(source.get("rule_library")) if isinstance(row, dict)]

    # Interface-declared contract rules (OpenAPI description 业务约束/关键契约
    # lines): the runtime API material documents per-endpoint contracts inside
    # the operation's own description, but no extractor turns them into rules.
    # Materialize them here as operation-attached rules (source_locator carries
    # ``#interface=<id>`` so the Behavior IR builder treats them as attached
    # to the operation that declared them) and bind them with accepted edges.
    contract_rules, contract_edges = _materialize_interface_contract_rules(
        interfaces,
        existing_statements={_text(r.get("statement")) for r in rules},
    )
    if contract_rules:
        rules = [*rules, *contract_rules]
        source["rule_library"] = rules
    if contract_edges:
        source["relationships"] = [
            *_list(source.get("relationships")),
            *contract_edges,
        ]

    existing_keys = _existing_edge_keys(_list(source.get("relationships")))
    new_edges, channel_counts = _bind_rules_to_interfaces(
        rules,
        interfaces,
        api_spec_text,
        existing_keys,
    )
    if new_edges:
        source["relationships"] = [
            *_list(source.get("relationships")),
            *new_edges,
        ]

    # Bound rule ids = rules participating in accepted edges.
    bound_rule_ids: set[str] = set()
    for edge in _list(source.get("relationships")):
        if (
            isinstance(edge, dict)
            and _text(edge.get("relation") or edge.get("relation_type")).lower()
            == "rule_to_interface"
            and _text(edge.get("status")).lower() == "accepted"
        ):
            bound_rule_ids.add(_text(edge.get("from") or edge.get("from_ref")))
    bound_rule_ids = {rule_id for rule_id in bound_rule_ids if rule_id}

    structure_counts = _structure_rule_expressions(
        rules,
        source,
        interfaces,
        bound_rule_ids,
    )
    transition_count = _bind_state_transitions_to_operations(source)

    source["semantic_contract_binding_receipt"] = {
        "schema_version": SCHEMA_VERSION,
        "edge_count": len(new_edges) + len(contract_edges),
        "channel_counts": dict(channel_counts),
        "materialized_contract_rule_count": len(contract_rules),
        "structured_rule_count": sum(structure_counts.values()),
        "structure_counts": dict(structure_counts),
        "state_transition_bound_count": transition_count,
        "bound_rule_count": len(bound_rule_ids),
    }
    return source


def _materialize_interface_contract_rules(
    interfaces: list[dict[str, Any]],
    *,
    existing_statements: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn interface-declared contract lines into operation-attached rules.

    The runtime API material documents per-endpoint business contracts in the
    operation's own description ("业务约束：…" / "关键契约：…" lines). The
    knowledge extractors parse these as interface prose but never as rules, so
    the contract semantics die at the interface. This materializer splits the
    declared contract into atomic constraint statements and creates rules that
    are operation-attached by construction (``source_locator`` ends with
    ``#interface=<interface_id>``, the same identity the Behavior IR builder
    already recognizes as ``openapi_interface_prose`` attachment).
    """
    from ._parsing import _risk_type_from_text  # noqa: PLC0415

    _CONTRACT_MARKERS = (
        "业务约束",
        "关键契约",
        "约束",
    )
    _ATOMIC_SPLIT_RE = re.compile(r"[；;。]")
    rules: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for interface in interfaces:
        interface_id = _text(interface.get("interface_id"))
        if not interface_id:
            continue
        description = _text(interface.get("description"))
        summary = _text(interface.get("summary"))
        contract_text = ""
        for part in (description, summary):
            marker_index = -1
            for marker in _CONTRACT_MARKERS:
                index = part.find(marker)
                if index >= 0 and (marker_index < 0 or index < marker_index):
                    marker_index = index
            if marker_index >= 0:
                remainder = part[marker_index:]
                contract_text = contract_text or remainder
                break
        if not contract_text:
            continue
        # Strip the leading marker label (关键契约：/业务约束：).
        contract_text = re.sub(r"^(业务约束|关键契约|约束)\s*[：:]?\s*", "", contract_text)
        for raw in _ATOMIC_SPLIT_RE.split(contract_text):
            statement = _clean_statement(raw)
            if len(statement) < 6:
                continue
            # Constraint statements must carry a modality signal.
            if not re.search(r"(必须|不得|不能|仅|禁止|应|需|不允许|须)", statement):
                continue
            statement_key = _prose_identity(statement)
            if not statement_key or statement_key in existing_statements:
                continue
            key = (interface_id, statement_key)
            if key in seen:
                continue
            seen.add(key)
            existing_statements.add(statement_key)
            source_id = _text(interface.get("source_id")) or "api_spec"
            rule_id = (
                f"rule:interface_contract:{_short_hash(interface_id)}:"
                f"{_short_hash(statement_key)}"
            )
            kind = _risk_type_from_text(statement) or "business_rule"
            rule = {
                "rule_id": rule_id,
                "source_id": source_id,
                "source_type": _text(interface.get("source_kind") or source_id),
                "source_locator": f"{source_id}#interface={interface_id}",
                "statement": statement,
                "risk_type": kind,
                "kind": kind,
                "severity": "P2",
                "tokens": [],
                # Operation attachment by construction: the rule IS the
                # interface's own declared contract, so its operation identity
                # is the interface that declared it (exact source identity).
                "operation_ref": interface_id,
                "operation_refs": [interface_id],
            }
            rules.append(rule)
            edges.append(_edge(
                rule_id,
                interface_id,
                channel="interface_contract_declaration",
                evidence={
                    "operation_locator": (
                        f"{_text(interface.get('method')).upper()} "
                        f"{_text(interface.get('path'))}"
                    ),
                    "statement_hash": _short_hash(statement_key),
                    "interface_id": interface_id,
                },
                confidence=0.98,
            ))
    return rules, edges


__all__ = ["SCHEMA_VERSION", "apply_semantic_contract_binding"]
