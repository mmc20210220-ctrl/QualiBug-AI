"""At-rest encryption for sensitive credential fields stored in config files.

Uses PBKDF2-HMAC-SHA256 for key derivation and an HMAC-keystream XOR cipher
(standard library only --- no external dependencies). The master key is read
from the ``QUALIBUG_CRED_ENC_KEY`` environment variable.

When no master key is configured, ``encrypt`` emits a clear warning (and in
production mode refuses to write credentials), so operators notice the gap
before secrets are persisted in plaintext.

Encrypted values use the format::

    enc$v1$<salt_b64>$<nonce_b64>$<tag_b64>$<ct_b64>

This prevents trivial secret recovery if ``multi_service_config.json`` is
read off disk; it is not a substitute for OS-level key management (KMS /
keyring) in high-assurance deployments.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets

logger = logging.getLogger(__name__)

_MASTER_KEY_ENV = "QUALIBUG_CRED_ENC_KEY"
_PBKDF2_ITER = 100_000
_PREFIX = "enc$v1$"


class CredentialDecryptionError(ValueError):
    """An encrypted credential cannot be authenticated or decrypted."""


def _master_key() -> bytes | None:
    raw = os.environ.get(_MASTER_KEY_ENV, "").strip()
    return raw.encode("utf-8") if raw else None


def _is_production() -> bool:
    return os.environ.get("QUALIBUG_PRODUCTION", "").strip() in ("1", "true", "yes")


def ensure_credential_key() -> str:
    """Verify the credential encryption key is configured.

    Returns ``"ok"`` when available, ``"plaintext_warning"`` when missing but
    non-production, or raises ``RuntimeError`` when missing in production.
    Should be called at startup by every entrypoint that may write credentials.
    """
    if _master_key():
        return "ok"
    if _is_production():
        raise RuntimeError(
            f"{_MASTER_KEY_ENV} is not set.  "
            "Refusing to run in production with credential encryption disabled."
        )
    logger.warning(
        "%s is not set --- credentials will be stored as plaintext.  "
        "Set this environment variable to enable at-rest encryption.",
        _MASTER_KEY_ENV,
    )
    return "plaintext_warning"


def _derive_keys(master: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Derive independent encryption and MAC keys from the master key."""
    enc_key = hashlib.pbkdf2_hmac("sha256", master, salt + b"enc", _PBKDF2_ITER, dklen=32)
    mac_key = hashlib.pbkdf2_hmac("sha256", master, salt + b"mac", _PBKDF2_ITER, dklen=32)
    return enc_key, mac_key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Generate a pseudorandom keystream via HMAC-SHA256 in counter mode."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str) -> str:
    """Encrypt *plaintext*.

    Returns a prefixed ciphertext string.  When no master key is configured
    in non-production mode a warning is emitted and the plaintext is returned
    unchanged.  In production mode the call refuses to write credentials.
    """
    if not plaintext:
        return plaintext
    master = _master_key()
    if not master:
        if _is_production():
            raise RuntimeError(
                f"{_MASTER_KEY_ENV} is not set --- refusing to write credentials "
                "in plaintext while QUALIBUG_PRODUCTION=1."
            )
        logger.warning(
            "QUALIBUG_CRED_ENC_KEY is not set --- storing credential in plaintext.  "
            "Set the environment variable to enable at-rest encryption."
        )
        return plaintext
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    enc_key, mac_key = _derive_keys(master, salt)
    pt = plaintext.encode("utf-8")
    ks = _keystream(enc_key, nonce, len(pt))
    ct = bytes(a ^ b for a, b in zip(pt, ks))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    return (
        _PREFIX
        + base64.b64encode(salt).decode() + "$"
        + base64.b64encode(nonce).decode() + "$"
        + base64.b64encode(tag).decode() + "$"
        + base64.b64encode(ct).decode()
    )


def decrypt(value: str) -> str:
    """Decrypt a value produced by :func:`encrypt`.

    Non-encrypted (plaintext) values are returned unchanged so that legacy
    config files keep working. Encrypted values fail closed when their key is
    unavailable, their envelope is malformed, or authentication fails.
    """
    if not isinstance(value, str) or not value:
        return value or ""
    if not value.startswith(_PREFIX):
        return value  # plaintext — backward compatibility
    rest = value[len(_PREFIX):]
    parts = rest.split("$")
    if len(parts) != 4:
        raise CredentialDecryptionError("encrypted credential envelope is malformed")
    try:
        salt = base64.b64decode(parts[0], validate=True)
        nonce = base64.b64decode(parts[1], validate=True)
        tag = base64.b64decode(parts[2], validate=True)
        ct = base64.b64decode(parts[3], validate=True)
    except Exception as exc:
        raise CredentialDecryptionError("encrypted credential envelope is malformed") from exc
    if len(salt) != 16 or len(nonce) != 16 or len(tag) != hashlib.sha256().digest_size:
        raise CredentialDecryptionError("encrypted credential envelope has invalid field sizes")
    master = _master_key()
    if not master:
        raise CredentialDecryptionError("encrypted credential master key is unavailable")
    enc_key, mac_key = _derive_keys(master, salt)
    expected_tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise CredentialDecryptionError("encrypted credential authentication failed")
    ks = _keystream(enc_key, nonce, len(ct))
    pt = bytes(a ^ b for a, b in zip(ct, ks))
    try:
        return pt.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialDecryptionError("encrypted credential payload is not valid UTF-8") from exc


def is_encrypted(value: str) -> bool:
    """Return True if *value* looks like an encrypted credential blob."""
    return isinstance(value, str) and value.startswith(_PREFIX)
