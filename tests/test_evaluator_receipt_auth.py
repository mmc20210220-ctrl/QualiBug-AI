from __future__ import annotations

import hashlib
import json

import pytest

from ai_test_asset_center.evaluator_receipt_auth import (
    EVALUATOR_HMAC_KEY_ENV,
    EVALUATOR_HMAC_KEYRING_ENV,
    EvaluatorReceiptAuthError,
    seal_evaluator_artifact,
    verify_evaluator_artifact,
)


DOMAIN = "qualibug.test-artifact.v1"
FINGERPRINT = "artifact_fingerprint"
AUTHENTICATION = "artifact_authentication"
OLD_KEY = "old-evaluator-key-0123456789-abcdef"
NEW_KEY = "new-evaluator-key-0123456789-abcdef"


def _key_id(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:24]


def _seal(secret: str) -> dict:
    return seal_evaluator_artifact(
        {"schema_version": DOMAIN, "value": 1},
        signing_key=secret,
        domain=DOMAIN,
        fingerprint_field=FINGERPRINT,
        authentication_field=AUTHENTICATION,
    )


def _verify(artifact: dict) -> dict:
    return verify_evaluator_artifact(
        artifact,
        domain=DOMAIN,
        fingerprint_field=FINGERPRINT,
        authentication_field=AUTHENTICATION,
    )


def test_keyring_signs_with_active_key_and_verifies_retired_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(EVALUATOR_HMAC_KEY_ENV, raising=False)
    monkeypatch.setenv(
        EVALUATOR_HMAC_KEYRING_ENV,
        json.dumps({
            "active_key_id": _key_id(NEW_KEY),
            "keys": {
                _key_id(OLD_KEY): OLD_KEY,
                _key_id(NEW_KEY): NEW_KEY,
            },
        }),
    )

    current = seal_evaluator_artifact(
        {"schema_version": DOMAIN, "value": 2},
        domain=DOMAIN,
        fingerprint_field=FINGERPRINT,
        authentication_field=AUTHENTICATION,
    )
    retired = _seal(OLD_KEY)

    assert current[AUTHENTICATION]["key_id"] == _key_id(NEW_KEY)
    assert _verify(current) == current
    assert _verify(retired) == retired


def test_keyring_rejects_mislabeled_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(EVALUATOR_HMAC_KEY_ENV, raising=False)
    monkeypatch.setenv(
        EVALUATOR_HMAC_KEYRING_ENV,
        json.dumps({
            "active_key_id": "wrong-key-id",
            "keys": {"wrong-key-id": NEW_KEY},
        }),
    )

    with pytest.raises(EvaluatorReceiptAuthError, match="key_id mismatch"):
        _verify(_seal(NEW_KEY))
