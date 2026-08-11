"""Truthful disposable account-locator materialization.

A governed non-production create may need a fresh account locator so repeated
runs do not collide with a documented/demo identity. That authority is narrow:
only explicit login locators (email/phone/mobile/username/login) are disposable.
Business names, lifecycle/status fields, passwords, credentials and generic
``account*`` fields are not rewritten here. UNIQUE business keys belong to the
separate DDL table-scoped authority.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .disposable_identity_materializer import _numeric_suffix


def _text(value: Any) -> str:
    return str(value or "").strip()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _path(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", ".", _text(value).lower()).strip(".")


def disposable_locator_kind(key: str) -> str:
    """Classify only explicit identity locators; return empty for business data."""

    normalized = _key(key)
    if not normalized:
        return ""
    if normalized == "email" or normalized.endswith("email") or normalized.endswith("emailaddress"):
        return "email"
    if (
        normalized in {"phone", "phonenumber", "mobile", "mobilenumber"}
        or normalized.endswith("phonenumber")
        or normalized.endswith("mobilenumber")
    ):
        return "phone"
    if normalized == "username" or normalized.endswith("username"):
        return "username"
    if normalized in {"login", "loginname"} or normalized.endswith("loginname"):
        return "login"
    return ""


def has_disposable_identity_anchor(value: Any) -> bool:
    """Whether the payload contains a concrete disposable locator string."""

    if isinstance(value, dict):
        for key, child in value.items():
            if disposable_locator_kind(str(key)) and isinstance(child, str) and child:
                return True
            if has_disposable_identity_anchor(child):
                return True
    elif isinstance(value, list):
        return any(has_disposable_identity_anchor(child) for child in value)
    return False


def _should_skip(path: str, key: str, skipped: set[str]) -> bool:
    if not skipped:
        return False
    normalized_path = _path(path)
    normalized_key = _path(key)
    return normalized_path in skipped or normalized_key in skipped


def disposable_locator_value(key: str, value: Any, nonce: str) -> Any:
    if not isinstance(value, str):
        return None
    kind = disposable_locator_kind(key)
    if kind == "email":
        return f"qb-auto-{nonce}@qualibug.local"
    if kind == "phone":
        return "155" + _numeric_suffix(nonce, 8)
    if kind in {"username", "login"}:
        return f"qb_auto_{kind}_{nonce}"
    return None


def materialize_disposable_identity_fields(
    value: Any,
    nonce: str,
    *,
    skip_keys: Iterable[str] | None = None,
    prefix: str = "",
) -> tuple[Any, list[str]]:
    """Rewrite only explicit account locators, preserving all other semantics."""

    skipped = {
        _path(item)
        for item in (skip_keys or [])
        if _text(item)
    }
    materialized: list[str] = []
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if _should_skip(path, str(key), skipped):
                rendered[str(key)] = child
                continue
            replacement = disposable_locator_value(str(key), child, nonce)
            if replacement is not None:
                rendered[str(key)] = replacement
                materialized.append(path)
                continue
            nested, nested_fields = materialize_disposable_identity_fields(
                child,
                nonce,
                skip_keys=skipped,
                prefix=path,
            )
            rendered[str(key)] = nested
            materialized.extend(nested_fields)
        return rendered, materialized
    if isinstance(value, list):
        rows: list[Any] = []
        for index, child in enumerate(value):
            nested, nested_fields = materialize_disposable_identity_fields(
                child,
                nonce,
                skip_keys=skipped,
                prefix=f"{prefix}[{index}]",
            )
            rows.append(nested)
            materialized.extend(nested_fields)
        return rows, materialized
    return value, materialized


__all__ = [
    "disposable_locator_kind",
    "has_disposable_identity_anchor",
    "disposable_locator_value",
    "materialize_disposable_identity_fields",
]
