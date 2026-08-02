"""Source-bound remote ACL snapshots and local visibility decisions.

The connector sync authority owns the durable registry.  This module only owns
the ACL contract and its projections so every connector uses the same path:
adapter evidence -> connector sync receipt -> Source Occurrence observation ->
local visibility decision.

An absent or incomplete remote ACL is not interpreted as public access.  It is
an explicit fail-closed state.  Raw remote principal references are never
persisted or returned to ordinary projections; only project/connector-scoped
fingerprints are retained.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

CONNECTOR_ACL_SNAPSHOT_SCHEMA = "qualibug.connector-acl-snapshot.v1"
CONNECTOR_ACL_DECISION_SCHEMA = "qualibug.connector-acl-decision.v1"

ACL_VISIBILITIES = frozenset({"PRIVATE", "PROJECT", "TENANT", "PUBLIC"})
ACL_AVAILABILITIES = frozenset(
    {
        "AVAILABLE",
        "PERMISSION_DENIED",
        "REMOTE_DELETED",
        "REMOTE_UNAVAILABLE",
        "UNKNOWN",
    }
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SOURCE_REF_RE = re.compile(r"^connector://([^/]+)/([^/]+)/(.+)$")
_MANAGER_ROLES = frozenset({"knowledge_admin", "project_owner", "qa_lead", "admin"})


class ConnectorAclError(RuntimeError):
    """Connector ACL evidence or local visibility policy is invalid."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _identifier(value: Any, field: str) -> str:
    result = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ConnectorAclError(f"{field}_invalid")
    return result


def _now() -> str:
    from .enterprise_knowledge_center._utils import _now as knowledge_now

    return knowledge_now()


def _source_ref_connector(source_ref: str) -> str:
    match = _SOURCE_REF_RE.fullmatch(_text(source_ref, 2000))
    if match is None:
        raise ConnectorAclError("connector_acl_source_ref_invalid")
    return unquote(match.group(1))


