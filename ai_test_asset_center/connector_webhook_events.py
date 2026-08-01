"""Configuration-driven webhook intake for managed connector synchronization.

The webhook boundary authenticates an event, records a bounded fingerprint-only ledger, and
then invokes the existing managed connector sync authority.  It never interprets provider
payloads or mutates source material directly.  Provider-specific header names and signing
layouts are explicit instance policy, so an unfamiliar enterprise integration can use the
same contract without adding another adapter parser.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import quote

from .connector_auto_sync import run_managed_connector_sync
from .connector_connection_profiles import (
    resolve_connector_connection_profile,
)
from .connector_registry import (
    ConnectorRegistryError,
    build_default_connector_registry,
)
from .connector_sync_authority import list_connector_instances
from .enterprise_knowledge_center._common import ROOT
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)
from .enterprise_knowledge_center._utils import _now, _redact_text, _short_hash
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .real_project_onboarding import _safe_project_id

CONNECTOR_WEBHOOK_POLICY_SCHEMA = "qualibug.connector-webhook-policy.v1"
CONNECTOR_WEBHOOK_LEDGER_SCHEMA = "qualibug.connector-webhook-ledger.v1"
CONNECTOR_WEBHOOK_PROJECTION_SCHEMA = "qualibug.connector-webhook-projection.v1"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,160}$")
_SIGNATURE_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_POLICY_KEYS = {
    "enabled",
    "signature_header",
    "event_id_header",
    "timestamp_header",
    "sequence_header",
    "algorithm",
    "encoding",
    "signed_payload",
    "signature_prefix",
    "max_age_seconds",
    "future_skew_seconds",
    "event_retention_count",
}
_DEFAULT_POLICY: dict[str, Any] = {
    "enabled": False,
    "signature_header": "X-Webhook-Signature",
    "event_id_header": "X-Webhook-Event-Id",
    "timestamp_header": "X-Webhook-Timestamp",
    "sequence_header": "",
    "algorithm": "hmac-sha256",
    "encoding": "hex",
    "signed_payload": "timestamp.body",
    "signature_prefix": "",
    "max_age_seconds": 300,
    "future_skew_seconds": 30,
    "event_retention_count": 500,
}
_WEBHOOK_ACTOR = {"name": "qualibug_webhook", "role": "knowledge_admin"}


class ConnectorWebhookError(RuntimeError):
    """A webhook cannot be trusted or cannot be reconciled safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _identifier(value: Any, field: str) -> str:
    result = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ConnectorWebhookError(f"{field}_invalid")
    return result


def _header_name(value: Any, field: str, *, required: bool = True) -> str:
    result = _text(value, 160)
    if not result and not required:
        return ""
    if not _HEADER_NAME_RE.fullmatch(result):
        raise ConnectorWebhookError(f"webhook_{field}_invalid")
    return result


