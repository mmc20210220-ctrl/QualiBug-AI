"""Rule contract-field validation binding — coupon/constraint semantic layer.

A source rule that constrains an ENTITY's field eligibility (优惠券必须在有效期
内 → expires_at, 优惠券状态必须为 ACTIVE → status, 折扣券必须遵守封顶金额 →
max_discount) is a validation contract on the operations that consume that
entity: the consuming operation must reject inputs whose entity state violates
the constraint. Without this binding the rule stays an unbound invariant, the
obligation compiler's ``validation -> validation_rejection`` mapping never
fires, and the defect class (validation accepts violating inputs) stays
invisible.

This stage is the coupon semantic expression layer of the generic
rule-to-interface binding channel. It is fully data-driven:

* the SUBJECT of the rule's grounded semantic frame is resolved to schema
  tables through the entity field nodes' own source descriptions (visible
  enterprise material — a rule that says 优惠券 must satisfy X binds to the
  entity whose declared fields carry 优惠券 in their source description);
* the rule's CONSTRAINT vocabulary (state / validity / usage / scope / cap /
  minimum / money — industry-neutral business language, never industry
  terms) is matched against the resolved entity's declared fields only;
* the entity's consuming operations are found through path/module identity
  and request/response contract-field overlap — never bare trigger-text
  token matching;
* the derived invariant carries kind ``validation`` with entity-scoped
  operands and the constraint operator, so the existing downstream compiler /
  validation_rejection evaluator / observer / delivery gate take over
  unchanged.

The stage is ADDITIVE and non-duplicating: it derives an invariant for a rule
only when every invariant already derived from that rule is unbound (no
operation refs), and it never touches invariants the shared subject-frame
channel or legacy binding already resolved. Rules that cannot resolve to a
unique entity + field + consuming operation stay unbound and are counted in
the receipt (never fabricated).

Honesty invariants: operands carry field identities from the entity's own
declared schema (never inferred values); the receipt lists every skipped rule
with a named reason; no GT, benchmark or industry vocabulary enters prompts
or product output (the vocabulary below is the same generic business language
the schema field names themselves are written in).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

_RECEIPT_SCHEMA = "qualibug.rule-contract-validation-binding.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if _text(v)))


# ── Constraint vocabulary → contract-field tokens ──────────────────────────
# Industry-neutral business language: state / validity / usage / scope / cap /
# minimum / money categories exist in every system's naming. Specific groups
# score 2, the generic money group scores 1, so 必须满足最低订单金额 resolves to
# the entity declaring min_order_amount instead of every amount field.
_CONSTRAINT_FIELD_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...], int], ...] = (
    (("状态",), ("status", "state"), 2),
    (
        ("有效期", "生效", "失效", "过期", "到期", "时间"),
        ("expires", "expiry", "valid", "start", "effective", "end", "time"),
        2,
    ),
    (("次数", "限制", "限额", "超限"), ("limit", "count", "usage", "uses"), 2),
    (("类目", "分类", "范围"), ("categor", "scope", "class", "type"), 2),
    (("封顶", "上限", "最大"), ("max", "cap", "ceiling", "maximum"), 2),
    (("最低", "门槛", "最小"), ("min", "minimum", "floor"), 2),
    (
        ("金额", "价格", "费用", "非负", "为负", "负数"),
        ("amount", "price", "total", "fee", "cost", "discount", "payable"),
        1,
    ),
)

# Decision/validation operations: the surface where eligibility is decided and
# where an accepting response for a violating input is a defect. Generic
# technical verbs, never industry terms.
_DECISION_OPERATION_TOKENS = (
    "validate", "check", "verify", "eligible", "usable", "consume",
    "apply", "simulate", "quote", "estimate", "calculate", "use",
    "claim", "校验", "验证", "使用", "领取", "可用", "模拟", "计算",
    "预估", "报价", "试算",
)

# Constraint operators extracted from the rule's behavior phrase. The literal
# expected value (ACTIVE) is carried through when the statement names it.
_OPERATOR_PATTERNS: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"必须?为|必须?是|应为|should be|must be"), "must_equal", "LITERAL"),
    (re.compile(r"不能?超过|不得?超过|超限|不得超过|under"), "under_limit", "USAGE_LIMIT"),
    (re.compile(r"只能用于|仅能用于|指定|scope"), "scope_restricted", "SCOPE"),
    (re.compile(r"封顶|上限|cap"), "capped", "CAP"),
    (re.compile(r"有效期内|生效|失效|过期|到期|valid|expire|within"), "within_time_window", "TIME_WINDOW"),
    (re.compile(r"不能?为负|不得?为负|非负|不允许为负|negative"), "non_negative", "MONEY"),
    (re.compile(r"满足|达到|不低于|不得低于|minimum|at least"), "minimum", "MINIMUM"),
)

_MONEY_TOKEN_PATTERN = re.compile(
    r"(?:amount|price|total|fee|cost|discount|payable|金额|价格|费用|应付|折扣)"
)


def _constraint_field_score(statement: str, field_name: str) -> int:
    """Score a declared field against the rule's constraint vocabulary.

    A rule that names the exact declared field (discount_amount 不能小于 0)
    carries the strongest evidence: weight 4, above any vocabulary group.
    """
    combined = f" {statement} ".casefold()
    field = _text(field_name).casefold()
    if not field:
        return 0
    if re.search(rf"(?<![a-z0-9_]){re.escape(field)}(?![a-z0-9_])", combined):
        return 4
    score = 0
    for terms, tokens, weight in _CONSTRAINT_FIELD_GROUPS:
        if not any(term in combined for term in terms):
            continue
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", field)
            for token in tokens
        ):
            score += weight
    return score


def _top_scored_operands(
    operands: list[dict[str, Any]],
    statement: str,
) -> list[dict[str, Any]]:
    """Keep only the entity's fields with the rule's top constraint score.

    A validation obligation asserts ONE constraint (must satisfy minimum order
    amount); carrying every amount-ish field would smear the assertion across
    unrelated fields. Fields tied at the top score (starts_at/expires_at for a
    time window, user_limit/global_limit for a usage limit) are all kept.
    """
    scored = [
        (row, _constraint_field_score(statement, _text(row.get("field"))))
        for row in operands
    ]
    scored = [(row, score) for row, score in scored if score > 0]
    if not scored:
        return []
    top = max(score for _, score in scored)
    return [dict(row) for row, score in scored if score == top]


def _constraint_operator(statement: str) -> tuple[str, str]:
    """Extract the constraint operator + expected-value kind from the text."""
    combined = f" {statement} "
    for pattern, operator, value_kind in _OPERATOR_PATTERNS:
        if pattern.search(combined):
            return operator, value_kind
    return "must_hold", ""


def _expected_literal(statement: str) -> str:
    """Extract a literal expected value when the statement names one.

    必须为 ACTIVE / must be ACTIVE / 状态为 DISABLED — the trailing token
    after a state verb. Only a bare identifier (no spaces) is accepted, so a
    sentence tail like 在有效期内 never becomes a literal.
    """
    match = re.search(
        r"(?:必须?为|必须?是|应为|must be|should be)\s*([A-Za-z][A-Za-z0-9_]*)",
        statement,
    )
    if match:
        return match.group(1)
    return ""


def _entity_index(model: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Index IR entities by table name (casefolded) and by entity id."""
    by_table: dict[str, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for ent in _list(model.get("entities")):
        if not isinstance(ent, dict):
            continue
        ent_id = _text(ent.get("id"))
        table = _text(ent.get("table") or ent.get("name"))
        if ent_id:
            by_id[ent_id] = ent
        if table:
            by_table[table.casefold()] = ent
    return by_table, by_id


def _entity_fields(entity: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _list(entity.get("fields")) if isinstance(row, dict)]


def _field_description_match(
    subject_terms: set[str],
    field: dict[str, Any],
    *,
    description: str = "",
) -> bool:
    """Subject → field-description containment (cross-language via source docs).

    The rule subject 优惠券 binds to the entity whose declared field carries
    优惠券 in its own source description (coupons.expires_at — 优惠券过期时间).
    Compound subjects (优惠券状态) match through a CJK prefix: any prefix of
    length >= 2 (优惠券) contained in the description counts. This is visible
    enterprise material — never a translation table.
    """
    description = _text(description or field.get("description") or field.get("comment"))
    if not description:
        return False
    lowered = description.casefold()
    for term in subject_terms:
        term = _text(term)
        if not term:
            continue
        if term.casefold() in lowered:
            return True
        # CJK compound subject: prefix containment (优惠券 ⊂ 优惠券状态).
        for prefix_len in range(max(2, len(term) - 2), len(term)):
            prefix = term[:prefix_len].casefold()
            if prefix and ord(prefix[0]) > 127 and prefix in lowered:
                return True
    return False


def _asset_description_index(asset: dict[str, Any]) -> dict[str, dict[str, str]]:
    """(table.casefold() → {field: description}) from the asset's own
    field dictionary — visible enterprise material (DB_SCHEMA-style
    inventories), used to resolve a rule subject to its governed entity."""
    index: dict[str, dict[str, str]] = {}
    for row in _list(asset.get("field_dictionary")):
        if not isinstance(row, dict):
            continue
        table = _text(row.get("table"))
        field = _text(row.get("field") or row.get("field_id"))
        description = _text(row.get("description") or row.get("comment"))
        if not table or not field or not description:
            continue
        index.setdefault(table.casefold(), {})[field.casefold()] = description
    return index


def _resolve_subject_entity(
    statement: str,
    subject_terms: set[str],
    model: dict[str, Any],
    *,
    entity_index: dict[str, dict[str, Any]],
    description_index: dict[str, dict[str, str]] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Resolve the governed entity for a rule, independent of business_objects.

    Two evidence channels, combined per entity:
    * subject-description channel: rule subject appears in a declared field's
      source description (优惠券 ↔ 优惠券过期时间);
    * constraint-field channel: the rule's constraint vocabulary scores the
      entity's declared fields (状态 → status, 有效期 → expires_at...).

    Returns (entity_id, table, matched_field_operands). Empty entity_id means
    the rule cannot be resolved to a unique entity (caller keeps it unbound).
    """
    description_index = description_index or {}
    scores: list[tuple[int, int, str, str, list[dict[str, Any]]]] = []
    for table, entity in entity_index.items():
        subject_hits = 0
        field_score = 0
        operands: list[dict[str, Any]] = []
        ent_id = _text(entity.get("id")) or table
        table_descriptions = description_index.get(table, {})
        for field in _entity_fields(entity):
            fname = _text(field.get("name"))
            if not fname:
                continue
            score = _constraint_field_score(statement, fname)
            if score > 0:
                field_score += score
                operand: dict[str, Any] = {
                    "entity_ref": ent_id,
                    "field": fname,
                }
                if _text(field.get("field_id")):
                    operand["field_id"] = _text(field.get("field_id"))
                if _text(field.get("semantic_type")):
                    operand["semantic_type"] = _text(field.get("semantic_type"))
                operands.append(operand)
            if _field_description_match(
                subject_terms,
                field,
                description=table_descriptions.get(fname.casefold(), ""),
            ):
                subject_hits += 1
        if subject_hits or field_score:
            scores.append((field_score, subject_hits, ent_id, table, operands))
    if not scores:
        return "", "", []
    # Subject-description evidence dominates; among entities with it, the one
    # with the higher constraint-field score wins. Otherwise the highest
    # constraint-field score wins. Ties stay unresolved (honest ambiguity).
    max_subject = max(score[1] for score in scores)
    candidates = [score for score in scores if score[1] == max_subject]
    if max_subject > 0:
        max_field = max(score[0] for score in candidates)
        candidates = [score for score in candidates if score[0] == max_field]
    else:
        max_field = max(score[0] for score in scores)
        candidates = [score for score in scores if score[0] == max_field]
    if len(candidates) != 1:
        return "", "", []
    _, _, ent_id, table, operands = candidates[0]
    return ent_id, table, _top_scored_operands(operands, statement)


def _consuming_operations(
    entity_tables: set[str],
    statement: str,
    model: dict[str, Any],
    *,
    constraint_fields: list[str],
    prefer_decision: bool = True,
) -> list[str]:
    """Operations consuming the entity: path/module identity + contract fields.

    An operation whose path segment names the entity's table/module (coupons
    ↔ coupon) or whose request/response contract fields include the rule's
    OWN constraint fields (min_order_amount — not any amount field) is the
    governed surface. Health probes are never governed. Decision operations
    (validate/check/use/claim/试算/校验/验证...) sort first when present —
    they are the surfaces where an accepting response for a violating input
    is a defect.
    """
    constraint_fields = {_text(f).casefold() for f in constraint_fields if _text(f)}
    bound: list[str] = []
    decision: list[str] = []
    for op in _list(model.get("operations")):
        if not isinstance(op, dict):
            continue
        op_id = _text(op.get("id"))
        path = _text(op.get("path") or op.get("raw_path"))
        if not op_id or not path:
            continue
        if re.search(r"(?:^|/)(?:health)(?:/|$)", path.casefold()):
            continue
        segments = [
            seg
            for seg in path.casefold().strip("/").split("/")
            if seg and seg not in {"api", "health", "v1"}
        ]
        op_ents = {
            _text(e).casefold() for e in _list(op.get("entity_refs")) if _text(e)
        }
        path_hit = any(
            _seg.startswith(_t) or _t.startswith(_seg)
            for _seg in segments
            for _t in entity_tables
        )
        entity_hit = bool(op_ents & entity_tables)
        contract_hit = bool(
            constraint_fields
            and (set(_op_contract_fields(op)) & constraint_fields)
        )
        if not (path_hit or entity_hit or contract_hit):
            continue
        combined = f"{path.casefold()} {_text(op.get('summary')).casefold()}"
        if any(token in combined for token in _DECISION_OPERATION_TOKENS):
            decision.append(op_id)
        else:
            bound.append(op_id)
    if prefer_decision and decision:
        return _unique([*decision, *bound])
    return _unique([*bound, *decision])


def _op_contract_fields(op: dict[str, Any]) -> list[str]:
    """Flat request/response field names declared on the operation."""
    fields: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                for name in props:
                    if isinstance(name, str) and name:
                        fields.append(name)
            elif isinstance(node.get("required"), list):
                for name in node.get("required") or []:
                    if isinstance(name, str) and name:
                        fields.append(name)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    _walk(value)
        elif isinstance(node, list):
            for value in node:
                if isinstance(value, (dict, list)):
                    _walk(value)

    for key in ("request_schema", "requestBody", "response_schema", "responses"):
        _walk(op.get(key))
    return _unique(fields)


def _rules_with_unbound_invariants(
    behavior_ir: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Map rule_id → its derived invariants, keeping only fully unbound rules.

    A rule is eligible for this stage only when NONE of its derived
    invariants carries an operation binding — the shared subject-frame channel
    or legacy binding already resolved it, and this stage must never
    duplicate.
    """
    invariants_by_rule: dict[str, list[dict[str, Any]]] = {}
    for inv in _list(behavior_ir.get("invariants")):
        if not isinstance(inv, dict):
            continue
        for rule_ref in _list(inv.get("source_rule_refs")):
            rid = _text(rule_ref)
            if not rid:
                continue
            invariants_by_rule.setdefault(rid, []).append(inv)
    return {
        rid: invs
        for rid, invs in invariants_by_rule.items()
        if invs
        and all(
            not _list(inv.get("operation_refs"))
            for inv in invs
            if isinstance(inv, dict)
        )
    }


def _derive_validation_invariants(
    behavior_ir: dict[str, Any],
    asset: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Derive validation-contract invariants for unbound constraint rules.

    Returns (new_invariants, receipt). Receipt carries per-rule dispositions:
    BOUND / ALREADY_BOUND / NO_SUBJECT / NO_FIELDS / NO_OPERATIONS /
    AMBIGUOUS_ENTITY — every skip is named, nothing is fabricated.
    """
    model = behavior_ir
    entity_index, _ = _entity_index(model)
    description_index = _asset_description_index(asset)
    unbound = _rules_with_unbound_invariants(behavior_ir)
    new_invariants: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    rules_by_id: dict[str, dict[str, Any]] = {}
    for rule in _list(asset.get("rule_library")):
        rid = _text(rule.get("rule_id") or rule.get("id"))
        if rid:
            rules_by_id[rid] = rule

    for rule_id, existing_invs in unbound.items():
        rule = rules_by_id.get(rule_id)
        if not isinstance(rule, dict):
            dispositions.append({
                "rule_id": rule_id,
                "disposition": "RULE_NOT_IN_LIBRARY",
            })
            continue
        statement = _text(
            rule.get("statement") or rule.get("expression") or rule.get("title")
        )
        if not statement or len(statement) < 4:
            dispositions.append({
                "rule_id": rule_id,
                "disposition": "STATEMENT_TOO_SHORT",
                "statement": statement[:120],
            })
            continue
        frame = _dict(rule.get("semantic_frame"))
        subject = _text(frame.get("subject"))
        behavior = _text(frame.get("behavior"))
        subject_terms = {
            term for term in (subject, behavior) if _text(term)
        }
        ent_id, table, field_operands = _resolve_subject_entity(
            statement,
            subject_terms,
            model,
            entity_index=entity_index,
            description_index=description_index,
        )
        if not ent_id:
            # No entity-level resolution. A rule that names exact declared
            # fields (discount_amount 不能小于 0) still binds through the
            # explicit-name channel: collect every entity's scored fields and
            # keep only the GLOBAL top-scored ones — a unique entity at the
            # top is the governed surface. Chinese-only rules whose top score
            # ties across entities (bare 金额 rules) stay unbound (honest).
            candidates_by_entity: list[tuple[int, str, str, list[dict[str, Any]]]] = []
            for _t, entity in entity_index.items():
                operands = _top_scored_operands(_entity_fields(entity), statement)
                if not operands:
                    continue
                score = max(
                    _constraint_field_score(statement, _text(row.get("field")))
                    for row in operands
                )
                candidates_by_entity.append(
                    (score, _t, _text(entity.get("id")) or _t, operands)
                )
            if not candidates_by_entity:
                dispositions.append({
                    "rule_id": rule_id,
                    "disposition": "NO_ENTITY_FIELD_MATCH",
                    "statement": statement[:120],
                })
                continue
            top = max(score for score, _, _, _ in candidates_by_entity)
            winners = [c for c in candidates_by_entity if c[0] == top]
            if len(winners) != 1:
                dispositions.append({
                    "rule_id": rule_id,
                    "disposition": "MULTI_ENTITY_MONEY_AMBIGUOUS",
                    "entities": sorted(c[1] for c in winners),
                    "statement": statement[:120],
                })
                continue
            _, table, ent_id, field_operands = winners[0]
            entity_tables = {table}
        else:
            entity_tables = {table}
        op_refs = _consuming_operations(
            entity_tables,
            statement,
            model,
            constraint_fields=[_text(row.get("field")) for row in field_operands],
        )
        if not op_refs:
            dispositions.append({
                "rule_id": rule_id,
                "disposition": "NO_CONSUMING_OPERATION",
                "entity": ent_id or table,
                "statement": statement[:120],
            })
            continue
        operator, value_kind = _constraint_operator(statement)
        expected = _expected_literal(statement)
        operands = [dict(row) for row in field_operands]
        if expected and operator == "must_equal":
            for operand in operands:
                operand["expected_value"] = expected
        expression: dict[str, Any] = {
            "kind": "validation",
            "operator": operator,
            "operands": operands,
            "raw": statement,
        }
        if value_kind:
            expression["constraint_kind"] = value_kind
        inv_id = _stable_id("inv", "rule_contract_validation", rule_id)
        inv_node: dict[str, Any] = {
            "id": inv_id,
            "description": statement,
            "expression": expression,
            "operation_refs": op_refs,
            "source_rule_refs": [rule_id],
            "derived_from_rule_refs": [rule_id],
            "derived_invariant_kind": "rule_contract_validation",
            "subject_entity_refs": [ent_id] if ent_id else [],
            "source_refs": [{
                "source_id": _text(rule.get("source_id")) or "rule_library",
                "locator": _text(rule.get("source_locator")),
                "quote": statement[:200],
                "kind": "rule_contract_validation",
            }],
            "confidence": float(rule.get("confidence") or 0.7),
            "derivation": "explicit",
            "status": "accepted",
        }
        new_invariants.append(inv_node)
        dispositions.append({
            "rule_id": rule_id,
            "disposition": "BOUND",
            "entity": ent_id or table,
            "table": table,
            "fields": [op.get("field") for op in operands],
            "operations": op_refs,
            "operator": operator,
        })
    receipt = {
        "schema_version": _RECEIPT_SCHEMA,
        "status": "OK",
        "rules_scanned": len(unbound),
        "invariants_derived": len(new_invariants),
        "dispositions": dispositions,
        "bound_count": sum(
            1 for d in dispositions if d.get("disposition") == "BOUND"
        ),
        "skipped_count": sum(
            1 for d in dispositions if d.get("disposition") != "BOUND"
        ),
    }
    return new_invariants, receipt


def _stable_id(*parts: Any) -> str:
    import hashlib

    digest = hashlib.sha1(
        "|".join(_text(p) for p in parts).encode("utf-8")
    ).hexdigest()[:12]
    return f"bir_{digest}"


def bind_rule_contract_validation_invariants(
    behavior_ir: dict[str, Any],
    asset: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Additive IR stage: derive validation-contract invariants for unbound
    constraint rules and return (ir, receipt)."""
    ir = dict(behavior_ir or {})
    derived, receipt = _derive_validation_invariants(ir, asset)
    if derived:
        ir["invariants"] = [*(ir.get("invariants") or []), *derived]
        ir["rule_contract_validation_receipt"] = receipt
    return ir, receipt
