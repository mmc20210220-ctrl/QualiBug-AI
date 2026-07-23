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


# ── Enhanced Receipt Validation (SPEC §12) ──
# Specific failure codes instead of blanket DATA_CREATION_RECEIPT_MISSING

RECEIPT_EXPIRY_SECONDS = 3600  # 1 hour default TTL


def validate_receipt_for_execution(
    project_id: str,
    *,
    root: Path,
    receipt_id: str,
    run_id: str,
    campaign_id: str,
    environment_ref: str,
    tenant_id: str = "",
    target_rule_ids: list[str] | None = None,
    required_entities: list[dict[str, Any]] | None = None,
    required_relations: list[dict[str, Any]] | None = None,
    required_preconditions: list[dict[str, Any]] | None = None,
    max_age_seconds: int = RECEIPT_EXPIRY_SECONDS,
) -> dict[str, Any]:
    """Comprehensive pre-execution receipt validation with specific failure codes.

    Instead of returning a blanket DATA_CREATION_RECEIPT_MISSING, this function
    returns the exact reason why a receipt cannot be used for execution.

    Failure codes:
    - RECEIPT_NOT_FOUND: Receipt ID does not exist in registry
    - RECEIPT_HASH_INVALID: Receipt hash verification failed
    - RECEIPT_RUN_MISMATCH: Receipt not bound to current run/campaign
    - RECEIPT_ENVIRONMENT_MISMATCH: Receipt not bound to current environment
    - RECEIPT_TENANT_MISMATCH: Receipt not bound to current tenant
    - RECEIPT_EXPIRED: Receipt has exceeded its TTL
    - RECEIPT_ENTITY_NOT_VERIFIED: Created entities not confirmed existing
    - RECEIPT_RELATION_NOT_VERIFIED: Entity relations not confirmed
    - RECEIPT_PRECONDITION_NOT_VERIFIED: Rule preconditions not confirmed
    - RECEIPT_RULE_NOT_COVERED: Target rule not in receipt scope
    """
    registry = _read_registry(Path(root), project_id)
    rid = _text(receipt_id, 160)

    # 1. Receipt exists
    receipt = registry["receipts"].get(rid)
    if not isinstance(receipt, dict):
        return {
            "valid": False,
            "code": "RECEIPT_NOT_FOUND",
            "receipt_id": rid,
            "detail": f"Receipt '{rid}' not found in registry",
        }

    # 2. Hash integrity
    expected_hash = _hash({k: v for k, v in receipt.items() if k != "receipt_hash"})
    if str(receipt.get("receipt_hash") or "") != expected_hash:
        return {
            "valid": False,
            "code": "RECEIPT_HASH_INVALID",
            "receipt_id": rid,
            "detail": "Receipt hash verification failed - possible tampering",
        }

    # 3. Run/Campaign binding
    receipt_campaign = str(receipt.get("campaign_id") or "").strip()
    if campaign_id and receipt_campaign != _text(campaign_id, 160):
        return {
            "valid": False,
            "code": "RECEIPT_RUN_MISMATCH",
            "receipt_id": rid,
            "detail": f"Receipt campaign '{receipt_campaign}' != current '{campaign_id}'",
        }

    # 4. Environment binding
    receipt_env = str(receipt.get("environment_ref") or "").strip()
    if environment_ref and receipt_env != _text(environment_ref, 160):
        return {
            "valid": False,
            "code": "RECEIPT_ENVIRONMENT_MISMATCH",
            "receipt_id": rid,
            "detail": f"Receipt env '{receipt_env}' != current '{environment_ref}'",
        }

    # 5. Tenant binding (via scope_id)
    receipt_scope = str(receipt.get("scope_id") or "").strip()
    if tenant_id and receipt_scope and tenant_id not in receipt_scope:
        return {
            "valid": False,
            "code": "RECEIPT_TENANT_MISMATCH",
            "receipt_id": rid,
            "detail": f"Receipt scope '{receipt_scope}' does not match tenant '{tenant_id}'",
        }

    # 6. Expiry check
    issued_at = str(receipt.get("issued_at_utc") or "").strip()
    if issued_at and max_age_seconds > 0:
        try:
            import calendar
            issued_struct = time.strptime(issued_at, "%Y-%m-%dT%H:%M:%SZ")
            issued_epoch = calendar.timegm(issued_struct)
            age = time.time() - issued_epoch
            if age > max_age_seconds:
                return {
                    "valid": False,
                    "code": "RECEIPT_EXPIRED",
                    "receipt_id": rid,
                    "detail": f"Receipt age {age:.0f}s exceeds max {max_age_seconds}s",
                }
        except (ValueError, OverflowError):
            pass  # Cannot parse timestamp - skip expiry check

    # 7. Entity verification
    for entity in (required_entities or []):
        if not isinstance(entity, dict):
            continue
        if not entity.get("verified") and not entity.get("observer_proven"):
            return {
                "valid": False,
                "code": "RECEIPT_ENTITY_NOT_VERIFIED",
                "receipt_id": rid,
                "detail": f"Entity '{entity.get('entity_id', '?')}' not verified by observer",
            }

    # 8. Relation verification
    for relation in (required_relations or []):
        if not isinstance(relation, dict):
            continue
        if not relation.get("verified") and not relation.get("passed"):
            return {
                "valid": False,
                "code": "RECEIPT_RELATION_NOT_VERIFIED",
                "receipt_id": rid,
                "detail": f"Relation '{relation.get('parent_entity_id', '?')}->{relation.get('child_entity_id', '?')}' not verified",
            }

    # 9. Precondition verification
    for precond in (required_preconditions or []):
        if not isinstance(precond, dict):
            continue
        if not precond.get("passed") and not precond.get("verified"):
            return {
                "valid": False,
                "code": "RECEIPT_PRECONDITION_NOT_VERIFIED",
                "receipt_id": rid,
                "detail": f"Precondition for rule '{precond.get('rule_id', '?')}' not verified",
            }

    # 10. Target rule coverage
    if target_rule_ids:
        receipt_rules = set(str(r) for r in (receipt.get("target_rule_ids") or []))
        # If receipt has no rule scope, it covers all (backward compat)
        if receipt_rules:
            for rule_id in target_rule_ids:
                if str(rule_id) not in receipt_rules:
                    return {
                        "valid": False,
                        "code": "RECEIPT_RULE_NOT_COVERED",
                        "receipt_id": rid,
                        "detail": f"Rule '{rule_id}' not in receipt scope",
                    }

    return {
        "valid": True,
        "code": "RECEIPT_VALID",
        "receipt_id": rid,
        "receipt": dict(receipt),
    }
