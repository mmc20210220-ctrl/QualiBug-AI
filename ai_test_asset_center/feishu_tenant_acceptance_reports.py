"""Safe public projection for connector tenant acceptance reports.

Acceptance reports are operator evidence, not customer-content storage. This module owns report
lookup, path confinement, bounded listing, and explicit public allowlists for both fields and
values. Raw report JSON and arbitrary diagnostic text are never returned directly, so future
checks cannot accidentally expose credentials, source content, cursors, or filesystem paths.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .enterprise_knowledge_center._common import ROOT
from .feishu_tenant_acceptance import (
    CONNECTOR_TENANT_ACCEPTANCE_SCHEMA,
    FEISHU_TENANT_ACCEPTANCE_SCHEMA,
)
from .real_project_onboarding import _safe_project_id

FEISHU_ACCEPTANCE_REPORT_INVENTORY_SCHEMA = (
    "qualibug.feishu-tenant-acceptance-report-inventory.v1"
)
CONNECTOR_ACCEPTANCE_REPORT_INVENTORY_SCHEMA = (
    "qualibug.connector-tenant-acceptance-report-inventory.v1"
)
_REPORT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[a-f0-9]{12}$")
_CONNECTOR_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_THRESHOLD_RE = re.compile(r"^(?:<=|>=) [0-9]+(?:\.[0-9]+)?$")
_ERROR_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}(?:Error|Exception)$")
_SAFE_OBSERVATION_LITERALS = {
    "AVAILABLE",
    "READ_ONLY",
    "COMPLETE",
    "NO_EXCEPTION",
    "RECOVERABLE_TWO_STAGE",
    "REMOTE_SNAPSHOT_CHANGED",
    "REPEATABLE_OR_EXPLAINED",
    ">= covered_resource_count",
}
_SAFE_OBSERVATION_KEYS = {
    "materialized_resource_count",
    "export_avoided_count",
    "covered_resource_count",
}


class FeishuTenantAcceptanceReportError(RuntimeError):
    """A tenant acceptance report could not be located or projected safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _connector_id(value: Any) -> str:
    connector = _text(value, 160)
    if not _CONNECTOR_ID_RE.fullmatch(connector):
        raise FeishuTenantAcceptanceReportError("acceptance_connector_id_invalid")
    return connector


def _report_id(value: Any) -> str:
    report_id = _text(value, 80)
    if not _REPORT_ID_RE.fullmatch(report_id):
        raise FeishuTenantAcceptanceReportError("acceptance_report_id_invalid")
    return report_id


def _reports_dir(project: str, connector: str, root: Path) -> Path:
    return (
        root
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_acceptance_reports"
        / connector
    )


