"""Compatibility facade for discovery runtime execution support.

Historical helpers remain re-exported from
``discovery_runtime_execution_support_base``.  Multi-round continuation lives
in ``discovery_continuation_authority`` so preview formatting and exact resume
authority cannot drift inside the compatibility layer.
"""
from __future__ import annotations

from typing import Any

from . import discovery_runtime_execution_support_base as _base

# Preserve the complete historical module surface, including private helpers
# imported by discovery_runtime_execution and compatibility tests.
for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))

# Override only the continuation authority with the lossless implementation.
from .discovery_continuation_authority import (  # noqa: E402,F401
    _consume_pending_obligation_rounds,
    _continuation_obligation_universe,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _finalize_campaign(handle: Any, ledger: dict[str, Any]) -> dict[str, Any]:
    """Finalize through historical authority and release continuation capture."""
    campaign_id = ""
    try:
        campaign_id = _text(
            getattr(_base._campaign_object(handle), "campaign_id", "")
        )
    except Exception:
        campaign_id = ""
    try:
        return _base._finalize_campaign(handle, ledger)
    finally:
        if campaign_id:
            from .experiment_executor import clear_continuation_retry_receipts

            clear_continuation_retry_receipts(campaign_id)