def fingerprint_connector_principal(
    project_id: str, connector_instance_id: str, principal_ref: Any
) -> str:
    """Return the internal comparison value for one source-declared principal.

    The caller is a trusted authentication/connector boundary.  The returned
    value is safe for ordinary projections; the input reference is never
    persisted by this authority.
    """
    project = _identifier(project_id, "project_id")
    connector = _identifier(connector_instance_id, "connector_instance_id")
    principal = _text(principal_ref, 2000)
    if not principal:
        raise ConnectorAclError("connector_acl_principal_ref_required")
    material = json.dumps(
        {
            "version": "qualibug.connector-principal-fingerprint.v1",
            "project_id": project,
            "connector_instance_id": connector,
            "principal_ref": principal,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _principal_rows(
    project: str, connector: str, value: Any
) -> tuple[list[dict[str, str]], str]:
    if not isinstance(value, list):
        return [], "ACL_PRINCIPALS_INVALID"
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value:
        if isinstance(raw, Mapping):
            reference = _text(
                raw.get("principal_ref")
                or raw.get("ref")
                or raw.get("id")
                or raw.get("subject"),
                2000,
            )
            principal_type = _text(raw.get("type") or raw.get("kind"), 80) or "UNKNOWN"
        else:
            reference = _text(raw, 2000)
            principal_type = "UNKNOWN"
        if not reference:
            return [], "ACL_PRINCIPAL_REF_MISSING"
        fingerprint = fingerprint_connector_principal(project, connector, reference)
        if fingerprint in seen:
            return [], "ACL_PRINCIPALS_DUPLICATE"
        seen.add(fingerprint)
        rows.append(
            {
                "principal_fingerprint": fingerprint,
                "principal_type": principal_type[:80],
            }
        )
    return rows, ""


def _acl_input(raw: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    candidate = raw.get("acl")
    if candidate is None:
        candidate = raw.get("remote_acl")
    if candidate is None:
        direct = {
            key: raw.get(key)
            for key in (
                "acl_version",
                "acl_fingerprint",
                "principals",
                "visibility",
                "inherited_from",
                "captured_at",
                "complete",
                "availability",
                "remote_availability",
            )
            if key in raw
        }
        candidate = direct if direct else None
    if candidate is None:
        return {}, "ACL_EVIDENCE_MISSING"
    if not isinstance(candidate, Mapping):
        return {}, "ACL_EVIDENCE_INVALID"
    return dict(candidate), ""


def normalize_connector_acl_snapshot(
    project_id: str,
    connector_instance_id: str,
    *,
    source_ref: str,
    raw: Mapping[str, Any] | None,
    availability_default: str = "UNKNOWN",
    captured_at_default: str = "",
) -> dict[str, Any]:
    """Normalize adapter ACL evidence without retaining raw principal values."""
    project = _identifier(project_id, "project_id")
    connector = _identifier(connector_instance_id, "connector_instance_id")
    ref = _text(source_ref, 2000)
    if not ref:
        raise ConnectorAclError("connector_acl_source_ref_required")
    if not ref.startswith("connector://"):
        raise ConnectorAclError("connector_acl_source_ref_not_connector_owned")
    if _source_ref_connector(ref) != connector:
        raise ConnectorAclError("connector_acl_connector_mismatch")
    source = dict(raw or {})
    acl, input_reason = _acl_input(source)
    availability = _text(
        acl.get("availability") or acl.get("remote_availability")
        or availability_default,
        60,
    ).upper() or "UNKNOWN"
    if availability not in ACL_AVAILABILITIES:
        availability = "UNKNOWN"
        input_reason = input_reason or "ACL_AVAILABILITY_INVALID"
    acl_version = _text(acl.get("acl_version") or acl.get("version"), 240)
    if not acl_version:
        acl_version = _text(acl.get("acl_fingerprint"), 240)
    visibility = _text(acl.get("visibility"), 40).upper()
    visibility_valid = visibility in ACL_VISIBILITIES
    if not visibility_valid:
        visibility = "PRIVATE"
        input_reason = input_reason or "ACL_VISIBILITY_MISSING"
    principals, principal_reason = _principal_rows(project, connector, acl.get("principals", []))
    if principal_reason:
        input_reason = input_reason or principal_reason
    inherited_from = _text(acl.get("inherited_from"), 2000)
    captured_at = _text(acl.get("captured_at"), 80) or _text(captured_at_default, 80)
    if not captured_at:
        input_reason = input_reason or "ACL_CAPTURE_TIME_MISSING"
    complete = acl.get("complete") is True
    if not complete:
        input_reason = input_reason or "ACL_MARKED_INCOMPLETE"
    if not acl_version:
        input_reason = input_reason or "ACL_VERSION_MISSING"
    if visibility == "PRIVATE" and not principals:
        input_reason = input_reason or "ACL_PRIVATE_PRINCIPALS_MISSING"
    if availability != "AVAILABLE":
        propagation_allowed = False
    else:
        propagation_allowed = complete and not input_reason
    fingerprint_material = {
        "schema": CONNECTOR_ACL_SNAPSHOT_SCHEMA,
        "source_ref": ref,
        "acl_version": acl_version,
        "principals": principals,
        "visibility": visibility,
        "inherited_from": inherited_from,
        "complete": complete,
        "availability": availability,
    }
    snapshot = {
        **fingerprint_material,
        "captured_at": captured_at,
        "project_id": project,
        "connector_instance_id": connector,
        "acl_fingerprint": hashlib.sha256(
            json.dumps(
                fingerprint_material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
        "evidence_status": "COMPLETE" if not input_reason else "INCOMPLETE",
        "incomplete_reason": input_reason,
        "propagation_allowed": propagation_allowed,
        "raw_principals_persisted": False,
        "raw_principals_returned": False,
    }
    return snapshot


def acl_observation_metadata(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Project ACL state into scalar Source Occurrence transport metadata."""
    return {
        "acl_version": _text(snapshot.get("acl_version"), 240),
        "acl_fingerprint": _text(snapshot.get("acl_fingerprint"), 128),
        "acl_visibility": _text(snapshot.get("visibility"), 40),
        "acl_complete": bool(snapshot.get("complete")),
        "acl_availability": _text(snapshot.get("availability"), 60),
        "acl_evidence_status": _text(snapshot.get("evidence_status"), 40),
        "acl_propagation_allowed": bool(snapshot.get("propagation_allowed")),
    }


def reconcile_connector_acl_snapshots(
    registry: dict[str, Any],
    *,
    project_id: str,
    connector_instance_id: str,
    observations: list[Mapping[str, Any]],
    sync_epoch_id: str,
    actor: Mapping[str, Any],
    captured_at: str = "",
) -> dict[str, Any]:
    """Reconcile current ACL snapshots and append an immutable audit history."""
    project = _identifier(project_id, "project_id")
    connector = _identifier(connector_instance_id, "connector_instance_id")
    if not isinstance(observations, list):
        raise ConnectorAclError("connector_acl_observations_must_be_list")
    current = [
        dict(row)
        for row in registry.get("source_acl_snapshots") or []
        if isinstance(row, Mapping)
        and _text(row.get("connector_instance_id"), 160) == connector
    ]
    other_current = [
        dict(row)
        for row in registry.get("source_acl_snapshots") or []
        if isinstance(row, Mapping)
        and _text(row.get("connector_instance_id"), 160) != connector
    ]
    by_ref = {_text(row.get("source_ref"), 2000): row for row in current}
    changed: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(observations):
        if not isinstance(raw, Mapping):
            raise ConnectorAclError(f"connector_acl_observation_invalid:{index}")
        source_ref = _text(raw.get("source_ref"), 2000)
        if not source_ref:
            raise ConnectorAclError(f"connector_acl_source_ref_required:{index}")
        snapshot = normalize_connector_acl_snapshot(
            project,
            connector,
            source_ref=source_ref,
            raw=raw,
            availability_default=_text(raw.get("availability_default"), 60)
            or "UNKNOWN",
            captured_at_default=captured_at or _now(),
        )
        snapshot["sync_epoch_id"] = _text(sync_epoch_id, 160)
        snapshot["source_occurrence_id"] = _text(raw.get("source_occurrence_id"), 300)
        previous = by_ref.get(source_ref)
        if previous is None or any(
            previous.get(key) != snapshot.get(key)
            for key in (
                "acl_fingerprint",
                "availability",
                "evidence_status",
                "propagation_allowed",
            )
        ):
            changed.append(
                {
                    "source_ref": source_ref,
                    "source_occurrence_id": snapshot.get("source_occurrence_id"),
                    "previous_acl_fingerprint": _text(
                        (previous or {}).get("acl_fingerprint"), 128
                    ),
                    "current_acl_fingerprint": snapshot.get("acl_fingerprint"),
                    "previous_availability": _text(
                        (previous or {}).get("availability"), 60
                    ),
                    "current_availability": snapshot.get("availability"),
                    "reason_code": (
                        "ACL_SNAPSHOT_CREATED"
                        if previous is None
                        else "ACL_SNAPSHOT_CHANGED"
                    ),
                }
            )
        by_ref[source_ref] = snapshot
        normalized.append(snapshot)
    registry["source_acl_snapshots"] = other_current + sorted(
        by_ref.values(), key=lambda row: _text(row.get("source_ref"), 2000)
    )
    history = [
        dict(row)
        for row in registry.get("source_acl_history") or []
        if isinstance(row, Mapping)
    ]
    history.extend(copy.deepcopy(normalized))
    registry["source_acl_history"] = history[-5000:]
    observed_at = captured_at or _now()
    registry.setdefault("audit_events", []).append(
        {
            "event": "reconcile_connector_acl_snapshots",
            "at_utc": observed_at,
            "actor": dict(actor),
            "connector_instance_id": connector,
            "sync_epoch_id": _text(sync_epoch_id, 160),
            "snapshot_count": len(normalized),
            "changed_count": len(changed),
            "incomplete_count": sum(
                row.get("evidence_status") != "COMPLETE" for row in normalized
            ),
            "permission_denied_count": sum(
                row.get("availability") == "PERMISSION_DENIED" for row in normalized
            ),
            "remote_deleted_count": sum(
                row.get("availability") == "REMOTE_DELETED" for row in normalized
            ),
            "raw_principals_persisted": False,
        }
    )
    return {
        "schema": CONNECTOR_ACL_SNAPSHOT_SCHEMA,
        "status": "RECORDED",
        "project_id": project,
        "connector_instance_id": connector,
        "sync_epoch_id": _text(sync_epoch_id, 160),
        "snapshot_count": len(normalized),
        "changed_count": len(changed),
        "incomplete_count": sum(
            row.get("evidence_status") != "COMPLETE" for row in normalized
        ),
        "propagation_allowed_count": sum(
            bool(row.get("propagation_allowed")) for row in normalized
        ),
        "permission_denied_count": sum(
            row.get("availability") == "PERMISSION_DENIED" for row in normalized
        ),
        "remote_deleted_count": sum(
            row.get("availability") == "REMOTE_DELETED" for row in normalized
        ),
        "changed": changed,
        "raw_principals_persisted": False,
    }


def _actor_project_scope(project: str, actor: Mapping[str, Any]) -> bool:
    if _text(actor.get("project_id"), 160) == project:
        return True
    values = actor.get("project_ids") or actor.get("projects") or []
    return isinstance(values, list) and project in {_text(value, 160) for value in values}


def _actor_principal_fingerprints(actor: Mapping[str, Any]) -> set[str]:
    values = actor.get("connector_principal_fingerprints") or actor.get(
        "principal_fingerprints"
    ) or []
    if isinstance(values, str):
        values = [values]
    return {
        _text(value, 128).lower()
        for value in values
        if re.fullmatch(r"[0-9a-f]{64}", _text(value, 128).lower())
    }


def _load_acl_registry(project: str, root: Path) -> dict[str, Any]:
    from .connector_sync_authority import _load_connector_registry

    return _load_connector_registry(project, root)


def _save_acl_registry(project: str, root: Path, registry: dict[str, Any]) -> None:
    from .connector_sync_authority import _save_connector_registry

    _save_connector_registry(project, root, registry)


def record_connector_project_share(
    project_id: str,
    *,
    source_ref: str,
    root: Path,
    actor: Mapping[str, Any],
    enabled: bool = True,
) -> dict[str, Any]:
    """Record an explicit local project-share override by an authorized manager."""
    project = _identifier(project_id, "project_id")
    source = _text(source_ref, 2000)
    connector = _source_ref_connector(source)
    role = _text(actor.get("role"), 80)
    if role not in _MANAGER_ROLES:
        raise ConnectorAclError("connector_acl_project_share_requires_manager")
    registry = _load_acl_registry(project, root)
    snapshot = next(
        (
            row
            for row in registry.get("source_acl_snapshots") or []
            if isinstance(row, Mapping)
            and _text(row.get("source_ref"), 2000) == source
            and _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if snapshot is None:
        raise ConnectorAclError("connector_acl_snapshot_required_for_project_share")
    if enabled and snapshot.get("propagation_allowed") is not True:
        raise ConnectorAclError("connector_acl_project_share_requires_complete_acl")
    overrides = [
        dict(row)
        for row in registry.get("source_acl_overrides") or []
        if isinstance(row, Mapping)
        and _text(row.get("source_ref"), 2000) != source
    ]
    if enabled:
        overrides.append(
            {
                "source_ref": source,
                "connector_instance_id": connector,
                "visibility": "PROJECT",
                "enabled": True,
                "updated_at_utc": _now(),
                "updated_by": {
                    "name": _text(actor.get("name") or actor.get("id"), 160),
                    "role": role,
                },
            }
        )
    registry["source_acl_overrides"] = overrides
    registry.setdefault("audit_events", []).append(
        {
            "event": "set_connector_source_project_share",
            "at_utc": _now(),
            "actor": {
                "name": _text(actor.get("name") or actor.get("id"), 160),
                "role": role,
            },
            "connector_instance_id": connector,
            "source_ref": source,
            "enabled": bool(enabled),
            "raw_principals_returned": False,
        }
    )
    _save_acl_registry(project, root, registry)
    return {
        "schema": CONNECTOR_ACL_DECISION_SCHEMA,
        "status": "RECORDED",
        "project_id": project,
        "source_ref": source,
        "visibility": "PROJECT" if enabled else "",
        "enabled": bool(enabled),
        "raw_remote_principal_returned": False,
    }


def connector_source_visibility_decision(
    project_id: str,
    *,
    source_ref: str,
    actor: Mapping[str, Any] | None,
    root: Path,
) -> dict[str, Any]:
    """Evaluate one source occurrence using the latest source-bound ACL."""
    project = _identifier(project_id, "project_id")
    source = _text(source_ref, 2000)
    connector = _source_ref_connector(source)
    registry = _load_acl_registry(project, root)
    snapshot = next(
        (
            dict(row)
            for row in registry.get("source_acl_snapshots") or []
            if isinstance(row, Mapping)
            and _text(row.get("source_ref"), 2000) == source
            and _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    base = {
        "schema": CONNECTOR_ACL_DECISION_SCHEMA,
        "project_id": project,
        "source_ref": source,
        "connector_instance_id": connector,
        "allowed": False,
        "reason_code": "",
        "visibility": "PRIVATE",
        "acl_fingerprint": "",
        "acl_version": "",
        "raw_remote_principal_returned": False,
        "historical_source_retained": True,
    }
    if snapshot is None:
        base["reason_code"] = "ACL_SNAPSHOT_MISSING"
        return base
    base.update(
        {
            "visibility": _text(snapshot.get("visibility"), 40) or "PRIVATE",
            "acl_fingerprint": _text(snapshot.get("acl_fingerprint"), 128),
            "acl_version": _text(snapshot.get("acl_version"), 240),
            "availability": _text(snapshot.get("availability"), 60) or "UNKNOWN",
            "evidence_status": _text(snapshot.get("evidence_status"), 40),
        }
    )
    if base["availability"] == "PERMISSION_DENIED":
        base["reason_code"] = "REMOTE_PERMISSION_DENIED"
        return base
    if base["availability"] == "REMOTE_DELETED":
        base["reason_code"] = "REMOTE_DELETED"
        return base
    if base["availability"] == "REMOTE_UNAVAILABLE":
        base["reason_code"] = "REMOTE_UNAVAILABLE"
        return base
    if snapshot.get("propagation_allowed") is not True:
        base["reason_code"] = _text(snapshot.get("incomplete_reason"), 160) or "ACL_INCOMPLETE"
        return base
    actor_value = actor if isinstance(actor, Mapping) else {}
    if not _text(actor_value.get("name") or actor_value.get("id"), 160):
        base["reason_code"] = "ACTOR_REQUIRED"
        return base
    overrides = [
        row
        for row in registry.get("source_acl_overrides") or []
        if isinstance(row, Mapping)
        and _text(row.get("source_ref"), 2000) == source
        and row.get("enabled") is True
    ]
    if overrides:
        base.update({"allowed": True, "reason_code": "LOCAL_PROJECT_SHARE"})
        return base
    visibility = base["visibility"]
    if visibility == "PUBLIC":
        base.update({"allowed": True, "reason_code": "REMOTE_PUBLIC"})
    elif visibility == "TENANT":
        base.update(
            {
                "allowed": bool(_text(actor_value.get("tenant_id"), 160)),
                "reason_code": "REMOTE_TENANT_SCOPE"
                if _text(actor_value.get("tenant_id"), 160)
                else "TENANT_SCOPE_REQUIRED",
            }
        )
    elif visibility == "PROJECT":
        base.update(
            {
                "allowed": _actor_project_scope(project, actor_value),
                "reason_code": "REMOTE_PROJECT_SCOPE"
                if _actor_project_scope(project, actor_value)
                else "PROJECT_SCOPE_REQUIRED",
            }
        )
    else:
        allowed = bool(
            _actor_principal_fingerprints(actor_value).intersection(
                {
                    _text(row.get("principal_fingerprint"), 128).lower()
                    for row in snapshot.get("principals") or []
                    if isinstance(row, Mapping)
                }
            )
        )
        base.update(
            {
                "allowed": allowed,
                "reason_code": "REMOTE_PRINCIPAL_MATCH"
                if allowed
                else "REMOTE_PRINCIPAL_NOT_MATCHED",
            }
        )
    return base


def _source_keys(row: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in ("source_id", "source_occurrence_id", "canonical_source_id", "source_ref"):
        value = _text(row.get(key), 2000)
        if value:
            keys.add(value)
    for key in ("source_ids", "source_occurrence_ids", "source_refs"):
        values = row.get(key) or []
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            keys.update(_text(value, 2000) for value in values if _text(value, 2000))
    return keys


def filter_connector_sources_for_actor(
    project_id: str,
    sources: list[Mapping[str, Any]],
    *,
    actor: Mapping[str, Any] | None,
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Filter a user-facing source inventory and return an audit-safe summary."""
    project = _identifier(project_id, "project_id")
    visible: list[dict[str, Any]] = []
    denied = 0
    decisions: list[dict[str, Any]] = []
    denied_keys: set[str] = set()
    for raw in sources:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        source_ref = _text(row.get("source_ref"), 2000)
        if not source_ref.startswith("connector://"):
            visible.append(row)
            continue
        decision = connector_source_visibility_decision(
            project,
            source_ref=source_ref,
            actor=actor,
            root=root,
        )
        decisions.append(
            {
                "source_identity_fingerprint": hashlib.sha256(
                    source_ref.encode("utf-8")
                ).hexdigest()[:32],
                "allowed": bool(decision.get("allowed")),
                "reason_code": decision.get("reason_code"),
                "visibility": decision.get("visibility"),
            }
        )
        if decision.get("allowed") is True:
            visible.append(row)
        else:
            denied += 1
            denied_keys.update(_source_keys(row))
    return visible, {
        "schema": CONNECTOR_ACL_DECISION_SCHEMA,
        "project_id": project,
        "visible_count": len(visible),
        "denied_count": denied,
        "decisions": decisions,
        "raw_remote_principals_returned": False,
        "denied_source_keys": sorted(denied_keys),
    }


def _row_has_denied_provenance(row: Mapping[str, Any], denied_keys: set[str]) -> bool:
    if _source_keys(row).intersection(denied_keys):
        return True
    for key in ("evidence", "lineage", "source", "fact_evidence", "addresses"):
        nested = row.get(key)
        if isinstance(nested, Mapping) and _row_has_denied_provenance(nested, denied_keys):
            return True
        if isinstance(nested, list) and any(
            isinstance(item, Mapping) and _row_has_denied_provenance(item, denied_keys)
            for item in nested
        ):
            return True
    return False


def _redact_denied_nested(value: Any, denied_keys: set[str]) -> Any:
    """Remove denied source-backed rows at every nested projection boundary."""
    if isinstance(value, list):
        redacted: list[Any] = []
        for item in value:
            if isinstance(item, Mapping) and _row_has_denied_provenance(item, denied_keys):
                continue
            redacted.append(_redact_denied_nested(item, denied_keys))
        return redacted
    if isinstance(value, Mapping):
        if _row_has_denied_provenance(value, denied_keys):
            return None
        output: dict[str, Any] = {}
        for key, child in value.items():
            projected = _redact_denied_nested(child, denied_keys)
            if projected is not None:
                output[str(key)] = projected
        return output
    return value


def _connector_identity_values(value: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("source_ref", "source_refs", "source_id", "source_ids"):
        raw = value.get(key)
        candidates = raw if isinstance(raw, list) else [raw]
        for candidate in candidates:
            if isinstance(candidate, Mapping):
                candidate = (
                    candidate.get("source_ref")
                    or candidate.get("ref")
                    or candidate.get("source_id")
                )
            text = _text(candidate, 2000)
            if text.startswith("connector://"):
                values.append(text)
    return values


def _contains_connector_identity(value: Any) -> bool:
    if isinstance(value, Mapping):
        if _connector_identity_values(value):
            return True
        return any(_contains_connector_identity(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_connector_identity(child) for child in value)
    return False


def _publicize_connector_identity(value: Any) -> Any:
    """Hide connector remote identities at the ordinary knowledge-asset boundary."""
    if isinstance(value, list):
        return [_publicize_connector_identity(item) for item in value]
    if not isinstance(value, Mapping):
        return value

    direct_refs = _connector_identity_values(value)
    connector_backed = bool(direct_refs) or _contains_connector_identity(value)
    sensitive_keys = {
        "source_ref",
        "remote_resource_id",
        "_remote_resource_id",
        "source_occurrence_id",
        "source_occurrence_ids",
        "source_id",
        "source_ids",
        "canonical_url",
    }
    output: dict[str, Any] = {}
    for key, child in value.items():
        key_text = str(key)
        if connector_backed and key_text in sensitive_keys:
            continue
        if key_text in {"source_ref", "source_refs", "source_id", "source_ids"}:
            if key_text == "source_ref" and _text(child, 2000).startswith("connector://"):
                continue
            if key_text in {"source_refs", "source_ids"} and isinstance(child, list):
                filtered = []
                for item in child:
                    item_text = _text(item, 2000)
                    if item_text.startswith("connector://"):
                        continue
                    filtered.append(_publicize_connector_identity(item))
                output[key_text] = filtered
                continue
        output[key_text] = _publicize_connector_identity(child)
    if direct_refs:
        fingerprints = sorted(
            {hashlib.sha256(ref.encode("utf-8")).hexdigest()[:32] for ref in direct_refs}
        )
        output["source_identity_fingerprints"] = fingerprints
        output["source_origin"] = "ONLINE_CONNECTOR"
        output["remote_resource_identities_returned"] = False
        output["source_refs_returned"] = False
    return output


def filter_connector_asset_for_actor(
    project_id: str,
    asset: Mapping[str, Any],
    *,
    actor: Mapping[str, Any] | None,
    root: Path,
) -> dict[str, Any]:
    """Redact connector-backed source rows and derived rows for one user.

    This is a projection only.  It never deletes registry rows or historical
    bytes.  Derived rows without a denied source lineage remain available; the
    source-backed rows are removed before the response reaches the frontend.
    """
    result = copy.deepcopy(dict(asset))
    inventory = result.get("source_inventory") or result.get("sources") or []
    if not isinstance(inventory, list):
        return result
    visible, summary = filter_connector_sources_for_actor(
        project_id,
        [row for row in inventory if isinstance(row, Mapping)],
        actor=actor,
        root=root,
    )
    denied_keys = set(summary.pop("denied_source_keys", []))
    for key in ("source_inventory", "sources", "canonical_source_inventory", "source_occurrence_inventory"):
        rows = result.get(key)
        if isinstance(rows, list):
            result[key] = [
                row
                for row in rows
                if isinstance(row, Mapping) and not _row_has_denied_provenance(row, denied_keys)
            ]
    for key, value in list(result.items()):
        if key in {"source_inventory", "sources", "canonical_source_inventory", "source_occurrence_inventory", "summary", "governance"}:
            continue
        projected = _redact_denied_nested(value, denied_keys)
        if projected is not None:
            result[key] = projected
    result["source_inventory"] = visible
    result["sources"] = visible
    result["acl_visibility_projection"] = {
        **summary,
        "denied_source_keys_returned": False,
    }
    public_summary = result.get("summary")
    if isinstance(public_summary, Mapping):
        public_summary = dict(public_summary)
        public_summary["active_source_count"] = len(visible)
        result["summary"] = public_summary
    projected = _publicize_connector_identity(result)
    if not isinstance(projected, dict):
        raise ConnectorAclError("connector_acl_public_projection_invalid")
    return projected


__all__ = [
    "ACL_AVAILABILITIES",
    "ACL_VISIBILITIES",
    "CONNECTOR_ACL_DECISION_SCHEMA",
    "CONNECTOR_ACL_SNAPSHOT_SCHEMA",
    "ConnectorAclError",
    "acl_observation_metadata",
    "connector_source_visibility_decision",
    "filter_connector_asset_for_actor",
    "filter_connector_sources_for_actor",
    "fingerprint_connector_principal",
    "normalize_connector_acl_snapshot",
    "reconcile_connector_acl_snapshots",
    "record_connector_project_share",
]
