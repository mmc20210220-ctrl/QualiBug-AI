"""Knowledge API facade preserving exact runtime SQL-DDL authority."""
from __future__ import annotations
from typing import Any

from . import _api_mainline_base as _base

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_original_build_runtime_source_knowledge_overlay = _base.build_runtime_source_knowledge_overlay
_original_merge_knowledge_asset_overlay = _base.merge_knowledge_asset_overlay


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _authority_rank(row: dict[str, Any]) -> int:
    evidence = row.get("evidence_address") if isinstance(row.get("evidence_address"), dict) else {}
    declared = _text(row.get("derivation")) == "database_model_document_ir" or bool(_list(row.get("database_model_declarations")))
    if declared and evidence.get("exact") is True and _text(row.get("source_locator")) and _text(row.get("source_id")):
        return 3
    if declared:
        return 2
    return 1 if _text(row.get("source_locator")) and _text(row.get("source_id")) else 0


def _merge_tables_with_authority(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = _base._merge_table_identities([dict(row) for row in rows if isinstance(row, dict)])
    strongest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        table_id = _text(row.get("table_id"))
        if table_id and _authority_rank(row) > _authority_rank(strongest.get(table_id, {})):
            strongest[table_id] = row
    for row in merged:
        authority = strongest.get(_text(row.get("table_id")))
        if not authority:
            continue
        for field in (
            "source_id", "source_type", "source_locator", "evidence_address",
            "derivation", "schema_name", "qualified_name", "table_kind",
            "database_model_declarations",
        ):
            value = authority.get(field)
            if value not in (None, "", [], {}):
                row[field] = value
    return merged


def _exact_database_overlay(db_schema_text: str) -> dict[str, Any]:
    text = str(db_schema_text or "")
    if not text.strip() or not __import__("re").search(r"(?is)\bCREATE\s+TABLE\b", text):
        return {}
    from .runtime_sql_ddl_authority import parse_runtime_sql_ddl
    content_hash = _base._hash_bytes(text.encode("utf-8"))
    source_id = f"runtime:database_schema:{content_hash[:24]}"
    return parse_runtime_sql_ddl(text, source_id=source_id)


def build_runtime_source_knowledge_overlay(*, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "") -> dict[str, Any]:
    overlay = _original_build_runtime_source_knowledge_overlay(
        prd_text=prd_text, api_spec_text=api_spec_text, db_schema_text=db_schema_text
    )
    exact = _exact_database_overlay(db_schema_text)
    if not exact:
        return overlay
    overlay["data_tables"] = _merge_tables_with_authority([
        *[dict(row) for row in _list(overlay.get("data_tables")) if isinstance(row, dict)],
        *[dict(row) for row in _list(exact.get("tables")) if isinstance(row, dict)],
    ])
    overlay["database_model_relationships"] = list(exact.get("database_model_relationships") or [])
    overlay["database_model_indexes"] = list(exact.get("database_model_indexes") or [])
    model = exact.get("database_model")
    overlay["database_models"] = [dict(model)] if isinstance(model, dict) and model else []
    return overlay


def merge_knowledge_asset_overlay(asset: dict[str, Any] | None, overlay: dict[str, Any] | None) -> dict[str, Any]:
    merged = _original_merge_knowledge_asset_overlay(asset, overlay)
    extra = dict(overlay or {})
    merged["data_tables"] = _merge_tables_with_authority([
        *[dict(row) for row in _list((asset or {}).get("data_tables")) if isinstance(row, dict)],
        *[dict(row) for row in _list(extra.get("data_tables")) if isinstance(row, dict)],
    ])
    for key, identity in (("database_model_relationships", "relationship_id"), ("database_model_indexes", "index_id")):
        merged[key] = _base._dedupe_by_id(
            [dict(row) for row in [*(_list((asset or {}).get(key))), *(_list(extra.get(key)))] if isinstance(row, dict)], identity
        )
    merged["database_models"] = _base._dedupe_by_id(
        [dict(row) for row in [*(_list((asset or {}).get("database_models"))), *(_list(extra.get("database_models")))] if isinstance(row, dict)], "source_id"
    )
    return merged


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