def _read_report(
    path: Path,
    *,
    expected_schema: str = FEISHU_TENANT_ACCEPTANCE_SCHEMA,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeishuTenantAcceptanceReportError(
            "acceptance_report_not_found"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FeishuTenantAcceptanceReportError(
            "acceptance_report_unreadable"
        ) from exc
    if not isinstance(payload, dict):
        raise FeishuTenantAcceptanceReportError("acceptance_report_invalid")
    if _text(payload.get("schema"), 120) != expected_schema:
        raise FeishuTenantAcceptanceReportError("acceptance_report_schema_invalid")
    return payload


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _boolean_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_observation(value: Any, depth: int = 0) -> Any:
    if depth > 2:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = _text(value, 160)
        if (
            text in _SAFE_OBSERVATION_LITERALS
            or _THRESHOLD_RE.fullmatch(text)
            or _ERROR_TYPE_RE.fullmatch(text)
        ):
            return text
        return "REDACTED_UNSTRUCTURED_VALUE"
    if isinstance(value, Mapping):
        return {
            key: _safe_observation(value.get(key), depth + 1)
            for key in sorted(_SAFE_OBSERVATION_KEYS)
            if key in value
        }
    if isinstance(value, list):
        return [_safe_observation(item, depth + 1) for item in value[:20]]
    return None


def _public_run(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sync_epoch_fingerprint": _text(
            value.get("sync_epoch_fingerprint"), 128
        ),
        "status": _text(value.get("status"), 40),
        "duration_seconds": _number(value.get("duration_seconds")),
        "discovered_resource_count": _integer(
            value.get("discovered_resource_count")
        ),
        "covered_resource_count": _integer(value.get("covered_resource_count")),
        "materialized_resource_count": _integer(
            value.get("materialized_resource_count")
        ),
        "unchanged_resource_count": _integer(
            value.get("unchanged_resource_count")
        ),
        "unsupported_resource_count": _integer(
            value.get("unsupported_resource_count")
        ),
        "unknown_gap_count": _integer(value.get("unknown_gap_count")),
        "failure_count": _integer(value.get("failure_count")),
        "degraded_resource_count": _integer(
            value.get("degraded_resource_count")
        ),
        "export_avoided_count": _integer(value.get("export_avoided_count")),
        "knowledge_coverage_ratio": _number(
            value.get("knowledge_coverage_ratio")
        ),
        "knowledge_coverage_status": _text(
            value.get("knowledge_coverage_status"), 80
        ),
        "remote_discovery_complete": _boolean_or_none(
            value.get("remote_discovery_complete")
        ),
        "supported_materialization_complete": _boolean_or_none(
            value.get("supported_materialization_complete")
        ),
        "cursor_checkpoint_committed": _boolean_or_none(
            value.get("cursor_checkpoint_committed")
        ),
        "checkpoint_commit_protocol": _text(
            value.get("checkpoint_commit_protocol"), 80
        ),
        "customer_material_mutation_executed": _boolean_or_none(
            value.get("customer_material_mutation_executed")
        ),
        "source_content_persisted_in_adapter_receipt": _boolean_or_none(
            value.get("source_content_persisted_in_adapter_receipt")
        ),
        "next_cursor_fingerprint": _text(
            value.get("next_cursor_fingerprint"), 128
        ),
    }


def _public_check(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "check_id": _text(value.get("check_id"), 160),
        "status": _text(value.get("status"), 40),
        "severity": _text(value.get("severity"), 40),
        "observed": _safe_observation(value.get("observed")),
        "expected": _safe_observation(value.get("expected")),
        "detail": "",
        "arbitrary_diagnostic_text_returned": False,
    }


def _public_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "check_count": _integer(value.get("check_count")),
        "blocker_failure_count": _integer(value.get("blocker_failure_count")),
        "executed_run_count": _integer(value.get("executed_run_count")),
        "required_run_count": _integer(value.get("required_run_count")),
        "maximum_run_duration_seconds": _number(
            value.get("maximum_run_duration_seconds")
        ),
        "minimum_coverage_ratio": _number(value.get("minimum_coverage_ratio")),
        "maximum_discovered_resource_count": _integer(
            value.get("maximum_discovered_resource_count")
        ),
    }


