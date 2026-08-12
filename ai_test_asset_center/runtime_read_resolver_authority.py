"""Source-backed extension for runtime path identity resolvers."""
from __future__ import annotations
import re
from typing import Any


def _d(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _l(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _t(v: Any) -> str:
    return str(v or "").strip()


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


def _ref(schema: dict[str, Any]) -> str:
    return _t(_d(schema).get("$ref"))


def _array_refs(op: dict[str, Any]) -> set[str]:
    return {
        ref for schema in _schemas(op)
        if _t(schema.get("type")).lower() == "array"
        for ref in [_ref(_d(schema.get("items")))] if ref
    }


def _direct_resource_refs(op: dict[str, Any]) -> set[str]:
    return {
        ref for schema in _schemas(op)
        if _t(schema.get("type")).lower() != "array"
        for ref in [_ref(schema)] if ref
    }


def _resource_refs(op: dict[str, Any], ir: dict[str, Any], core: Any) -> set[str]:
    refs = set(_direct_resource_refs(op))
    path = core.normalize_path_placeholders(_t(op.get("path") or op.get("raw_path")))
    parts = [p for p in path.strip("/").split("/") if p]
    positions = [i for i, p in enumerate(parts) if core._PLACEHOLDER_RE.fullmatch(p)]
    if not positions:
        return refs
    parent = "/" + "/".join(parts[: positions[-1] + 1])
    for candidate in _l(_d(ir).get("operations")):
        if not isinstance(candidate, dict) or _t(candidate.get("method")).upper() != "GET":
            continue
        cpath = core.normalize_path_placeholders(_t(candidate.get("path") or candidate.get("raw_path")))
        if cpath == parent:
            refs.update(_direct_resource_refs(candidate))
    return refs


def _has_required_parameters(op: dict[str, Any]) -> bool:
    return any(
        isinstance(row, dict) and row.get("required") is True
        for row in _l(op.get("parameters"))
    )


def _cross_prefix_exact_schema_candidate(
    operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    core: Any,
    seen: set[tuple[str, str, str]],
) -> dict[str, str] | None:
    path = core.normalize_path_placeholders(_t(operation.get("path") or operation.get("raw_path")))
    # This fallback is deliberately narrower than the same-collection rule: one
    # path identity, one direct success resource, no array-valued target success.
    if len(re.findall(r"\{[^{}]+\}", path)) != 1 or _array_refs(operation):
        return None
    target_refs = _direct_resource_refs(operation)
    if len(target_refs) != 1:
        return None

    matches: list[dict[str, str]] = []
    for candidate in _l(_d(behavior_ir).get("operations")):
        if not isinstance(candidate, dict) or _t(candidate.get("method")).upper() != "GET":
            continue
        cpath = core.normalize_path_placeholders(_t(candidate.get("path") or candidate.get("raw_path")))
        key = (_t(candidate.get("id") or candidate.get("operation_id")), "GET", cpath)
        if (
            not key[0]
            or not cpath.startswith("/")
            or core.path_has_placeholders(cpath)
            or key in seen
            or _has_required_parameters(candidate)
            or _array_refs(candidate) != target_refs
        ):
            continue
        matches.append({"operation_ref": key[0], "method": "GET", "path": cpath})
        if len(matches) > 1:
            return None
    return matches[0] if len(matches) == 1 else None


def install_runtime_read_resolver_authority(target: Any) -> None:
    semantic = getattr(target, "_semantic", None)
    authority = getattr(semantic, "_authority", None) if semantic else None
    core = getattr(authority, "_core", None) if authority else None
    if core is None or getattr(target, "_qualibug_read_resolver_authority", False):
        return
    original = core.declared_runtime_read_resolvers

    def declared_runtime_read_resolvers(
        operation: dict[str, Any], *, behavior_ir: dict[str, Any], max_candidates: int = 2
    ) -> list[dict[str, str]]:
        limit = max(1, min(int(max_candidates or 1), 5))
        rows = list(original(operation, behavior_ir=behavior_ir, max_candidates=limit))
        if len(rows) >= limit:
            return rows[:limit]
        seen = {(_t(r.get("operation_ref")), _t(r.get("method")), _t(r.get("path"))) for r in rows}
        path = core.normalize_path_placeholders(_t(_d(operation).get("path")))
        if not path.startswith("/") or not core.path_has_placeholders(path):
            return rows[:limit]
        prefix = core.normalize_path_placeholders(core.collection_path(path)).rstrip("/")
        refs = _resource_refs(operation, _d(behavior_ir), core)
        if refs and prefix.startswith("/") and not core.path_has_placeholders(prefix):
            for candidate in _l(_d(behavior_ir).get("operations")):
                if not isinstance(candidate, dict):
                    continue
                cpath = core.normalize_path_placeholders(_t(candidate.get("path") or candidate.get("raw_path")))
                key = (_t(candidate.get("id") or candidate.get("operation_id")), "GET", cpath)
                if (
                    _t(candidate.get("method")).upper() != "GET" or not key[0]
                    or not cpath.startswith(prefix + "/") or core.path_has_placeholders(cpath)
                    or not (refs & _array_refs(candidate)) or key in seen
                ):
                    continue
                rows.append({"operation_ref": key[0], "method": "GET", "path": cpath})
                seen.add(key)
                if len(rows) >= limit:
                    break
        if len(rows) < limit:
            candidate = _cross_prefix_exact_schema_candidate(
                operation,
                behavior_ir=_d(behavior_ir),
                core=core,
                seen=seen,
            )
            if candidate is not None:
                rows.append(candidate)
        return rows[:limit]

    for module in (core, authority, semantic, target):
        module.declared_runtime_read_resolvers = declared_runtime_read_resolvers
    target._qualibug_read_resolver_authority = True
