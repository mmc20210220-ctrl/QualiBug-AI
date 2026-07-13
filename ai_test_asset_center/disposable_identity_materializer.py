"""Disposable identity field materialization for governed non-production writes.

Repeated source-bound probes must not reuse customer/demo identity literals such
as documented emails or phone numbers. This module creates deterministic-shaped
but per-call unique values for disposable fixture creation while preserving the
specific field a fuzzer is mutating.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Iterable

_IDENTITY_ANCHOR_TOKENS = (
    "email",
    "phone",
    "mobile",
    "username",
    "login",
    "account",
)


def disposable_identity_nonce(*parts: Any) -> str:
    seed = "|".join(
        [
            *(str(part or "") for part in parts),
            str(time.time_ns()),
            str(os.getpid()),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def has_disposable_identity_anchor(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            key_l = _normalize_key(str(key))
            if any(token in key_l for token in _IDENTITY_ANCHOR_TOKENS):
                return True
            if has_disposable_identity_anchor(child):
                return True
    if isinstance(value, list):
        return any(has_disposable_identity_anchor(child) for child in value)
    return False


def materialize_disposable_identity_fields(
    value: Any,
    nonce: str,
    *,
    skip_keys: Iterable[str] | None = None,
    prefix: str = "",
) -> tuple[Any, list[str]]:
    """Return ``value`` with reusable identity literals replaced.

    ``skip_keys`` preserves the exact parameter currently under mutation, so the
    fuzzer still tests the requested invalid value while avoiding uniqueness
    collisions in the rest of the disposable identity fixture.
    """

    skipped = {_normalize_path(item) for item in (skip_keys or []) if str(item or "").strip()}
    materialized_fields: list[str] = []
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if _should_skip_materialization(path, str(key), skipped):
                rendered[str(key)] = child
                continue
            replacement = disposable_identity_value_for_key(str(key), child, nonce)
            if replacement is not None:
                rendered[str(key)] = replacement
                materialized_fields.append(path)
                continue
            rendered_child, child_fields = materialize_disposable_identity_fields(
                child,
                nonce,
                skip_keys=skipped,
                prefix=path,
            )
            rendered[str(key)] = rendered_child
            materialized_fields.extend(child_fields)
        return rendered, materialized_fields
    if isinstance(value, list):
        rendered_items: list[Any] = []
        for index, child in enumerate(value):
            rendered_child, child_fields = materialize_disposable_identity_fields(
                child,
                nonce,
                skip_keys=skipped,
                prefix=f"{prefix}[{index}]",
            )
            rendered_items.append(rendered_child)
            materialized_fields.extend(child_fields)
        return rendered_items, materialized_fields
    return value, materialized_fields


def disposable_identity_value_for_key(key: str, value: Any, nonce: str) -> Any:
    if not isinstance(value, str):
        return None
    key_l = _normalize_key(str(key))
    if not key_l:
        return None
    if "email" in key_l:
        return f"qb-auto-{nonce}@qualibug.local"
    if any(token in key_l for token in ("phone", "mobile")):
        return "155" + _numeric_suffix(nonce, 8)
    if any(token in key_l for token in ("username", "login", "account")) and "password" not in key_l:
        return f"qb_auto_{key_l}_{nonce}"
    if key_l in {"name", "displayname", "fullname", "nickname"} or key_l.endswith("name"):
        return f"qb_auto_{key_l}_{nonce}"
    if "password" in key_l or "credential" in key_l or "secret" in key_l:
        return f"QbAuto!{nonce[:8]}1"
    return None


def _numeric_suffix(nonce: str, width: int) -> str:
    try:
        number = int(str(nonce or "0"), 16)
    except ValueError:
        number = 0
    return str(number % (10 ** width)).zfill(width)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _normalize_path(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", str(value or "").lower()).strip(".")


def _should_skip_materialization(path: str, key: str, skipped: set[str]) -> bool:
    if not skipped:
        return False
    normalized_path = _normalize_path(path)
    normalized_key = _normalize_path(key)
    return normalized_path in skipped or normalized_key in skipped
