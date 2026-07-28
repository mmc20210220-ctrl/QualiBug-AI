"""Integrity hardening for upload fixture approval identities.

An approved copy can be revoked independently while its registered source remains
active. Re-approving that source must never revive the old ``binding_ref`` carried
by an already frozen browser contract. Each genuinely new approval therefore gets
one fresh approval-generation nonce; duplicate-active approval remains idempotent
and returns the existing binding unchanged.
"""
from __future__ import annotations

import contextvars
import hashlib
import secrets
from pathlib import Path
from typing import Any

from . import ui_upload_fixture_registry as _registry

_INSTALL_MARKER = "_qualibug_upload_fixture_registry_integrity_installed"
_ORIGINAL_APPROVE = "_qualibug_upload_fixture_approve_before_integrity"
ORIGINAL_BINDING_REF = "_qualibug_upload_fixture_binding_ref_before_integrity"
_APPROVAL_NONCE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "qualibug_ui_upload_fixture_approval_nonce",
    default="",
)


def install_upload_fixture_registry_integrity() -> None:
    if getattr(_registry, _INSTALL_MARKER, False):
        return
    original_approve = getattr(
        _registry,
        _ORIGINAL_APPROVE,
        _registry.approve_upload_fixture,
    )
    original_binding_ref = getattr(
        _registry,
        ORIGINAL_BINDING_REF,
        _registry._binding_ref,
    )
    setattr(_registry, _ORIGINAL_APPROVE, original_approve)
    setattr(_registry, ORIGINAL_BINDING_REF, original_binding_ref)

    def binding_ref_with_approval_generation(
        project: str,
        approved_ref: str,
        digest: str,
    ) -> str:
        nonce = _APPROVAL_NONCE.get()
        if not nonce:
            raise RuntimeError("ui_upload_fixture_approval_generation_missing")
        raw = (
            f"{project}|{approved_ref}|{digest}|approval-generation:{nonce}"
        ).encode("utf-8")
        return "uifb_" + hashlib.sha256(raw).hexdigest()[:20]

    def approve_without_binding_revival(
        project_id: str,
        *,
        fixture_id: str,
        root: Path | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = _APPROVAL_NONCE.set(secrets.token_hex(16))
        try:
            return original_approve(
                project_id,
                fixture_id=fixture_id,
                root=root,
                actor=actor,
            )
        finally:
            _APPROVAL_NONCE.reset(token)

    _registry._binding_ref = binding_ref_with_approval_generation
    _registry.approve_upload_fixture = approve_without_binding_revival
    setattr(_registry, _INSTALL_MARKER, True)


__all__ = ["install_upload_fixture_registry_integrity"]