def project_feishu_tenant_acceptance_report(
    payload: Mapping[str, Any],
    *,
    report_id: str,
    schema: str = FEISHU_TENANT_ACCEPTANCE_SCHEMA,
) -> dict[str, Any]:
    """Return the bounded public representation of one acceptance report."""
    connection = payload.get("connection")
    thresholds = payload.get("thresholds")
    summary = payload.get("summary")
    governance = payload.get("governance")
    return {
        "schema": schema,
        "report_id": report_id,
        "acceptance_id": _text(payload.get("acceptance_id"), 160),
        "project_id": _text(payload.get("project_id"), 160),
        "connector_instance_id": _text(
            payload.get("connector_instance_id"), 160
        ),
        "profile": _text(payload.get("profile"), 40),
        "verdict": _text(payload.get("verdict"), 40),
        "acceptance_ready": payload.get("acceptance_ready") is True,
        "started_at_utc": _text(payload.get("started_at_utc"), 80),
        "completed_at_utc": _text(payload.get("completed_at_utc"), 80),
        "thresholds": {
            "runs": _integer(
                thresholds.get("runs") if isinstance(thresholds, Mapping) else 0
            ),
            "min_discovered_resources": _integer(
                thresholds.get("min_discovered_resources")
                if isinstance(thresholds, Mapping)
                else 0
            ),
            "min_coverage_ratio": _number(
                thresholds.get("min_coverage_ratio")
                if isinstance(thresholds, Mapping)
                else 0
            ),
            "max_unsupported_ratio": _number(
                thresholds.get("max_unsupported_ratio")
                if isinstance(thresholds, Mapping)
                else 0
            ),
            "max_run_duration_seconds": _number(
                thresholds.get("max_run_duration_seconds")
                if isinstance(thresholds, Mapping)
                else 0
            ),
        },
        "connection": {
            "status": _text(
                connection.get("status") if isinstance(connection, Mapping) else "",
                80,
            ),
            "connector_type": _text(
                connection.get("connector_type")
                if isinstance(connection, Mapping)
                else "",
                80,
            ),
            "auth_mode": _text(
                connection.get("auth_mode")
                if isinstance(connection, Mapping)
                else "",
                80,
            ),
            "space_count": _integer(
                connection.get("space_count")
                if isinstance(connection, Mapping)
                else 0
            ),
            "network_side_effect": _text(
                connection.get("network_side_effect")
                if isinstance(connection, Mapping)
                else "",
                80,
            ),
            "credentials_persisted": _boolean_or_none(
                connection.get("credentials_persisted")
                if isinstance(connection, Mapping)
                else None
            ),
            "access_token_persisted": _boolean_or_none(
                connection.get("access_token_persisted")
                if isinstance(connection, Mapping)
                else None
            ),
        },
        "runs": [
            _public_run(row)
            for row in list(payload.get("runs") or [])[:10]
            if isinstance(row, Mapping)
        ],
        "checks": [
            _public_check(row)
            for row in list(payload.get("checks") or [])[:500]
            if isinstance(row, Mapping)
        ],
        "summary": _public_summary(
            summary if isinstance(summary, Mapping) else {}
        ),
        "execution_error": {
            "type": _text(
                dict(payload.get("execution_error") or {}).get("type"), 120
            )
        }
        if isinstance(payload.get("execution_error"), Mapping)
        else None,
        "governance": {
            "customer_material_access": _text(
                governance.get("customer_material_access")
                if isinstance(governance, Mapping)
                else "",
                80,
            ),
            "customer_material_mutation_executed": _boolean_or_none(
                governance.get("customer_material_mutation_executed")
                if isinstance(governance, Mapping)
                else None
            ),
            "deletion_policy": _text(
                governance.get("deletion_policy")
                if isinstance(governance, Mapping)
                else "",
                40,
            ),
            "customer_source_content_in_report": _boolean_or_none(
                governance.get("customer_source_content_in_report")
                if isinstance(governance, Mapping)
                else None
            ),
            "raw_cursor_values_in_report": _boolean_or_none(
                governance.get("raw_cursor_values_in_report")
                if isinstance(governance, Mapping)
                else None
            ),
            "credential_values_in_report": _boolean_or_none(
                governance.get("credential_values_in_report")
                if isinstance(governance, Mapping)
                else None
            ),
            "source_occurrence_content_loaded_by_acceptance": _boolean_or_none(
                governance.get("source_occurrence_content_loaded_by_acceptance")
                if isinstance(governance, Mapping)
                else None
            ),
            "existing_managed_sync_authority_reused": _boolean_or_none(
                governance.get("existing_managed_sync_authority_reused")
                if isinstance(governance, Mapping)
                else None
            ),
        },
        "source_content_returned": False,
        "raw_cursor_returned": False,
        "credential_values_returned": False,
        "filesystem_path_returned": False,
        "arbitrary_diagnostic_text_returned": False,
    }


