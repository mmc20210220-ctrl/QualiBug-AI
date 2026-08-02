"""Runtime overlay, entity relations, knowledge asset API, CLI."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Semantic extraction budget: one provider round-trip per zero-output source.
_MAX_LLM_SOURCES_PER_BUILD = 12
# Matches the project-wide parallel LLM worker default; higher hits rate limits.
_LLM_EXTRACTION_WORKERS = 4

try:
    import docx2txt
except ImportError:
    docx2txt = None

from ._common import *  # noqa: F401,F403
from ._common import _safe_project_id, _load_json, _write_json, _icon  # explicit: underscore names not exported by *
from ._utils import *  # noqa: F401,F403
from ._utils import _hash_bytes, _short_hash, _norm, _tokens, _now, _paths, _load_registry, _save_registry, _redact_text  # noqa: F401
from ._parsing import *  # noqa: F401,F403
from ._parsing import _parse_source, _openapi_operations, _risk_type_from_text, _merge_table_identities  # noqa: F401
from ._crud import *  # noqa: F401,F403
from ._linking import *  # noqa: F401,F403
from ._linking import (_dedupe_by_id, _authoritative_rule_to_interface_edges, _links_by_overlap,  # noqa: F401
    _evidence_bundle, _merge_openapi, _module_tree, _oracle_dsl_pack_from_recognized_industries,
    _oracle_library, _probes_from_asset, _sync_declared_project_sources, _risk_domains)


def build_runtime_source_knowledge_overlay(
    *,
    prd_text: str = "",
    api_spec_text: str = "",
    db_schema_text: str = "",
) -> dict[str, Any]:
    """Parse immutable in-run source text through the existing knowledge parsers.

    The overlay contains structured facts and parser receipts only; raw source
    bodies are not copied into the returned asset.
    """

    api_text = str(api_spec_text or "")
    api_prefix = api_text.lstrip()
    api_is_json = api_prefix.startswith("{")
    api_is_yaml = bool(
        re.search(r"(?m)^\s*(?:openapi|swagger)\s*:", api_text)
    )
    api_source_type = "openapi" if api_is_json or api_is_yaml else "markdown_api"
    api_filename = (
        "runtime_api.json"
        if api_is_json
        else "runtime_api.yaml"
        if api_is_yaml
        else "runtime_api.md"
    )
    documents = (
        ("prd", "runtime_prd.md", str(prd_text or "")),
        (api_source_type, api_filename, api_text),
        ("database_schema", "runtime_schema.sql", str(db_schema_text or "")),
    )
    parsed_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for source_type, filename, text in documents:
        if not text.strip():
            continue
        content_hash = _hash_bytes(text.encode("utf-8"))
        source_id = f"runtime:{source_type}:{content_hash[:24]}"
        source = {
            "source_id": source_id,
            "source_type": source_type,
            "original_name": filename,
            "filename": filename,
            "text_hash": content_hash,
            "content_hash": content_hash,
            "status": "active",
            "origin": "runtime_view",
        }
        parsed_rows.append((
            source,
            _parse_source(text.encode("utf-8"), filename, source_type, source_id),
        ))

    interfaces = _dedupe_by_id(
        [row for _, parsed in parsed_rows for row in parsed.get("operations") or []],
        "interface_id",
    )
    rules = _dedupe_by_id(
        [row for _, parsed in parsed_rows for row in parsed.get("rules") or []],
        "rule_id",
    )
    exact_edges = _authoritative_rule_to_interface_edges(rules, interfaces)
    exact_keys = {
        (str(edge.get("from")), str(edge.get("to")), str(edge.get("relation")))
        for edge in exact_edges
    }
    candidate_edges = [
        edge
        for edge in _links_by_overlap(
            rules,
            interfaces,
            "rule_id",
            "interface_id",
            relation="rule_to_interface",
        )
        if (
            str(edge.get("from")),
            str(edge.get("to")),
            str(edge.get("relation")),
        ) not in exact_keys
    ]
    parser_receipts = [
        dict(parsed.get("parser_receipt") or {})
        for _, parsed in parsed_rows
        if isinstance(parsed.get("parser_receipt"), dict)
    ]
    coverage_gaps = [
        {
            "gap_type": "runtime_source_parse_degraded",
            "reason_code": (
                "RUNTIME_SOURCE_PARSE_DEGRADED"
                if str(receipt.get("parser_status") or "") == "degraded"
                else "RUNTIME_SOURCE_PARSE_FAILED"
            ),
            "source_id": receipt.get("source_id"),
            "parser_receipt_id": receipt.get("receipt_id"),
            "errors": list(receipt.get("errors") or []),
        }
        for receipt in parser_receipts
        if str(receipt.get("parser_status") or "") in {"degraded", "failed"}
    ]
    return {
        "schema_version": "qualibug.runtime-source-knowledge-overlay.v1",
        "source_inventory": [dict(source) for source, _ in parsed_rows],
        "parser_receipts": parser_receipts,
        "interfaces": interfaces,
        # Same identity as persisted-asset composition: identical table_id rows
        # (inventory + DDL) must union columns, not first-wins drop.
        "data_tables": _merge_table_identities(
            [row for _, parsed in parsed_rows for row in parsed.get("tables") or []]
        ),
        "field_dictionary": _dedupe_by_id(
            [row for _, parsed in parsed_rows for row in parsed.get("field_dictionary") or []],
            "field_id",
        ),
        "ui_design_specs": _dedupe_by_id(
            [row for _, parsed in parsed_rows for row in parsed.get("ui_specs") or []],
            "ui_spec_id",
        ),
        "permission_matrix": _dedupe_by_id(
            [row for _, parsed in parsed_rows for row in parsed.get("permissions") or []],
            "permission_id",
        ),
        "rule_library": rules,
        "roles": _dedupe_by_id(
            [row for _, parsed in parsed_rows for row in parsed.get("roles") or []],
            "role_id",
        ),
        "state_machines": _dedupe_by_id(
            [row for _, parsed in parsed_rows for row in parsed.get("state_machines") or []],
            "state_machine_id",
        ),
        "relationships": _dedupe_by_id(
            [*exact_edges, *candidate_edges],
            "edge_id",
        ),
        "coverage_gaps": coverage_gaps,
    }


def _merge_interface_identities(rows: list[Any]) -> list[dict[str, Any]]:
    """Merge interfaces by ``interface_id``, preserving richer request contracts.

    First-wins ``_dedupe_by_id`` kept persisted markdown stubs (no schema) over
    runtime overlay rows that carry field-table ``request_schema``. That made
    required body fields invisible to IR/compile/pre-transport guards and turned
    empty POST bodies into ``CONTROL_SUCCESS_NOT_PROVEN`` at execution.
    """
    merged_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        interface_id = str(raw.get("interface_id") or "").strip()
        if not interface_id:
            continue
        row = dict(raw)
        if interface_id not in merged_by_id:
            merged_by_id[interface_id] = row
            order.append(interface_id)
            continue
        base = merged_by_id[interface_id]
        for field in ("request_schema", "requestBody", "request_example", "response_schema"):
            incoming = row.get(field)
            existing = base.get(field)
            if incoming in (None, "", {}, []):
                continue
            if existing in (None, "", {}, []):
                base[field] = incoming
                continue
            # Prefer the side that declares required properties / concrete example.
            if field in {"request_schema", "requestBody"} and isinstance(incoming, dict):
                in_props = dict(incoming.get("properties") or {})
                if not in_props:
                    for media in dict(incoming.get("content") or {}).values():
                        if isinstance(media, dict):
                            in_props.update(
                                dict(dict(media.get("schema") or {}).get("properties") or {})
                            )
                ex_props = dict(existing.get("properties") or {}) if isinstance(existing, dict) else {}
                if not ex_props and isinstance(existing, dict):
                    for media in dict(existing.get("content") or {}).values():
                        if isinstance(media, dict):
                            ex_props.update(
                                dict(dict(media.get("schema") or {}).get("properties") or {})
                            )
                in_required = list(incoming.get("required") or []) if isinstance(incoming, dict) else []
                ex_required = list(existing.get("required") or []) if isinstance(existing, dict) else []
                if len(in_props) > len(ex_props) or len(in_required) > len(ex_required):
                    base[field] = incoming
            elif field == "request_example" and isinstance(incoming, dict):
                if isinstance(existing, dict) and len(incoming) > len(existing):
                    base[field] = incoming
                elif not isinstance(existing, dict):
                    base[field] = incoming
        for field, value in row.items():
            if field in base and base.get(field) not in (None, "", {}, []):
                continue
            if value not in (None, "", {}, []):
                base[field] = value
        merged_by_id[interface_id] = base
    return [merged_by_id[key] for key in order]


def merge_knowledge_asset_overlay(
    asset: dict[str, Any] | None,
    overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge structured runtime facts into a persisted knowledge asset by identity.

    ``data_tables`` use column-union merge for the same ``table_id``. A weak
    inventory row (e.g. entity list with one field) must not erase runtime DDL /
    OpenAPI columns that share that identity — first-wins dedupe was projection loss.

    ``interfaces`` similarly prefer richer ``request_schema`` / examples from the
    runtime overlay over stale persisted stubs.
    """

    merged = dict(asset or {})
    extra = dict(overlay or {})
    identity_fields = {
        "source_inventory": "source_id",
        "parser_receipts": "receipt_id",
        "field_dictionary": "field_id",
        "ui_design_specs": "ui_spec_id",
        "permission_matrix": "permission_id",
        "rule_library": "rule_id",
        "roles": "role_id",
        "state_machines": "state_machine_id",
        "relationships": "edge_id",
    }
    for key, identity_field in identity_fields.items():
        merged[key] = _dedupe_by_id(
            [
                dict(row)
                for row in [*(merged.get(key) or []), *(extra.get(key) or [])]
                if isinstance(row, dict)
            ],
            identity_field,
        )
    merged["interfaces"] = _merge_interface_identities(
        [
            dict(row)
            for row in [*(merged.get("interfaces") or []), *(extra.get("interfaces") or [])]
            if isinstance(row, dict)
        ]
    )
    merged["data_tables"] = _merge_table_identities(
        [
            dict(row)
            for row in [*(merged.get("data_tables") or []), *(extra.get("data_tables") or [])]
            if isinstance(row, dict)
        ]
    )
    merged["coverage_gaps"] = [
        dict(row)
        for row in [
            *(merged.get("coverage_gaps") or []),
            *(extra.get("coverage_gaps") or []),
        ]
        if isinstance(row, dict)
    ]
    merged["runtime_source_overlay"] = {
        "schema_version": extra.get("schema_version"),
        "source_count": len(extra.get("source_inventory") or []),
        "source_fingerprints": [
            {
                "source_id": row.get("source_id"),
                "source_type": row.get("source_type"),
                "content_hash": row.get("content_hash"),
            }
            for row in extra.get("source_inventory") or []
            if isinstance(row, dict) and row.get("source_id")
        ],
        "parser_receipt_ids": [
            row.get("receipt_id")
            for row in extra.get("parser_receipts") or []
            if isinstance(row, dict) and row.get("receipt_id")
        ],
        "coverage_gap_count": len(extra.get("coverage_gaps") or []),
    }
    return merged


