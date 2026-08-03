"""Relationship linking, contract fields, module tree, oracle, probes."""
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
from pathlib import Path
from typing import Any, Iterable
from ._parsing import _risk_type_from_text  # noqa: F401
from ._utils import _dedupe_by_id, _hash_bytes, _load_registry, _now, _redact_text, _short_hash, _tokens  # noqa: F401

logger = logging.getLogger(__name__)

try:
    import docx2txt
except ImportError:
    docx2txt = None

from ._common import *  # noqa: F401,F403
from ._utils import *  # noqa: F401,F403
from ._parsing import *  # noqa: F401,F403
from ._crud import *  # noqa: F401,F403

__all__ = [
    "_authoritative_rule_to_interface_edges", "_cleanup_documents_primary_action",
    "_contract_fields_for_interface", "_declared_project_source_files",
    "_dedupe_by_id", "_evidence_bundle", "_has_documented_sibling_compensation",
    "_interface_parent_path", "_interface_path_terminal", "_interface_summary_blob",
    "_interface_text_blob", "_is_cleanup_action_interface", "_is_plausible_contract_field",
    "_links_by_exact_source_section", "_links_by_exclusive_contract_fields",
    "_links_by_overlap", "_links_by_same_source_exclusive_module_neighbors",
    "_looks_inverse_delta_capable", "_merge_openapi", "_module_field_universe",
    "_module_tree", "_normalize_contract_field", "_oracle_dsl_pack_from_recognized_industries",
    "_oracle_family", "_oracle_library", "_path_module_prefix",
    "_prefer_reversible_write_targets", "_probes_from_asset",
    "_relationship_is_authoritative", "_reversible_module_write_targets",
    "_risk_domains", "_rule_mentioned_contract_fields",
    "_sync_declared_project_sources",
    "TOKEN_OVERLAP_RELATION_GATE",
]


