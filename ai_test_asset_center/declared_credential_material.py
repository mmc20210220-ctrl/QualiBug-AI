"""Declared credential material authority shared by runtime consumers.

Credential coordinates may resolve to plaintext legacy values or to ``enc$v1$``
values written by the private-pilot credential surface. Runtime consumers must
never send the encrypted envelope to the target as if it were a password/API
key.

For encrypted material this module uses only an already configured environment
key or an already existing local private-pilot key file. It never provisions a
new key merely because a runtime read asked to decrypt an old credential: doing
so would create a different key and turn an unavailable credential into a
misleading authentication failure.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .credential_crypto import (
    CredentialDecryptionError,
    decrypt,
    is_encrypted,
)

SCHEMA_VERSION = "qualibug.declared-credential-material.v1"
_KEY_ENV = "QUALIBUG_CRED_ENC_KEY"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _load_existing_local_key(root: Path) -> tuple[bool, str]:
    """Load the existing private-pilot key into the process, never create one."""

    if _text(os.environ.get(_KEY_ENV)):
        return True, "environment"
    key_path = (
        Path(root)
        / "platform_workspace"
        / ".secrets"
        / "credential_encryption.key"
    )
    if not key_path.is_file():
        return False, "DECLARED_CREDENTIAL_KEY_UNAVAILABLE"
    try:
        key = key_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False, "DECLARED_CREDENTIAL_KEY_UNREADABLE"
    if not key:
        return False, "DECLARED_CREDENTIAL_KEY_EMPTY"
    os.environ[_KEY_ENV] = key
    return True, "existing_local_key_file"


def prepare_declared_credential_decryption(root: Path) -> dict[str, Any]:
    """Prepare runtime decryption from existing authority without creating keys."""

    ready, source = _load_existing_local_key(Path(root))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "READY" if ready else "UNAVAILABLE",
        "reason_code": "" if ready else source,
        "key_source": source if ready else "",
        "key_created": False,
        "secret_value_persisted": False,
    }


def resolve_declared_credential_material(
    value: Any,
    *,
    root: Path,
) -> tuple[str, dict[str, Any]]:
    """Return usable plaintext material plus a non-secret authority receipt."""

    raw = _text(value)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "UNRESOLVED",
        "reason_code": "",
        "encrypted_at_rest": bool(is_encrypted(raw)),
        "key_source": "",
        "secret_value_persisted": False,
    }
    if not raw:
        return "", {
            **receipt,
            "reason_code": "DECLARED_CREDENTIAL_MATERIAL_EMPTY",
        }
    if not is_encrypted(raw):
        return raw, {
            **receipt,
            "status": "RESOLVED",
            "authority": "declared_plaintext_compatibility",
            "encrypted_at_rest": False,
        }

    key_receipt = prepare_declared_credential_decryption(Path(root))
    if _text(key_receipt.get("status")) != "READY":
        return "", {
            **receipt,
            "reason_code": _text(key_receipt.get("reason_code"))
            or "DECLARED_CREDENTIAL_KEY_UNAVAILABLE",
            "key_source": "",
        }
    key_source = _text(key_receipt.get("key_source"))
    try:
        resolved = decrypt(raw)
    except CredentialDecryptionError:
        return "", {
            **receipt,
            "reason_code": "DECLARED_CREDENTIAL_DECRYPT_FAILED",
            "key_source": key_source,
        }
    except Exception:
        return "", {
            **receipt,
            "reason_code": "DECLARED_CREDENTIAL_DECRYPT_FAILED",
            "key_source": key_source,
        }
    if not _text(resolved) or is_encrypted(resolved):
        return "", {
            **receipt,
            "reason_code": "DECLARED_CREDENTIAL_DECRYPT_RESULT_INVALID",
            "key_source": key_source,
        }
    return _text(resolved), {
        **receipt,
        "status": "RESOLVED",
        "authority": "credential_crypto_authenticated_decryption",
        "encrypted_at_rest": True,
        "key_source": key_source,
    }


__all__ = [
    "SCHEMA_VERSION",
    "prepare_declared_credential_decryption",
    "resolve_declared_credential_material",
]
