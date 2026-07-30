"""Create fail-visible alignment candidates between database source families.

Native database models preserve schema-qualified physical identities. SQL DDL and data
 dictionaries may omit schema information, so matching names are supporting evidence only.
This stage never merges tables or chooses a source authority automatically.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Iterable

DATABASE_TABLE_ALIGNMENT_SCHEMA = "qualibug.database-table-source-alignment.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _dedupe(rows: Iterable[Any], identity_field: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = deepcopy(raw)
        identity = _text(row.get(identity_field))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _table_name(table: dict[str, Any]) -> str:
    return _text(table.get("name") or table.get("table_name"))


def _schema_name(table: dict[str, Any]) -> str:
    return _text(table.get("schema_name") or table.get("schema"))


def _columns(table: dict[str, Any]) -> set[str]:
    return {_text(value) for value in _list(table.get("columns")) if _text(value)}


def _identity_fields(table: dict[str, Any]) -> set[str]:
    return {
        _text(value)
        for value in _list(table.get("identity_fields"))
        if _text(value)
    }


def _source_ids(table: dict[str, Any]) -> list[str]:
    return sorted(
        {
            _text(table.get("source_id")),
            *[
                _text(value)
                for value in _list(table.get("source_refs"))
                if _text(value)
            ],
        }
        - {""}
    )


def _overlap(left: set[str], right: set[str]) -> dict[str, Any]:
    shared = sorted(left & right)
    union = left | right
    return {
        "shared": shared,
        "shared_count": len(shared),
        "left_count": len(left),
        "right_count": len(right),
        "jaccard": round(len(shared) / len(union), 4) if union else 1.0,
        "left_subset_of_right": bool(left) and left <= right,
        "right_subset_of_left": bool(right) and right <= left,
    }


def _is_model_table(table: dict[str, Any]) -> bool:
    return bool(_list(table.get("database_model_declarations"))) or _text(
        table.get("derivation")
    ) == "database_model_document_ir"


def _is_unqualified(table: dict[str, Any]) -> bool:
    return not _schema_name(table)


def enrich_asset_with_database_table_alignment_candidates(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Create exact-name, non-authoritative links without mutating table identity."""
    result = dict(asset or {})
    tables = [
        deepcopy(row)
        for row in _list(result.get("tables"))
        if isinstance(row, dict)
    ]
    model_tables = [row for row in tables if _is_model_table(row)]
    other_tables = [row for row in tables if not _is_model_table(row)]

    models_by_name: dict[str, list[dict[str, Any]]] = {}
    for table in model_tables:
        name = _table_name(table)
        if name:
            models_by_name.setdefault(name, []).append(table)

    candidates: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for source_table in other_tables:
        source_name = _table_name(source_table)
        source_table_id = _text(source_table.get("table_id"))
        if not source_name or not source_table_id or not _is_unqualified(source_table):
            continue
        matches = models_by_name.get(source_name, [])
        if not matches:
            continue

        match_rows: list[dict[str, Any]] = []
        for target in matches:
            target_table_id = _text(target.get("table_id"))
            if not target_table_id or target_table_id == source_table_id:
                continue
            match_rows.append(
                {
                    "target_table_id": target_table_id,
                    "target_schema_name": _schema_name(target),
                    "target_source_ids": _source_ids(target),
                    "column_overlap": _overlap(
                        _columns(source_table),
                        _columns(target),
                    ),
                    "identity_overlap": _overlap(
                        _identity_fields(source_table),
                        _identity_fields(target),
                    ),
                    "target_evidence": [
                        {
                            "source_id": _text(declaration.get("source_id")),
                            "source_locator": _text(
                                declaration.get("source_locator")
                            ),
                        }
                        for declaration in _list(
                            target.get("database_model_declarations")
                        )
                        if isinstance(declaration, dict)
                    ],
                }
            )
        if not match_rows:
            continue

        ambiguous = len(match_rows) > 1
        candidate = {
            "schema": DATABASE_TABLE_ALIGNMENT_SCHEMA,
            "alignment_id": _stable_id(
                "database_table_alignment",
                source_table_id,
                *sorted(row["target_table_id"] for row in match_rows),
            ),
            "source_table_id": source_table_id,
            "source_table_name": source_name,
            "source_schema_name": "",
            "source_ids": _source_ids(source_table),
            "source_evidence": {
                "source_locator": _text(source_table.get("source_locator")),
                "derivations": deepcopy(_list(source_table.get("derivations"))),
            },
            "matches": match_rows,
            "match_basis": "EXACT_UNQUALIFIED_TABLE_NAME",
            "status": (
                "AMBIGUOUS_REQUIRES_AUTHORITY"
                if ambiguous
                else "PENDING_SCHEMA_OR_SOURCE_AUTHORITY"
            ),
            "automatic_merge_allowed": False,
            "automatic_winner_selected": False,
            "operator_authority_required": True,
            "field_overlap_is_supporting_not_identity_authority": True,
            "name_case_or_vocabulary_inference_used": False,
        }
        candidates.append(candidate)
        if ambiguous:
            unresolved.append(
                {
                    "kind": "DATABASE_TABLE_ALIGNMENT_AMBIGUOUS",
                    "gap_type": "unqualified_table_matches_multiple_schemas",
                    "alignment_id": candidate["alignment_id"],
                    "source_table_id": source_table_id,
                    "candidate_table_ids": sorted(
                        row["target_table_id"] for row in match_rows
                    ),
                    "operator_action": (
                        "select the authoritative schema/table declaration or provide "
                        "schema-qualified DDL/data dictionary evidence"
                    ),
                }
            )

    candidates = _dedupe(
        [*_list(result.get("database_table_alignment_candidates")), *candidates],
        "alignment_id",
    )
    unresolved = _dedupe(
        [*_list(result.get("database_table_alignment_gaps")), *unresolved],
        "alignment_id",
    )

    relationships = [
        deepcopy(row)
        for row in _list(result.get("relationships"))
        if isinstance(row, dict)
    ]
    for candidate in candidates:
        for match in _list(candidate.get("matches")):
            if not isinstance(match, dict):
                continue
            relationships.append(
                {
                    "edge_id": _stable_id(
                        "edge_database_table_alignment",
                        candidate.get("alignment_id"),
                        match.get("target_table_id"),
                    ),
                    "from": _text(candidate.get("source_table_id")),
                    "to": _text(match.get("target_table_id")),
                    "relation": "database_table_alignment_candidate",
                    "confidence": 1.0,
                    "status": "pending_authority",
                    "derivation": "exact_unqualified_table_name",
                    "evidence": {
                        "alignment_id": candidate.get("alignment_id"),
                        "column_overlap": deepcopy(
                            _dict(match.get("column_overlap"))
                        ),
                        "identity_overlap": deepcopy(
                            _dict(match.get("identity_overlap"))
                        ),
                        "automatic_merge_allowed": False,
                    },
                }
            )
    relationships = _dedupe(relationships, "edge_id")

    result["tables"] = tables
    result["relationships"] = relationships
    result["database_table_alignment_candidates"] = candidates
    result["database_table_alignment_gaps"] = unresolved
    result["database_table_source_alignment"] = {
        "schema": DATABASE_TABLE_ALIGNMENT_SCHEMA,
        "model_table_count": len(model_tables),
        "other_table_count": len(other_tables),
        "candidate_count": len(candidates),
        "ambiguous_candidate_count": sum(
            1
            for row in candidates
            if _text(row.get("status")) == "AMBIGUOUS_REQUIRES_AUTHORITY"
        ),
        "pending_authority_count": len(candidates),
        "automatic_merge_count": 0,
        "automatic_winner_count": 0,
        "exact_name_only": True,
        "schema_omission_blocks_automatic_identity": True,
    }

    coverage_gaps = [
        deepcopy(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
    ]
    coverage_gaps.extend(deepcopy(row) for row in unresolved)
    result["coverage_gaps"] = coverage_gaps

    summary = _dict(result.get("summary"))
    summary.update(
        {
            "database_table_alignment_candidate_count": len(candidates),
            "database_table_alignment_ambiguous_count": len(unresolved),
        }
    )
    result["summary"] = summary

    governance = _dict(result.get("governance"))
    governance.update(
        {
            "database_table_alignment_never_auto_merges_unqualified_names": True,
            "database_table_alignment_requires_schema_or_source_authority": True,
            "database_table_field_overlap_is_not_identity_authority": True,
            "database_table_alignment_uses_no_vocabulary_inference": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "DATABASE_TABLE_ALIGNMENT_SCHEMA",
    "enrich_asset_with_database_table_alignment_candidates",
]
