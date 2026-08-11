"""Blocker-attribution facade with complete cleanup reason registration.

The historical attribution engine lives in ``_blocker_attribution_mechanics``.
This facade keeps that implementation intact and extends its single reason-code
authority for cleanup outcomes now emitted by the governed experiment finalizer.
No free-form detail matching is introduced.
"""
from __future__ import annotations

from typing import Any

from . import _blocker_attribution_mechanics as _core
from ._blocker_attribution_mechanics import *  # noqa: F401,F403


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


# Phase-A attribution and the public registry must describe the same terminal
# vocabulary.  Keep both maps synchronized because legacy blocker projections
# still consult _REASON_ATTRIBUTION while newer funnel diagnostics call
# profile_reason_code / REASON_CODE_REGISTRY.
_REASON_ATTRIBUTION = dict(_core._REASON_ATTRIBUTION)
_REASON_ATTRIBUTION.update({
    "HARNESS_CLEANUP_RESPONSE_REJECTED": (
        "CLEANUP_CAPABILITY_GAP",
        "RECOVERABLE",
        False,
    ),
    "HARNESS_CLEANUP_FAILURE_UNATTRIBUTED": (
        "CLEANUP_CAPABILITY_GAP",
        "UNKNOWN",
        False,
    ),
})

REASON_CODE_REGISTRY = dict(_core.REASON_CODE_REGISTRY)
REASON_CODE_REGISTRY.update({
    "HARNESS_CLEANUP_RESPONSE_REJECTED": _core._reason_definition(
        "CLEANUP_CAPABILITY_GAP",
        recoverability="RECOVERABLE",
    ),
    "HARNESS_CLEANUP_FAILURE_UNATTRIBUTED": _core._reason_definition(
        "CLEANUP_CAPABILITY_GAP",
        recoverability="UNKNOWN",
    ),
})

_core._REASON_ATTRIBUTION = _REASON_ATTRIBUTION
_core.REASON_CODE_REGISTRY = REASON_CODE_REGISTRY

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "REASON_CODE_REGISTRY",
    }
)
