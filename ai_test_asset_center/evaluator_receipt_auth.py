from __future__ import annotations

"""Evaluator-owned authentication for quality receipts and reports.

Plain content hashes detect accidental corruption but cannot establish who
issued an artifact.  This module adds a domain-separated HMAC trust anchor so
runtime or downstream code cannot relabel a policy, alter metrics, and simply
recompute a public SHA-256 value.
"""

import hashlib
import hmac
import json
import os
from typing import Any


EVALUATOR_HMAC_KEY_ENV = "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY"
EVALUATOR_HMAC_KEYRING_ENV = "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEYRING"
EVALUATOR_AUTHENTICATION_SCHEMA = (
    "qualibug.evaluator-artifact-authentication.v1"
)
EVALUATOR_HMAC_ALGORITHM = "HMAC-SHA256"
MINIMUM_HMAC_KEY_BYTES = 32

_AUTHENTICATION_FIELDS = {
    "schema_version",
    "algorithm",
    "domain",
    "key_id",
    "signature",
}


class EvaluatorReceiptAuthError(ValueError):
    """An evaluator artifact lacks a valid evaluator-owned trust anchor."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def resolve_evaluator_hmac_key(
    signing_key: str | bytes | bytearray | None = None,
) -> bytes:
    """Resolve and validate the secret without ever returning it in artifacts."""

    if signing_key is None and os.environ.get(EVALUATOR_HMAC_KEYRING_ENV):
        active_key_id, keys = resolve_evaluator_hmac_keyring()
        return keys[active_key_id]
    raw: str | bytes | bytearray | None = signing_key
    if raw is None:
        raw = os.environ.get(EVALUATOR_HMAC_KEY_ENV)
    if isinstance(raw, str):
        key = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray)):
        key = bytes(raw)
    else:
        raise EvaluatorReceiptAuthError(
            f"evaluator HMAC key missing: set {EVALUATOR_HMAC_KEY_ENV}"
        )
    if len(key) < MINIMUM_HMAC_KEY_BYTES:
        raise EvaluatorReceiptAuthError(
            "evaluator HMAC key must contain at least "
            f"{MINIMUM_HMAC_KEY_BYTES} bytes"
        )
    return key


def resolve_evaluator_hmac_keyring(
    signing_key: str | bytes | bytearray | None = None,
) -> tuple[str, dict[str, bytes]]:
    """Resolve the active signing key and retained verification keys.

    The keyring environment value is a JSON object with ``active_key_id`` and
    ``keys``. Key IDs are content-derived, so a mislabeled or substituted key
    fails at startup. An explicit ``signing_key`` and the legacy single-key
    environment remain a one-key development/test configuration.
    """

    if signing_key is not None or not os.environ.get(EVALUATOR_HMAC_KEYRING_ENV):
        key = resolve_evaluator_hmac_key(signing_key)
        key_id = _key_id(key)
        return key_id, {key_id: key}
    try:
        raw = json.loads(os.environ[EVALUATOR_HMAC_KEYRING_ENV])
    except (json.JSONDecodeError, TypeError) as exc:
        raise EvaluatorReceiptAuthError(
            "evaluator HMAC keyring must be valid JSON"
        ) from exc
    if not isinstance(raw, dict) or set(raw) != {"active_key_id", "keys"}:
        raise EvaluatorReceiptAuthError("evaluator HMAC keyring fields invalid")
    active_key_id = str(raw.get("active_key_id") or "").strip()
    raw_keys = raw.get("keys")
    if not active_key_id or not isinstance(raw_keys, dict) or not raw_keys:
        raise EvaluatorReceiptAuthError("evaluator HMAC keyring is incomplete")
    keys: dict[str, bytes] = {}
    for declared_id, secret in raw_keys.items():
        key_id = str(declared_id or "").strip()
        if not isinstance(secret, str):
            raise EvaluatorReceiptAuthError(
                f"evaluator HMAC keyring secret invalid: {key_id or 'missing'}"
            )
        key = resolve_evaluator_hmac_key(secret)
        if not key_id or key_id != _key_id(key):
            raise EvaluatorReceiptAuthError(
                f"evaluator HMAC keyring key_id mismatch: {key_id or 'missing'}"
            )
        keys[key_id] = key
    if active_key_id not in keys:
        raise EvaluatorReceiptAuthError(
            "evaluator HMAC keyring active key is unavailable"
        )
    return active_key_id, keys


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:24]


def _signature(*, key: bytes, domain: str, payload: dict[str, Any]) -> str:
    message = domain.encode("utf-8") + b"\0" + _canonical_json(payload)
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def seal_evaluator_artifact(
    payload: dict[str, Any],
    *,
    signing_key: str | bytes | bytearray | None = None,
    domain: str,
    fingerprint_field: str,
    authentication_field: str,
) -> dict[str, Any]:
    """Return a content-addressed artifact authenticated by the evaluator."""

    if not isinstance(payload, dict):
        raise EvaluatorReceiptAuthError("evaluator artifact must be an object")
    normalized_domain = str(domain or "").strip()
    if not normalized_domain:
        raise EvaluatorReceiptAuthError("evaluator authentication domain missing")
    active_key_id, keys = resolve_evaluator_hmac_keyring(signing_key)
    key = keys[active_key_id]
    unsigned = {
        name: value
        for name, value in payload.items()
        if name not in {fingerprint_field, authentication_field}
    }
    fingerprint = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    signed_payload = {**unsigned, fingerprint_field: fingerprint}
    authentication = {
        "schema_version": EVALUATOR_AUTHENTICATION_SCHEMA,
        "algorithm": EVALUATOR_HMAC_ALGORITHM,
        "domain": normalized_domain,
        "key_id": active_key_id,
        "signature": _signature(
            key=key,
            domain=normalized_domain,
            payload=signed_payload,
        ),
    }
    return {**signed_payload, authentication_field: authentication}


def verify_evaluator_artifact(
    payload: dict[str, Any],
    *,
    signing_key: str | bytes | bytearray | None = None,
    domain: str,
    fingerprint_field: str,
    authentication_field: str,
) -> dict[str, Any]:
    """Verify content hash, key identity, and HMAC in one fail-closed step."""

    if not isinstance(payload, dict):
        raise EvaluatorReceiptAuthError("evaluator artifact must be an object")
    authentication = payload.get(authentication_field)
    if not isinstance(authentication, dict):
        raise EvaluatorReceiptAuthError(
            f"evaluator authentication missing: {authentication_field}"
        )
    if set(authentication) != _AUTHENTICATION_FIELDS:
        raise EvaluatorReceiptAuthError(
            f"evaluator authentication fields invalid: {authentication_field}"
        )
    expected_domain = str(domain or "").strip()
    _, keys = resolve_evaluator_hmac_keyring(signing_key)
    claimed_key_id = str(authentication.get("key_id") or "").strip()
    key = keys.get(claimed_key_id)
    if key is None:
        raise EvaluatorReceiptAuthError(
            "evaluator authentication key_id is not trusted"
        )
    expected_metadata = {
        "schema_version": EVALUATOR_AUTHENTICATION_SCHEMA,
        "algorithm": EVALUATOR_HMAC_ALGORITHM,
        "domain": expected_domain,
        "key_id": claimed_key_id,
    }
    for field, expected in expected_metadata.items():
        if authentication.get(field) != expected:
            raise EvaluatorReceiptAuthError(
                f"evaluator authentication {field} mismatch"
            )

    unsigned = {
        name: value
        for name, value in payload.items()
        if name not in {fingerprint_field, authentication_field}
    }
    expected_fingerprint = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    claimed_fingerprint = str(payload.get(fingerprint_field) or "").strip()
    if not hmac.compare_digest(claimed_fingerprint, expected_fingerprint):
        raise EvaluatorReceiptAuthError(
            f"evaluator artifact fingerprint invalid: {fingerprint_field}"
        )
    signed_payload = {**unsigned, fingerprint_field: claimed_fingerprint}
    expected_signature = _signature(
        key=key,
        domain=expected_domain,
        payload=signed_payload,
    )
    claimed_signature = str(authentication.get("signature") or "").strip()
    if not hmac.compare_digest(claimed_signature, expected_signature):
        raise EvaluatorReceiptAuthError("evaluator authentication signature invalid")
    return dict(payload)
