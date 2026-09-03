"""Fast read path for one connector's resource preview.

The canonical connector inventory intentionally projects health, OAuth, webhook,
acceptance and coverage for every connector. A resource-preview read only needs the
target connector's persisted coverage receipt plus the project knowledge-source
inventory. Keeping that read separate prevents one ``/resources`` request from
rebuilding the complete connector inventory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from .connector_acl_authority import filter_connector_sources_for_actor
from .connector_sync_authority import ConnectorSyncError, list_connector_instances
from .enterprise_knowledge_center import list_enterprise_knowledge_sources
from .private_pilot_connector_handlers import _coverage_projection, _safe_int, _text


def _target_connector_instance(
    project: str,
    connector: str,
    root: Path,
) -> dict[str, Any]:
    """Resolve one persisted connector without projecting unrelated connectors."""
    rows = list_connector_instances(
        project,
        root=root,
        include_disabled=True,
    ).get("connector_instances") or []
    for row in rows:
        if (
            isinstance(row, dict)
            and _text(row.get("connector_instance_id"), 160) == connector
        ):
            return dict(row)
    raise KeyError("knowledge_connector_not_found")


def project_connector_resources_fast(
    project: str,
    connector: str,
    root: Path,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project at most 100 resource rows while preserving the public contract.

    Unlike the legacy path this does not call ``_connector_inventory_row``. That
    helper builds every connector's credential/health/webhook/OAuth/acceptance
    projection even though resource preview consumes only ``coverage``.
    """
    instance = _target_connector_instance(project, connector, root)
    coverage = _coverage_projection(project, connector, instance, root)
    prefix = f"connector://{quote(connector, safe='._-')}/"
    source_inventory = list_enterprise_knowledge_sources(
        project,
        root=root,
        include_deleted=False,
    )

    # Narrow to the requested connector before ACL evaluation. The endpoint can
    # never return another connector's source, while ACL filtering remains the
    # authority for every candidate that may be returned.
    source_rows = [
        row
        for row in source_inventory.get("sources") or []
        if isinstance(row, dict)
        and _text(row.get("source_ref"), 2000).startswith(prefix)
    ]
    acl_projection: dict[str, Any] = {}
    if actor is not None:
        source_rows, acl_projection = filter_connector_sources_for_actor(
            project,
            source_rows,
            actor={**actor, "project_id": project},
            root=root,
        )

    resources: list[dict[str, Any]] = []
    for source in source_rows:
        raw_permission_scope = source.get("permission_scope")
        permission_scope = (
            {
                key: raw_permission_scope[key]
                for key in (
                    "visibility",
                    "availability",
                    "evidence_status",
                    "acl_version",
                    "complete",
                    "propagation_allowed",
                    "raw_remote_principals_returned",
                )
                if key in raw_permission_scope
            }
            if isinstance(raw_permission_scope, dict)
            else {}
        )
        resources.append(
            {
                "resource_index": len(resources),
                "display_title": _text(source.get("original_name"), 300)
                or "UNNAMED_RESOURCE",
                "resource_kind": _text(source.get("source_type"), 120),
                "state": "MATERIALIZED",
                "updated_at_utc": _text(source.get("updated_at_utc"), 80),
                "source_updated_at": _text(source.get("source_updated_at"), 240),
                "permission_scope": permission_scope,
            }
        )
        if len(resources) >= 100:
            break

    materialized_count = len(resources)
    for unsupported in coverage.get("unsupported_resources") or []:
        if not isinstance(unsupported, dict) or len(resources) >= 100:
            break
        resources.append(
            {
                "resource_index": len(resources),
                "display_title": _text(unsupported.get("display_title"), 300),
                "resource_kind": _text(unsupported.get("resource_kind"), 120),
                "remote_object_type": _text(
                    unsupported.get("remote_object_type"), 80
                ),
                "state": "UNSUPPORTED",
                "reason_code": _text(unsupported.get("reason_code"), 160),
            }
        )

    return {
        "schema": "qualibug.knowledge-connector-resources.v1",
        "project_id": project,
        "connector_instance_id": connector,
        "status": _text(coverage.get("status"), 80) or "NOT_AVAILABLE",
        "discovered_count": _safe_int(coverage.get("discovered_count")),
        "covered_count": _safe_int(coverage.get("covered_count")),
        "unsupported_count": _safe_int(coverage.get("unsupported_count")),
        "materialized_preview_count": materialized_count,
        "resources": resources,
        "preview_truncated": len(resources) >= 100,
        "source_content_returned": False,
        "raw_cursor_returned": False,
        "credential_values_returned": False,
        "remote_resource_identities_returned": False,
        "source_refs_returned": False,
        "acl_visibility_projection": {
            key: value
            for key, value in acl_projection.items()
            if key != "denied_source_keys"
        },
    }


class ConnectorReadFastPathMixin:
    """Intercept only the expensive resource-preview GET; delegate all else."""

    def _handle_knowledge_connector_get(
        self,
        project: str,
        tail: list[str],
        root: Path,
        actor: dict[str, Any] | None = None,
    ) -> Any:
        if len(tail) != 2 or tail[1] != "resources":
            return super()._handle_knowledge_connector_get(project, tail, root, actor)

        connector = _text(tail[0], 160)
        try:
            projection = project_connector_resources_fast(
                project,
                connector,
                root,
                actor,
            )
            return self._json({"ok": True, "data": projection})
        except (ConnectorSyncError, KeyError) as exc:
            return self._knowledge_connector_error(exc)


__all__ = [
    "ConnectorReadFastPathMixin",
    "project_connector_resources_fast",
]
