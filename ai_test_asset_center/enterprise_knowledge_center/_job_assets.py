"""Add source-backed Job assets to the existing enterprise knowledge mainline.

The stage is additive: it discovers/normalizes Job definitions and projects them
into the existing enterprise understanding operation collection. It does not own a
parallel behavior model, planner, executor, Oracle, finding or receipt authority.
"""
from __future__ import annotations

import re
from functools import wraps
from pathlib import Path
from typing import Any, Iterable

from ..job_platform_contract import (
    ASYNC_OPERATION_KIND,
    get_job_platform_adapter,
    normalize_job_definition,
    to_async_operation,
)

JOB_ASSET_ENRICHMENT_SCHEMA = "qualibug.enterprise-job-asset-enrichment.v1"
JOB_CONNECTOR_KINDS = frozenset(
    {
        "job_platform",
        "xxl_job",
        "powerjob",
        "quartz_scheduler",
        "scheduler_export",
    }
)
_TEXT_SOURCE_SUFFIXES = frozenset(
    {
        ".java",
        ".kt",
        ".kts",
        ".groovy",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".yaml",
        ".yml",
        ".xml",
        ".properties",
        ".conf",
        ".ini",
        ".sh",
        ".sql",
    }
)
_MAX_JOB_SOURCE_BYTES = 2 * 1024 * 1024

_XXL_JOB_RE = re.compile(r'@XxlJob\s*\(\s*["\']([^"\']+)["\']\s*\)')
_SCHEDULED_RE = re.compile(r"@Scheduled\s*\((?P<args>[^)]*)\)")
_CRON_ATTR_RE = re.compile(r'(?:cron\s*=\s*)?["\']([^"\']{3,160})["\']')
_JAVA_METHOD_RE = re.compile(
    r"(?:public|protected|private)?\s*(?:static\s+)?(?:[\w<>,.?\[\]]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\("
)
_QUARTZ_RE = re.compile(
    r"class\s+(?P<name>[A-Za-z_]\w*)\s+(?:extends\s+\w+\s+)?implements\s+(?:org\.quartz\.)?Job\b"
)
_POWERJOB_RE = re.compile(
    r"class\s+(?P<name>[A-Za-z_]\w*)\s+implements\s+(?:BasicProcessor|ProcessProcessor|MapProcessor)\b"
)
_AIRFLOW_DAG_RE = re.compile(r"\bDAG\s*\(\s*[\"'](?P<name>[^\"']+)[\"']")
_PY_SCHEDULE_RE = re.compile(
    r"@(?:app\.)?(?:task|periodic_task)\s*\((?P<args>[^)]*)\)"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe(rows: Iterable[dict[str, Any]], identity: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = _text(row.get(identity))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _source_evidence(
    source: dict[str, Any],
    *,
    locator: str = "",
    quote: str = "",
    connector_id: str = "",
) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "source_id": _text(source.get("source_id")) or _text(connector_id),
            "source_locator": locator,
            "quote": quote[:600],
            "connector_id": _text(connector_id),
            "external_ref": _text(source.get("external_ref")),
            "derivation": "source_backed_job_discovery",
        }.items()
        if value
    }


def _method_after(text: str, end: int) -> str:
    match = _JAVA_METHOD_RE.search(text[end : end + 500])
    return _text(match.group("name")) if match else ""


def _raw_definition(
    *,
    platform_type: str,
    source: dict[str, Any],
    locator: str,
    platform_job_id: str,
    display_name: str,
    handler: str = "",
    trigger_type: str = "UNKNOWN",
    cron: str = "",
) -> dict[str, Any]:
    return {
        "platform_type": platform_type,
        "platform_job_id": platform_job_id,
        "display_name": display_name or platform_job_id,
        "handler": handler,
        "trigger": {
            "type": trigger_type,
            "cron": cron,
        },
        "source_refs": [
            _source_evidence(
                source,
                locator=locator,
                quote=display_name or handler or platform_job_id,
            )
        ],
        # Static source discovery deliberately does not invent runtime, fixture,
        # observer, business effects or cleanup contracts.
        "behavior": {
            "process_steps": [],
            "selection_predicates": [],
            "object_refs": [],
            "read_set": [],
            "write_set": [],
            "expected_effects": [],
        },
    }


