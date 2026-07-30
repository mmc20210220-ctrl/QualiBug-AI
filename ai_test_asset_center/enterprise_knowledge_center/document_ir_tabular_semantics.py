"""High-fidelity semantic projections for enterprise tabular materials.

This module never opens files and never guesses behavior from formatting. It consumes exact
``TABLE_CELL`` blocks already produced by the canonical Document IR pipeline, maps source-
declared column labels to stable enterprise record fields, and preserves cell-level evidence.
Generic text extraction remains the fallback when no supported tabular profile is proven.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

TABULAR_SEMANTIC_RECEIPT_SCHEMA = "qualibug.document-ir-tabular-semantics-receipt.v2"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_header(value: Any) -> str:
    return re.sub(r"[\s_\-./\\:：()（）\[\]【】]+", "", _text(value).lower())


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


_BUG_ALIASES: dict[str, set[str]] = {
    "bug_id": {"bugid", "bug编号", "缺陷id", "缺陷编号", "问题编号", "issuekey", "编号", "id"},
    "title": {"标题", "缺陷标题", "问题标题", "bug标题", "summary", "subject", "title"},
    "module": {"模块", "所属模块", "功能模块", "module", "component", "组件"},
    "severity": {"严重程度", "严重级别", "严重性", "severity", "bug级别"},
    "priority": {"优先级", "priority", "优先程度"},
    "status": {"状态", "缺陷状态", "status", "处理状态"},
    "precondition": {"前置条件", "前提条件", "precondition", "prerequisite"},
    "steps": {"复现步骤", "重现步骤", "操作步骤", "步骤", "stepstoreproduce", "steps", "reprosteps"},
    "expected": {"预期结果", "期望结果", "expectedresult", "expected"},
    "actual": {"实际结果", "当前结果", "actualresult", "actual"},
    "environment": {"环境", "测试环境", "environment", "env"},
    "found_version": {"发现版本", "影响版本", "版本", "affectedversion", "foundinversion"},
    "fixed_version": {"修复版本", "解决版本", "fixversion", "fixedversion"},
    "reporter": {"创建人", "报告人", "提交人", "reporter", "creator"},
    "assignee": {"指派给", "处理人", "负责人", "assignee", "owner"},
    "created_at": {"创建时间", "提交时间", "created", "createdat", "创建日期"},
    "closed_at": {"关闭时间", "解决时间", "closed", "resolvedat", "完成时间"},
    "requirement": {"关联需求", "需求编号", "需求", "requirement", "story", "storyid"},
    "comments": {"备注", "评论", "处理意见", "comments", "comment", "remark"},
}
_TEST_CASE_ALIASES: dict[str, set[str]] = {
    "case_id": {"用例id", "用例编号", "测试用例编号", "caseid", "testcaseid", "编号", "id"},
    "title": {"用例标题", "用例名称", "测试点", "标题", "casetitle", "title", "name"},
    "module": {"模块", "所属模块", "功能模块", "module", "feature"},
    "precondition": {"前置条件", "前提条件", "precondition", "prerequisite"},
    "step_no": {"步骤序号", "步骤编号", "序号", "stepno", "stepnumber", "stepindex"},
    "steps": {"测试步骤", "操作步骤", "执行步骤", "步骤", "teststeps", "steps"},
    "expected": {"预期结果", "期望结果", "检查点", "expectedresult", "expected"},
    "test_data": {"测试数据", "输入数据", "数据", "testdata", "input"},
    "priority": {"优先级", "priority", "级别"},
    "case_type": {"用例类型", "测试类型", "类型", "casetype", "testtype"},
    "status": {"状态", "用例状态", "status"},
    "requirement": {"关联需求", "需求编号", "需求", "requirement", "story", "storyid"},
    "expected_api": {"关联接口", "接口", "api", "endpoint"},
    "owner": {"负责人", "创建人", "设计人", "owner", "author"},
    "remarks": {"备注", "说明", "remarks", "remark", "comment"},
}


def _reverse_aliases(aliases: dict[str, set[str]]) -> dict[str, str]:
    return {
        _normalize_header(label): semantic
        for semantic, labels in aliases.items()
        for label in labels
    }


_BUG_HEADERS = _reverse_aliases(_BUG_ALIASES)
_TEST_CASE_HEADERS = _reverse_aliases(_TEST_CASE_ALIASES)


def _table_membership(document_ir: dict[str, Any]) -> dict[str, str]:
    membership: dict[str, str] = {}
    for index, raw in enumerate(_list(document_ir.get("tables")), start=1):
        if not isinstance(raw, dict):
            continue
        table_id = _text(raw.get("block_id") or raw.get("table_id")) or f"table:{index}"
        for field in ("cell_block_ids", "block_ids", "child_block_ids"):
            for block_id in _list(raw.get(field)):
                if _text(block_id):
                    membership[_text(block_id)] = table_id
    return membership


def _fallback_table_key(block: dict[str, Any]) -> str:
    if _text(block.get("sheet")):
        return f"sheet:{_text(block.get('sheet'))}"
    locator = _text(block.get("source_locator"))
    for marker in (";cell=", ";table-cell="):
        if marker in locator:
            return locator.split(marker, 1)[0]
    return _text(block.get("parent_id")) or locator or "ungrouped-table"


def _tables(document_ir: dict[str, Any]) -> list[dict[str, Any]]:
    membership = _table_membership(document_ir)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in _list(document_ir.get("blocks")):
        if not isinstance(raw, dict) or _text(raw.get("type")) != "TABLE_CELL":
            continue
        cell = dict(raw)
        if int(cell.get("row_index") or 0) <= 0 or int(cell.get("column_index") or 0) <= 0:
            continue
        key = membership.get(_text(cell.get("block_id"))) or _fallback_table_key(cell)
        grouped[key].append(cell)

    result: list[dict[str, Any]] = []
    for table_id, cells in grouped.items():
        grid: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
        for cell in cells:
            grid[int(cell["row_index"])][int(cell["column_index"])] = cell
        if not grid:
            continue
        row_numbers = sorted(grid)
        max_column = max(max(columns) for columns in grid.values() if columns)
        locator = _text(cells[0].get("source_locator"))
        result.append(
            {
                "table_id": table_id,
                "rows": grid,
                "row_numbers": row_numbers,
                "max_column": max_column,
                "source_locator": locator.split(";cell=", 1)[0].split(";table-cell=", 1)[0],
                "sheet": _text(cells[0].get("sheet")),
            }
        )
    return result


def _header_candidates(table: dict[str, Any], limit: int = 5) -> list[int]:
    return [
        row_number
        for row_number in table["row_numbers"][:limit]
        if sum(
            1
            for cell in table["rows"][row_number].values()
            if _text(cell.get("text"))
        )
        >= 2
    ]


def _profile_score(
    table: dict[str, Any],
    header_row: int,
    mapping: dict[str, str],
    profile: str,
) -> tuple[int, dict[int, str]]:
    mapped: dict[int, str] = {}
    for column, cell in table["rows"][header_row].items():
        semantic = mapping.get(_normalize_header(cell.get("text")))
        if semantic and semantic not in mapped.values():
            mapped[column] = semantic
    values = set(mapped.values())
    score = len(values)
    if profile == "historical_bug":
        score += 4 if "title" in values else 0
        score += 3 if "actual" in values else 0
        score += 3 if "steps" in values else 0
        score += 2 if values & {"severity", "status", "bug_id"} else 0
        score += 2 if {"expected", "actual"} <= values else 0
    else:
        score += 4 if "title" in values else 0
        score += 4 if "steps" in values else 0
        score += 4 if "expected" in values else 0
        score += 2 if values & {"case_id", "precondition", "test_data", "step_no"} else 0
    return score, mapped


def _choose_profile(
    table: dict[str, Any], source_type: str
) -> tuple[str, int, int, dict[int, str]] | None:
    best: tuple[str, int, int, dict[int, str]] | None = None
    for header_row in _header_candidates(table):
        candidates = [
            ("historical_bug", *_profile_score(table, header_row, _BUG_HEADERS, "historical_bug")),
            ("test_case", *_profile_score(table, header_row, _TEST_CASE_HEADERS, "test_case")),
        ]
        for profile, score, mapped in candidates:
            explicit = (
                profile == "historical_bug" and source_type in {"historical_bug", "ticket"}
            ) or (profile == "test_case" and source_type in {"test_case", "test_plan", "test_data"})
            total = score + (4 if explicit else 0)
            if total < (8 if explicit else 11):
                continue
            candidate = (profile, total, header_row, mapped)
            if best is None or candidate[1] > best[1]:
                best = candidate
    return best


def _cell_evidence(cell: dict[str, Any]) -> dict[str, Any]:
    address = dict(cell.get("evidence_address") or {})
    return {
        "block_id": _text(cell.get("block_id")),
        "source_id": _text(cell.get("source_id") or address.get("source_id")),
        "source_hash": _text(cell.get("source_hash") or address.get("source_hash")),
        "source_locator": _text(cell.get("source_locator") or address.get("source_locator")),
        "sheet": _text(cell.get("sheet") or address.get("sheet")),
        "cell_ref": _text(cell.get("cell_ref") or address.get("cell_ref")),
        "row_index": int(cell.get("row_index") or 0),
        "column_index": int(cell.get("column_index") or 0),
        "address_kind": _text(address.get("address_kind")) or "SPREADSHEET_CELL",
    }


def _record(
    *,
    profile: str,
    source_id: str,
    table: dict[str, Any],
    header_row: int,
    row_number: int,
    mapped_columns: dict[int, str],
) -> dict[str, Any] | None:
    row = table["rows"].get(row_number) or {}
    semantic_values: dict[str, str] = {}
    evidence: dict[str, dict[str, Any]] = {}
    evidence_spans: dict[str, list[dict[str, Any]]] = {}
    original_fields: dict[str, str] = {}
    for column in range(1, int(table["max_column"]) + 1):
        cell = row.get(column)
        if not isinstance(cell, dict):
            continue
        value = _text(cell.get("text"))
        if not value:
            continue
        header_cell = table["rows"].get(header_row, {}).get(column, {})
        original_header = _text(header_cell.get("text")) or f"column_{column}"
        original_fields[original_header] = value
        semantic = mapped_columns.get(column)
        if semantic:
            semantic_values[semantic] = value
            cell_evidence = _cell_evidence(cell)
            evidence[semantic] = cell_evidence
            evidence_spans[semantic] = [cell_evidence]
    if not semantic_values:
        return None
    if profile == "historical_bug" and not (
        semantic_values.get("title")
        or semantic_values.get("bug_id")
        or semantic_values.get("actual")
    ):
        return None
    if profile == "test_case" and not (
        semantic_values.get("title")
        or semantic_values.get("case_id")
        or semantic_values.get("steps")
    ):
        return None
    prefix = "bug" if profile == "historical_bug" else "test_case_row"
    return {
        f"{prefix}_id": _stable_id(prefix, source_id, table["table_id"], row_number),
        "source_id": source_id,
        "source_profile": profile,
        "table_id": table["table_id"],
        "table_source_locator": table["source_locator"],
        "sheet": table["sheet"],
        "header_row_index": header_row,
        "row_index": row_number,
        "row_indices": [row_number],
        **semantic_values,
        "original_fields": original_fields,
        "field_evidence": evidence,
        "field_evidence_spans": evidence_spans,
        "source_locators": sorted(
            {
                _text(item.get("source_locator"))
                for item in evidence.values()
                if _text(item.get("source_locator"))
            }
        ),
        "business_semantics_inferred": False,
        "field_mapping_method": "source_declared_header_alias",
    }


def _append_unique(values: list[str], value: Any) -> None:
    normalized = _text(value)
    if normalized and normalized not in values:
        values.append(normalized)


def _test_case_identity(record: dict[str, Any]) -> str:
    case_id = _text(record.get("case_id"))
    title = _text(record.get("title"))
    if case_id:
        return f"id:{case_id.casefold()}"
    if title:
        return f"title:{title.casefold()}"
    return ""


def _merge_test_case_row(target: dict[str, Any], row: dict[str, Any]) -> None:
    multi_value_fields = {"steps", "expected", "test_data", "remarks"}
    conflicts = list(target.get("field_conflicts") or [])
    for field, raw_value in row.items():
        if field in {
            "test_case_row_id",
            "source_id",
            "source_profile",
            "table_id",
            "table_source_locator",
            "sheet",
            "header_row_index",
            "row_index",
            "row_indices",
            "original_fields",
            "field_evidence",
            "field_evidence_spans",
            "source_locators",
            "business_semantics_inferred",
            "field_mapping_method",
        }:
            continue
        value = _text(raw_value)
        if not value:
            continue
        existing = _text(target.get(field))
        if field in multi_value_fields:
            values = list(target.setdefault("_aggregated_values", {}).setdefault(field, []))
            _append_unique(values, existing)
            step_no = _text(row.get("step_no"))
            rendered = f"{step_no}. {value}" if step_no and field in {"steps", "expected"} else value
            _append_unique(values, rendered)
            target["_aggregated_values"][field] = values
            target[field] = "\n".join(values)
        elif not existing:
            target[field] = value
        elif existing != value and field != "step_no":
            conflicts.append(
                {
                    "field": field,
                    "kept_value": existing,
                    "conflicting_value": value,
                    "row_index": int(row.get("row_index") or 0),
                    "source_locator": next(
                        (
                            _text(item.get("source_locator"))
                            for item in (row.get("field_evidence_spans") or {}).get(field, [])
                            if _text(item.get("source_locator"))
                        ),
                        "",
                    ),
                }
            )
    target["field_conflicts"] = conflicts
    target["row_indices"] = sorted(
        {
            *[int(value) for value in target.get("row_indices") or [] if int(value) > 0],
            *[int(value) for value in row.get("row_indices") or [] if int(value) > 0],
        }
    )
    target["row_index"] = min(target["row_indices"]) if target["row_indices"] else 0
    for header, value in (row.get("original_fields") or {}).items():
        target.setdefault("original_fields", {}).setdefault(header, value)
    for field, spans in (row.get("field_evidence_spans") or {}).items():
        bucket = target.setdefault("field_evidence_spans", {}).setdefault(field, [])
        known = {_text(item.get("block_id")) for item in bucket if isinstance(item, dict)}
        for evidence in spans or []:
            if not isinstance(evidence, dict):
                continue
            block_id = _text(evidence.get("block_id"))
            if block_id and block_id not in known:
                bucket.append(dict(evidence))
                known.add(block_id)
        if bucket:
            target.setdefault("field_evidence", {})[field] = dict(bucket[0])
    target["source_locators"] = sorted(
        {
            *[_text(value) for value in target.get("source_locators") or [] if _text(value)],
            *[_text(value) for value in row.get("source_locators") or [] if _text(value)],
        }
    )


def _group_test_case_rows(
    records: list[dict[str, Any]],
    *,
    source_id: str,
    table_id: str,
) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    by_identity: dict[str, dict[str, Any]] = {}
    last: dict[str, Any] | None = None
    for row in records:
        identity = _test_case_identity(row)
        target = by_identity.get(identity) if identity else None
        if target is None and not identity and last is not None and (
            _text(row.get("steps"))
            or _text(row.get("expected"))
            or _text(row.get("test_data"))
        ):
            target = last
        if target is None:
            target = dict(row)
            target.pop("test_case_row_id", None)
            stable_identity = identity or f"row:{int(row.get('row_index') or 0)}"
            target["test_case_id"] = _stable_id(
                "test_case", source_id, table_id, stable_identity
            )
            target["aggregated_from_multiple_rows"] = False
            grouped.append(target)
            if identity:
                by_identity[identity] = target
        else:
            _merge_test_case_row(target, row)
            target["aggregated_from_multiple_rows"] = len(target.get("row_indices") or []) > 1
        last = target
    for record in grouped:
        record.pop("_aggregated_values", None)
        record["field_conflict_count"] = len(record.get("field_conflicts") or [])
    return grouped


def extract_tabular_enterprise_semantics(
    document_ir: dict[str, Any],
    *,
    source_id: str,
    source_type: str = "",
    filename: str = "",
) -> dict[str, Any]:
    """Extract source-declared bug/test-case rows with exact cell evidence."""

    bugs: list[dict[str, Any]] = []
    test_cases: list[dict[str, Any]] = []
    table_receipts: list[dict[str, Any]] = []
    exact_evidence_fields = 0
    total_evidence_fields = 0
    conflict_count = 0
    for table in _tables(document_ir):
        chosen = _choose_profile(table, source_type)
        if chosen is None:
            table_receipts.append(
                {
                    "table_id": table["table_id"],
                    "status": "NOT_APPLICABLE",
                    "profile": "",
                    "row_count": len(table["row_numbers"]),
                    "business_semantics_added": False,
                }
            )
            continue
        profile, score, header_row, mapped_columns = chosen
        raw_records: list[dict[str, Any]] = []
        for row_number in table["row_numbers"]:
            if row_number <= header_row:
                continue
            record = _record(
                profile=profile,
                source_id=source_id,
                table=table,
                header_row=header_row,
                row_number=row_number,
                mapped_columns=mapped_columns,
            )
            if record is not None:
                raw_records.append(record)
        records = (
            _group_test_case_rows(
                raw_records,
                source_id=source_id,
                table_id=table["table_id"],
            )
            if profile == "test_case"
            else raw_records
        )
        for record in records:
            for spans in (record.get("field_evidence_spans") or {}).values():
                for evidence in spans or []:
                    total_evidence_fields += 1
                    if _text(evidence.get("source_locator")) and _text(evidence.get("source_hash")):
                        exact_evidence_fields += 1
            conflict_count += int(record.get("field_conflict_count") or 0)
        (bugs if profile == "historical_bug" else test_cases).extend(records)
        table_receipts.append(
            {
                "table_id": table["table_id"],
                "status": "EXTRACTED" if records else "EMPTY_ROWS",
                "profile": profile,
                "profile_score": score,
                "header_row": header_row,
                "mapped_columns": {
                    str(column): semantic
                    for column, semantic in sorted(mapped_columns.items())
                },
                "row_count": len(table["row_numbers"]),
                "raw_record_count": len(raw_records),
                "record_count": len(records),
                "multi_row_records_grouped": max(0, len(raw_records) - len(records)),
                "source_locator": table["source_locator"],
                "business_semantics_added": False,
            }
        )

    return {
        "schema": TABULAR_SEMANTIC_RECEIPT_SCHEMA,
        "filename": filename,
        "source_id": source_id,
        "source_type": source_type,
        "historical_bugs": bugs,
        "test_cases": test_cases,
        "table_count": len(table_receipts),
        "matched_table_count": sum(
            1 for row in table_receipts if row["status"] != "NOT_APPLICABLE"
        ),
        "historical_bug_count": len(bugs),
        "test_case_count": len(test_cases),
        "multi_row_test_case_count": sum(
            1 for row in test_cases if row.get("aggregated_from_multiple_rows")
        ),
        "field_conflict_count": conflict_count,
        "exact_field_evidence_rate": (
            round(exact_evidence_fields / total_evidence_fields, 4)
            if total_evidence_fields
            else 1.0
        ),
        "tables": table_receipts,
        "container_format_parsing_performed": False,
        "business_semantics_added": False,
        "header_aliases_are_source_label_normalization_only": True,
        "blank_identity_rows_attach_only_to_preceding_source_declared_case": True,
        "table_order_is_not_business_flow": True,
    }


__all__ = [
    "TABULAR_SEMANTIC_RECEIPT_SCHEMA",
    "extract_tabular_enterprise_semantics",
]
