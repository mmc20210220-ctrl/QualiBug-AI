"""Narrow source-backed authorities for write-effect observers."""
from __future__ import annotations
import re
from typing import Any


def _d(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _l(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _t(v: Any) -> str:
    return str(v or "").strip()


def _field(v: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _t(v).lower())


def _schemas(op: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for status, raw in _d(op.get("response_schema") or op.get("responses")).items():
        if not _t(status).startswith("2"):
            continue
        response = _d(raw)
        for media in _d(response.get("content")).values():
            schema = _d(_d(media).get("schema"))
            if schema:
                out.append(schema)
        schema = _d(response.get("schema"))
        if schema:
            out.append(schema)
    return out


def _refs(op: dict[str, Any]) -> set[str]:
    return {_t(s.get("$ref")) for s in _schemas(op) if _t(s.get("$ref"))}


def install_effect_observer_source_authority(target: Any) -> None:
    semantic = getattr(target, "_semantic", None)
    authority = getattr(semantic, "_authority", None) if semantic else None
    core = getattr(authority, "_core", None) if authority else None
    if core is None or getattr(target, "_qualibug_effect_observer_authority", False):
        return
    candidates = semantic._candidate_effect_observers
    explicit = semantic._explicit_observer_relation_authority
    frozen = semantic._create_identity_observer_authority

    def parent(source: dict[str, Any], observer: dict[str, Any]) -> bool:
        if _t(observer.get("method")).upper() not in {"GET", "HEAD"}:
            return False
        spath = core.normalize_path_placeholders(_t(source.get("path") or source.get("raw_path")))
        opath = core.normalize_path_placeholders(_t(observer.get("path") or observer.get("raw_path")))
        parts = [p for p in spath.strip("/").split("/") if p]
        pos = [i for i, p in enumerate(parts) if p.startswith("{") and p.endswith("}")]
        return bool(pos) and pos[-1] < len(parts) - 1 and opath == "/" + "/".join(parts[: pos[-1] + 1])

    def body_bound(source: dict[str, Any], observer: dict[str, Any]) -> bool:
        if _t(observer.get("method")).upper() not in {"GET", "HEAD"}:
            return False
        spath = core.normalize_path_placeholders(_t(source.get("path") or source.get("raw_path"))).rstrip("/")
        opath = core.normalize_path_placeholders(_t(observer.get("path") or observer.get("raw_path")))
        placeholders = list(core.extract_placeholders(opath))
        collection = core.normalize_path_placeholders(core.collection_path(opath)).rstrip("/")
        if not placeholders or not collection.startswith("/") or not (
            spath == collection or spath.startswith(collection + "/")
        ):
            return False
        fields = {_field(k) for k in _d(core._request_example(source)) if _field(k)}
        return bool(fields) and all(_field(p) in fields for p in placeholders)

    def response_bound(source: dict[str, Any], observer: dict[str, Any]) -> bool:
        if _t(source.get("method")).upper() != "POST" or _t(observer.get("method")).upper() not in {"GET", "HEAD"}:
            return False
        spath = core.normalize_path_placeholders(_t(source.get("path") or source.get("raw_path"))).rstrip("/")
        opath = core.normalize_path_placeholders(_t(observer.get("path") or observer.get("raw_path")))
        return (
            spath.startswith("/") and not core.path_has_placeholders(spath)
            and len(list(core.extract_placeholders(opath))) == 1
            and core.normalize_path_placeholders(core.collection_path(opath)).rstrip("/") == spath
            and bool(_refs(source) & _refs(observer))
        )

    def authority_name(source: dict[str, Any], observer: dict[str, Any], ir: dict[str, Any]) -> str:
        spath = core.normalize_path_placeholders(_t(source.get("path") or source.get("raw_path")))
        opath = core.normalize_path_placeholders(_t(observer.get("path") or observer.get("raw_path")))
        if _t(observer.get("method")).upper() in {"GET", "HEAD"} and spath.startswith("/") and spath == opath:
            return "exact_transport_path"
        if parent(source, observer):
            return "exact_parent_resource_path"
        if body_bound(source, observer):
            return "request_body_placeholder_path"
        if response_bound(source, observer):
            return "matching_response_resource_ref"
        if frozen(source, observer):
            return "frozen_identity_output"
        if explicit(source, observer, ir):
            return "source_relation_chain"
        return ""

    def declared_effect_observers(
        operation: dict[str, Any], *, behavior_ir: dict[str, Any], max_candidates: int = 2
    ) -> list[dict[str, str]]:
        source_ref = _t(operation.get("id") or operation.get("operation_id"))
        ops = {
            _t(row.get("id") or row.get("operation_id")): row
            for row in _l(_d(behavior_ir).get("operations"))
            if isinstance(row, dict) and _t(row.get("id") or row.get("operation_id"))
        }
        source = _d(ops.get(source_ref))
        if not source:
            return []
        # Fail-closed operation identity: a supplied operation id cannot be
        # reused under a different transport path/method to gain observer
        # authority. The authority-mechanics candidate builder enforces this
        # when it sees the caller's operation, so this facade must apply the
        # same boundary before resolving the id down to the source operation
        # (otherwise the forged path is silently discarded and never checked).
        supplied_path = _t(operation.get("path") or operation.get("raw_path"))
        source_path = _t(source.get("path") or source.get("raw_path"))
        if supplied_path and supplied_path != source_path:
            return []
        supplied_method = _t(operation.get("method")).upper()
        source_method = _t(source.get("method")).upper()
        if supplied_method and source_method and supplied_method != source_method:
            return []
        priority = {
            "exact_transport_path": 0, "exact_parent_resource_path": 1,
            "request_body_placeholder_path": 2, "matching_response_resource_ref": 3,
            "frozen_identity_output": 4, "source_relation_chain": 5,
        }
        ranked: list[tuple[int, dict[str, str]]] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in candidates(source, behavior_ir=behavior_ir, max_candidates=5):
            observer = _d(ops.get(_t(_d(raw).get("operation_ref"))))
            name = authority_name(source, observer, behavior_ir) if observer else ""
            if not name:
                continue
            key = (
                _t(observer.get("id") or observer.get("operation_id")),
                _t(observer.get("method")).upper(),
                core.normalize_path_placeholders(_t(observer.get("path") or observer.get("raw_path"))),
            )
            if key in seen:
                continue
            seen.add(key)
            ranked.append((priority.get(name, 99), {"operation_ref": key[0], "method": key[1], "path": key[2]}))
        ranked.sort(key=lambda x: (x[0], x[1]["operation_ref"], x[1]["method"], x[1]["path"]))
        if int(max_candidates or 1) <= 1:
            if not ranked:
                return []
            best = ranked[0][0]
            peers = [row for rank, row in ranked if rank == best]
            return peers if len(peers) == 1 else []
        return [row for _, row in ranked[: max(1, min(int(max_candidates or 1), 5))]]

    for module in (core, authority, semantic, target):
        module.declared_effect_observers = declared_effect_observers
    semantic._observer_authority = authority_name
    target._qualibug_effect_observer_authority = True