def discover_job_definitions_from_text(
    text: str,
    *,
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    """Discover framework-declared Job entry points without inferring business truth."""

    rows: list[dict[str, Any]] = []
    source_id = _text(source.get("source_id")) or _text(source.get("original_name")) or "source"

    for match in _XXL_JOB_RE.finditer(text):
        job_name = _text(match.group(1))
        handler = _method_after(text, match.end())
        rows.append(
            _raw_definition(
                platform_type="xxl_job",
                source=source,
                locator=f"{source_id}#offset={match.start()}",
                platform_job_id=job_name,
                display_name=job_name,
                handler=handler,
                trigger_type="MANUAL",
            )
        )

    for match in _SCHEDULED_RE.finditer(text):
        args = _text(match.group("args"))
        cron_match = _CRON_ATTR_RE.search(args)
        cron = _text(cron_match.group(1)) if cron_match else ""
        handler = _method_after(text, match.end())
        platform_job_id = f"{source_id}:{handler or match.start()}"
        rows.append(
            _raw_definition(
                platform_type="spring_scheduler",
                source=source,
                locator=f"{source_id}#offset={match.start()}",
                platform_job_id=platform_job_id,
                display_name=handler or "Spring Scheduled Job",
                handler=handler,
                trigger_type="CRON",
                cron=cron,
            )
        )

    for match in _QUARTZ_RE.finditer(text):
        handler = _text(match.group("name"))
        rows.append(
            _raw_definition(
                platform_type="quartz_scheduler",
                source=source,
                locator=f"{source_id}#offset={match.start()}",
                platform_job_id=f"{source_id}:{handler}",
                display_name=handler,
                handler=handler,
                trigger_type="UNKNOWN",
            )
        )

    for match in _POWERJOB_RE.finditer(text):
        handler = _text(match.group("name"))
        rows.append(
            _raw_definition(
                platform_type="powerjob",
                source=source,
                locator=f"{source_id}#offset={match.start()}",
                platform_job_id=f"{source_id}:{handler}",
                display_name=handler,
                handler=handler,
                trigger_type="UNKNOWN",
            )
        )

    for match in _AIRFLOW_DAG_RE.finditer(text):
        name = _text(match.group("name"))
        rows.append(
            _raw_definition(
                platform_type="airflow",
                source=source,
                locator=f"{source_id}#offset={match.start()}",
                platform_job_id=name,
                display_name=name,
                trigger_type="DEPENDENCY",
            )
        )

    for match in _PY_SCHEDULE_RE.finditer(text):
        handler = ""
        following = text[match.end() : match.end() + 300]
        function_match = re.search(r"def\s+([A-Za-z_]\w*)\s*\(", following)
        if function_match:
            handler = _text(function_match.group(1))
        rows.append(
            _raw_definition(
                platform_type="python_scheduler",
                source=source,
                locator=f"{source_id}#offset={match.start()}",
                platform_job_id=f"{source_id}:{handler or match.start()}",
                display_name=handler or "Python Scheduled Task",
                handler=handler,
                trigger_type="CRON",
            )
        )
    return _dedupe(rows, "platform_job_id")


def _safe_source_path(root: Path, stored_path: str) -> Path | None:
    if not stored_path:
        return None
    candidate = Path(stored_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _definitions_from_source_inventory(
    asset: dict[str, Any],
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    definitions: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for source in _list(asset.get("source_inventory")):
        if not isinstance(source, dict) or _text(source.get("status")) not in {"", "active"}:
            continue
        path = _safe_source_path(root, _text(source.get("stored_path")))
        if path is None or path.suffix.lower() not in _TEXT_SOURCE_SUFFIXES:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_JOB_SOURCE_BYTES:
            gaps.append(
                {
                    "kind": "JOB_SOURCE_TOO_LARGE_FOR_STATIC_DISCOVERY",
                    "source_id": source.get("source_id"),
                    "size_bytes": size,
                    "limit_bytes": _MAX_JOB_SOURCE_BYTES,
                }
            )
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            gaps.append(
                {
                    "kind": "JOB_SOURCE_TEXT_UNREADABLE",
                    "source_id": source.get("source_id"),
                    "error": type(exc).__name__,
                }
            )
            continue
        definitions.extend(discover_job_definitions_from_text(content, source=source))
    return definitions, gaps


def _connector_job_sources(
    project_id: str,
    root: Path,
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from ..enterprise_pilot_runtime import load_connector_registry

    registry = load_connector_registry(project_id, root)
    sources: list[dict[str, Any]] = []
    definitions: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    supplied_by_connector = _dict(options.get("job_definitions_by_connector"))

    for connector in _list(registry.get("connectors")):
        if not isinstance(connector, dict):
            continue
        kind = _text(connector.get("kind")).lower()
        external_ref = _text(connector.get("external_ref"))
        declared_job_kind = ""
        if external_ref.lower().startswith("job_platform:"):
            declared_job_kind = external_ref.split(":", 1)[1].strip().lower()
        if (
            kind not in JOB_CONNECTOR_KINDS
            and not declared_job_kind
        ) or not bool(connector.get("enabled", True)):
            continue
        adapter_kind = declared_job_kind or kind
        connector_id = _text(connector.get("connector_id"))
        source = {
            "connector_id": connector_id,
            "kind": adapter_kind,
            "display_name": _text(connector.get("display_name")) or adapter_kind,
            "endpoint_ref": _text(connector.get("endpoint_ref")),
            "external_ref": external_ref,
            "read_only": bool(connector.get("read_only", True)),
            "last_sync_at_utc": _text(connector.get("last_sync_at_utc")),
            "last_sync_status": _text(connector.get("last_sync_status")) or "not_synced",
            "status": "CONNECTED_READ_ONLY",
        }
        sources.append(source)

        explicit = supplied_by_connector.get(connector_id)
        if isinstance(explicit, list):
            for raw in explicit:
                if not isinstance(raw, dict):
                    continue
                row = dict(raw)
                row.setdefault("platform_type", adapter_kind)
                row.setdefault(
                    "source_refs",
                    [_source_evidence(source, connector_id=connector_id)],
                )
                definitions.append(row)
            continue

        adapter = get_job_platform_adapter(adapter_kind)
        if adapter is None:
            gaps.append(
                {
                    "kind": "JOB_PLATFORM_ADAPTER_NOT_REGISTERED",
                    "connector_id": connector_id,
                    "connector_kind": adapter_kind,
                    "blocks_remote_job_discovery": True,
                    "operator_action": (
                        "install a source-bound adapter or import a platform export; "
                        "do not manually recreate Job definitions"
                    ),
                }
            )
            continue
        try:
            for summary in adapter.list_jobs(connector):
                if not isinstance(summary, dict):
                    continue
                platform_job_id = _text(
                    summary.get("platform_job_id")
                    or summary.get("job_id")
                    or summary.get("id")
                )
                if not platform_job_id:
                    continue
                full = adapter.get_job_definition(connector, platform_job_id)
                if not isinstance(full, dict):
                    continue
                row = {**summary, **full}
                row.setdefault("platform_job_id", platform_job_id)
                row.setdefault("platform_type", adapter_kind)
                row.setdefault(
                    "source_refs",
                    [_source_evidence(source, connector_id=connector_id)],
                )
                definitions.append(row)
        except Exception as exc:
            gaps.append(
                {
                    "kind": "JOB_PLATFORM_DISCOVERY_FAILED",
                    "connector_id": connector_id,
                    "connector_kind": adapter_kind,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
    return sources, definitions, gaps


def _job_gap_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    testability = _dict(asset.get("testability"))
    gaps: list[dict[str, Any]] = []
    checks = (
        ("trigger_ready", "JOB_TRIGGER_CONTRACT_UNRESOLVED"),
        ("identity_ready", "JOB_RUN_IDENTITY_CONTRACT_UNRESOLVED"),
        ("fixture_ready", "JOB_FIXTURE_CONTRACT_UNRESOLVED"),
        ("observer_ready", "JOB_OBSERVER_CONTRACT_UNRESOLVED"),
        ("cleanup_ready", "JOB_CLEANUP_CONTRACT_UNRESOLVED"),
    )
    for field, kind in checks:
        if not bool(testability.get(field)):
            gaps.append(
                {
                    "kind": kind,
                    "job_asset_id": asset.get("job_asset_id"),
                    "platform_job_id": asset.get("platform_job_id"),
                    "execution_status": testability.get("execution_status"),
                }
            )
    if not bool(testability.get("oracle_ready")):
        gaps.append(
            {
                "kind": "JOB_BUSINESS_ORACLE_SOURCE_NOT_CONFIRMED",
                "job_asset_id": asset.get("job_asset_id"),
                "platform_job_id": asset.get("platform_job_id"),
                "blocks_formal_business_finding": True,
                "blocks_runtime_integrity_experiments": False,
            }
        )
    return gaps


def enrich_job_assets(
    asset: dict[str, Any],
    *,
    project_id: str,
    root: Path,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach Job assets and map them into the existing operation authority."""

    resolved_options = dict(options or {})
    enriched = asset
    connector_sources, connector_definitions, connector_gaps = _connector_job_sources(
        project_id, root, resolved_options
    )
    source_definitions, source_gaps = _definitions_from_source_inventory(enriched, root)
    explicit_definitions = [
        dict(row)
        for row in _list(resolved_options.get("job_definitions"))
        if isinstance(row, dict)
    ]
    definitions = [
        *connector_definitions,
        *source_definitions,
        *explicit_definitions,
    ]

    job_assets: list[dict[str, Any]] = []
    normalize_gaps: list[dict[str, Any]] = []
    for index, raw in enumerate(definitions):
        try:
            job_assets.append(
                normalize_job_definition(
                    raw,
                    source_refs=_list(raw.get("source_refs")),
                )
            )
        except (TypeError, ValueError) as exc:
            normalize_gaps.append(
                {
                    "kind": "JOB_DEFINITION_NOT_NORMALIZED",
                    "definition_index": index,
                    "platform_job_id": raw.get("platform_job_id") or raw.get("job_id"),
                    "reason": str(exc),
                }
            )
    job_assets = _dedupe(job_assets, "job_asset_id")
    async_operations = _dedupe(
        [to_async_operation(row) for row in job_assets],
        "operation_id",
    )

    model = _dict(enriched.get("enterprise_understanding_model"))
    if model:
        existing_operations = [
            dict(row)
            for row in _list(model.get("operations"))
            if isinstance(row, dict)
        ]
        model["operations"] = _dedupe(
            [*existing_operations, *async_operations],
            "operation_id",
        )
        metrics = _dict(model.get("metrics"))
        metrics["async_job_operation_count"] = len(
            [
                row
                for row in model["operations"]
                if _text(row.get("operation_kind")) == ASYNC_OPERATION_KIND
            ]
        )
        model["metrics"] = metrics
        enriched["enterprise_understanding_model"] = model

    old_gaps = [
        dict(row)
        for row in _list(enriched.get("coverage_gaps"))
        if isinstance(row, dict)
        and not _text(row.get("kind")).startswith("JOB_")
    ]
    job_gaps = [
        *connector_gaps,
        *source_gaps,
        *normalize_gaps,
        *[gap for row in job_assets for gap in _job_gap_rows(row)],
    ]

    status_counts: dict[str, int] = {}
    for row in job_assets:
        status = _text(_dict(row.get("testability")).get("execution_status")) or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1

    summary = _dict(enriched.get("summary"))
    summary.update(
        {
            "job_platform_source_count": len(connector_sources),
            "job_asset_count": len(job_assets),
            "job_async_operation_count": len(async_operations),
            "job_execution_ready_count": status_counts.get("EXECUTION_READY", 0),
            "job_partially_executable_count": status_counts.get(
                "PARTIALLY_EXECUTABLE", 0
            ),
            "job_unsafe_count": status_counts.get("UNSAFE", 0),
            "job_customer_confirmation_required_count": 0,
            "customer_manual_job_creation_count": 0,
            "customer_manual_job_step_configuration_count": 0,
            "customer_manual_job_oracle_authoring_count": 0,
            "customer_manual_job_cleanup_authoring_count": 0,
        }
    )
    enriched["summary"] = summary
    enriched["job_platform_sources"] = connector_sources
    enriched["job_assets"] = job_assets
    enriched["async_operations"] = async_operations
    enriched["job_asset_summary"] = {
        "schema": JOB_ASSET_ENRICHMENT_SCHEMA,
        "source_count": len(connector_sources),
        "asset_count": len(job_assets),
        "execution_status_counts": status_counts,
        "coverage_gap_count": len(job_gaps),
        "automatic_discovery_only": True,
        "manual_job_editor_present": False,
        "customer_effort_contract": {
            "manual_job_creation": 0,
            "manual_step_configuration": 0,
            "manual_field_binding": 0,
            "manual_test_case_authoring": 0,
            "manual_oracle_authoring": 0,
            "manual_cleanup_authoring": 0,
            "long_text_input": 0,
        },
        "fact_authority_contract": (
            "source code and platform configuration describe implemented behavior; "
            "they are not automatically promoted to business expectation"
        ),
    }
    enriched["coverage_gaps"] = [*old_gaps, *job_gaps]
    return enriched


def _persist_job_enrichment(asset: dict[str, Any], *, project_id: str, root: Path) -> None:
    from ._api import render_enterprise_business_knowledge_center
    from ._common import _write_json
    from ._utils import _paths

    paths = _paths(project_id, root)
    for key in ("asset", "asset_copy"):
        path = paths.get(key)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _write_json(path, asset)
    center_page = paths.get("center_page")
    if center_page:
        Path(center_page).parent.mkdir(parents=True, exist_ok=True)
        Path(center_page).write_text(
            render_enterprise_business_knowledge_center(
                project_id,
                root,
                asset=asset,
            ),
            encoding="utf-8",
        )


def install_job_asset_enrichment():
    """Install one additive Job stage on the current knowledge builder authority."""

    from . import _api
    from ._common import ROOT, _safe_project_id

    current = _api.build_enterprise_business_knowledge_asset
    if getattr(current, "_qualibug_job_asset_enrichment", False):
        return current
    original = current

    @wraps(original)
    def wrapped(
        project_id: str = "real_project_demo",
        root: Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_root = root or ROOT
        project = _safe_project_id(project_id)
        resolved_options = options or {}
        asset = original(project, resolved_root, resolved_options)
        enriched = enrich_job_assets(
            asset,
            project_id=project,
            root=resolved_root,
            options=resolved_options,
        )
        _persist_job_enrichment(enriched, project_id=project, root=resolved_root)
        return enriched

    wrapped._qualibug_job_asset_enrichment = True  # type: ignore[attr-defined]
    wrapped._qualibug_original_builder = original  # type: ignore[attr-defined]
    _api.build_enterprise_business_knowledge_asset = wrapped
    return wrapped


__all__ = [
    "JOB_ASSET_ENRICHMENT_SCHEMA",
    "JOB_CONNECTOR_KINDS",
    "discover_job_definitions_from_text",
    "enrich_job_assets",
    "install_job_asset_enrichment",
]
