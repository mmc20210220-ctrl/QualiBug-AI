from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    updated = source[:start_index] + replacement.rstrip() + "\n\n\n" + source[end_index:]
    target.write_text(updated, encoding="utf-8")


def replace_exact(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    if old not in source:
        raise RuntimeError(f"expected patch anchor missing: {path}: {old[:80]!r}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    if marker in source:
        return
    target.write_text(source.rstrip() + "\n\n\n" + addition.strip() + "\n", encoding="utf-8")


GOVERNED_CONFLICT_BLOCK = r'''
def _explicit_source_combinator(behavior: dict[str, Any]) -> str:
    """Return an explicit source-backed Boolean combinator, never a guessed default."""
    frame = as_dict(behavior.get("condition_frame"))
    for candidate in (
        behavior.get("condition_combinator"),
        as_dict(behavior.get("trigger")).get("condition_combinator"),
        frame.get("combinator"),
    ):
        value = text(candidate).upper()
        if value in {"AND", "OR"}:
            return value
    return ""


def _incompatible_equalities(behavior: dict[str, Any]) -> dict[str, list[str]]:
    """Recompute one behavior's exact equality contradiction from its condition slots."""
    equal_values: dict[str, set[str]] = defaultdict(set)
    for condition in as_list(behavior.get("preconditions")):
        if (
            not isinstance(condition, dict)
            or text(condition.get("operator_candidate")) != "EQUALS"
        ):
            continue
        field = text(condition.get("field_candidate"))
        if field:
            equal_values[field].add(
                json.dumps(
                    _canonical_value(as_dict(condition.get("value_candidate"))),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            )
    return {
        field: sorted(values)
        for field, values in equal_values.items()
        if len(values) > 1
    }


def _recalculate_conflicts(behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild formal conflicts from source-backed behavior content.

    Accepted facts are evaluated from their condition slots, explicit source combinator,
    permission decision and evidence. Prior status flags are not authority. Decision-matrix
    interpretations remain candidate-only and cannot downgrade or block a formal fact.
    """
    conflicts: list[dict[str, Any]] = []
    families: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for behavior in behaviors:
        source_kind = text(behavior.get("source_kind"))
        unresolved = unique_text(as_list(behavior.get("unresolved_semantics")))
        is_fact_backed = source_kind == "ACCEPTED_BUSINESS_FACT"

        if not is_fact_backed:
            if text(behavior.get("permission_decision")) == "CONFLICTED":
                unresolved.append("BEHAVIOR_CANDIDATE_RESULT_CONFLICT")
            behavior["unresolved_semantics"] = unique_text(unresolved)
            behavior["status"] = "INCOMPLETE" if unresolved else "CANDIDATE"
            behavior["candidate_only"] = True
            behavior["formal_business_rule"] = False
            continue

        conditions = [
            row for row in as_list(behavior.get("preconditions")) if isinstance(row, dict)
        ]
        explicit_combinator = _explicit_source_combinator(behavior)
        unresolved = [
            value
            for value in unresolved
            if value != "BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED"
        ]
        if len(conditions) > 1:
            if explicit_combinator:
                behavior["condition_combinator"] = explicit_combinator
            else:
                behavior["condition_combinator"] = "UNRESOLVED"
                unresolved.append("BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED")
        else:
            behavior["condition_combinator"] = "SINGLE_CONDITION" if conditions else ""
        behavior["unresolved_semantics"] = unique_text(unresolved)
        behavior["candidate_only"] = False

        # Missing operation/object/evidence or unresolved source logic remains incomplete;
        # it cannot become a formal rule or an authority-eligible contradiction.
        if unresolved:
            behavior["status"] = "INCOMPLETE"
            behavior["formal_business_rule"] = False
            continue

        contradictions = (
            _incompatible_equalities(behavior)
            if explicit_combinator == "AND"
            else {}
        )
        if contradictions:
            behavior["status"] = "CONFLICTED"
            behavior["formal_business_rule"] = False
            conflicts.append(
                {
                    "conflict_id": stable_id(
                        "behavior_conflict", behavior.get("behavior_id"), contradictions
                    ),
                    "kind": "BEHAVIOR_CONDITION_CONTRADICTION",
                    "status": "UNRESOLVED",
                    "severity": "P0",
                    "behavior_refs": [behavior.get("behavior_id")],
                    "source_refs": unique_text(as_list(behavior.get("source_refs"))),
                    "details": {
                        "condition_combinator": "AND",
                        "contradictory_equalities": contradictions,
                    },
                    "evidence": as_list(behavior.get("evidence")),
                    "automatic_resolution_allowed": False,
                }
            )
            continue

        behavior["status"] = "CONFIRMED"
        behavior["formal_business_rule"] = True
        signature = _condition_signature(conditions)
        families[(text(behavior.get("behavior_family_id")), signature)].append(behavior)

    # Cross-fact permission conflicts are calculated only from complete formal facts.
    for (_family, _signature), rows in families.items():
        decisions = {
            text(row.get("permission_decision"))
            for row in rows
            if text(row.get("permission_decision"))
            not in {"", "UNSPECIFIED", "CONFLICTED"}
        }
        if len(decisions) <= 1:
            continue
        for row in rows:
            row["status"] = "CONFLICTED"
            row["formal_business_rule"] = False
        conflicts.append(
            {
                "conflict_id": stable_id(
                    "behavior_conflict", [row.get("behavior_id") for row in rows], sorted(decisions)
                ),
                "kind": "BEHAVIOR_PERMISSION_DECISION_CONFLICT",
                "status": "UNRESOLVED",
                "severity": "P0",
                "behavior_refs": [row.get("behavior_id") for row in rows],
                "source_refs": unique_text(
                    [source_ref for row in rows for source_ref in as_list(row.get("source_refs"))]
                ),
                "details": {"permission_decisions": sorted(decisions)},
                "evidence": dedupe_evidence(
                    [evidence for row in rows for evidence in as_list(row.get("evidence"))]
                ),
                "automatic_resolution_allowed": False,
            }
        )
    return conflicts
'''


FIELD_DICTIONARY_BLOCK = r'''
def _field_dictionary_entries(text: str, payload: Any, source_id: str) -> list[dict[str, Any]]:
    """Project structured field declarations without manufacturing verbatim quotes."""
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    def add_candidate(item: dict[str, Any], locator: str) -> None:
        candidate = dict(item)
        if locator:
            candidate["__source_locator"] = locator
        candidates.append(candidate)

    if isinstance(payload, dict):
        for key in ("fields", "items", "columns", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        add_candidate(item, f"#/{key}/{index}")
        tables = payload.get("tables")
        if isinstance(tables, list):
            for table_index, table in enumerate(tables):
                if not isinstance(table, dict):
                    continue
                table_name = str(table.get("name") or table.get("table") or "")
                field_key = "fields" if isinstance(table.get("fields"), list) else "columns"
                for field_index, field in enumerate(table.get(field_key) or []):
                    if isinstance(field, dict):
                        item = dict(field)
                        item.setdefault("table", table_name)
                        add_candidate(
                            item,
                            f"#/tables/{table_index}/{field_key}/{field_index}",
                        )
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            if isinstance(item, dict):
                add_candidate(item, f"#/{index}")
    for row_index, item in enumerate(_csv_rows(text), start=2):
        if isinstance(item, dict):
            add_candidate(item, f"csv:row={row_index}")

    for item in candidates:
        table_name = _pick_first(item, ("table", "table_name", "tableName", "表", "数据表"))
        field_name = _pick_first(item, ("field", "field_name", "fieldName", "column", "column_name", "字段", "列名", "name"))
        if not field_name:
            continue
        field_type = _pick_first(item, ("type", "data_type", "dataType", "字段类型", "类型"))
        description = _pick_first(item, ("description", "desc", "comment", "说明", "描述", "remark", "备注"))
        required = _pick_first(item, ("required", "nullable", "必填", "is_required"))
        constraint = _pick_first(item, tuple(sorted(_IDENTITY_CONSTRAINT_HEADERS)))

        normalized_bits: list[str] = []
        if table_name:
            normalized_bits.append(f"table={table_name}")
        normalized_bits.append(f"field={field_name}")
        if required is not None and str(required).strip() != "":
            normalized_bits.append(f"required={required}")
        if field_type:
            normalized_bits.append(f"type={field_type}")
        normalized_evidence = _redact_text("; ".join(normalized_bits), 320)

        # A quote is admitted only when the source itself supplied it and the exact
        # string is present in the immutable source text. Normalized key/value prose
        # is never copied into quote/source_excerpt.
        quote_candidate = _pick_first(
            item,
            ("quote", "verbatim_quote", "原文", "原文摘录"),
        )
        exact_quote = (
            _redact_text(quote_candidate, 500)
            if quote_candidate and quote_candidate in str(text or "")
            else ""
        )
        source_locator = str(item.get("__source_locator") or "").strip()
        rows.append({
            "field_id": f"field:{source_id}:{_short_hash({'table': table_name or 'default', 'field': field_name})}",
            "source_id": source_id,
            "source_locator": source_locator,
            "table": table_name or "default",
            "table_id": f"table:{table_name or 'default'}",
            "field": field_name,
            "field_path": field_name,
            "type": field_type,
            "required": _doc_bool(required),
            "constraint": _redact_text(constraint, 160),
            "identity": _declares_identity(constraint) or _doc_bool(
                _pick_first(item, ("primary_key", "primaryKey", "unique", "is_unique", "主键", "唯一"))
            ) is True,
            "description": _redact_text(description, 320),
            "normalized_evidence": normalized_evidence,
            "evidence_kind": (
                "EXACT_SOURCE_QUOTE"
                if exact_quote
                else "NORMALIZED_STRUCTURED_DECLARATION"
            ),
            "evidence_derivation": (
                "exact_source_quote"
                if exact_quote
                else "normalized_field_dictionary_projection"
            ),
            **({"quote": exact_quote} if exact_quote else {}),
            "tokens": sorted(_tokens(f"{table_name} {field_name} {field_type} {description}")),
        })
    rows.extend(_infer_field_rows_from_markdown(text, source_id))
    return _dedupe_by_id(rows, "field_id")
'''


PARSING_TEST_BLOCK = r'''
def test_field_dictionary_json_marks_normalized_projection_without_fake_quote() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import (
        _field_dictionary_entries,
    )

    source_text = (
        '{"fields":['
        '{"table":"orders","field":"warehouse_id","required":false},'
        '{"table":"orders","field":"sku","required":true}'
        ']}'
    )
    rows = _field_dictionary_entries(
        source_text,
        {
            "fields": [
                {"table": "orders", "field": "warehouse_id", "required": False},
                {"table": "orders", "field": "sku", "required": True},
            ]
        },
        "src_fields",
    )
    by_field = {row["field"]: row for row in rows}
    warehouse = by_field["warehouse_id"]
    sku = by_field["sku"]

    assert warehouse["required"] is False
    assert warehouse["source_locator"] == "#/fields/0"
    assert "required=false" in warehouse["normalized_evidence"]
    assert warehouse["evidence_kind"] == "NORMALIZED_STRUCTURED_DECLARATION"
    assert warehouse["evidence_derivation"] == "normalized_field_dictionary_projection"
    assert "quote" not in warehouse
    assert "source_excerpt" not in warehouse

    assert sku["required"] is True
    assert sku["source_locator"] == "#/fields/1"
    assert "required=true" in sku["normalized_evidence"]
    assert "quote" not in sku
    assert "source_excerpt" not in sku
'''


GOVERNED_TESTS = r'''
def _accepted_and_conflict_fact() -> dict[str, Any]:
    fact = _accepted_allow_fact()
    fact.update(
        {
            "fact_id": "fact-and-conflict",
            "raw_statement": "订单状态同时为已审核且待发货时可以发货",
            "conditions": ["状态=已审核", "状态=待发货"],
            "trigger": {"condition_combinator": "AND"},
            "source_spans": [
                {
                    "source_id": "source-rule",
                    "source_locator": "rules.md#fact=fact-and-conflict",
                    "quote": "订单状态同时为已审核且待发货时可以发货",
                }
            ],
        }
    )
    return fact


def test_governed_fact_authority_recomputes_internal_and_contradiction() -> None:
    fact = _accepted_and_conflict_fact()
    _rows, behaviors, conflicts, _unknowns, gate = build_governed_business_behavior_ir(
        {"business_fact_ledger": {"items": [fact]}, "document_structure_assets": {"items": []}},
        [fact],
        [_operation()],
    )

    assert len(behaviors) == 1
    assert behaviors[0]["source_kind"] == "ACCEPTED_BUSINESS_FACT"
    assert behaviors[0]["condition_combinator"] == "AND"
    assert behaviors[0]["status"] == "CONFLICTED"
    assert behaviors[0]["formal_business_rule"] is False
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "BEHAVIOR_CONDITION_CONTRADICTION"
    assert conflicts[0]["details"]["condition_combinator"] == "AND"
    assert conflicts[0]["evidence"]
    assert gate["status"] == "BLOCKED_BUSINESS_BEHAVIOR_CONFLICT"


def test_incomplete_accepted_fact_never_becomes_formal_rule_or_conflict() -> None:
    fact = _accepted_allow_fact()
    fact["fact_id"] = "fact-missing-evidence"
    fact["source_spans"] = []
    _rows, behaviors, conflicts, _unknowns, gate = build_governed_business_behavior_ir(
        {"business_fact_ledger": {"items": [fact]}, "document_structure_assets": {"items": []}},
        [fact],
        [_operation()],
    )

    assert len(behaviors) == 1
    assert behaviors[0]["status"] == "INCOMPLETE"
    assert behaviors[0]["formal_business_rule"] is False
    assert "BEHAVIOR_EVIDENCE_MISSING" in behaviors[0]["unresolved_semantics"]
    assert conflicts == []
    assert gate["status"] == "PARTIAL_BUSINESS_BEHAVIOR_IR"
'''


def apply() -> None:
    replace_between(
        "ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/behavior_ir_governance.py",
        "def _recalculate_conflicts(",
        "def _governance_unknowns(",
        GOVERNED_CONFLICT_BLOCK,
    )
    replace_between(
        "ai_test_asset_center/enterprise_knowledge_center/_parsing.py",
        "def _field_dictionary_entries(",
        "def _field_dictionary_tables(",
        FIELD_DICTIONARY_BLOCK,
    )

    replace_exact(
        "ai_test_asset_center/enterprise_knowledge_center/_api.py",
        '''    # A structured projection may summarize the declaration in ``statement``,\n    # but generated prose is never source evidence. Only an exact captured quote\n    # and locator may enter the evidence span.\n    evidence_quote = str(quote or "")[:500]\n    evidence_locator = str(locator or "").strip()\n    return {\n''',
        '''    # A structured projection may summarize the declaration in ``statement``,\n    # but generated prose is never source evidence. Only an exact captured quote\n    # and locator may enter the evidence span. Normalized evidence is kept explicitly\n    # labelled and never copied into ``quote``.\n    technical_details = dict(details or {})\n    evidence_quote = str(quote or "")[:500]\n    evidence_locator = str(locator or "").strip()\n    normalized_evidence = str(technical_details.get("normalized_evidence") or "")[:500]\n    evidence_kind = str(technical_details.get("evidence_kind") or "").strip()\n    evidence_derivation = str(technical_details.get("evidence_derivation") or "").strip()\n    return {\n''',
    )
    replace_exact(
        "ai_test_asset_center/enterprise_knowledge_center/_api.py",
        '''        "technical_declaration": dict(details or {}),\n''',
        '''        "technical_declaration": technical_details,\n''',
    )
    replace_exact(
        "ai_test_asset_center/enterprise_knowledge_center/_api.py",
        '''                "quote_hash": (\n                    hashlib.sha256(evidence_quote.encode("utf-8")).hexdigest()\n                    if evidence_quote\n                    else ""\n                ),\n                "derivation": "structured_source_declaration_projection",\n''',
        '''                "quote_hash": (\n                    hashlib.sha256(evidence_quote.encode("utf-8")).hexdigest()\n                    if evidence_quote\n                    else ""\n                ),\n                "normalized_evidence": normalized_evidence,\n                "evidence_kind": (\n                    "EXACT_SOURCE_QUOTE"\n                    if evidence_quote\n                    else evidence_kind or "STRUCTURED_SOURCE_LOCATOR"\n                ),\n                "derivation": evidence_derivation or "structured_source_declaration_projection",\n''',
    )
    replace_exact(
        "ai_test_asset_center/enterprise_knowledge_center/_api.py",
        '''                    details={\n                        "required": required,\n                        "table": declaration.get("table"),\n                        "field": declaration.get("field"),\n                    },\n                    quote=str(declaration.get("quote") or declaration.get("source_excerpt") or ""),\n''',
        '''                    details={\n                        "required": required,\n                        "table": declaration.get("table"),\n                        "field": declaration.get("field"),\n                        "normalized_evidence": declaration.get("normalized_evidence"),\n                        "evidence_kind": declaration.get("evidence_kind"),\n                        "evidence_derivation": declaration.get("evidence_derivation"),\n                    },\n                    quote=str(\n                        declaration.get("quote")\n                        or declaration.get("verbatim_quote")\n                        or ""\n                    ),\n''',
    )

    # Preserve normalized evidence through the authority conflict receipt without
    # promoting it to a verbatim quote.
    for path in ["ai_test_asset_center/enterprise_knowledge_center/_chinese_business_conflicts.py"]:
        replace_exact(
            path,
            '''                "quote_hash": span.get("quote_hash"),\n                "modality": fact.get("modality"),\n''',
            '''                "quote_hash": span.get("quote_hash"),\n                "normalized_evidence": span.get("normalized_evidence"),\n                "evidence_kind": span.get("evidence_kind"),\n                "evidence_derivation": span.get("derivation"),\n                "modality": fact.get("modality"),\n''',
        )
        replace_exact(
            path,
            '''            "quote_hash": row.get("quote_hash"),\n            "fact_id": row.get("fact_id"),\n            "derivation": "unresolved_business_fact_conflict",\n''',
            '''            "quote_hash": row.get("quote_hash"),\n            "normalized_evidence": row.get("normalized_evidence"),\n            "evidence_kind": row.get("evidence_kind"),\n            "fact_id": row.get("fact_id"),\n            "derivation": row.get("evidence_derivation") or "unresolved_business_fact_conflict",\n''',
        )
        replace_exact(
            path,
            '''        if _text(row.get("quote") or row.get("statement") or row.get("fact_id") or row.get("source_id"))\n''',
            '''        if _text(\n            row.get("quote")\n            or row.get("normalized_evidence")\n            or row.get("statement")\n            or row.get("fact_id")\n            or row.get("source_id")\n        )\n''',
        )
        # The same source projection appears in the >2 participant branch.
        replace_exact(
            path,
            '''                    "quote_hash": span.get("quote_hash"),\n                    "modality": fact.get("modality"),\n''',
            '''                    "quote_hash": span.get("quote_hash"),\n                    "normalized_evidence": span.get("normalized_evidence"),\n                    "evidence_kind": span.get("evidence_kind"),\n                    "evidence_derivation": span.get("derivation"),\n                    "modality": fact.get("modality"),\n''',
        )
        replace_exact(
            path,
            '''                "quote_hash": row.get("quote_hash"),\n                "fact_id": row.get("fact_id"),\n                "derivation": "unresolved_technical_cross_source_conflict",\n''',
            '''                "quote_hash": row.get("quote_hash"),\n                "normalized_evidence": row.get("normalized_evidence"),\n                "evidence_kind": row.get("evidence_kind"),\n                "fact_id": row.get("fact_id"),\n                "derivation": row.get("evidence_derivation") or "unresolved_technical_cross_source_conflict",\n''',
        )
        replace_exact(
            path,
            '''            if _text(row.get("quote") or row.get("statement") or row.get("fact_id") or row.get("source_id"))\n''',
            '''            if _text(\n                row.get("quote")\n                or row.get("normalized_evidence")\n                or row.get("statement")\n                or row.get("fact_id")\n                or row.get("source_id")\n            )\n''',
        )

    replace_between(
        "tests/test_enterprise_knowledge_center_parsing.py",
        "def test_field_dictionary_json_preserves_required_false_in_excerpt()",
        "def test_permission_entries_prefer_source_evidence_string()",
        PARSING_TEST_BLOCK,
    )
    append_once(
        "tests/test_behavior_ir_single_fact_authority.py",
        "def test_governed_fact_authority_recomputes_internal_and_contradiction()",
        GOVERNED_TESTS,
    )

    replace_exact(
        "tests/test_operator_authority_decision.py",
        '''                "source_excerpt": "table=orders; field=warehouse_id; required=true",\n''',
        '''                "source_locator": "#/fields/0",\n                "source_excerpt": "table=orders; field=warehouse_id; required=true",\n                "normalized_evidence": "table=orders; field=warehouse_id; required=true",\n                "evidence_kind": "NORMALIZED_STRUCTURED_DECLARATION",\n                "evidence_derivation": "normalized_field_dictionary_projection",\n''',
    )
    replace_exact(
        "tests/test_operator_authority_decision.py",
        '''                "source_excerpt": "table=orders; field=warehouse_id; required=false",\n''',
        '''                "source_locator": "#/fields/1",\n                "source_excerpt": "table=orders; field=warehouse_id; required=false",\n                "normalized_evidence": "table=orders; field=warehouse_id; required=false",\n                "evidence_kind": "NORMALIZED_STRUCTURED_DECLARATION",\n                "evidence_derivation": "normalized_field_dictionary_projection",\n''',
    )
    replace_exact(
        "tests/test_operator_authority_decision.py",
        '''    assert all(str(row.get("quote") or "").strip() for row in evidence)\n    assert any("required=true" in str(row.get("quote") or "") for row in evidence)\n    assert any("required=false" in str(row.get("quote") or "") for row in evidence)\n''',
        '''    # Legacy normalized source_excerpt values must not be promoted to exact quotes.\n    assert all(not str(row.get("quote") or "").strip() for row in evidence)\n    assert all(\n        row.get("evidence_kind") == "NORMALIZED_STRUCTURED_DECLARATION"\n        for row in evidence\n    )\n    assert any(\n        "required=true" in str(row.get("normalized_evidence") or "")\n        for row in evidence\n    )\n    assert any(\n        "required=false" in str(row.get("normalized_evidence") or "")\n        for row in evidence\n    )\n    assert {row.get("source_locator") for row in evidence} == {"#/fields/0", "#/fields/1"}\n''',
    )

    changed_python = [
        "ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/behavior_ir_governance.py",
        "ai_test_asset_center/enterprise_knowledge_center/_parsing.py",
        "ai_test_asset_center/enterprise_knowledge_center/_api.py",
        "ai_test_asset_center/enterprise_knowledge_center/_chinese_business_conflicts.py",
        "tests/test_behavior_ir_single_fact_authority.py",
        "tests/test_enterprise_knowledge_center_parsing.py",
        "tests/test_operator_authority_decision.py",
    ]
    for relative in changed_python:
        ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        print(f"AST_OK {relative}")


if __name__ == "__main__":
    apply()