def _merge_openapi(parts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {"openapi": "3.0.3", "info": {"title": "Enterprise Knowledge Unified API", "version": "derived"}, "paths": {}, "components": {"schemas": {}}}
    for item in parts:
        if not isinstance(item, dict):
            continue
        for path, methods in (item.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            target = merged["paths"].setdefault(str(path), {})
            for method, spec in methods.items():
                if str(method).lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                target[str(method).lower()] = spec
        schemas = ((item.get("components") or {}).get("schemas") if isinstance(item.get("components"), dict) else {}) or {}
        if isinstance(schemas, dict):
            merged["components"]["schemas"].update(schemas)
    return merged


TOKEN_OVERLAP_RELATION_GATE = "token_overlap_only_requires_explicit_source_relation"
_NON_AUTHORITATIVE_RELATION_STATUSES = {"candidate", "proposed", "unknown", "unsupported", "rejected"}


def _relationship_is_authoritative(edge: dict[str, Any]) -> bool:
    """Return True only when a relationship is backed by explicit source evidence.

    Token overlap is useful for diagnostics and operator review, but it is not
    a semantic join that may drive executable probes or Behavior IR obligations.
    """

    if not isinstance(edge, dict):
        return False
    status = str(edge.get("status") or "accepted").strip().lower()
    if status in _NON_AUTHORITATIVE_RELATION_STATUSES:
        return False
    evidence_gate = str(edge.get("evidence_gate") or "").strip()
    derivation = str(edge.get("derivation") or "").strip().lower().replace("-", "_")
    evidence = edge.get("evidence") if isinstance(edge.get("evidence"), dict) else {}
    if evidence_gate == TOKEN_OVERLAP_RELATION_GATE:
        return False
    if derivation == "token_overlap":
        return False
    if evidence and set(evidence) <= {"token_overlap"}:
        return False
    return True


def _links_by_overlap(left: Iterable[dict[str, Any]], right: Iterable[dict[str, Any]], left_id: str, right_id: str, min_overlap: int = 1, relation: str = "related_to") -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for a in left:
        at = set(a.get("tokens") or _tokens(a.get("statement") or a.get("title") or a.get("name") or ""))
        if not at:
            continue
        best: list[tuple[int, dict[str, Any]]] = []
        for b in right:
            bt = set(b.get("tokens") or _tokens(f"{b.get('path') or ''} {b.get('summary') or ''} {b.get('name') or ''} {' '.join(b.get('columns') or [])}"))
            overlap = len(at & bt)
            if overlap >= min_overlap:
                best.append((overlap, b))
        for overlap, b in sorted(best, key=lambda x: (-x[0], str(x[1].get(right_id))))[:3]:
            edges.append({
                "edge_id": f"edge:{_short_hash({'a': a.get(left_id), 'b': b.get(right_id), 'relation': relation})}",
                "from": a.get(left_id),
                "to": b.get(right_id),
                "relation": relation,
                "confidence": round(min(0.95, 0.45 + overlap * 0.13), 3),
                "status": "candidate",
                "derivation": "token_overlap",
                "evidence_gate": TOKEN_OVERLAP_RELATION_GATE,
                "evidence": {"token_overlap": sorted(at & set(b.get("tokens") or _tokens(str(b))))[:10]},
            })
    return _dedupe_by_id(edges, "edge_id")


_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def _statement_eligible_for_exact_source_section(statement: str) -> bool:
    """ASCII prose keeps the historical 8-char floor; short CJK rules stay eligible.

    Runtime evidence on held-in materials showed exact excerpt hits for statements
    such as ``后台创建商品`` / ``取消订单`` that were dropped solely by ``len < 8``.
    Require at least two CJK characters so single-glyph noise cannot bind.
    """

    text = str(statement or "").strip()
    if not text:
        return False
    cjk_count = len(_CJK_CHAR_RE.findall(text))
    if cjk_count >= 2:
        return True
    return len(text) >= 8


def _links_by_exact_source_section(
    rules: Iterable[dict[str, Any]],
    interfaces: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bind a rule when its exact statement appears in an interface source excerpt.

    Same-document and cross-document matches are both accepted: the evidence is
    the verbatim statement inside the interface contract section, not source_id
    equality. Token-overlap candidates remain non-authoritative elsewhere.
    """

    edges: list[dict[str, Any]] = []
    interface_rows = [row for row in interfaces if isinstance(row, dict)]
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        statement = str(rule.get("statement") or "").strip()
        rule_id = str(rule.get("rule_id") or "").strip()
        if not statement or not rule_id or not _statement_eligible_for_exact_source_section(statement):
            continue
        for interface in interface_rows:
            excerpt = str(interface.get("source_excerpt") or "")
            if statement not in excerpt:
                continue
            interface_id = str(interface.get("interface_id") or "").strip()
            if not interface_id:
                continue
            operation_locator = (
                f"{str(interface.get('method') or '').upper()} "
                f"{str(interface.get('path') or '')}"
            ).strip()
            edge_identity = {
                "rule": rule_id,
                "interface": interface_id,
                "derivation": "exact_source_section",
            }
            edges.append({
                "edge_id": "edge:" + _short_hash(edge_identity),
                "from": rule_id,
                "to": interface_id,
                "relation": "rule_to_interface",
                "confidence": 1.0,
                "status": "accepted",
                "derivation": "exact_source_section",
                "evidence_gate": "exact_source_section",
                "evidence": {
                    "rule_source_id": str(rule.get("source_id") or "").strip(),
                    "interface_source_id": str(interface.get("source_id") or "").strip(),
                    "operation_locator": operation_locator,
                    "statement_hash": _short_hash(statement),
                },
            })
    return _dedupe_by_id(edges, "edge_id")


_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CONTRACT_FIELD_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{1,64})`")
_JSON_KEY_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]{1,64})"\s*:')
_SNAKE_FIELD_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
_CAMEL_FIELD_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*)\b")
_CLEANUP_ACTION_RE = re.compile(
    r"(?:cancel|close|void|disable|archive|reject|release|rollback|revoke|"
    r"remove|delete|deactivate|suspend|expire|invalidate|terminate|withdraw|"
    r"abandon|discard|retire|freeze|reset|clear|purge)$",
    re.I,
)
_EXCLUDED_CONTRACT_FIELD_TOKENS = frozenset({
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "http",
    "https",
    "api",
    "json",
    "bearer",
    "authorization",
    "token",
    "true",
    "false",
    "null",
})


def _is_plausible_contract_field(field: str) -> bool:
    text = str(field or "").strip()
    if len(text) < 2:
        return False
    if text.lower() in _EXCLUDED_CONTRACT_FIELD_TOKENS:
        return False
    if text.isupper() and "_" not in text and len(text) <= 8:
        return False
    return True


def _path_module_prefix(path: str) -> str:
    parts = [part for part in str(path or "").split("/") if part]
    if len(parts) >= 2 and parts[0].lower() == "api":
        return "/" + "/".join(parts[:2])
    if parts:
        return "/" + parts[0]
    return str(path or "").strip() or "/"


def _normalize_contract_field(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split(".", 1)[0].replace("[]", "").strip()


def _contract_fields_for_interface(interface: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for item in interface.get("field_dictionary") or []:
        if isinstance(item, str):
            normalized = _normalize_contract_field(item)
        elif isinstance(item, dict):
            normalized = _normalize_contract_field(
                item.get("field") or item.get("name") or item.get("field_path")
            )
        else:
            normalized = ""
        if normalized:
            fields.add(normalized)
    for item in interface.get("parameters") or []:
        if isinstance(item, str):
            normalized = _normalize_contract_field(item)
        elif isinstance(item, dict):
            normalized = _normalize_contract_field(item.get("name") or item.get("field"))
        else:
            normalized = ""
        if normalized:
            fields.add(normalized)
    for item in interface.get("request_body_fields") or []:
        if isinstance(item, str):
            normalized = _normalize_contract_field(item)
        elif isinstance(item, dict):
            normalized = _normalize_contract_field(
                item.get("name") or item.get("field") or item.get("field_path")
            )
        else:
            normalized = ""
        if normalized:
            fields.add(normalized)
            if "." in normalized:
                leaf = _normalize_contract_field(normalized.rsplit(".", 1)[-1])
                if leaf:
                    fields.add(leaf)
    for item in interface.get("parameter_contracts") or []:
        if isinstance(item, str):
            normalized = _normalize_contract_field(item)
        elif isinstance(item, dict):
            normalized = _normalize_contract_field(item.get("name") or item.get("field"))
        else:
            normalized = ""
        if normalized:
            fields.add(normalized)
    excerpt = str(interface.get("source_excerpt") or "")
    for match in _CONTRACT_FIELD_RE.finditer(excerpt):
        fields.add(match.group(1))
    for match in _JSON_KEY_RE.finditer(excerpt):
        fields.add(match.group(1))
    for match in _SNAKE_FIELD_RE.finditer(excerpt):
        fields.add(match.group(1))
    for match in _CAMEL_FIELD_RE.finditer(excerpt):
        fields.add(match.group(1))
    summary = str(interface.get("summary") or "")
    for match in _SNAKE_FIELD_RE.finditer(summary):
        fields.add(match.group(1))
    return {field for field in fields if _is_plausible_contract_field(field)}


def _rule_mentioned_contract_fields(statement: str, universe: set[str]) -> set[str]:
    if not statement or not universe:
        return set()
    lower_universe = {field.lower(): field for field in universe}
    mentioned: set[str] = set()
    for match in _CONTRACT_FIELD_RE.finditer(statement):
        canonical = lower_universe.get(match.group(1).lower())
        if canonical:
            mentioned.add(canonical)
    for match in _SNAKE_FIELD_RE.finditer(statement):
        canonical = lower_universe.get(match.group(1).lower())
        if canonical:
            mentioned.add(canonical)
    for match in _CAMEL_FIELD_RE.finditer(statement):
        canonical = lower_universe.get(match.group(1).lower())
        if canonical:
            mentioned.add(canonical)
    for field in universe:
        if len(field) < 3:
            continue
        if re.search(rf"\b{re.escape(field)}\b", statement, flags=re.IGNORECASE):
            mentioned.add(field)
    return mentioned


def _interface_path_terminal(path: str) -> str:
    return str(path or "").rstrip("/").rsplit("/", 1)[-1]


def _is_cleanup_action_interface(interface: dict[str, Any]) -> bool:
    method = str(interface.get("method") or "").upper()
    if method == "DELETE":
        return True
    return bool(_CLEANUP_ACTION_RE.search(_interface_path_terminal(str(interface.get("path") or ""))))


def _interface_parent_path(path: str) -> str:
    normalized = str(path or "").rstrip("/")
    if "/" not in normalized:
        return normalized
    return normalized.rsplit("/", 1)[0]


def _looks_inverse_delta_capable(interface: dict[str, Any]) -> bool:
    fields = {field.lower() for field in _contract_fields_for_interface(interface)}
    return "delta" in fields


def _interface_text_blob(interface: dict[str, Any]) -> str:
    return re.sub(
        r"[\W_]+",
        "",
        " ".join([
            str(interface.get("summary") or ""),
            str(interface.get("description") or ""),
            str(interface.get("source_excerpt") or "")[:240],
        ]).lower(),
    )


def _interface_summary_blob(interface: dict[str, Any]) -> str:
    return re.sub(
        r"[\W_]+",
        "",
        " ".join([
            str(interface.get("summary") or ""),
            str(interface.get("description") or ""),
        ]).lower(),
    )


def _cleanup_documents_primary_action(*, source: dict[str, Any], candidate: dict[str, Any]) -> bool:
    terminal = _interface_path_terminal(str(source.get("path") or "")).lower()
    source_text = _interface_text_blob(source)
    source_summary = _interface_summary_blob(source)
    candidate_text = _interface_text_blob(candidate)
    candidate_summary = _interface_summary_blob(candidate)
    if not candidate_text and not candidate_summary:
        return False
    if len(terminal) >= 4 and terminal in candidate_text:
        return True
    if len(source_text) >= 4 and source_text in candidate_text:
        return True
    if source_summary and candidate_summary:
        if len(source_summary) >= 2 and source_summary in candidate_summary:
            return True
        if len(candidate_summary) >= 2 and candidate_summary in source_summary:
            return True
        for run in re.findall(r"[\u4e00-\u9fff]{3,}|[a-z]{4,}", source_summary):
            if run in candidate_summary:
                return True
    return False


def _has_documented_sibling_compensation(interface: dict[str, Any], interfaces: list[dict[str, Any]]) -> bool:
    parent = _interface_parent_path(str(interface.get("path") or ""))
    interface_id = str(interface.get("interface_id") or "").strip()
    source_fields = _contract_fields_for_interface(interface)
    if not parent:
        return False
    candidates: list[dict[str, Any]] = []
    for candidate in interfaces:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("interface_id") or "").strip() == interface_id:
            continue
        if str(candidate.get("method") or "").upper() not in _WRITE_METHODS:
            continue
        if _interface_parent_path(str(candidate.get("path") or "")) != parent:
            continue
        if not _is_cleanup_action_interface(candidate):
            continue
        candidate_fields = _contract_fields_for_interface(candidate)
        text_ok = _cleanup_documents_primary_action(source=interface, candidate=candidate)
        fields_ok = bool(source_fields) and source_fields == candidate_fields
        if not (text_ok or fields_ok):
            continue
        if not text_ok and fields_ok:
            peer_primaries = [
                peer
                for peer in interfaces
                if isinstance(peer, dict)
                and str(peer.get("interface_id") or "").strip()
                not in {interface_id, str(candidate.get("interface_id") or "").strip()}
                and str(peer.get("method") or "").upper() in _WRITE_METHODS
                and _interface_parent_path(str(peer.get("path") or "")) == parent
                and not _is_cleanup_action_interface(peer)
                and _contract_fields_for_interface(peer) == source_fields
            ]
            if peer_primaries:
                continue
        candidates.append(candidate)
    return len(candidates) == 1


def _prefer_reversible_write_targets(write_targets: list[dict[str, Any]], *, interface_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    non_cleanup = [interface for interface in write_targets if not _is_cleanup_action_interface(interface)]
    reversible = [
        interface
        for interface in non_cleanup
        if _has_documented_sibling_compensation(interface, interface_rows)
        or _looks_inverse_delta_capable(interface)
    ]
    return reversible or non_cleanup or list(write_targets)


_MODULE_NEIGHBOR_RISK_TYPES = frozenset({"concurrency", "idempotency", "data_conservation"})


def _module_field_universe(interfaces: Iterable[dict[str, Any]], *, module_prefix: str) -> set[str]:
    fields: set[str] = set()
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        if _path_module_prefix(str(interface.get("path") or "")) != module_prefix:
            continue
        fields |= _contract_fields_for_interface(interface)
    return fields


def _reversible_module_write_targets(
    *,
    interface_rows: list[dict[str, Any]],
    module_prefix: str,
    mentions: set[str],
    covering_ids: set[str] | None = None,
    iface_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    module_interfaces = [interface for interface in interface_rows if _path_module_prefix(str(interface.get("path") or "")) == module_prefix]
    write_targets = [interface for interface in module_interfaces if str(interface.get("method") or "").upper() in _WRITE_METHODS]
    field_bearing_writes = [interface for interface in write_targets if mentions & _contract_fields_for_interface(interface)]
    if field_bearing_writes:
        return _prefer_reversible_write_targets(field_bearing_writes, interface_rows=interface_rows)
    if write_targets:
        return _prefer_reversible_write_targets(write_targets, interface_rows=interface_rows)
    if covering_ids and iface_by_id is not None:
        return [iface_by_id[interface_id] for interface_id in covering_ids if interface_id in iface_by_id]
    return []


def _links_by_exclusive_contract_fields(rules: Iterable[dict[str, Any]], interfaces: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    interface_rows = [row for row in interfaces if isinstance(row, dict)]
    iface_by_id: dict[str, dict[str, Any]] = {}
    field_to_interfaces: dict[str, set[str]] = defaultdict(set)
    for interface in interface_rows:
        interface_id = str(interface.get("interface_id") or "").strip()
        if not interface_id:
            continue
        iface_by_id[interface_id] = interface
        for field in _contract_fields_for_interface(interface):
            field_to_interfaces[field].add(interface_id)
    universe = set(field_to_interfaces)
    if not universe:
        return []
    edges: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("rule_id") or "").strip()
        statement = str(rule.get("statement") or "").strip()
        if not rule_id or not statement:
            continue
        mentions = _rule_mentioned_contract_fields(statement, universe)
        if not mentions:
            continue
        covering_ids: set[str] = set()
        for field in mentions:
            covering_ids |= field_to_interfaces.get(field, set())
        if not covering_ids:
            continue
        prefixes = {_path_module_prefix(str(iface_by_id[interface_id].get("path") or "")) for interface_id in covering_ids if interface_id in iface_by_id}
        if len(prefixes) != 1:
            continue
        module_prefix = next(iter(prefixes))
        targets = _reversible_module_write_targets(
            interface_rows=interface_rows,
            module_prefix=module_prefix,
            mentions=mentions,
            covering_ids=covering_ids,
            iface_by_id=iface_by_id,
        )
        for interface in targets:
            interface_id = str(interface.get("interface_id") or "").strip()
            if not interface_id:
                continue
            operation_locator = (
                f"{str(interface.get('method') or '').upper()} "
                f"{str(interface.get('path') or '')}"
            ).strip()
            edge_identity = {
                "rule": rule_id,
                "interface": interface_id,
                "derivation": "exclusive_contract_field_module",
                "fields": sorted(mentions),
            }
            edges.append({
                "edge_id": "edge:" + _short_hash(edge_identity),
                "from": rule_id,
                "to": interface_id,
                "relation": "rule_to_interface",
                "confidence": 0.92,
                "status": "accepted",
                "derivation": "exclusive_contract_field_module",
                "evidence_gate": "exclusive_contract_field_module",
                "evidence": {
                    "contract_fields": sorted(mentions),
                    "module_prefix": module_prefix,
                    "operation_locator": operation_locator,
                    "statement_hash": _short_hash(statement),
                },
            })
    return _dedupe_by_id(edges, "edge_id")


def _links_by_same_source_exclusive_module_neighbors(
    rules: Iterable[dict[str, Any]],
    interfaces: Iterable[dict[str, Any]],
    *,
    seed_edges: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    interface_rows = [row for row in interfaces if isinstance(row, dict)]
    rule_rows = [row for row in rules if isinstance(row, dict)]
    rules_by_id = {
        str(row.get("rule_id") or "").strip(): row
        for row in rule_rows
        if str(row.get("rule_id") or "").strip()
    }
    universe: set[str] = set()
    for interface in interface_rows:
        universe |= _contract_fields_for_interface(interface)
    if not universe:
        return []
    seeded_rule_ids = {
        str(edge.get("from") or "").strip()
        for edge in seed_edges
        if isinstance(edge, dict)
        and str(edge.get("derivation") or "") == "exclusive_contract_field_module"
        and str(edge.get("status") or "") == "accepted"
    }
    module_by_source: dict[str, set[str]] = defaultdict(set)
    seed_rule_by_source: dict[str, set[str]] = defaultdict(set)
    for edge in seed_edges:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("derivation") or "") != "exclusive_contract_field_module":
            continue
        if str(edge.get("status") or "") != "accepted":
            continue
        rule_id = str(edge.get("from") or "").strip()
        evidence = edge.get("evidence") if isinstance(edge.get("evidence"), dict) else {}
        module_prefix = str(evidence.get("module_prefix") or "").strip()
        rule = rules_by_id.get(rule_id) or {}
        source_id = str(rule.get("source_id") or "").strip()
        if not rule_id or not module_prefix or not source_id:
            continue
        module_by_source[source_id].add(module_prefix)
        seed_rule_by_source[source_id].add(rule_id)
    edges: list[dict[str, Any]] = []
    for rule in rule_rows:
        rule_id = str(rule.get("rule_id") or "").strip()
        statement = str(rule.get("statement") or "").strip()
        source_id = str(rule.get("source_id") or "").strip()
        risk_type = str(rule.get("risk_type") or "").strip().lower()
        if not rule_id or not statement or not source_id:
            continue
        if rule_id in seeded_rule_ids:
            continue
        if risk_type not in _MODULE_NEIGHBOR_RISK_TYPES:
            continue
        modules = module_by_source.get(source_id) or set()
        if len(modules) != 1:
            continue
        module_prefix = next(iter(modules))
        mentions = _rule_mentioned_contract_fields(statement, universe)
        if not mentions:
            continue
        module_fields = _module_field_universe(interface_rows, module_prefix=module_prefix)
        if not mentions.issubset(module_fields):
            continue
        targets = _reversible_module_write_targets(
            interface_rows=interface_rows,
            module_prefix=module_prefix,
            mentions=mentions,
        )
        seed_rule_ids = sorted(seed_rule_by_source.get(source_id) or [])
        for interface in targets:
            interface_id = str(interface.get("interface_id") or "").strip()
            if not interface_id:
                continue
            operation_locator = (
                f"{str(interface.get('method') or '').upper()} "
                f"{str(interface.get('path') or '')}"
            ).strip()
            edge_identity = {
                "rule": rule_id,
                "interface": interface_id,
                "derivation": "same_source_exclusive_module_neighbor",
                "module": module_prefix,
                "fields": sorted(mentions),
            }
            edges.append({
                "edge_id": "edge:" + _short_hash(edge_identity),
                "from": rule_id,
                "to": interface_id,
                "relation": "rule_to_interface",
                "confidence": 0.88,
                "status": "accepted",
                "derivation": "same_source_exclusive_module_neighbor",
                "evidence_gate": "same_source_exclusive_module_neighbor",
                "evidence": {
                    "contract_fields": sorted(mentions),
                    "module_prefix": module_prefix,
                    "seed_rule_ids": seed_rule_ids,
                    "operation_locator": operation_locator,
                    "statement_hash": _short_hash(statement),
                },
            })
    return _dedupe_by_id(edges, "edge_id")


def _authoritative_rule_to_interface_edges(rules: Iterable[dict[str, Any]], interfaces: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    exclusive_edges = _links_by_exclusive_contract_fields(rules, interfaces)
    neighbor_edges = _links_by_same_source_exclusive_module_neighbors(rules, interfaces, seed_edges=exclusive_edges)
    return _dedupe_by_id([
        *_links_by_exact_source_section(rules, interfaces),
        *exclusive_edges,
        *neighbor_edges,
    ], "edge_id")


def _module_tree(interfaces: list[dict[str, Any]], rules: list[dict[str, Any]], tables: list[dict[str, Any]], objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules: dict[str, dict[str, Any]] = {}
    for interface in interfaces:
        path = str(interface.get("path") or "/").strip("/")
        module = path.split("/")[0] or "root"
        row = modules.setdefault(module, {"module_id": f"module:{module}", "name": module, "interfaces": [], "objects": set(), "rules": set(), "tables": set()})
        row["interfaces"].append(interface.get("interface_id"))
        for token in interface.get("tokens") or []:
            if token in {str(x.get("object") or "") for x in objects}:
                row["objects"].add(token)
    object_names = {str(x.get("object") or x.get("name") or "") for x in objects}
    for rule in rules:
        targets = _tokens(rule.get("statement") or "")
        candidates = [name for name in object_names if name and (name in targets or name in str(rule.get("statement") or "").lower())]
        module = candidates[0] if candidates else "business_rules"
        row = modules.setdefault(module, {"module_id": f"module:{module}", "name": module, "interfaces": [], "objects": set(), "rules": set(), "tables": set()})
        row["rules"].add(rule.get("rule_id"))
    for table in tables:
        name = str(table.get("name") or "table")
        module = name.split("_")[0] or "data"
        row = modules.setdefault(module, {"module_id": f"module:{module}", "name": module, "interfaces": [], "objects": set(), "rules": set(), "tables": set()})
        row["tables"].add(table.get("table_id"))
    result = []
    for row in modules.values():
        result.append({"module_id": row["module_id"], "name": row["name"], "interfaces": sorted(x for x in row["interfaces"] if x), "objects": sorted(x for x in row["objects"] if x), "rules": sorted(x for x in row["rules"] if x), "tables": sorted(x for x in row["tables"] if x)})
    return sorted(result, key=lambda x: (-len(x["interfaces"]) - len(x["rules"]), x["name"]))


def _risk_domains(rules: list[dict[str, Any]], tickets: list[dict[str, Any]], industry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rule in rules:
        risk_type = str(rule.get("risk_type") or _risk_type_from_text(rule.get("statement") or ""))
        rows.append({"risk_id": f"risk:{rule.get('rule_id')}", "source_rule_id": rule.get("rule_id"), "source_id": rule.get("source_id"), "risk_type": risk_type, "severity": rule.get("severity") or "P2", "title": f"企业知识规则风险：{rule.get('statement')}", "expected": rule.get("statement"), "oracle_family": _oracle_family(risk_type), "evidence": [rule.get("source_id")]})
    rows.extend(tickets)
    for risk in industry.get("risk_domains") or []:
        if not isinstance(risk, dict):
            continue
        rows.append({"risk_id": f"industry:{risk.get('risk_id') or _short_hash(risk)}", "source_rule_id": risk.get("rule_id"), "source_id": "industry_inference", "risk_type": risk.get("risk_type") or "industry_business_rule", "severity": risk.get("severity") or "P1", "title": risk.get("title"), "expected": risk.get("expected"), "oracle_family": risk.get("oracle_family") or "industry_oracle", "evidence": ["industry_inference"]})
    return _dedupe_by_id(rows, "risk_id")[:240]


def _oracle_family(risk_type: str) -> str:
    return {
        "permission_boundary": "authorization_boundary_oracle",
        "state_machine": "state_transition_oracle",
        "data_conservation": "conservation_oracle",
        "data_reconciliation": "reconciliation_oracle",
        "idempotency": "idempotency_oracle",
        "sensitive_data": "sensitive_data_scope_oracle",
        "historical_regression": "historical_regression_oracle",
    }.get(str(risk_type), "business_rule_oracle")


def _oracle_dsl_pack_from_recognized_industries(recognized_industries: list[dict[str, Any]] | list[str] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        from .oracle_dsl import DSLCompiler, RuleLibrary, normalize_industry_key
    except ImportError:
        return [], []
    industries: list[str] = []
    confidences: dict[str, float] = {}
    for row in recognized_industries or []:
        if isinstance(row, dict):
            key = str(row.get("industry") or "").strip().lower()
            if not key:
                continue
            industries.append(key)
            confidences[key] = float(row.get("confidence") or 0.0)
        else:
            key = str(row or "").strip().lower()
            if key:
                industries.append(key)
                confidences.setdefault(key, 1.0)
    if not industries:
        return [], []
    lib = RuleLibrary()
    compiler = DSLCompiler()
    rules: list[dict[str, Any]] = []
    oracles: list[dict[str, Any]] = []
    seen_markers: set[str] = set()
    for raw_key in industries:
        catalog_key = normalize_industry_key(raw_key)
        if not catalog_key:
            continue
        confidence = float(confidences.get(raw_key, confidences.get(catalog_key, 0.0)) or 0.0)
        if confidence < 0.58:
            continue
        for rule in lib.get_rules(catalog_key):
            marker = str(getattr(rule, "raw_text", None) or id(rule))
            if marker in seen_markers:
                continue
            seen_markers.add(marker)
            compiled = compiler.compile_to_oracle_object(rule)
            rule_id = f"oracle_dsl:{catalog_key}:{_short_hash(marker)}"
            statement = _redact_text(marker or compiled.expected_behavior or "", 720)
            risk_type = str(getattr(rule, "rule_type", None) or "business_rule")
            rules.append({
                "rule_id": rule_id,
                "source_id": "oracle_dsl_library",
                "source_type": "derived_inference",
                "statement": statement,
                "tokens": sorted(_tokens(statement)),
                "risk_type": risk_type,
                "kind": risk_type,
                "severity": getattr(rule, "severity", None) or compiled.severity or "P1",
                "expected": compiled.expected_behavior,
                "oracle_family": compiled.oracle_family,
                "industry": catalog_key,
                "evidence_gate": "recognized_industry_min_confidence",
            })
            oracles.append({
                "oracle_id": f"DSL_{_short_hash(rule_id)}",
                "rule_id": rule_id,
                "oracle_family": compiled.oracle_family,
                "expected": compiled.expected_behavior,
                "assertion": compiled.expected_behavior,
                "oracle_rules": compiled.oracle_rules,
                "industry": catalog_key,
                "source": "oracle_dsl_library",
            })
    return rules, oracles


def _oracle_library(rules: list[dict[str, Any]], industry_oracles: list[dict[str, Any]], relation_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    related_interfaces: dict[str, list[str]] = defaultdict(list)
    related_tables: dict[str, list[str]] = defaultdict(list)
    for edge in relation_edges:
        if not _relationship_is_authoritative(edge):
            continue
        if edge.get("relation") == "rule_to_interface":
            related_interfaces[str(edge.get("from"))].append(str(edge.get("to")))
        elif edge.get("relation") == "rule_to_table":
            related_tables[str(edge.get("from"))].append(str(edge.get("to")))
    result: list[dict[str, Any]] = []
    for rule in rules:
        risk_type = str(rule.get("risk_type") or "business_rule")
        rid = str(rule.get("rule_id"))
        result.append({"oracle_id": f"oracle:{rid}", "rule_id": rid, "family": _oracle_family(risk_type), "assertion": rule.get("statement"), "linked_interfaces": sorted(set(related_interfaces.get(rid, []))), "linked_tables": sorted(set(related_tables.get(rid, []))), "execution_policy": "read_only_evidence_or_sandbox", "evidence_requirements": ["source_document_version", "interface_contract", "response_or_data_snapshot"]})
    for row in industry_oracles:
        if isinstance(row, dict):
            result.append({"oracle_id": f"industry_oracle:{row.get('oracle_id') or _short_hash(row)}", "rule_id": row.get("rule_id"), "family": row.get("oracle_family") or "industry_oracle", "assertion": row.get("expected") or row.get("assertion"), "linked_interfaces": [], "linked_tables": [], "execution_policy": "read_only_evidence_or_sandbox", "evidence_requirements": ["industry_evidence", "interface_contract", "response_or_data_snapshot"]})
    return _dedupe_by_id(result, "oracle_id")[:260]


def _probes_from_asset(asset: dict[str, Any], max_count: int = 140) -> list[dict[str, Any]]:
    interfaces = {str(row.get("interface_id")): row for row in asset.get("interfaces") or [] if isinstance(row, dict)}
    interface_edges: dict[str, list[str]] = defaultdict(list)
    for edge in asset.get("relationships") or []:
        if isinstance(edge, dict) and edge.get("relation") == "rule_to_interface" and _relationship_is_authoritative(edge):
            interface_edges[str(edge.get("from"))].append(str(edge.get("to")))
    probes: list[dict[str, Any]] = []
    for risk in asset.get("risk_domains") or []:
        if not isinstance(risk, dict):
            continue
        rule_id = str(risk.get("source_rule_id") or "")
        candidate_ids = interface_edges.get(rule_id) or list(interfaces)[:1]
        for interface_id in candidate_ids[:2]:
            operation = interfaces.get(interface_id)
            if not operation:
                continue
            method = str(operation.get("method") or "GET").upper()
            risk_type = str(risk.get("risk_type") or "business_rule")
            destructive = method in WRITE_METHODS or risk_type in {"data_conservation", "state_machine", "idempotency", "data_reconciliation"}
            execution_policy = "sandbox_required" if destructive else "candidate_only"
            probe_id = f"RP_KNOWLEDGE_{len(probes)+1:04d}"
            probes.append({
                "probe_id": probe_id,
                "source": "enterprise_business_knowledge_asset",
                "knowledge_asset_id": asset.get("asset_id"),
                "risk_type": f"enterprise_knowledge_{risk_type}",
                "knowledge_risk_type": risk_type,
                "severity": risk.get("severity") or "P1",
                "title": risk.get("title") or "企业知识规则验证",
                "method": method,
                "path": operation.get("path") or "/",
                "operation_id": operation.get("operation_id") or "",
                "actor": "secondary_identity_required" if risk_type in {"permission_boundary", "sensitive_data"} else "normal_user",
                "expected": risk.get("expected") or "业务规则必须由服务端与数据事实共同满足。",
                "bug_signal": "接口、数据表、状态机或权限事实与企业资料归纳的业务规则不一致。",
                "oracle_family": risk.get("oracle_family") or _oracle_family(risk_type),
                "oracle_assertion": risk.get("expected"),
                "destructive": destructive,
                "execution_policy": execution_policy,
                "knowledge_lineage": {"risk_id": risk.get("risk_id"), "rule_id": rule_id, "source_ids": risk.get("evidence") or [], "interface_id": interface_id},
                "evidence_requirements": ["enterprise_knowledge_asset", "source_document_version", "interface_contract", "runtime_evidence_or_sandbox_replay"],
            })
            if len(probes) >= max_count:
                return probes
    return probes


def _evidence_bundle(asset: dict[str, Any], probes: list[dict[str, Any]]) -> dict[str, Any]:
    source_versions = []
    for source in asset.get("source_inventory") or []:
        if isinstance(source, dict):
            source_versions.append({"source_id": source.get("source_id"), "source_type": source.get("source_type"), "version": source.get("version"), "content_hash": source.get("content_hash"), "parse_status": (source.get("parse") or {}).get("parse_status")})
    return {
        "phase": PHASE,
        "asset_id": asset.get("asset_id"),
        "generated_at_utc": _now(),
        "source_versions": source_versions,
        "rule_oracle_trace_count": len(asset.get("oracle_library") or []),
        "probe_trace_count": len(probes),
        "evidence_policy": {"raw_source_payload_not_embedded": True, "secret_redaction_applied_to_excerpts": True, "writes_require_sandbox": True},
        "probe_lineage": [{"probe_id": p.get("probe_id"), "risk_type": p.get("risk_type"), "lineage": p.get("knowledge_lineage"), "execution_policy": p.get("execution_policy")} for p in probes],
    }


def _declared_project_source_files(project: str, root: Path) -> list[Path]:
    """Discover project-scoped source material while excluding credential/data files."""
    supported_suffixes = set(TEXT_SUFFIXES) | set(SOURCE_CODE_SUFFIXES) | {".docx", ".pdf"}
    control_plane_filenames = {
        "real_project_config.json",
        "multi_service_config.json",
        # Product-built knowledge assets / ledgers nest under input trees when
        # operators copy platform_outputs into platform_inputs. Re-ingesting
        # them as customer sources collides logical keys (e.g. permission_matrix)
        # and is never valid enterprise evidence.
        "enterprise_business_knowledge_asset.json",
        "enterprise_knowledge_registry.json",
        "enterprise_source_registry.json",
    }
    # Nested product output trees under an input root are not customer materials.
    product_output_path_segments = {
        "platform_outputs",
        "platform_workspace",
        "enterprise_knowledge_center",
        "defect_discovery",
    }
    secret_name_tokens = {
        "credential", "credentials", "secret", "secrets", "password", "passwords",
        "token", "tokens", "private_key", "apikey", "api_key", "test_account", "test_accounts",
    }
    data_seed_tokens = {"seed", "seeds", "fixture", "fixtures", "dump", "backup", "sample_data"}
    input_roots = (
        root / "platform_inputs" / project,
        root / "projects" / project / "input",
        root / "platform_workspace" / project / "input",
    )
    discovered: list[Path] = []
    seen: set[str] = set()
    for input_root in input_roots:
        if not input_root.is_dir():
            continue
        for candidate in sorted(input_root.rglob("*")):
            if not candidate.is_file() or candidate.suffix.lower() not in supported_suffixes:
                continue
            relative_parts = {part.lower() for part in candidate.relative_to(input_root).parts[:-1]}
            if relative_parts.intersection(product_output_path_segments):
                continue
            # These files are runtime control-plane state, not customer evidence.
            # They live beside legacy inputs for compatibility but must not be
            # re-ingested into the enterprise source registry.
            if candidate.name.lower() in control_plane_filenames:
                continue
            # Legacy onboarding can leave an empty PRD placeholder while the
            # canonical knowledge registry already contains the uploaded source.
            # An empty file cannot carry evidence and is rejected by canonical
            # ingestion, so it is not a declared material candidate here.
            if candidate.stat().st_size == 0:
                continue
            name_tokens = {token for token in re.split(r"[^a-z0-9_]+", candidate.stem.lower()) if token}
            normalized_stem = re.sub(r"[^a-z0-9]+", "_", candidate.stem.lower()).strip("_")
            if candidate.name.lower() == ".env" or secret_name_tokens.intersection(name_tokens) or normalized_stem in secret_name_tokens:
                continue
            if candidate.suffix.lower() == ".sql" and (data_seed_tokens.intersection(name_tokens) or normalized_stem in data_seed_tokens):
                continue
            resolved = str(candidate.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            discovered.append(candidate)
    return discovered


def _sync_declared_project_sources(project: str, root: Path, registry: dict[str, Any]) -> dict[str, Any]:
    from ._crud import ingest_enterprise_knowledge_files, _logical_key
    active_hashes = {
        str(row.get("content_hash") or "")
        for row in registry.get("sources") or []
        if isinstance(row, dict) and row.get("status") == "active"
    }
    pending: list[Path] = []
    pending_hashes: set[str] = set()
    # Same stem+type under platform_inputs and projects/<id>/input must not
    # enter one ingest batch with conflicting bytes. Identical content is
    # skipped via hash; divergent content fails closed with both paths named.
    # Line-ending variants (CRLF vs LF) of the same customer material are the
    # same document: a Git checkout that normalizes line endings must not turn
    # one source into two conflicting logical keys, so both the raw bytes and
    # the CRLF-normalized identity are compared against what is already known.
    pending_logical_keys: dict[str, Path] = {}
    for candidate in _declared_project_source_files(project, root):
        blob = candidate.read_bytes()
        if len(blob) > MAX_SOURCE_BYTES:
            raise ValueError(f"declared source exceeds {MAX_SOURCE_BYTES // (1024 * 1024)}MB limit: {candidate}")
        content_hash = _hash_bytes(blob)
        normalized_hash = _hash_bytes(blob.replace(b"\r\n", b"\n"))
        # The same customer material may appear under platform_inputs and
        # projects/<id>/input. Duplicate paths in one batch collide logical keys.
        if (
            content_hash in active_hashes
            or content_hash in pending_hashes
            or normalized_hash in active_hashes
            or normalized_hash in pending_hashes
        ):
            continue
        raw_text = blob.decode("utf-8", errors="replace")
        source_type = _classify_source(candidate.name, raw_text, "")
        logical_key = _logical_key(candidate.name, source_type)
        prior_path = pending_logical_keys.get(logical_key)
        if prior_path is not None:
            # A second file with the same logical key and different bytes is a
            # genuine declaration conflict. Line-ending-only differences were
            # already skipped above via the normalized identity, so reaching
            # here means the content truly diverges and must fail closed.
            raise RuntimeError(
                "declared enterprise source logical-key conflict: "
                + json.dumps(
                    {
                        "code": "DECLARED_SOURCE_LOGICAL_KEY_CONFLICT",
                        "logical_key": logical_key,
                        "paths": [str(prior_path), str(candidate)],
                        "blocks_formal_understanding": True,
                    },
                    ensure_ascii=False,
                )
            )
        pending_logical_keys[logical_key] = candidate
        pending_hashes.add(content_hash)
        pending_hashes.add(normalized_hash)
        pending.append(candidate)
    if not pending:
        return registry
    result = ingest_enterprise_knowledge_files(
        project,
        pending,
        root=root,
        actor={"name": "knowledge_builder", "role": "knowledge_admin"},
    )
    errors = [row for row in result.get("errors") or [] if isinstance(row, dict)]
    if errors:
        raise RuntimeError(f"declared enterprise source ingestion failed: {json.dumps(errors, ensure_ascii=False)}")
    return _load_registry(project, root)