def load_feishu_tenant_acceptance_report(
    project_id: str,
    connector_instance_id: str,
    report_id: str,
    *,
    root: Path | None = None,
    schema: str = FEISHU_TENANT_ACCEPTANCE_SCHEMA,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    safe_report_id = _report_id(report_id)
    path = _reports_dir(project, connector, resolved_root) / f"{safe_report_id}.json"
    payload = _read_report(path, expected_schema=schema)
    if _text(payload.get("project_id"), 160) != project:
        raise FeishuTenantAcceptanceReportError(
            "acceptance_report_project_mismatch"
        )
    if _text(payload.get("connector_instance_id"), 160) != connector:
        raise FeishuTenantAcceptanceReportError(
            "acceptance_report_connector_mismatch"
        )
    return project_feishu_tenant_acceptance_report(
        payload,
        report_id=safe_report_id,
        schema=schema,
    )


def list_feishu_tenant_acceptance_reports(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    limit: int = 20,
    schema: str = FEISHU_TENANT_ACCEPTANCE_SCHEMA,
    inventory_schema: str = FEISHU_ACCEPTANCE_REPORT_INVENTORY_SCHEMA,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    bounded_limit = max(1, min(int(limit), 100))
    directory = _reports_dir(project, connector, resolved_root)
    rows: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json"), reverse=True):
            if len(rows) >= bounded_limit:
                break
            if not _REPORT_ID_RE.fullmatch(path.stem):
                continue
            try:
                report = load_feishu_tenant_acceptance_report(
                    project,
                    connector,
                    path.stem,
                    root=resolved_root,
                    schema=schema,
                )
            except FeishuTenantAcceptanceReportError:
                continue
            rows.append(
                {
                    "report_id": report["report_id"],
                    "acceptance_id": report["acceptance_id"],
                    "profile": report["profile"],
                    "verdict": report["verdict"],
                    "acceptance_ready": report["acceptance_ready"],
                    "started_at_utc": report["started_at_utc"],
                    "completed_at_utc": report["completed_at_utc"],
                    "summary": dict(report["summary"]),
                    "source_content_returned": False,
                    "raw_cursor_returned": False,
                    "credential_values_returned": False,
                    "filesystem_path_returned": False,
                    "arbitrary_diagnostic_text_returned": False,
                }
            )
    return {
        "schema": inventory_schema,
        "project_id": project,
        "connector_instance_id": connector,
        "reports": rows,
        "summary": {
            "report_count": len(rows),
            "passing_report_count": sum(
                int(row.get("acceptance_ready") is True) for row in rows
            ),
            "failing_report_count": sum(
                int(row.get("acceptance_ready") is not True) for row in rows
            ),
        },
        "governance": {
            "raw_report_json_returned": False,
            "source_content_returned": False,
            "raw_cursor_returned": False,
            "credential_values_returned": False,
            "filesystem_paths_returned": False,
            "arbitrary_diagnostic_text_returned": False,
        },
    }


def latest_feishu_tenant_acceptance_summary(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    schema: str = FEISHU_TENANT_ACCEPTANCE_SCHEMA,
    inventory_schema: str = FEISHU_ACCEPTANCE_REPORT_INVENTORY_SCHEMA,
) -> dict[str, Any]:
    inventory = list_feishu_tenant_acceptance_reports(
        project_id,
        connector_instance_id,
        root=root,
        limit=1,
        schema=schema,
        inventory_schema=inventory_schema,
    )
    reports = list(inventory.get("reports") or [])
    if not reports:
        return {
            "status": "NOT_RUN",
            "acceptance_ready": False,
            "latest_report": None,
        }
    latest = dict(reports[0])
    return {
        "status": "PASS" if latest.get("acceptance_ready") is True else "FAIL",
        "acceptance_ready": latest.get("acceptance_ready") is True,
        "latest_report": latest,
    }


def load_connector_tenant_acceptance_report(
    project_id: str,
    connector_instance_id: str,
    report_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Load one generic acceptance report through the bounded Feishu-compatible projector."""
    return load_feishu_tenant_acceptance_report(
        project_id,
        connector_instance_id,
        report_id,
        root=root,
        schema=CONNECTOR_TENANT_ACCEPTANCE_SCHEMA,
    )


def list_connector_tenant_acceptance_reports(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List generic acceptance reports without returning raw report contents."""
    return list_feishu_tenant_acceptance_reports(
        project_id,
        connector_instance_id,
        root=root,
        limit=limit,
        schema=CONNECTOR_TENANT_ACCEPTANCE_SCHEMA,
        inventory_schema=CONNECTOR_ACCEPTANCE_REPORT_INVENTORY_SCHEMA,
    )


def latest_connector_tenant_acceptance_summary(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Project the latest generic acceptance verdict for inventory consumers."""
    return latest_feishu_tenant_acceptance_summary(
        project_id,
        connector_instance_id,
        root=root,
        schema=CONNECTOR_TENANT_ACCEPTANCE_SCHEMA,
        inventory_schema=CONNECTOR_ACCEPTANCE_REPORT_INVENTORY_SCHEMA,
    )


__all__ = [
    "CONNECTOR_ACCEPTANCE_REPORT_INVENTORY_SCHEMA",
    "FEISHU_ACCEPTANCE_REPORT_INVENTORY_SCHEMA",
    "FeishuTenantAcceptanceReportError",
    "latest_connector_tenant_acceptance_summary",
    "latest_feishu_tenant_acceptance_summary",
    "list_connector_tenant_acceptance_reports",
    "list_feishu_tenant_acceptance_reports",
    "load_connector_tenant_acceptance_report",
    "load_feishu_tenant_acceptance_report",
    "project_feishu_tenant_acceptance_report",
]