def _bounded_integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ConnectorWebhookError(f"webhook_{field}_invalid")
    if isinstance(value, float) and not value.is_integer():
        raise ConnectorWebhookError(f"webhook_{field}_invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConnectorWebhookError(f"webhook_{field}_invalid") from exc
    if not minimum <= parsed <= maximum:
        raise ConnectorWebhookError(f"webhook_{field}_out_of_range")
    return parsed


def normalize_webhook_policy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the non-secret instance policy used by the webhook boundary."""
    if value is None:
        return dict(_DEFAULT_POLICY)
    if not isinstance(value, Mapping):
        raise ConnectorWebhookError("webhook_policy_must_be_object")
    unknown = sorted(set(value) - _POLICY_KEYS)
    if unknown:
        raise ConnectorWebhookError(f"webhook_policy_field_not_supported:{unknown[0]}")
    policy = dict(_DEFAULT_POLICY)
    policy.update(dict(value))
    if not isinstance(policy["enabled"], bool):
        raise ConnectorWebhookError("webhook_enabled_invalid")
    policy["signature_header"] = _header_name(
        policy["signature_header"], "signature_header"
    )
    policy["event_id_header"] = _header_name(
        policy["event_id_header"], "event_id_header"
    )
    policy["timestamp_header"] = _header_name(
        policy["timestamp_header"], "timestamp_header"
    )
    policy["sequence_header"] = _header_name(
        policy["sequence_header"], "sequence_header", required=False
    )
    if _text(policy["algorithm"], 40).lower() != "hmac-sha256":
        raise ConnectorWebhookError("webhook_algorithm_not_supported")
    policy["algorithm"] = "hmac-sha256"
    encoding = _text(policy["encoding"], 20).lower()
    if encoding not in {"hex", "base64"}:
        raise ConnectorWebhookError("webhook_encoding_not_supported")
    policy["encoding"] = encoding
    signed_payload = _text(policy["signed_payload"], 80).lower()
    if signed_payload not in {
        "body",
        "timestamp.body",
        "event_id.timestamp.body",
    }:
        raise ConnectorWebhookError("webhook_signed_payload_not_supported")
    policy["signed_payload"] = signed_payload
    prefix = _text(policy["signature_prefix"], 80)
    if any(ord(char) < 0x20 or ord(char) > 0x7E for char in prefix):
        raise ConnectorWebhookError("webhook_signature_prefix_invalid")
    policy["signature_prefix"] = prefix
    policy["max_age_seconds"] = _bounded_integer(
        policy["max_age_seconds"], "max_age_seconds", 1, 86_400
    )
    policy["future_skew_seconds"] = _bounded_integer(
        policy["future_skew_seconds"], "future_skew_seconds", 0, 600
    )
    policy["event_retention_count"] = _bounded_integer(
        policy["event_retention_count"], "event_retention_count", 100, 2_000
    )
    return policy


def serialize_webhook_policy(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        normalize_webhook_policy(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _policy_from_instance(instance: Mapping[str, Any]) -> dict[str, Any]:
    metadata = instance.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise ConnectorWebhookError("connector_instance_metadata_must_be_object")
    raw = metadata.get("webhook_policy_json")
    if raw in (None, ""):
        return normalize_webhook_policy(None)
    if not isinstance(raw, str):
        raise ConnectorWebhookError("webhook_policy_persisted_value_invalid")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectorWebhookError("webhook_policy_persisted_json_invalid") from exc
    if not isinstance(parsed, dict):
        raise ConnectorWebhookError("webhook_policy_persisted_json_invalid")
    return normalize_webhook_policy(parsed)


def _instance(project: str, connector: str, root: Path) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    rows = list_connector_instances(
        project,
        root=root,
        include_disabled=True,
    ).get("connector_instances") or []
    instance = next(
        (
            dict(row)
            for row in rows
            if isinstance(row, dict)
            and _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if instance is None:
        raise ConnectorWebhookError("connector_instance_not_registered")
    if _text(instance.get("status"), 32).upper() != "ACTIVE":
        raise ConnectorWebhookError("connector_instance_not_active")
    try:
        manifest = build_default_connector_registry().manifest(
            _text(instance.get("connector_type"), 160)
        )
    except ConnectorRegistryError as exc:
        raise ConnectorWebhookError("webhook_connector_manifest_unavailable") from exc
    if manifest.webhook_supported is not True:
        raise ConnectorWebhookError("webhook_not_supported_by_connector")
    policy = _policy_from_instance(instance)
    if policy["enabled"] is not True:
        raise ConnectorWebhookError("webhook_not_enabled")
    return instance, manifest, policy


def _header(headers: Mapping[str, Any], name: str, *, required: bool = True) -> str:
    matches: list[str] = []
    wanted = name.casefold()
    for raw_key, raw_value in headers.items():
        if str(raw_key).casefold() == wanted:
            if isinstance(raw_value, (list, tuple)):
                matches.extend(_text(item, 4_000) for item in raw_value)
            else:
                matches.append(_text(raw_value, 4_000))
    if len(matches) > 1:
        raise ConnectorWebhookError(f"webhook_header_duplicated:{name}")
    value = matches[0] if matches else ""
    if required and not value:
        raise ConnectorWebhookError(f"webhook_header_missing:{name}")
    return value


def _timestamp_epoch(value: Any) -> float:
    raw = _text(value, 160)
    if not raw:
        raise ConnectorWebhookError("webhook_timestamp_missing")
    try:
        numeric = float(raw)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConnectorWebhookError("webhook_timestamp_invalid") from exc
        if parsed.tzinfo is None:
            raise ConnectorWebhookError("webhook_timestamp_timezone_required")
        return parsed.astimezone(timezone.utc).timestamp()
    if not math.isfinite(numeric):
        raise ConnectorWebhookError("webhook_timestamp_invalid")
    if abs(numeric) > 100_000_000_000:
        numeric /= 1_000
    if numeric <= 0:
        raise ConnectorWebhookError("webhook_timestamp_invalid")
    return numeric


def _utc_text(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _signed_payload(
    policy: Mapping[str, Any],
    *,
    body: bytes,
    event_id: str,
    timestamp: str,
) -> bytes:
    mode = _text(policy.get("signed_payload"), 80)
    if mode == "body":
        return body
    if mode == "timestamp.body":
        return timestamp.encode("utf-8") + b"." + body
    if mode == "event_id.timestamp.body":
        return (
            event_id.encode("utf-8")
            + b"."
            + timestamp.encode("utf-8")
            + b"."
            + body
        )
    raise ConnectorWebhookError("webhook_signed_payload_not_supported")


def _verify_signature(
    policy: Mapping[str, Any],
    *,
    headers: Mapping[str, Any],
    body: bytes,
    secret: str,
    now_epoch: float,
) -> dict[str, Any]:
    if not isinstance(body, bytes):
        raise ConnectorWebhookError("webhook_body_must_be_bytes")
    if not secret:
        raise ConnectorWebhookError("webhook_secret_missing")
    event_id = _header(headers, _text(policy["event_id_header"], 160))
    timestamp_raw = _header(headers, _text(policy["timestamp_header"], 160))
    timestamp_epoch = _timestamp_epoch(timestamp_raw)
    age = now_epoch - timestamp_epoch
    if age > int(policy["max_age_seconds"]):
        raise ConnectorWebhookError("webhook_replay_window_exceeded")
    if age < -int(policy["future_skew_seconds"]):
        raise ConnectorWebhookError("webhook_timestamp_from_future")
    sequence = None
    sequence_header = _text(policy.get("sequence_header"), 160)
    if sequence_header:
        sequence_raw = _header(headers, sequence_header)
        sequence = _bounded_integer(sequence_raw, "sequence", 0, 2**63 - 1)
    provided = _header(headers, _text(policy["signature_header"], 160))
    prefix = _text(policy.get("signature_prefix"), 80)
    if prefix:
        if not provided.startswith(prefix):
            raise ConnectorWebhookError("webhook_signature_prefix_mismatch")
        provided = provided[len(prefix) :]
    payload = _signed_payload(
        policy,
        body=body,
        event_id=event_id,
        timestamp=timestamp_raw,
    )
    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    encoding = _text(policy.get("encoding"), 20)
    if encoding == "hex":
        if not _SIGNATURE_HEX_RE.fullmatch(provided):
            raise ConnectorWebhookError("webhook_signature_encoding_invalid")
        valid = hmac.compare_digest(provided.lower(), expected.hex())
    elif encoding == "base64":
        try:
            decoded = base64.b64decode(provided.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise ConnectorWebhookError("webhook_signature_encoding_invalid") from exc
        valid = hmac.compare_digest(decoded, expected)
    else:
        raise ConnectorWebhookError("webhook_encoding_not_supported")
    if not valid:
        raise ConnectorWebhookError("webhook_signature_invalid")
    return {
        "event_id_hash": hashlib.sha256(event_id.encode("utf-8")).hexdigest(),
        "event_timestamp_utc": _utc_text(timestamp_epoch),
        "event_timestamp_epoch": timestamp_epoch,
        "sequence": sequence,
        "body_fingerprint": hashlib.sha256(body).hexdigest(),
    }


def _ledger_path(project: str, connector: str, root: Path) -> Path:
    filename = quote(connector, safe="") + ".json"
    return (
        root
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_webhook_events"
        / filename
    )


def _default_ledger(project: str, connector: str) -> dict[str, Any]:
    now = _now()
    return {
        "schema": CONNECTOR_WEBHOOK_LEDGER_SCHEMA,
        "project_id": project,
        "connector_instance_id": connector,
        "created_at_utc": now,
        "updated_at_utc": now,
        "events": [],
        "state": {
            "last_sequence": None,
            "last_event_timestamp_utc": "",
            "calibration_required": False,
            "last_success_event": None,
            "last_failure_event": None,
        },
        "governance": {
            "raw_event_body_persisted": False,
            "signature_persisted": False,
            "event_id_plaintext_persisted": False,
            "event_only_triggers_managed_sync": True,
            "source_content_mutated_by_webhook": False,
        },
    }


def _load_ledger(project: str, connector: str, root: Path) -> dict[str, Any]:
    try:
        raw = _read_json_object(_ledger_path(project, connector, root))
    except (OSError, ValueError) as exc:
        raise ConnectorWebhookError("webhook_ledger_unavailable") from exc
    ledger = _default_ledger(project, connector)
    if raw:
        if raw.get("schema") not in {None, CONNECTOR_WEBHOOK_LEDGER_SCHEMA}:
            raise ConnectorWebhookError("webhook_ledger_schema_invalid")
        if raw.get("project_id") not in {None, project}:
            raise ConnectorWebhookError("webhook_ledger_project_mismatch")
        if raw.get("connector_instance_id") not in {None, connector}:
            raise ConnectorWebhookError("webhook_ledger_connector_mismatch")
        ledger.update(raw)
    raw_events = ledger.get("events")
    if raw_events in (None, ""):
        raw_events = []
    if not isinstance(raw_events, list) or any(
        not isinstance(row, dict) for row in raw_events
    ):
        raise ConnectorWebhookError("webhook_ledger_events_invalid")
    ledger["events"] = [dict(row) for row in raw_events]
    raw_state = ledger.get("state")
    if raw_state in (None, ""):
        raw_state = {}
    if not isinstance(raw_state, Mapping):
        raise ConnectorWebhookError("webhook_ledger_state_invalid")
    state = dict(raw_state)
    state.setdefault("last_sequence", None)
    state.setdefault("last_event_timestamp_utc", "")
    state.setdefault("calibration_required", False)
    state.setdefault("last_success_event", None)
    state.setdefault("last_failure_event", None)
    ledger["state"] = state
    raw_governance = (raw or {}).get("governance")
    if raw_governance in (None, ""):
        raw_governance = {}
    if not isinstance(raw_governance, Mapping):
        raise ConnectorWebhookError("webhook_ledger_governance_invalid")
    ledger["governance"] = dict(_default_ledger(project, connector)["governance"])
    ledger["governance"].update(dict(raw_governance))
    return ledger


def _save_ledger(project: str, connector: str, root: Path, ledger: dict[str, Any]) -> None:
    ledger["updated_at_utc"] = _now()
    _write_json_object_atomic(_ledger_path(project, connector, root), ledger)


@contextmanager
def _ledger_transaction(
    project: str,
    connector: str,
    root: Path,
    *,
    operation: str,
) -> Iterator[dict[str, Any]]:
    try:
        with knowledge_transaction(
            root,
            project,
            operation=operation,
            actor=_WEBHOOK_ACTOR,
            wait_seconds=5.0,
        ):
            ledger = _load_ledger(project, connector, root)
            yield ledger
            _save_ledger(project, connector, root, ledger)
    except KnowledgeTransactionBusy as exc:
        raise ConnectorWebhookError("webhook_ledger_transaction_busy") from exc


def _event_record_id(project: str, connector: str, event_id_hash: str) -> str:
    return "webhook_evt_" + _short_hash(
        {
            "project": project,
            "connector": connector,
            "event_id_hash": event_id_hash,
            "nonce": uuid.uuid4().hex,
        },
        28,
    )


def _public_event(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(event, Mapping):
        return None
    fields = (
        "event_record_id",
        "event_id_hash",
        "received_at_utc",
        "event_timestamp_utc",
        "sequence",
        "ordering_status",
        "status",
        "calibration_requested",
        "calibration_status",
        "sync_status",
        "sync_epoch_id",
        "completed_at_utc",
        "body_fingerprint",
        "error_code",
        "error_detail",
    )
    return {key: event[key] for key in fields if key in event}


def _append_event(ledger: dict[str, Any], event: dict[str, Any], retention: int) -> None:
    events = list(ledger.get("events") or [])
    events.append(event)
    ledger["events"] = events[-retention:]


def _state_timestamp_epoch(state: Mapping[str, Any]) -> float | None:
    value = _text(state.get("last_event_timestamp_utc"), 80)
    if not value:
        return None
    try:
        return _timestamp_epoch(value)
    except ConnectorWebhookError:
        raise ConnectorWebhookError("webhook_ledger_timestamp_invalid")


def _reserve_event(
    project: str,
    connector: str,
    verified: Mapping[str, Any],
    policy: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    event_id_hash = _text(verified.get("event_id_hash"), 128)
    sequence = verified.get("sequence")
    timestamp_epoch = float(verified["event_timestamp_epoch"])
    with _ledger_transaction(
        project,
        connector,
        root,
        operation="receive_connector_webhook",
    ) as ledger:
        retention = int(policy["event_retention_count"])
        if len(ledger["events"]) > retention:
            ledger["events"] = ledger["events"][-retention:]
        existing = next(
            (
                row
                for row in ledger["events"]
                if _text(row.get("event_id_hash"), 128) == event_id_hash
            ),
            None,
        )
        if existing is not None:
            return {
                "reserved": False,
                "status": "DUPLICATE",
                "event": _public_event(existing),
                "calibration_required": bool(
                    ledger["state"].get("calibration_required")
                ),
            }

        state = ledger["state"]
        previous_sequence = state.get("last_sequence")
        previous_timestamp = _state_timestamp_epoch(state)
        if sequence is not None and previous_sequence is not None:
            if sequence <= int(previous_sequence):
                ordering = "OUT_OF_ORDER"
            elif sequence > int(previous_sequence) + 1:
                ordering = "GAP_DETECTED"
            else:
                ordering = "IN_ORDER"
        elif previous_timestamp is not None and timestamp_epoch < previous_timestamp:
            ordering = "OUT_OF_ORDER"
        else:
            ordering = "IN_ORDER"

        event = {
            "event_record_id": _event_record_id(project, connector, event_id_hash),
            "event_id_hash": event_id_hash,
            "received_at_utc": _now(),
            "event_timestamp_utc": _text(verified.get("event_timestamp_utc"), 80),
            "sequence": sequence,
            "ordering_status": ordering,
            "status": "OUT_OF_ORDER" if ordering == "OUT_OF_ORDER" else "PENDING",
            "calibration_requested": bool(
                ordering == "GAP_DETECTED" or state.get("calibration_required")
            ),
            "calibration_status": (
                "NOT_REQUIRED"
                if ordering != "GAP_DETECTED" and not state.get("calibration_required")
                else "REQUESTED"
            ),
            "sync_status": "NOT_TRIGGERED" if ordering == "OUT_OF_ORDER" else "PENDING",
            "sync_epoch_id": "",
            "completed_at_utc": "",
            "body_fingerprint": _text(verified.get("body_fingerprint"), 128),
            "error_code": "",
            "error_detail": "",
        }
        _append_event(
            ledger,
            event,
            int(policy["event_retention_count"]),
        )
        if ordering == "OUT_OF_ORDER":
            return {
                "reserved": False,
                "status": "OUT_OF_ORDER",
                "event": _public_event(event),
                "calibration_required": bool(state.get("calibration_required")),
            }
        if sequence is not None and (
            previous_sequence is None or sequence > int(previous_sequence)
        ):
            state["last_sequence"] = sequence
        if previous_timestamp is None or timestamp_epoch >= previous_timestamp:
            state["last_event_timestamp_utc"] = _text(
                verified.get("event_timestamp_utc"), 80
            )
        if ordering == "GAP_DETECTED":
            state["calibration_required"] = True
        return {
            "reserved": True,
            "status": "CALIBRATION_REQUIRED" if event["calibration_requested"] else "ACCEPTED",
            "event_record_id": event["event_record_id"],
            "event": _public_event(event),
            "calibration_required": bool(state.get("calibration_required")),
        }


def _public_sync(run: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(run, Mapping):
        return {
            "status": "FAILED",
            "raw_cursor_returned": False,
            "source_content_returned": False,
            "credentials_returned": False,
        }
    fields = (
        "status",
        "sync_epoch_id",
        "started_at_utc",
        "completed_at_utc",
        "item_count",
        "discovered_resource_count",
        "materialized_resource_count",
        "unchanged_resource_count",
        "coverage_observation_count",
        "failure_count",
        "snapshot_complete",
        "semantic_refresh_status",
        "acl_propagation_status",
    )
    result = {key: run[key] for key in fields if key in run}
    result.update(
        {
            "raw_cursor_returned": False,
            "source_content_returned": False,
            "credentials_returned": False,
            "customer_material_mutation_executed": False,
        }
    )
    return result


def _finalize_event(
    project: str,
    connector: str,
    event_record_id: str,
    *,
    run: Mapping[str, Any] | None,
    error: Exception | None,
    root: Path,
) -> dict[str, Any]:
    public_run = _public_sync(run)
    complete = error is None and _text(public_run.get("status"), 40).upper() == "COMPLETE"
    snapshot_complete = public_run.get("snapshot_complete") is True
    with _ledger_transaction(
        project,
        connector,
        root,
        operation="complete_connector_webhook",
    ) as ledger:
        event = next(
            (
                row
                for row in ledger["events"]
                if _text(row.get("event_record_id"), 160) == event_record_id
            ),
            None,
        )
        if event is None:
            raise ConnectorWebhookError("webhook_event_reservation_missing")
        event["status"] = "SUCCESS" if complete else "FAILED"
        event["sync_status"] = "COMPLETE" if complete else "FAILED"
        event["sync_epoch_id"] = _text(public_run.get("sync_epoch_id"), 160)
        event["completed_at_utc"] = _now()
        if error is not None:
            event["error_code"] = type(error).__name__[:120]
            event["error_detail"] = _redact_text(str(error), 300)
        elif not complete:
            event["error_code"] = "managed_sync_incomplete"
            event["error_detail"] = _text(public_run.get("status"), 120)
        if event.get("calibration_requested"):
            if complete and snapshot_complete:
                event["calibration_status"] = "COMPLETED"
                ledger["state"]["calibration_required"] = False
            elif complete:
                event["calibration_status"] = "PENDING_CONFIRMATION"
                ledger["state"]["calibration_required"] = True
            else:
                event["calibration_status"] = "FAILED"
                ledger["state"]["calibration_required"] = True
        summary = _public_event(event)
        if complete:
            ledger["state"]["last_success_event"] = summary
        else:
            ledger["state"]["last_failure_event"] = summary
        return {
            "event": summary,
            "sync": public_run,
            "calibration_required": bool(
                ledger["state"].get("calibration_required")
            ),
        }


def receive_connector_webhook(
    project_id: str,
    connector_instance_id: str,
    *,
    headers: Mapping[str, Any],
    body: bytes,
    root: Path | None = None,
    now_utc: Any = None,
    sync_runner: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Authenticate one event and trigger exactly one managed sync attempt."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    if not isinstance(headers, Mapping) and not callable(getattr(headers, "items", None)):
        raise ConnectorWebhookError("webhook_headers_must_be_mapping")
    if not isinstance(body, bytes):
        raise ConnectorWebhookError("webhook_body_must_be_bytes")
    instance, _manifest, policy = _instance(project, connector, resolved_root)
    profile_ref = _text(instance.get("connection_profile_ref"), 500)
    if not profile_ref:
        raise ConnectorWebhookError("webhook_connection_profile_missing")
    profile = resolve_connector_connection_profile(
        project,
        profile_ref,
        root=resolved_root,
    )
    secret = _text(profile.get("webhook_secret"), 8_000)
    now_epoch = time.time() if now_utc in (None, "") else _timestamp_epoch(now_utc)
    verified = _verify_signature(
        policy,
        headers=headers,
        body=body,
        secret=secret,
        now_epoch=now_epoch,
    )
    reservation = _reserve_event(
        project,
        connector,
        verified,
        policy,
        resolved_root,
    )
    if not reservation.get("reserved"):
        return {
            "schema": CONNECTOR_WEBHOOK_PROJECTION_SCHEMA,
            "status": reservation["status"],
            "accepted": True,
            "event": reservation.get("event"),
            "sync": None,
            "calibration_required": bool(reservation.get("calibration_required")),
            "governance": dict(_default_ledger(project, connector)["governance"]),
        }

    runner = sync_runner or run_managed_connector_sync
    try:
        raw_run = runner(
            project,
            connector,
            root=resolved_root,
            actor=dict(_WEBHOOK_ACTOR),
            deletion_policy="RETAIN",
        )
        if not isinstance(raw_run, Mapping):
            raise ConnectorWebhookError("managed_sync_result_invalid")
    except Exception as exc:
        finalized = _finalize_event(
            project,
            connector,
            _text(reservation["event_record_id"], 160),
            run=None,
            error=exc,
            root=resolved_root,
        )
        return {
            "schema": CONNECTOR_WEBHOOK_PROJECTION_SCHEMA,
            "status": "SYNC_FAILED",
            "accepted": True,
            **finalized,
            "governance": dict(_default_ledger(project, connector)["governance"]),
        }
    finalized = _finalize_event(
        project,
        connector,
        _text(reservation["event_record_id"], 160),
        run=raw_run,
        error=None,
        root=resolved_root,
    )
    return {
        "schema": CONNECTOR_WEBHOOK_PROJECTION_SCHEMA,
        "status": (
            "CALIBRATION_SYNC_COMPLETE"
            if finalized["event"].get("calibration_status") == "COMPLETED"
            else "SYNC_TRIGGERED"
        ),
        "accepted": True,
        **finalized,
        "governance": dict(_default_ledger(project, connector)["governance"]),
    }


def project_connector_webhook(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Return an operator-safe event and calibration projection."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    rows = list_connector_instances(
        project,
        root=resolved_root,
        include_disabled=True,
    ).get("connector_instances") or []
    instance = next(
        (
            dict(row)
            for row in rows
            if isinstance(row, dict)
            and _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if instance is None:
        raise ConnectorWebhookError("connector_instance_not_registered")
    try:
        manifest = build_default_connector_registry().manifest(
            _text(instance.get("connector_type"), 160)
        )
    except ConnectorRegistryError as exc:
        raise ConnectorWebhookError("webhook_connector_manifest_unavailable") from exc
    policy = _policy_from_instance(instance)
    ledger = _load_ledger(project, connector, resolved_root)
    state = dict(ledger["state"])
    events = [_public_event(row) for row in ledger["events"]]
    instance_status = _text(instance.get("status"), 32).upper() or "UNKNOWN"
    return {
        "schema": CONNECTOR_WEBHOOK_PROJECTION_SCHEMA,
        "connector_instance_id": connector,
        "connector_type": _text(instance.get("connector_type"), 160),
        "connector_status": instance_status,
        "supported": manifest.webhook_supported is True,
        "enabled": policy["enabled"] is True,
        "status": (
            "DISABLED"
            if instance_status != "ACTIVE"
            else "CALIBRATION_REQUIRED"
            if state.get("calibration_required")
            else "ENABLED"
            if (
                manifest.webhook_supported is True
                and policy["enabled"] is True
            )
            else "DISABLED"
        ),
        "policy": {
            key: value
            for key, value in policy.items()
            if key != "signature_prefix"
        },
        "state": {
            "last_sequence": state.get("last_sequence"),
            "last_event_timestamp_utc": _text(
                state.get("last_event_timestamp_utc"), 80
            ),
            "calibration_required": bool(state.get("calibration_required")),
            "last_success_event": _public_event(state.get("last_success_event")),
            "last_failure_event": _public_event(state.get("last_failure_event")),
        },
        "events": [event for event in events if event is not None],
        "governance": dict(_default_ledger(project, connector)["governance"]),
    }


__all__ = [
    "CONNECTOR_WEBHOOK_LEDGER_SCHEMA",
    "CONNECTOR_WEBHOOK_POLICY_SCHEMA",
    "CONNECTOR_WEBHOOK_PROJECTION_SCHEMA",
    "ConnectorWebhookError",
    "normalize_webhook_policy",
    "project_connector_webhook",
    "receive_connector_webhook",
    "serialize_webhook_policy",
]
