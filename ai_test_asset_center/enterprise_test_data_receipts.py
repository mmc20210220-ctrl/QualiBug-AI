"""Immutable receipts for enterprise test-data lifecycle governance.

A receipt records that an approved operator created, verified, or cleaned a
specific test-data scope. A receipt is not test data itself and never contains
record payloads, credentials, or customer data.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


RECEIPT_KINDS = {"provenance", "creation", "cleanup", "fixture"}


class TestDataReceiptError(ValueError):
    """A test-data receipt cannot be issued or verified safely."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_project(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or "unscoped"


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _hash(value: Any, length: int = 64) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def _paths(root: Path, project_id: str) -> dict[str, Path]:
    base = Path(root) / "platform_workspace" / _safe_project(project_id) / "test_data_receipts"
    return {"base": base, "registry": base / "receipts.json", "audit": base / "audit.jsonl"}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _read_registry(root: Path, project_id: str) -> dict[str, Any]:
    path = _paths(root, project_id)["registry"]
    if not path.exists():
        return {"schema_version": "enterprise-test-data-receipts-v1", "project_id": _safe_project(project_id), "receipts": {}, "updated_at_utc": _now()}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "null")
    except (OSError, json.JSONDecodeError) as exc:
        raise TestDataReceiptError("test_data_receipt_registry_unreadable") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("receipts"), dict):
        raise TestDataReceiptError("test_data_receipt_registry_invalid")
    return payload


def _append_audit(root: Path, project_id: str, event: str, receipt: dict[str, Any]) -> None:
    path = _paths(root, project_id)["audit"]
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_hash = ""
    if path.exists():
        try:
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                previous_hash = str(json.loads(lines[-1]).get("event_hash") or "")
        except Exception as exc:
            raise TestDataReceiptError("test_data_receipt_audit_unreadable") from exc
    entry = {
        "at_utc": _now(),
        "event": event,
        "receipt_id": receipt["receipt_id"],
        "kind": receipt["kind"],
        "campaign_id": receipt["campaign_id"],
        "scope_id": receipt["scope_id"],
        "environment_ref": receipt["environment_ref"],
        "previous_event_hash": previous_hash,
    }
    entry["event_hash"] = _hash(entry)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def issue_test_data_receipt(
    project_id: str,
    *,
    root: Path,
    kind: str,
    campaign_id: str,
    scope_id: str,
    environment_ref: str,
    actor: dict[str, Any] | None,
    data_scope_ref: str = "",
    fixture_ref: str = "",
    provenance_ref: str = "",
    operation_ref: str = "",
) -> dict[str, Any]:
    """Issue an immutable metadata-only receipt for one governed data action."""
    receipt_kind = _text(kind, 40).lower()
    if receipt_kind not in RECEIPT_KINDS:
        raise TestDataReceiptError("test_data_receipt_kind_invalid")
    campaign = _text(campaign_id, 160)
    scope = _text(scope_id, 160)
    environment = _text(environment_ref, 160)
    if not campaign or not scope or not environment:
        raise TestDataReceiptError("test_data_receipt_scope_missing")
    if receipt_kind == "creation" and not _text(data_scope_ref):
        raise TestDataReceiptError("test_data_creation_scope_missing")
    if receipt_kind == "fixture" and not _text(fixture_ref):
        raise TestDataReceiptError("test_data_fixture_reference_missing")
    if receipt_kind == "provenance" and not _text(provenance_ref):
        raise TestDataReceiptError("test_data_provenance_reference_missing")
    record = {
        "kind": receipt_kind,
        "campaign_id": campaign,
        "scope_id": scope,
        "environment_ref": environment,
        "data_scope_ref": _text(data_scope_ref),
        "fixture_ref": _text(fixture_ref),
        "provenance_ref": _text(provenance_ref),
        "operation_ref": _text(operation_ref),
        "actor": {
            "name": _text((actor or {}).get("name") or (actor or {}).get("actor") or "system", 120),
            "role": _text((actor or {}).get("role") or "system", 64),
        },
        "issued_at_utc": _now(),
    }
    receipt_id = "tdr_" + _hash(record, 24)
    receipt = {"receipt_id": receipt_id, **record, "receipt_hash": ""}
    receipt["receipt_hash"] = _hash({key: value for key, value in receipt.items() if key != "receipt_hash"})
    registry = _read_registry(Path(root), project_id)
    existing = registry["receipts"].get(receipt_id)
    if existing:
        if existing.get("receipt_hash") != receipt["receipt_hash"]:
            raise TestDataReceiptError("test_data_receipt_collision")
        return dict(existing)
    registry["receipts"][receipt_id] = receipt
    registry["updated_at_utc"] = _now()
    _atomic_json(_paths(Path(root), project_id)["registry"], registry)
    _append_audit(Path(root), project_id, "receipt_issued", receipt)
    return receipt


def verify_test_data_receipt(
    project_id: str,
    receipt_id: str,
    *,
    root: Path,
    kind: str,
    campaign_id: str,
    scope_id: str,
    environment_ref: str,
) -> dict[str, Any]:
    """Verify receipt hash and binding without exposing any test-data content."""
    registry = _read_registry(Path(root), project_id)
    receipt = registry["receipts"].get(_text(receipt_id, 160))
    if not isinstance(receipt, dict):
        return {"valid": False, "code": "TEST_DATA_RECEIPT_NOT_FOUND"}
    expected_hash = _hash({key: value for key, value in receipt.items() if key != "receipt_hash"})
    if str(receipt.get("receipt_hash") or "") != expected_hash:
        return {"valid": False, "code": "TEST_DATA_RECEIPT_HASH_MISMATCH"}
    expected = {
        "kind": _text(kind, 40).lower(),
        "campaign_id": _text(campaign_id, 160),
        "scope_id": _text(scope_id, 160),
        "environment_ref": _text(environment_ref, 160),
    }
    for key, value in expected.items():
        if str(receipt.get(key) or "") != value:
            return {"valid": False, "code": f"TEST_DATA_RECEIPT_{key.upper()}_MISMATCH"}
    return {"valid": True, "receipt": dict(receipt)}
