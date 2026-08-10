"""Tenant/project-scoped collaboration metadata for persisted findings.

Automated finding evidence and verdict fields remain owned by the scan pipeline.
This module stores only human workflow metadata beside the existing SQLite
``findings`` SSOT and projects the stable persistence id back into display-ready
findings when the identity match is unambiguous.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import db_persistence as db_persist
from .project_runtime_primitives import safe_project_id

_ALLOWED_WORKFLOW_STATUSES = frozenset({"open", "resolved", "falsified"})
_ALLOWED_DISPOSITIONS = frozenset({"none", "accepted_risk", "false_positive"})


def _text(value: Any, limit: int = 0) -> str:
    text = str(value or "").strip()
    return text[:limit] if limit > 0 else text


def _ensure_table(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS finding_collaboration (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            finding_id TEXT NOT NULL,
            assignee TEXT DEFAULT '',
            fix_version TEXT DEFAULT '',
            developer_feedback TEXT DEFAULT '',
            disposition TEXT NOT NULL DEFAULT 'none',
            disposition_note TEXT DEFAULT '',
            external_issue_url TEXT DEFAULT '',
            updated_by TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (tenant_id, project_id, finding_id),
            FOREIGN KEY (finding_id) REFERENCES findings(id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_finding_collaboration_project "
        "ON finding_collaboration(tenant_id, project_id, updated_at)"
    )


def _validate_external_issue_url(value: Any) -> str:
    text = _text(value, 1000)
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("external_issue_url must be an absolute HTTP(S) URL")
    return text


def list_finding_collaboration(
    root: Path,
    tenant_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    db_persist.init_db(root)
    with db_persist._conn(root) as db:  # package-internal persistence composition
        db_persist._require_owned_project(db, tenant, project)
        _ensure_table(db)
        rows = db.execute(
            """
            SELECT
                f.id AS finding_id,
                f.status AS workflow_status,
                c.assignee,
                c.fix_version,
                c.developer_feedback,
                c.disposition,
                c.disposition_note,
                c.external_issue_url,
                c.updated_by,
                c.created_at AS collaboration_created_at,
                c.updated_at AS collaboration_updated_at
            FROM findings AS f
            LEFT JOIN finding_collaboration AS c
              ON c.tenant_id = f.tenant_id
             AND c.project_id = f.project_id
             AND c.finding_id = f.id
            WHERE f.tenant_id = ? AND f.project_id = ?
            ORDER BY f.created_at, f.id
            """,
            (tenant, project),
        ).fetchall()
        return [
            {
                "finding_persistence_id": row["finding_id"],
                "workflow_status": row["workflow_status"] or "open",
                "assignee": row["assignee"] or "",
                "fix_version": row["fix_version"] or "",
                "developer_feedback": row["developer_feedback"] or "",
                "disposition": row["disposition"] or "none",
                "disposition_note": row["disposition_note"] or "",
                "external_issue_url": row["external_issue_url"] or "",
                "updated_by": row["updated_by"] or "",
                "created_at": row["collaboration_created_at"] or "",
                "updated_at": row["collaboration_updated_at"] or "",
            }
            for row in rows
        ]


def update_finding_collaboration(
    root: Path,
    tenant_id: str,
    project_id: str,
    finding_id: str,
    patch: dict[str, Any],
    *,
    actor_name: str = "",
) -> dict[str, Any]:
    tenant = safe_project_id(tenant_id)
    project = safe_project_id(project_id)
    persisted_id = _text(finding_id, 160)
    if not persisted_id:
        raise ValueError("finding_persistence_id is required")
    if not isinstance(patch, dict):
        raise TypeError("collaboration patch must be an object")

    db_persist.init_db(root)
    with db_persist._conn(root) as db:  # package-internal persistence composition
        db_persist._require_owned_project(db, tenant, project)
        _ensure_table(db)
        finding = db.execute(
            "SELECT id, status FROM findings "
            "WHERE id = ? AND tenant_id = ? AND project_id = ?",
            (persisted_id, tenant, project),
        ).fetchone()
        if finding is None:
            raise KeyError("finding_persistence_id is outside the current tenant/project")

        workflow_status = _text(patch.get("workflow_status") if "workflow_status" in patch else finding["status"])
        if workflow_status not in _ALLOWED_WORKFLOW_STATUSES:
            raise ValueError("workflow_status must be open, resolved, or falsified")
        if "workflow_status" in patch:
            db.execute(
                "UPDATE findings SET status = ? "
                "WHERE id = ? AND tenant_id = ? AND project_id = ?",
                (workflow_status, persisted_id, tenant, project),
            )

        existing = db.execute(
            "SELECT * FROM finding_collaboration "
            "WHERE tenant_id = ? AND project_id = ? AND finding_id = ?",
            (tenant, project, persisted_id),
        ).fetchone()
        current = dict(existing) if existing is not None else {}

        def field(name: str, limit: int) -> str:
            return _text(patch[name], limit) if name in patch else _text(current.get(name), limit)

        disposition = field("disposition", 40) or "none"
        if disposition not in _ALLOWED_DISPOSITIONS:
            raise ValueError("disposition must be none, accepted_risk, or false_positive")

        assignee = field("assignee", 200)
        fix_version = field("fix_version", 200)
        developer_feedback = field("developer_feedback", 4000)
        disposition_note = field("disposition_note", 2000)
        external_issue_url = (
            _validate_external_issue_url(patch.get("external_issue_url"))
            if "external_issue_url" in patch
            else _text(current.get("external_issue_url"), 1000)
        )
        updated_by = _text(actor_name, 200)

        db.execute(
            """
            INSERT INTO finding_collaboration (
                tenant_id, project_id, finding_id, assignee, fix_version,
                developer_feedback, disposition, disposition_note,
                external_issue_url, updated_by, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(tenant_id, project_id, finding_id) DO UPDATE SET
                assignee = excluded.assignee,
                fix_version = excluded.fix_version,
                developer_feedback = excluded.developer_feedback,
                disposition = excluded.disposition,
                disposition_note = excluded.disposition_note,
                external_issue_url = excluded.external_issue_url,
                updated_by = excluded.updated_by,
                updated_at = datetime('now')
            """,
            (
                tenant,
                project,
                persisted_id,
                assignee,
                fix_version,
                developer_feedback,
                disposition,
                disposition_note,
                external_issue_url,
                updated_by,
            ),
        )

    items = list_finding_collaboration(root, tenant, project)
    for item in items:
        if item.get("finding_persistence_id") == persisted_id:
            return item
    raise RuntimeError("finding collaboration update was not readable after commit")


def _normalized_title(value: Any) -> str:
    text = _text(value, 500).lower()
    text = re.sub(r"^(\[[^\]]*\]\s*)+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _display_method_path(finding: dict[str, Any]) -> tuple[str, str]:
    reproduction = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    technical = finding.get("technical_details") if isinstance(finding.get("technical_details"), dict) else {}
    endpoint = technical.get("api_endpoint") if isinstance(technical.get("api_endpoint"), dict) else {}
    method = _text(
        finding.get("repro_method")
        or reproduction.get("method")
        or endpoint.get("method")
    ).upper()
    path = _text(
        finding.get("repro_path")
        or reproduction.get("path")
        or endpoint.get("path")
    )
    return method, path


def _row_method_path(finding: dict[str, Any]) -> tuple[str, str]:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    return (
        _text(finding.get("_api_method") or evidence.get("method")).upper(),
        _text(finding.get("_api_path") or evidence.get("path")),
    )


def _evidence_hash(value: dict[str, Any]) -> str:
    evidence = value.get("evidence") if isinstance(value.get("evidence"), dict) else {}
    proof = value.get("proof") if isinstance(value.get("proof"), dict) else {}
    for candidate in (proof.get("hash"), evidence.get("hash"), evidence.get("evidence_hash")):
        text = _text(candidate, 256)
        if text:
            return text
    return ""


def _match_persisted_finding(
    display_finding: dict[str, Any],
    persisted_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    display_hash = _evidence_hash(display_finding)
    if display_hash:
        hash_matches = [row for row in persisted_rows if _evidence_hash(row) == display_hash]
        if len(hash_matches) == 1:
            return hash_matches[0]

    method, path = _display_method_path(display_finding)
    if method or path:
        endpoint_matches = [
            row for row in persisted_rows
            if _row_method_path(row) == (method, path)
        ]
        if len(endpoint_matches) == 1:
            return endpoint_matches[0]
        if endpoint_matches:
            display_title = _normalized_title(display_finding.get("title"))
            title_matches = [
                row for row in endpoint_matches
                if _normalized_title(row.get("title")) == display_title
            ]
            if len(title_matches) == 1:
                return title_matches[0]

    display_title = _normalized_title(display_finding.get("title"))
    if display_title:
        title_matches = [
            row for row in persisted_rows
            if _normalized_title(row.get("title")) == display_title
        ]
        if len(title_matches) == 1:
            return title_matches[0]
    return None


def annotate_command_center_collaboration(
    payload: dict[str, Any],
    root: Path,
    tenant_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Attach workflow metadata without changing delivery/evidence authority."""

    if not isinstance(payload, dict):
        return payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    defects = data.get("defects") if isinstance(data.get("defects"), list) else []
    if not defects:
        return payload

    persisted_rows = db_persist.get_cumulative_findings(
        root,
        tenant_id,
        project_id,
        include_resolved=True,
    )
    collaboration_rows = list_finding_collaboration(root, tenant_id, project_id)
    collaboration_by_id = {
        _text(row.get("finding_persistence_id")): row
        for row in collaboration_rows
        if _text(row.get("finding_persistence_id"))
    }

    annotated: list[Any] = []
    for raw in defects:
        if not isinstance(raw, dict):
            annotated.append(raw)
            continue
        finding = dict(raw)
        persisted = _match_persisted_finding(finding, persisted_rows)
        if persisted is not None:
            persistence_id = _text(persisted.get("risk_id"), 160)
            if persistence_id:
                finding["finding_persistence_id"] = persistence_id
                collaboration = collaboration_by_id.get(persistence_id, {})
                finding["workflow_status"] = _text(
                    collaboration.get("workflow_status") or persisted.get("status") or "open"
                )
                finding["collaboration"] = {
                    key: collaboration.get(key, "")
                    for key in (
                        "assignee",
                        "fix_version",
                        "developer_feedback",
                        "disposition",
                        "disposition_note",
                        "external_issue_url",
                        "updated_by",
                        "updated_at",
                    )
                }
        annotated.append(finding)

    data["defects"] = annotated
    data["risks"] = annotated
    payload["data"] = data
    return payload


__all__ = [
    "annotate_command_center_collaboration",
    "list_finding_collaboration",
    "update_finding_collaboration",
]