# ── Entity-Relation Graph + Cross-Document Conflict Detection (RAGFlow-inspired) ──


def _extract_entity_relations(
    interfaces: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    field_dictionary: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    state_machines: list[dict[str, Any]],
    permissions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract typed entity-relationship edges from already-parsed knowledge.

    Reuses existing parsed outputs (no new NLP dependency). Produces a graph
    of business entities and their relationships for downstream GraphRAG-style
    retrieval and Behavior IR enrichment.
    """
    relations: list[dict[str, Any]] = []

    def _add_rel(
        from_e: str,
        to_e: str,
        rel_type: str,
        source_id: str,
        confidence: float,
        chunk_ref: str = "",
        *,
        derivation: str = "parsed_technical_relation",
    ) -> None:
        if not from_e or not to_e:
            return
        relations.append({
            "from_entity": from_e,
            "to_entity": to_e,
            "relation_type": rel_type,
            "source_id": source_id,
            "source_chunk_id": chunk_ref,
            "confidence": round(min(1.0, max(0.0, confidence)), 3),
            "derivation": derivation,
            "status": "candidate" if derivation == "path_segment_heuristic" else "accepted",
        })

    # Table FK relationships (from table foreign_keys)
    for table in tables:
        tname = str(table.get("name") or "")
        sid = str(table.get("source_id") or "")
        for fk in table.get("foreign_keys") or []:
            target = str(fk) if isinstance(fk, str) else str(fk.get("ref_table") or fk.get("to") or "")
            if target:
                _add_rel(tname, target, "foreign_key", sid, 0.95, derivation="declared_foreign_key")
        # Field-to-table ownership
        for col in table.get("columns") or []:
            col_name = str(col) if isinstance(col, str) else str(col.get("name") or "")
            if col_name and tname:
                _add_rel(col_name, tname, "belongs_to", sid, 0.9, derivation="declared_column_ownership")

    # Field dictionary ownership
    for field in field_dictionary:
        fname = str(field.get("field") or "")
        tname = str(field.get("table") or "")
        sid = str(field.get("source_id") or "")
        if fname and tname:
            _add_rel(fname, tname, "field_of", sid, 0.85, derivation="declared_field_dictionary")

    # Interface-to-table relationships (path segment matching) — diagnostic only.
    # Never treat path vocabulary as source-declared object identity.
    table_names = {str(t.get("name") or "").lower(): str(t.get("name") or "") for t in tables}
    for iface in interfaces:
        path = str(iface.get("path") or "").lower()
        sid = str(iface.get("source_id") or "")
        op_id = str(iface.get("interface_id") or "")
        segments = {seg for seg in re.split(r"[/\-_{}]", path) if len(seg) >= 3}
        for seg in segments:
            matched_table = table_names.get(seg) or table_names.get(seg.rstrip("s")) or table_names.get(seg + "s")
            if matched_table:
                _add_rel(
                    op_id,
                    matched_table,
                    "operates_on",
                    sid,
                    0.7,
                    derivation="path_segment_heuristic",
                )

    # State machine transitions
    for sm in state_machines:
        entity = str(sm.get("entity") or sm.get("object") or sm.get("state_machine_id") or "")
        sid = str(sm.get("source_id") or "")
        states = sm.get("states") or sm.get("transitions") or []
        if isinstance(states, list):
            for i in range(len(states) - 1):
                from_s = str(states[i].get("from") or states[i]) if isinstance(states[i], dict) else str(states[i])
                to_s = str(states[i + 1].get("to") or states[i + 1]) if isinstance(states[i + 1], dict) else str(states[i + 1])
                if from_s and to_s and entity:
                    _add_rel(
                        f"{entity}:{from_s}",
                        f"{entity}:{to_s}",
                        "transitions",
                        sid,
                        0.8,
                        derivation="declared_state_machine",
                    )

    # Permission-to-role relationships
    for perm in permissions:
        role = str(perm.get("role") or perm.get("actor") or "")
        resource = str(perm.get("resource") or perm.get("scope") or perm.get("permission_id") or "")
        sid = str(perm.get("source_id") or "")
        if role and resource:
            action = str(perm.get("action") or perm.get("effect") or "access")
            _add_rel(
                role,
                resource,
                f"permission:{action}",
                sid,
                0.85,
                derivation="declared_permission",
            )

    # Rule-to-entity relationships (rules referencing known tables/interfaces)
    known_entities = {str(t.get("name") or "").lower(): str(t.get("name") or "") for t in tables}
    known_entities.update({str(i.get("interface_id") or "").lower(): str(i.get("interface_id") or "") for i in interfaces})
    for rule in rules:
        sid = str(rule.get("source_id") or "")
        rule_id = str(rule.get("rule_id") or "")
        rule_tokens = set(rule.get("tokens") or [])
        for token in rule_tokens:
            matched = known_entities.get(token.lower())
            if matched and rule_id:
                _add_rel(
                    rule_id,
                    matched,
                    "constrains",
                    sid,
                    0.6,
                    derivation="token_overlap",
                )

    # Deduplicate
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for rel in relations:
        key = f"{rel['from_entity']}|{rel['to_entity']}|{rel['relation_type']}"
        if key not in seen:
            seen.add(key)
            deduped.append(rel)
    return deduped


def _technical_declaration_fact(
    *,
    kind: str,
    source_id: str,
    entity: str,
    statement: str,
    locator: str = "",
    details: dict[str, Any] | None = None,
    quote: str = "",
    normalized_evidence: str = "",
    evidence_kind: str = "",
    evidence_derivation: str = "",
) -> dict[str, Any]:
    """Selectable projection of one exact technical source declaration.

    These are not Chinese business rules; they only give operators a SELECT_FACT
    target. Path vocabulary alone is never used to invent business meaning.
    """
    source = str(source_id or "").strip()
    if not source:
        raise ValueError("technical_declaration_source_id_required")
    material = f"{kind}|{source_id}|{entity}|{statement}"
    fact_id = "techfact:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    # A structured projection may summarize the declaration in ``statement``,
    # but generated prose is never source evidence. Only an exact captured quote
    # and locator may enter the evidence span.
    evidence_quote = str(quote or "")[:500]
    evidence_locator = str(locator or "").strip()
    safe_normalized_evidence = str(normalized_evidence or "").strip()[:1000]
    safe_evidence_kind = str(evidence_kind or "").strip()[:120]
    safe_evidence_derivation = str(evidence_derivation or "").strip()[:240]
    return {
        "fact_id": fact_id,
        "kind": kind,
        "status": "ACCEPTED",
        "source_id": source,
        "source_locator": evidence_locator,
        "raw_statement": statement,
        "statement": statement,
        "entity": entity,
        "technical_declaration": dict(details or {}),
        "formal_promotion_allowed": False,
        "source_spans": [
            {
                "source_id": source,
                "locator": evidence_locator,
                "quote": evidence_quote,
                "normalized_evidence": safe_normalized_evidence,
                "evidence_kind": safe_evidence_kind,
                "evidence_derivation": safe_evidence_derivation,
                "quote_hash": (
                    hashlib.sha256(evidence_quote.encode("utf-8")).hexdigest()
                    if evidence_quote
                    else ""
                ),
                "derivation": "structured_source_declaration_projection",
            }
        ],
    }


def _permission_action_decisions(
    entry: dict[str, Any],
) -> list[tuple[str, str]]:
    """Return exact (action, allow|deny) declarations from one permission row.

    Role/resource vocabulary is insufficient to prove a contradiction. A formal
    conflict requires the same explicit action identity on both sides.
    """
    raw_decision = str(entry.get("decision") or entry.get("effect") or "").strip().lower()
    if raw_decision in {"allow", "grant", "permit", "allowed"}:
        decision = "allow"
    elif raw_decision in {
        "deny",
        "forbid",
        "prohibit",
        "denied",
        "forbidden",
    }:
        decision = "deny"
    else:
        decision = ""

    actions_value = entry.get("actions")
    if isinstance(actions_value, list):
        actions = [str(value).strip().lower() for value in actions_value]
    elif isinstance(actions_value, str):
        actions = [actions_value.strip().lower()]
    else:
        action = str(entry.get("action") or "").strip().lower()
        actions = [action] if action else []

    decisions: list[tuple[str, str]] = [
        (action, decision)
        for action in actions
        if action and decision
    ]
    denied_value = entry.get("denied_actions")
    if isinstance(denied_value, list):
        denied_actions = [str(value).strip().lower() for value in denied_value]
    elif isinstance(denied_value, str):
        denied_actions = [denied_value.strip().lower()]
    else:
        denied_actions = []
    decisions.extend(
        (action, "deny")
        for action in denied_actions
        if action
    )
    return list(dict.fromkeys(decisions))


def _detect_cross_document_conflicts(
    field_dictionary: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    interfaces: list[dict[str, Any]],
    permissions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect contradictions between knowledge extracted from different sources.

    Authority-eligible conflicts require exact source identities and exact
    technical coordinates. Token overlap alone, path vocabulary, and permission
    action verbs without an explicit decision are never sufficient.
    """
    from ._chinese_business_conflicts import make_authority_eligible_conflict

    conflicts: list[dict[str, Any]] = []

    # 1. Field required/nullable mismatches across sources
    field_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in field_dictionary:
        table = str(field.get("table") or "").strip().lower()
        field_name = str(field.get("field") or "").strip().lower()
        if table and field_name:
            field_by_name[f"{table}:{field_name}"].append(field)
    for key, entries in field_by_name.items():
        if len(entries) < 2:
            continue
        required_true = [e for e in entries if e.get("required") is True]
        required_false = [e for e in entries if e.get("required") is False]
        if not required_true or not required_false:
            continue
        participants: list[dict[str, Any]] = []
        for declaration, required in [
            *((row, True) for row in required_true),
            *((row, False) for row in required_false),
        ]:
            source_id = str(declaration.get("source_id") or "").strip()
            if not source_id:
                continue
            participants.append(
                _technical_declaration_fact(
                    kind="TECHNICAL_FIELD_DECLARATION",
                    source_id=source_id,
                    entity=key,
                    statement=f"Field '{key}' declared required={str(required).lower()}",
                    locator=str(
                        declaration.get("source_locator")
                        or declaration.get("locator")
                        or declaration.get("field_id")
                        or ""
                    ),
                    details={
                        "required": required,
                        "table": declaration.get("table"),
                        "field": declaration.get("field"),
                    },
                    quote=str(declaration.get("quote") or declaration.get("source_excerpt") or ""),
                    normalized_evidence=str(declaration.get("normalized_evidence") or ""),
                    evidence_kind=str(declaration.get("evidence_kind") or ""),
                    evidence_derivation=str(declaration.get("evidence_derivation") or ""),
                )
            )
        participants = list(
            {
                str(row.get("fact_id") or ""): row
                for row in participants
                if str(row.get("fact_id") or "")
            }.values()
        )
        source_ids = {
            str(row.get("source_id") or "")
            for row in participants
            if str(row.get("source_id") or "")
        }
        required_values = {
            bool((row.get("technical_declaration") or {}).get("required"))
            for row in participants
        }
        if len(source_ids) < 2 or required_values != {True, False}:
            continue
        conflict = make_authority_eligible_conflict(
            "FIELD_REQUIRED_MISMATCH",
            participants,
            f"Field '{key}' is declared required in one source but nullable/optional in another",
            entity=key,
        )
        conflict["conflict_type"] = "field_required_mismatch"
        conflict["source_a"] = sorted(source_ids)[0]
        conflict["source_b"] = sorted(source_ids)[1]
        conflict["detail"] = conflict["reason"]
        conflicts.append(conflict)

    # 2. Exact role + resource + action permission contradictions.
    permission_by_coordinate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for perm in permissions:
        resource = str(perm.get("resource") or perm.get("scope") or "").strip().lower()
        role = str(perm.get("role") or perm.get("actor") or "").strip().lower()
        if not resource or not role:
            continue
        for action, decision in _permission_action_decisions(perm):
            declaration = dict(perm)
            declaration["_normalized_action"] = action
            declaration["_normalized_decision"] = decision
            permission_by_coordinate[f"{role}:{resource}:{action}"].append(declaration)

    for key, entries in permission_by_coordinate.items():
        allow_rows = [
            row for row in entries if row.get("_normalized_decision") == "allow"
        ]
        deny_rows = [
            row for row in entries if row.get("_normalized_decision") == "deny"
        ]
        if not allow_rows or not deny_rows:
            continue
        participants = []
        for declaration in [*allow_rows, *deny_rows]:
            source_id = str(declaration.get("source_id") or "").strip()
            if not source_id:
                continue
            decision = str(declaration.get("_normalized_decision") or "")
            evidence = declaration.get("evidence")
            if isinstance(evidence, str):
                quote = evidence
            elif isinstance(evidence, dict):
                quote = str(
                    evidence.get("quote")
                    or evidence.get("verbatim_quote")
                    or ""
                )
            else:
                quote = str(
                    declaration.get("quote")
                    or declaration.get("source_excerpt")
                    or ""
                )
            participants.append(
                _technical_declaration_fact(
                    kind="TECHNICAL_PERMISSION_DECLARATION",
                    source_id=source_id,
                    entity=key,
                    statement=f"Permission '{key}' decision={decision}",
                    locator=str(
                        declaration.get("source_locator")
                        or declaration.get("locator")
                        or declaration.get("permission_id")
                        or ""
                    ),
                    details={
                        "effect": decision,
                        "action": declaration.get("_normalized_action"),
                        "permission_id": declaration.get("permission_id"),
                    },
                    quote=quote,
                )
            )
        participants = list(
            {
                str(row.get("fact_id") or ""): row
                for row in participants
                if str(row.get("fact_id") or "")
            }.values()
        )
        source_ids = {
            str(row.get("source_id") or "")
            for row in participants
            if str(row.get("source_id") or "")
        }
        decisions = {
            str((row.get("technical_declaration") or {}).get("effect") or "")
            for row in participants
        }
        if len(source_ids) < 2 or decisions != {"allow", "deny"}:
            continue
        conflict = make_authority_eligible_conflict(
            "PERMISSION_CONTRADICTION",
            participants,
            f"Permission for '{key}' has conflicting allow/deny across sources",
            entity=key,
        )
        conflict["conflict_type"] = "permission_contradiction"
        conflict["source_a"] = sorted(source_ids)[0]
        conflict["source_b"] = sorted(source_ids)[1]
        conflict["detail"] = conflict["reason"]
        conflicts.append(conflict)

    # Prose rule overlap stays diagnostic-only. This inventory surface has no
    # exact source-backed rule identity or accepted semantic link, so it cannot
    # create a formal contradiction or authority target.

    return conflicts[:50]


def _structurize_rule_causal_chains(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Structurize text rules into causal chains (Daguan-style rule relationalization).

    For each rule, attempt to extract:
    - preconditions: list of {entity, field, value} that must hold before the action
    - trigger_action: the action/event that fires the rule
    - postconditions: list of {entity, field, must_become} or {entity, must_create}

    Industry-neutral: uses generic causal-pattern detection, no hardcoded
    business terms. Supports Chinese and English causal connectors.
    Rules that cannot be structured retain their original text-only form.
    """
    # Causal pattern indicators (language-neutral coverage)
    _TRIGGER_MARKERS = (
        "后", "之后", "时", "当", "执行", "触发", "调用",
        "after", "when", "upon", "on", "trigger", "execute",
    )
    _MUST_MARKERS = (
        "必须", "应当", "应该", "需要", "则", "就要", "确保",
        "must", "shall", "should", "required", "ensure", "then",
    )
    _POSTCONDITION_SPLIT = re.compile(r"[、；;，,]|(?:并且)|(?:同时)|(?:以及)| and |; ")

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        statement = str(rule.get("statement") or "").strip()
        if not statement or rule.get("postconditions") or rule.get("causal_chain"):
            continue  # Already structured or empty

        norm = _norm(statement)

        # Detect trigger + postcondition pattern
        trigger_part = ""
        must_part = ""

        # Pattern: "X后/时/当...必须/应当/则...Y"
        for marker in _MUST_MARKERS:
            idx = norm.find(marker)
            if idx > 0:
                before = statement[:idx].strip()
                after = statement[idx + len(marker):].strip()
                if after and len(after) >= 4:
                    trigger_part = before
                    must_part = after
                    break

        if not must_part:
            continue  # Cannot structurize

        # Extract trigger action from the before-part
        trigger_action = ""
        for tm in _TRIGGER_MARKERS:
            tidx = _norm(trigger_part).find(tm)
            if tidx > 0:
                trigger_action = trigger_part[:tidx].strip() or trigger_part
                break
        if not trigger_action and trigger_part:
            trigger_action = trigger_part

        # Extract preconditions from trigger ("当X并且Y" patterns)
        preconditions: list[dict[str, Any]] = []
        precond_markers = ("当", "并且", "且", "如果", "when", "and", "if")
        precond_parts = re.split(r"(?:并且)|(?:而且)| and |, ", trigger_part)
        for pp in precond_parts:
            pp = pp.strip().lstrip("当如果if ")
            if pp and len(pp) >= 3:
                # Try to extract entity.field = value
                eq_match = re.search(r"([\w\u4e00-\u9fff]+)[.．]([\w\u4e00-\u9fff]+)\s*[=是为]\s*(.+)", pp)
                if eq_match:
                    preconditions.append({
                        "entity": eq_match.group(1),
                        "field": eq_match.group(2),
                        "value": eq_match.group(3).strip(),
                    })
                else:
                    preconditions.append({"description": pp})

        # Split postconditions (multiple effects separated by connectors)
        postconditions: list[dict[str, Any]] = []
        post_parts = _POSTCONDITION_SPLIT.split(must_part)
        for pp in post_parts:
            pp = pp.strip()
            if not pp or len(pp) < 3:
                continue
            # Try "entity.field → value" or "entity → action"
            arrow_match = re.search(r"([\w\u4e00-\u9fff]+)[.．]?([\w\u4e00-\u9fff]*)\s*(?:→|->|=>)\s*(.+)", pp)
            if arrow_match:
                postconditions.append({
                    "entity": arrow_match.group(1),
                    "field": arrow_match.group(2) or "",
                    "must_become": arrow_match.group(3).strip(),
                })
            else:
                # Try "创建/释放/记录 + entity" pattern
                create_match = re.search(r"(创建|释放|记录|生成|发送|触发|create|release|record|send|emit)\s*([\w\u4e00-\u9fff]+)", pp)
                if create_match:
                    postconditions.append({
                        "entity": create_match.group(2),
                        "must_create": True,
                        "action": create_match.group(1),
                    })
                else:
                    postconditions.append({"description": pp})

        if postconditions:
            rule["causal_chain"] = {
                "preconditions": preconditions,
                "trigger_action": trigger_action,
                "postconditions": postconditions,
            }

    return rules


def build_enterprise_business_knowledge_asset(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    from ._crud import _record_parse  # lazy: avoid circular import
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = options or {}
    registry = _load_registry(project, root)
    if options.get("sync_declared_sources", True):
        registry = _sync_declared_project_sources(project, root, registry)
    active = [row for row in registry.get("sources") or [] if isinstance(row, dict) and row.get("status") == "active"]
    parsed_rows = [(source, _record_parse(source, root)) for source in active]
    parser_receipts = [
        dict(parsed.get("parser_receipt") or {})
        for _, parsed in parsed_rows
        if isinstance(parsed.get("parser_receipt"), dict)
    ]
    parse_coverage_gaps = [
        {
            "kind": "SOURCE_PARSE_DEGRADED" if str(receipt.get("parser_status") or "") == "degraded" else "SOURCE_PARSE_FAILED",
            "source_id": receipt.get("source_id"),
            "source_locator": receipt.get("source_locator"),
            "parser_receipt_id": receipt.get("receipt_id"),
            "errors": list(receipt.get("errors") or []),
            "operator_action": "inspect_parser_receipt",
        }
        for receipt in parser_receipts
        if str(receipt.get("parser_status") or "") in {"degraded", "failed"}
    ]
    # ── Phase 0: per-source extraction-empty coverage gaps ──
    for receipt in parser_receipts:
        for err in receipt.get("errors") or []:
            if isinstance(err, dict) and err.get("gap_type"):
                parse_coverage_gaps.append({
                    "kind": "STRUCTURED_EXTRACTION_EMPTY",
                    "gap_type": err["gap_type"],
                    "source_id": receipt.get("source_id"),
                    "source_locator": receipt.get("source_locator"),
                    "parser_receipt_id": receipt.get("receipt_id"),
                    "extraction_outcome": receipt.get("extraction_outcome", ""),
                    "operator_action": err.get("operator_action", "enhance_parser_or_provide_machine_readable_source"),
                })
    # ── Phase 3: LLM semantic extraction for zero-output sources ──
    # Rule extraction (SPEC §12/§13) runs independently of the legacy
    # zero-output gate: tables / field dictionaries never cover the textual
    # business rules the regex vocabulary may have missed.
    semantic_candidates: list[dict[str, Any]] = []
    semantic_receipts: list[dict[str, Any]] = []
    from ._semantic_extraction import (
        provider_status,
        resolve_semantic_rule_extraction_mode,
        run_semantic_extraction,
        semantic_extraction_availability,
    )
    _sem_requested = bool(
        options.get("enable_semantic_extraction")
        or os.getenv("QUALIBUG_SEMANTIC_EXTRACTION", "").strip() in {"1", "true", "yes"}
    )
    _rule_mode_receipt = resolve_semantic_rule_extraction_mode(
        requested_mode=str(options.get("semantic_rule_extraction_mode") or "shadow"),
        provider_status_value=provider_status(),
        governance_policy={
            "promotion_gates_met": options.get("rule_promotion_gates_met") is True
        },
    )
    _rule_mode_active = _rule_mode_receipt["effective_mode"] in {
        "shadow",
        "augment",
        "required",
    }
    _should_run_llm = _sem_requested or _rule_mode_active
    _sem_availability = semantic_extraction_availability(_should_run_llm)
    if not _sem_availability.get("available"):
        parse_coverage_gaps.append({
            "kind": "SEMANTIC_EXTRACTION_UNAVAILABLE",
            "gap_type": "semantic_extraction_unavailable",
            "source_id": "",
            "operator_action": (
                f"semantic layer disabled ({_sem_availability.get('reason')}): "
                f"{_sem_availability.get('detail')}"
            )[:200],
        })
    semantic_receipts.append(_rule_mode_receipt)
    _sem_targets: list[tuple[dict[str, Any], str]] = []
    if _sem_availability.get("available"):
        for source, parsed in parsed_rows:
            _src_text = parsed.get("text") or ""
            _src_structured = (
                len(parsed.get("tables") or [])
                + len(parsed.get("field_dictionary") or [])
                + len(parsed.get("permissions") or [])
            )
            if _rule_mode_active:
                if _src_text.strip():
                    _sem_targets.append((source, _src_text))
            elif _src_structured == 0 and _src_text.strip():
                _sem_targets.append((source, _src_text))
    # Each target costs one provider round-trip, so the layer is both capped and
    # run concurrently — otherwise a document-heavy project serializes minutes of
    # latency into asset construction.
    if len(_sem_targets) > _MAX_LLM_SOURCES_PER_BUILD:
        for _skipped_source, _ in _sem_targets[_MAX_LLM_SOURCES_PER_BUILD:]:
            parse_coverage_gaps.append({
                "kind": "SEMANTIC_EXTRACTION_SKIPPED",
                "gap_type": "semantic_extraction_budget_exhausted",
                "source_id": _skipped_source.get("source_id"),
                "operator_action": (
                    f"per-build semantic extraction budget is "
                    f"{_MAX_LLM_SOURCES_PER_BUILD} sources; this source was not attempted"
                ),
            })
        _sem_targets = _sem_targets[:_MAX_LLM_SOURCES_PER_BUILD]
    if _sem_targets:
        def _run_one(target: tuple[dict[str, Any], str]) -> tuple[dict[str, Any], Any]:
            _source, _text = target
            return _source, run_semantic_extraction(
                _text,
                source_id=str(_source.get("source_id") or ""),
                filename=str(_source.get("original_name") or ""),
            )

        with ThreadPoolExecutor(max_workers=_LLM_EXTRACTION_WORKERS) as _pool:
            _sem_results = list(_pool.map(_run_one, _sem_targets))
        for _source, _sem_receipt in _sem_results:
            semantic_receipts.append(_sem_receipt.to_dict())
            semantic_candidates.extend(_sem_receipt.candidates_validated)
            if _sem_receipt.status.startswith("FAILED"):
                parse_coverage_gaps.append({
                    "kind": "SEMANTIC_EXTRACTION_FAILED",
                    "gap_type": "semantic_extraction_error",
                    "source_id": _source.get("source_id"),
                    "operator_action": _sem_receipt.error[:200],
                })
    source_texts = {f"{source.get('source_type')}:{source.get('original_name')}": parsed.get("text") or "" for source, parsed in parsed_rows if parsed.get("text")}
    openapi_parts = [parsed.get("openapi") for _, parsed in parsed_rows if isinstance(parsed.get("openapi"), dict) and parsed.get("openapi")]
    merged_openapi = _merge_openapi(openapi_parts)
    interfaces = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("operations") or []], "interface_id")
    # Entities are routinely declared by more than one source (a data dictionary
    # section and an OpenAPI schema, say). Merge them so the later declaration's
    # columns survive instead of being dropped by identity deduplication.
    tables = _merge_table_identities([row for _, parsed in parsed_rows for row in parsed.get("tables") or []])
    field_dictionary = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("field_dictionary") or []], "field_id")
    ui_specs = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("ui_specs") or []], "ui_spec_id")
    known_tables = {str(row.get("table_id") or "") for row in tables}
    for row in field_dictionary:
        table_id = str(row.get("table_id") or "")
        table_name = str(row.get("table") or "default")
        if table_id and table_id not in known_tables:
            grouped_fields = [item for item in field_dictionary if str(item.get("table_id") or "") == table_id]
            tables.append({
                "table_id": table_id,
                "source_id": row.get("source_id"),
                "name": table_name,
                "columns": sorted({str(item.get("field") or "") for item in grouped_fields if str(item.get("field") or "")}),
                "foreign_keys": [],
                "field_dictionary": grouped_fields,
                "tokens": sorted(_tokens(f"{table_name} {' '.join(str(item.get('field') or '') for item in grouped_fields)}")),
            })
            known_tables.add(table_id)
    permissions = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("permissions") or []], "permission_id")
    rules = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("rules") or []], "rule_id")
    roles = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("roles") or []], "role_id")
    source_states = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("state_machines") or []], "state_machine_id")
    tickets = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("tickets") or []], "risk_id")
    cfg = load_real_project_config(project, root)
    industry = infer_multi_industry_business_model(source_texts, merged_openapi, cfg, project) if source_texts or interfaces else {"summary": {}, "business_objects": [], "roles": [], "state_machines": [], "permission_boundaries": [], "data_dependencies": [], "business_rules": [], "industry_oracles": [], "risk_domains": [], "recognized_industries": []}
    # The enterprise documents remain the source of truth. Inferred rows are appended
    # only as explicitly marked derived entries, never overwrite user material.
    for row in industry.get("business_rules") or []:
        if not isinstance(row, dict):
            continue
        copied = dict(row)
        copied["rule_id"] = f"industry:{row.get('rule_id') or _short_hash(row)}"
        copied["source_id"] = "industry_inference"
        copied["source_type"] = "derived_inference"
        copied["statement"] = _redact_text(row.get("expected") or row.get("statement") or "", 720)
        copied["tokens"] = sorted(_tokens(copied["statement"]))
        copied["risk_type"] = copied.get("kind") or _risk_type_from_text(copied["statement"])
        copied["severity"] = copied.get("severity") or "P1"
        rules.append(copied)
    dsl_rules, dsl_oracles = _oracle_dsl_pack_from_recognized_industries(
        industry.get("recognized_industries") or []
    )
    rules.extend(dsl_rules)
    rules = _dedupe_by_id(rules, "rule_id")
    # ── Augment promotion (SPEC §12.3): validated explicit LLM-only rule
    # candidates enter rule_library when the mode receipt resolved to augment,
    # then flow through the existing governance chain. Shadow keeps them as
    # candidates only.
    if _rule_mode_receipt["effective_mode"] == "augment":
        _llm_rule_candidates = [
            dict(row)
            for row in semantic_candidates
            if isinstance(row, dict)
            and str(row.get("kind") or "").lower() == "rule"
        ]
        if _llm_rule_candidates:
            from ._semantic_extraction import (
                build_rule_candidate_ledger,
                promote_rule_candidates_to_rules,
            )

            _regex_rules_by_source: dict[str, list[dict[str, Any]]] = {}
            for _r in rules:
                for _span in (_r.get("source_spans") or []):
                    if (
                        isinstance(_span, dict)
                        and str(_span.get("source_id") or "").strip()
                    ):
                        _regex_rules_by_source.setdefault(
                            str(_span.get("source_id") or "").strip(), []
                        ).append(_r)
                        break
            _promoted_all: list[dict[str, Any]] = []
            for _cand in _llm_rule_candidates:
                _src = str(_cand.get("source_id") or "").strip()
                if not _src:
                    continue
                _src_text = next(
                    (p.get("text") or "" for _, p in parsed_rows
                     if str(_.get("source_id") or "").strip() == _src),
                    "",
                )
                _ledger = build_rule_candidate_ledger(
                    _regex_rules_by_source.get(_src, []),
                    [_cand],
                    source_id=_src,
                    source_text=_src_text,
                )
                _promoted, _promo_receipt = promote_rule_candidates_to_rules(
                    _ledger.get("entries", []),
                    source_id=_src,
                )
                _promoted_all.extend(_promoted)
            if _promoted_all:
                _existing_rule_ids = {
                    str(row.get("rule_id") or "").strip() for row in rules
                }
                rules.extend(
                    [
                        dict(row)
                        for row in _promoted_all
                        if str(row.get("rule_id") or "").strip()
                        not in _existing_rule_ids
                    ]
                )
    industry_oracles = list(industry.get("industry_oracles") or []) + dsl_oracles
    objects = list(industry.get("business_objects") or [])
    object_names = {str(row.get("object") or "") for row in objects if isinstance(row, dict)}
    for table in tables:
        name = str(table.get("name") or "")
        if name and name not in object_names:
            objects.append({"object": name, "source": "database_schema", "evidence": [{"source_id": table.get("source_id"), "table_id": table.get("table_id")}], "confidence": 0.62})
            object_names.add(name)
    for row in industry.get("roles") or []:
        if isinstance(row, dict):
            copied = dict(row)
            copied["role_id"] = f"industry_role:{row.get('role') or _short_hash(row)}"
            copied["source_id"] = "industry_inference"
            roles.append(copied)
    roles = _dedupe_by_id(roles, "role_id")
    derived_state_machines = [
        dict(
            row,
            state_machine_id=f"industry_state:{row.get('state_machine_id') or _short_hash(row)}",
            source_id="industry_inference",
        )
        for row in industry.get("state_machines") or []
        if isinstance(row, dict)
    ]
    state_machines = _dedupe_by_id([*source_states, *derived_state_machines], "state_machine_id")
    dependencies: list[dict[str, Any]] = []
    for table in tables:
        for target in table.get("foreign_keys") or []:
            dependencies.append({"dependency_id": f"dbdep:{_short_hash({'from': table.get('name'), 'to': target})}", "source_id": table.get("source_id"), "from": table.get("table_id"), "to": f"table:{target}", "relation": "foreign_key"})
    for row in industry.get("data_dependencies") or []:
        if isinstance(row, dict):
            dependencies.append({"dependency_id": f"industrydep:{_short_hash(row)}", "source_id": "industry_inference", "from": row.get("from") or row.get("source"), "to": row.get("to") or row.get("target"), "relation": row.get("relation") or "business_dependency"})
    dependencies = _dedupe_by_id(dependencies, "dependency_id")
    exact_section_edges = _authoritative_rule_to_interface_edges(rules, interfaces)
    exact_section_keys = {
        (str(edge.get("from")), str(edge.get("to")), str(edge.get("relation")))
        for edge in exact_section_edges
    }
    overlap_edges = [
        *_links_by_overlap(rules, interfaces, "rule_id", "interface_id", relation="rule_to_interface"),
        *_links_by_overlap(rules, tables, "rule_id", "table_id", relation="rule_to_table"),
        *_links_by_overlap(interfaces, tables, "interface_id", "table_id", relation="interface_to_table"),
        *_links_by_overlap(ui_specs, interfaces, "ui_spec_id", "interface_id", relation="ui_to_interface"),
    ]
    relation_edges = [
        *exact_section_edges,
        *[
            edge
            for edge in overlap_edges
            if (
                str(edge.get("from")),
                str(edge.get("to")),
                str(edge.get("relation")),
            ) not in exact_section_keys
        ],
    ]
    for source in active:
        sid = str(source.get("source_id"))
        for row in [*rules, *interfaces, *tables, *field_dictionary, *ui_specs, *permissions, *state_machines]:
            if str(row.get("source_id") or "") == sid:
                node_id = row.get("rule_id") or row.get("interface_id") or row.get("table_id") or row.get("field_id") or row.get("ui_spec_id") or row.get("permission_id") or row.get("state_machine_id")
                if node_id:
                    relation_edges.append({"edge_id": f"edge:{_short_hash({'source': sid, 'node': node_id})}", "from": f"source:{sid}", "to": node_id, "relation": "source_to_asset", "confidence": 1.0, "evidence": {"source_version": source.get("version")}})
    relation_edges = _dedupe_by_id(relation_edges, "edge_id")
    oracles = _oracle_library(rules, industry_oracles, relation_edges)
    risks = _risk_domains(rules, tickets, industry)
    # Entity-relation graph and cross-document conflict detection (RAGFlow-inspired)
    # Rule relationalization: structurize text rules into causal chains
    rules = _structurize_rule_causal_chains(rules)
    # ── Phase 4: Candidate validation and promotion ──
    from ._candidate_validation import validate_and_promote_candidates, candidates_to_behavior_ir_entries
    _candidate_receipt = validate_and_promote_candidates(
        semantic_candidates,
        interfaces=interfaces,
        tables=tables,
        rules=rules,
        state_machines=state_machines,
    )
    # Feed validated + pending candidates into entity space
    _candidate_entities = candidates_to_behavior_ir_entries(
        _candidate_receipt.validated,
        _candidate_receipt.pending,
    )
    for _cand_ent in _candidate_entities:
        _cand_name = str(_cand_ent.get("object") or "")
        if _cand_name and _cand_name not in object_names:
            objects.append(_cand_ent)
            object_names.add(_cand_name)
    entity_relations = _extract_entity_relations(interfaces, tables, field_dictionary, rules, state_machines, permissions)
    cross_doc_conflicts = _detect_cross_document_conflicts(field_dictionary, rules, interfaces, permissions)
    # ── Phase 0: asset-level space health check ──
    # If entire entity/field/permission spaces are empty, emit asset-level gaps.
    if active and not tables and not objects:
        parse_coverage_gaps.append({
            "kind": "ASSET_SPACE_EMPTY",
            "gap_type": "entity_space_empty",
            "source_id": "*",
            "operator_action": "no data_tables or business_objects extracted from any source; provide machine-readable schema or field definitions",
        })
    if active and not field_dictionary:
        parse_coverage_gaps.append({
            "kind": "ASSET_SPACE_EMPTY",
            "gap_type": "field_space_empty",
            "source_id": "*",
            "operator_action": "no data_fields extracted from any source; provide field dictionary or schema with column definitions",
        })
    if active and not permissions:
        parse_coverage_gaps.append({
            "kind": "ASSET_SPACE_EMPTY",
            "gap_type": "permission_space_empty",
            "source_id": "*",
            "operator_action": "no permission_matrix extracted; provide role-permission definitions if applicable",
        })
    asset = {
        "phase": PHASE,
        "asset_id": f"knowledge_asset:{project}:{_short_hash({'sources': [(x.get('source_id'), x.get('content_hash'), x.get('version')) for x in active]})}",
        "project_id": project,
        "generated_at_utc": _now(),
        "source_inventory": active,
        "parser_receipts": parser_receipts,
        "coverage_gaps": parse_coverage_gaps,
        "module_tree": _module_tree(interfaces, rules, tables, objects),
        "business_objects": objects,
        "roles": roles,
        "state_machines": state_machines,
        "interfaces": interfaces,
        "data_fields": [{"table_id": table.get("table_id"), "table": table.get("name"), "fields": table.get("columns") or [], "source_id": table.get("source_id")} for table in tables],
        "field_dictionary": field_dictionary,
        "data_tables": tables,
        "ui_design_specs": ui_specs,
        "rule_library": rules,
        "permission_matrix": permissions,
        "data_dependencies": dependencies,
        "risk_domains": risks,
        "industry_business_understanding": {
            "summary": industry.get("summary") or {},
            "recognized_industries": industry.get("recognized_industries") or [],
            "risk_domains": industry.get("risk_domains") or [],
            "oracle_dsl_rule_count": len(dsl_rules),
            "oracle_dsl_activation": "evidence_gated" if dsl_rules else "suppressed_unknown_or_low_confidence",
        },
        "relationships": relation_edges,
        "entity_relations": entity_relations,
        "cross_document_conflicts": cross_doc_conflicts,
        "semantic_candidates": semantic_candidates,
        "semantic_extraction_availability": _sem_availability,
        "semantic_extraction_receipts": semantic_receipts,
        "candidate_validation_receipt": _candidate_receipt.to_dict(),
        "oracle_library": oracles,
        "governance": {
            "no_manual_customer_industry_pack_required": True,
            "source_version_traceable": True,
            "source_deduplication_by_content_hash": True,
            "remote_fetch_disabled_without_connector": True,
            "raw_sources_not_embedded_in_report_or_evidence_bundle": True,
            "safe_live_policy": "Unknown IDs, cross-user checks, writes, replays and state transitions are planned for sandbox or human-confirmed runtime evidence.",
        },
    }
    probes = _probes_from_asset(asset, int(options.get("probe_limit") or 140))
    for probe in probes:
        lineage = probe.get("knowledge_lineage") or {}
        risk_id = lineage.get("risk_id")
        if risk_id:
            relation_edges.append({"edge_id": f"edge:{_short_hash({'risk': risk_id, 'probe': probe.get('probe_id')})}", "from": risk_id, "to": f"probe:{probe.get('probe_id')}", "relation": "risk_to_probe", "confidence": 1.0, "evidence": {"execution_policy": probe.get("execution_policy")}})
    asset["relationships"] = _dedupe_by_id(relation_edges, "edge_id")
    asset["summary"] = {
        "active_source_count": len(active),
        "source_parse_succeeded": sum(1 for row in parser_receipts if str(row.get("parser_status") or "") == "parsed"),
        "source_parse_degraded": sum(1 for row in parser_receipts if str(row.get("parser_status") or "") == "degraded"),
        "source_parse_failed": sum(1 for row in parser_receipts if str(row.get("parser_status") or "") == "failed"),
        "source_type_distribution": dict(Counter(str(x.get("source_type") or "unknown") for x in active)),
        "module_count": len(asset["module_tree"]),
        "business_object_count": len(asset["business_objects"]),
        "role_count": len(asset["roles"]),
        "state_machine_count": len(asset["state_machines"]),
        "interface_count": len(asset["interfaces"]),
        "field_dictionary_count": len(asset["field_dictionary"]),
        "data_table_count": len(asset["data_tables"]),
        "ui_design_spec_count": len(asset["ui_design_specs"]),
        "rule_count": len(asset["rule_library"]),
        "permission_matrix_count": len(asset["permission_matrix"]),
        "data_dependency_count": len(asset["data_dependencies"]),
        "risk_domain_count": len(asset["risk_domains"]),
        "oracle_count": len(asset["oracle_library"]),
        "generated_probe_count": len(probes),
        "relationship_count": len(asset["relationships"]),
        "knowledge_ready": bool(active and (rules or interfaces or tables)),
        "claim_guard": {"absolute_understanding_allowed": False, "approved_product_language": "平台将企业资料归并为可追溯业务知识资产，并把规则、接口、数据依赖和高价值风险转化为可审计的 Bug 验证计划。", "prohibited_product_language": ["上传资料后自动完全理解所有业务", "不需要人工复核即可保证零缺陷", "覆盖全部业务 Bug"]},
    }
    bundle = _evidence_bundle(asset, probes)
    paths = _paths(project, root)
    paths["asset"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["asset"], asset)
    _write_json(paths["probe_catalog"], {"phase": PHASE, "asset_id": asset["asset_id"], "count": len(probes), "items": probes})
    _write_json(paths["evidence_bundle"], bundle)
    _write_json(paths["asset_copy"], asset)
    paths["report"].write_text(render_enterprise_business_knowledge_report(asset), encoding="utf-8")
    paths["center_page"].write_text(render_enterprise_business_knowledge_center(project, root, asset=asset), encoding="utf-8")
    registry["audit_events"].append({"event": "rebuild_asset", "at_utc": _now(), "actor": {"name": "system", "role": "knowledge_builder"}, "asset_id": asset["asset_id"], "source_count": len(active), "probe_count": len(probes)})
    _save_registry(project, root, registry)
    return asset


def load_enterprise_business_knowledge_asset(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_paths(project, root)["asset"], {})
    return data if isinstance(data, dict) and data else None


def generate_enterprise_business_knowledge_probes(openapi: dict[str, Any], cfg: dict[str, Any] | None = None, project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
    catalog = _load_json(_paths(project, root)["probe_catalog"], {})
    items = catalog.get("items") if isinstance(catalog, dict) else []
    probes = [dict(item) for item in items if isinstance(item, dict)]
    # If callers supply a fresher OpenAPI object than the asset, only retain probes
    # that still map to an available endpoint; generated contracts stay traceable.
    if isinstance(openapi, dict) and (openapi.get("paths") or {}):
        current = {(row["method"], row["path"]) for row in _openapi_operations(openapi)}
        probes = [p for p in probes if (str(p.get("method") or "GET"), str(p.get("path") or "/")) in current or p.get("path") == "/"]
    limit = int(max_count or (cfg or {}).get("max_probe_count") or 120)
    return probes[:limit]


def build_enterprise_knowledge_evidence_bundle(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_paths(project, root)["evidence_bundle"], {})
    if isinstance(data, dict) and data:
        return data
    asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
    probes = generate_enterprise_business_knowledge_probes({}, {}, project, root)
    return _evidence_bundle(asset, probes)


def render_enterprise_business_knowledge_report(asset: dict[str, Any]) -> str:
    """Render a shareable, read-only business-knowledge asset report."""
    summary = asset.get("summary") or {}
    sources = list(asset.get("source_inventory") or [])
    modules = list(asset.get("module_tree") or [])
    rules = list(asset.get("rule_library") or [])
    risks = list(asset.get("risk_domains") or [])
    edges = list(asset.get("relationships") or [])
    cards = "".join([
        metric_card("资料版本", summary.get("active_source_count", len(sources)), "已去重并保留来源版本", "default", "knowledge"),
        metric_card("业务模块", summary.get("module_count", len(modules)), "由资料、接口与对象自动归并", "default", "overview"),
        metric_card("业务规则", summary.get("rule_count", len(rules)), "每条规则可反向追溯来源", "success", "assets"),
        metric_card("Oracle", summary.get("oracle_count", 0), "服务于高价值业务 Bug 验证", "success", "risk"),
    ])
    source_rows = [[
        h(row.get("source_type") or "-"), h(row.get("original_name") or "-"), h(row.get("version") or "-"),
        status_badge((row.get("parse") or {}).get("parse_status") or "unknown"), f"<code>{h(str(row.get('content_hash') or '')[:12])}</code>",
    ] for row in sources[:100]]
    module_rows = [[
        h(row.get("name") or "-"), h(len(row.get("interfaces") or [])), h("、".join(row.get("objects") or []) or "-"),
        h(len(row.get("rules") or [])), h("、".join(row.get("tables") or []) or "-"),
    ] for row in modules[:80]]
    rule_rows = [[
        status_badge(row.get("severity") or "-"), h(row.get("rule_type") or "-"), h(row.get("statement") or "-"), f"<code>{h(row.get('source_id') or '-')}</code>",
    ] for row in rules[:120]]
    risk_rows = [[
        status_badge(row.get("severity") or "-"), h(row.get("risk_type") or "-"), h(row.get("title") or "-"), h(row.get("oracle_family") or "-"),
    ] for row in risks[:120]]
    edge_rows = [[h(row.get("relation") or "-"), f"<code>{h(row.get('from') or '-')}</code>", f"<code>{h(row.get('to') or '-')}</code>", h(row.get("confidence") or "-")] for row in edges[:120]]
    body = (
        f"<div class='metric-grid'>{cards}</div>"
        + section("资产边界", "资料被整理为可追溯测试资产，而不是把原始文档复制到报告中。", callout("资产 ID", str(asset.get("asset_id") or "尚未生成"), "info", "knowledge"), section_id="overview")
        + section("资料版本", "每份来源保留类型、版本、解析状态和内容指纹。", table(["类型", "资料", "版本", "解析", "内容指纹"], source_rows, "暂无资料。"), section_id="knowledge")
        + section("模块与对象", "模块树连接接口、业务对象、规则和数据表。", table(["模块", "接口", "对象", "规则", "数据表"], module_rows, "暂无模块信息。"), section_id="assets")
        + section("规则库", "规则会进入 Oracle 和 Probe 生成链路，并保留资料来源。", table(["等级", "类型", "规则", "来源"], rule_rows, "暂无规则。"), section_id="risk")
        + section("风险域与 Oracle", "只沉淀能服务于高价值业务缺陷验证的风险与 Oracle。", table(["等级", "风险", "业务影响", "Oracle"], risk_rows, "暂无风险域。"), section_id="release")
        + section("资料到验证的关系", "关系图谱支持从规则回溯接口、数据表和测试探针。", table(["关系", "起点", "终点", "置信度"], edge_rows, "暂无关联关系。"), section_id="runtime")
    )
    return product_shell(
        title="企业业务知识资产报告",
        project_id=str(asset.get("project_id") or "real_project_demo"),
        active="knowledge",
        eyebrow="Enterprise knowledge asset",
        headline="把企业资料转化为可追溯、可验证的业务质量资产。",
        description="规则、状态机、权限、接口与数据依赖会统一进入 Oracle、Probe、证据与发布决策链路。",
        body=body,
        payload=asset,
        environment_label="知识资产只读视图",
        page_hint="企业业务知识资产报告",
    )

def render_enterprise_business_knowledge_center(project_id: str, root: Path | None = None, asset: dict[str, Any] | None = None) -> str:
    root = root or ROOT
    project = _safe_project_id(project_id)
    inventory = list_enterprise_knowledge_sources(project, root, include_deleted=False)
    asset = asset or load_enterprise_business_knowledge_asset(project, root) or {}
    sources = list(inventory.get("sources") or [])
    summary = asset.get("summary") or {}
    source_rows = [[
        h(source.get("source_type") or "-"), h(source.get("original_name") or "-"), h(source.get("version") or "-"),
        status_badge(source.get("status") or "unknown"), h("、".join(source.get("tags") or []) or "-"),
        f"<code>{h(source.get('source_id') or '-')}</code>",
        f"<button class='btn-delete' onclick=\"deleteSource('{h(source.get('source_id') or '')}','{h(source.get('original_name') or '')}')\" title='删除'>×</button>"
        f"<button class='btn-preview' onclick=\"previewFile('{h(source.get('source_id') or '')}','{h(source.get('original_name') or '')}','{h(source.get('source_type') or '')}','/api/knowledge/preview?source_id={h(source.get('source_id') or '')}&project={project}')\" title='预览'>👁</button>",
    ] for source in sources]
    cards = "".join([
        metric_card("已接入资料", summary.get("active_source_count", len(sources)), "PRD、OpenAPI、表结构、权限、历史 Bug 等", "default", "knowledge"),
        metric_card("业务规则", summary.get("rule_count", 0), "自动转化为可审计验证规则", "success", "assets"),
        metric_card("风险域", summary.get("risk_domain_count", 0), "优先服务高价值业务 Bug 挖掘", "warning", "risk"),
        metric_card("生成 Probe", summary.get("generated_probe_count", 0), "通过来源、规则和 Oracle 反向可解释", "default", "runtime"),
    ])
    governance = (
        "<div class='two-col'>"
        "<div class='subtle-card'><h3>资料治理</h3>" + detail_list([
            ("内容去重", "内容哈希"),
            ("版本策略", "逻辑资料名版本化"),
            ("原始资料", "项目级受控存储"),
            ("报告内容", "仅展示脱敏摘要与关系"),
        ]) + "</div>"
        "<div class='subtle-card'><h3>测试资产边界</h3>" + detail_list([
            ("高风险写入", "隔离沙箱 / 人工确认"),
            ("跨账号验证", "安全策略约束"),
            ("生产类环境", "默认禁止破坏性执行"),
            ("无效资料", "不进入风险资产"),
        ]) + "</div></div>"
    )
    body = (
        f"<div class='metric-grid'>{cards}</div>"
        + section("资料接入与资产重建", "企业资料会经分类、去重、版本化和关联解析，形成可解释知识资产。",
            "<div class='upload-zone' id='upload-zone'>"
            "<div class='upload-inner'>"
            "<i>" + _icon("assets") + "</i>"
            "<strong>拖拽文件到此处，或点击上传</strong>"
            "<p>全格式兼容。Office/PDF/图片(PSD/AI/RAW等)/流程图(Visio/DrawIO/BPMN等)/思维导图(XMind/FreeMind)/CAD/代码/压缩包/数据库导出 — 任意企业文件拖入即解析</p>"
            "<input type='file' id='file-input' accept='*' multiple hidden>"
            "<button class='btn btn-primary' onclick=\"document.getElementById('file-input').click()\">选择文件</button>"
            "</div>"
            "<div class='upload-status' id='upload-status'></div>"
            "</div>"
            + callout("操作需要项目知识管理员权限。", "导入后的资料会进入版本化受控存储，原始资料不会直接进入风险报告。", "info", "security"),
            section_id="overview")
        + section("已接入资料", "可查看资料类型、版本、状态、标签与资产 ID。", table(["类型", "资料", "版本", "状态", "标签", "资料 ID", "", ""], source_rows, "尚未接入资料。"), section_id="knowledge")
        + section("资产治理与安全", "知识中心不替代业务测试；它只负责把资料变成能生成 Oracle 和高价值 Probe 的可追溯输入。", governance + "<div style='margin-top:16px'><button class='btn btn-secondary' onclick='reanalyze()'><i>" + _icon("refresh") + "</i> 重新分析所有资料</button></div>", section_id="release")
    )
    return product_shell(
        title="企业业务知识中心",
        project_id=project,
        active="knowledge",
        eyebrow="Enterprise knowledge center",
        headline="让企业资料自动沉淀为可追溯的业务质量知识资产。",
        description="通过统一分类、版本、关系和来源证据，把 PRD、接口、数据、权限与历史缺陷连接到高价值 Bug 验证。",
        body=body,
        payload={"asset": asset, "inventory": inventory},
        environment_label="资料受控接入模式",
        page_hint="企业业务知识中心",
    )

def run_enterprise_knowledge_demo() -> dict[str, Any]:
    """Create a self-contained multi-source demo without external services."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        project = "knowledge_demo"
        inputs = root / "fixtures"
        inputs.mkdir(parents=True, exist_ok=True)
        (inputs / "PRD_finance.md").write_text("""# 资金结算 PRD\n租户只能访问本租户账户和账本。交易状态 initiated -> pending -> settled 或 reversed。余额、账本和交易金额必须守恒；重复回调不得重复入账。""", encoding="utf-8")
        (inputs / "api.openapi.json").write_text(json.dumps({"openapi": "3.0.3", "info": {"title": "Tenant Ledger", "version": "1"}, "paths": {"/tenants/{tenant_id}/accounts/{account_id}": {"get": {"summary": "Get tenant account balance and ledger", "responses": {"200": {"description": "ok"}}}}, "/transactions": {"post": {"summary": "Create transfer transaction", "responses": {"201": {"description": "created"}}}, "get": {"summary": "List transaction ledger", "responses": {"200": {"description": "ok"}}}}}}, ensure_ascii=False), encoding="utf-8")
        (inputs / "schema.sql").write_text("""CREATE TABLE accounts (account_id varchar(64) primary key, tenant_id varchar(64), balance decimal(18,2));\nCREATE TABLE ledger_entries (entry_id varchar(64) primary key, account_id varchar(64), amount decimal(18,2), FOREIGN KEY(account_id) REFERENCES accounts(account_id));\nCREATE TABLE transactions (transaction_id varchar(64) primary key, account_id varchar(64), status varchar(32), amount decimal(18,2), FOREIGN KEY(account_id) REFERENCES accounts(account_id));""", encoding="utf-8")
        (inputs / "permission_matrix.csv").write_text("role,resource,actions,scope\ntenant_user,account,read,own_tenant\nrisk_officer,transaction,approve,assigned_tenant\nadmin,ledger,read,all_tenants\n", encoding="utf-8")
        (inputs / "historical_bugs.json").write_text(json.dumps({"bugs": [{"title": "重复回调导致账本重复入账", "severity": "P0", "status": "fixed"}, {"title": "跨租户读取账户余额", "severity": "P0", "status": "fixed"}]}, ensure_ascii=False), encoding="utf-8")
        ingest = ingest_enterprise_knowledge_files(project, list(inputs.iterdir()), root=root, actor={"name": "demo_owner", "role": "project_owner"})
        asset = build_enterprise_business_knowledge_asset(project, root)
        probes = generate_enterprise_business_knowledge_probes({}, {}, project, root)
        return {"phase": PHASE, "ingest": {"created": len(ingest.get("created") or []), "duplicates": len(ingest.get("duplicates") or [])}, "summary": asset.get("summary"), "probe_count": len(probes), "risk_types": sorted({str(p.get("risk_type")) for p in probes}), "passed": bool(asset.get("summary", {}).get("knowledge_ready") and probes and asset.get("interfaces") and asset.get("data_tables"))}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Enterprise knowledge unified ingestion")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--root", default="")
    parser.add_argument("--ingest", nargs="*", default=[])
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--render-center", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else ROOT
    if args.demo:
        print(json.dumps(run_enterprise_knowledge_demo(), ensure_ascii=False, indent=2))
        return 0
    result: dict[str, Any] = {}
    if args.ingest:
        result["ingest"] = ingest_enterprise_knowledge_files(args.project, args.ingest, root=root, actor={"name": "cli", "role": "knowledge_admin"})
    if args.rebuild or args.ingest:
        result["asset"] = build_enterprise_business_knowledge_asset(args.project, root).get("summary")
    if args.render_center:
        asset = load_enterprise_business_knowledge_asset(args.project, root) or build_enterprise_business_knowledge_asset(args.project, root)
        path = _paths(args.project, root)["center_page"]
        path.write_text(render_enterprise_business_knowledge_center(args.project, root, asset), encoding="utf-8")
        result["center_page"] = str(path)
    if not result:
        result = list_enterprise_knowledge_sources(args.project, root, include_deleted=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
