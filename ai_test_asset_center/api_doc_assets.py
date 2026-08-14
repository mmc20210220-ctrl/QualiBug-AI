"""Merge project-scoped API materials into one source-bound catalog.

Customers often upload API docs in several shapes (Markdown API_SPEC,
OpenAPI JSON, partial Postman exports). The discovery pipeline needs a
single, deduplicated endpoint catalog — never a single hardcoded file path.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_OPENAPI_FILENAMES = ("openapi.json", "swagger.json", "openapi.yaml", "openapi.yml")
_MARKDOWN_API_NAMES = ("API_SPEC.md", "API.md", "api_spec.md", "api.md", "OPENAPI.md")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _project_api_input_dirs(root: Path, project: str) -> list[Path]:
    safe = "".join(ch for ch in str(project or "") if ch.isalnum() or ch in "_-.") or "project"
    dirs: list[Path] = [
        root / "platform_inputs" / safe,
        root / "platform_workspace" / safe / "input",
        root / "projects" / safe / "input",
    ]
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in dirs:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def _read_file(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _load_openapi_dict(path: Path) -> dict[str, Any]:
    text = _read_file(path)
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _catalog_from_markdown(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    import re as _re

    from .business_state_graph import _api_facts

    state_re = _re.compile(
        r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])",
        _re.I,
    )
    _entities, _states, endpoints = _api_facts(text, state_re)
    return [dict(ep) for ep in endpoints if isinstance(ep, dict) and _text(ep.get("path")).startswith("/")]


def _catalog_from_openapi(path: Path) -> list[dict[str, str]]:
    from .system_behavior_space import _openapi_route_facts

    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(_read_file(path)) or {}
            if isinstance(data, dict):
                return _openapi_route_facts(json.dumps(data, ensure_ascii=False))
        except Exception:
            return []
    data = _load_openapi_dict(path)
    if data:
        return _openapi_route_facts(json.dumps(data, ensure_ascii=False))
    return _openapi_route_facts(_read_file(path))


def _merge_endpoint_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    from .system_behavior_space import _endpoint_entity, _merge_api_endpoints

    return _merge_api_endpoints(rows, [])


def _normalize_path_param(path: str) -> str:
    path = _text(path)
    if not path.startswith("/"):
        path = "/" + path
    path = re.sub(r"\{([^}]+)\}", r":\1", path)
    path = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r":\1", path)
    return path


def _render_merged_markdown(endpoints: list[dict[str, str]], *, header_note: str = "") -> str:
    if not endpoints:
        return ""
    grouped: dict[str, list[dict[str, str]]] = {}
    for ep in endpoints:
        entity = _text(ep.get("entity")) or "api"
        grouped.setdefault(entity, []).append(ep)
    lines = ["# API 接口文档（合并客户材料）", ""]
    if header_note:
        lines.extend([header_note, ""])
    lines.append("> 由 Markdown API 文档与 OpenAPI 材料自动合并，供源绑定发现使用。")
    lines.append("")
    for entity in sorted(grouped):
        lines.append(f"## {entity.title()}")
        lines.append("")
        seen: set[tuple[str, str]] = set()
        for ep in sorted(grouped[entity], key=lambda row: (_text(row.get("path")), _text(row.get("method")))):
            method = _text(ep.get("method")).upper() or "GET"
            path = _normalize_path_param(_text(ep.get("path")))
            key = (method, path)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"### {method} {path}")
            summary = _text(ep.get("summary") or ep.get("action"))
            if summary:
                lines.append("")
                lines.append(summary)
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def collect_merged_api_catalog(
    root: Path,
    project: str,
    *,
    primary_text: str = "",
) -> tuple[list[dict[str, str]], list[str]]:
    """Return merged endpoint rows and source labels used."""
    catalog: list[dict[str, str]] = []
    sources: list[str] = []

    if str(primary_text or "").strip():
        catalog.extend(_catalog_from_markdown(primary_text))
        sources.append("caller_primary")

    for input_dir in _project_api_input_dirs(root, project):
        if not input_dir.is_dir():
            continue
        for name in _MARKDOWN_API_NAMES:
            path = input_dir / name
            if path.is_file():
                rows = _catalog_from_markdown(_read_file(path))
                if rows:
                    catalog.extend(rows)
                    sources.append(str(path.relative_to(root)))
        for name in _OPENAPI_FILENAMES:
            path = input_dir / name
            if path.is_file():
                rows = _catalog_from_openapi(path)
                if rows:
                    catalog.extend(rows)
                    sources.append(str(path.relative_to(root)))

    merged = _merge_endpoint_rows(catalog)
    return merged, sources


def enrich_api_spec_text(root: Path, project: str, api_spec_text: str) -> str:
    """Merge all project API materials; render unified Markdown for the pipeline."""
    primary = str(api_spec_text or "").strip()
    merged, sources = collect_merged_api_catalog(root, project, primary_text=primary)
    if not merged:
        return primary
    note = f"Merged sources: {', '.join(sources[:8])}" + (" …" if len(sources) > 8 else "")
    rendered = _render_merged_markdown(merged, header_note=note)
    if primary:
        # Primary already covers the catalog — return it verbatim so request
        # schemas, examples, and role constraints survive (the markdown render
        # drops them). Compare the normalized (method, path) sets only: merged
        # rows may outnumber primary rows when several sources describe the
        # same endpoints, and that must never force a lossy markdown downgrade.
        primary_paths = {( _text(e.get("method")).upper(), _normalize_path_param(_text(e.get("path")))) for e in _catalog_from_markdown(primary)}
        merged_paths = {( _text(e.get("method")).upper(), _normalize_path_param(_text(e.get("path")))) for e in merged}
        if merged_paths <= primary_paths:
            return primary
    return rendered
