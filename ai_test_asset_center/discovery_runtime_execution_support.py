"""Compatibility facade with lossless pending-round continuation authority.

The current execution-support implementation is preserved byte-for-byte in
``discovery_runtime_execution_support_base``.  Only pending continuation is
overridden: the public pending preview may remain bounded while in-process
scheduling retains every eligible, not-yet-processed identity.
"""
from __future__ import annotations

from . import discovery_runtime_execution_support_base as _base
from .recall_pending_continuation_authority import (
    consume_pending_obligation_rounds as _exact_consume_pending_obligation_rounds,
)

# Preserve the complete historical module surface, including private helpers
# imported by discovery_runtime_execution and compatibility tests.
for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))

_consume_pending_obligation_rounds = _exact_consume_pending_obligation_rounds
