"""Stable public facade for relationship linking and Probe compilation.

The implementation is kept in ``_linking_impl``.  This facade owns public boundary
semantics, including the invariant that a non-positive Probe budget produces no
Probe at all.  No import-time replacement or process-global patch is involved.
"""
from __future__ import annotations

from typing import Any

from . import _linking_impl as _impl
from ._linking_impl import *  # noqa: F401,F403

__all__ = list(_impl.__all__)


def _probes_from_asset(
    asset: dict[str, Any], max_count: int = 140
) -> list[dict[str, Any]]:
    """Compile at most ``max_count`` Probes; zero means strict deferral."""
    limit = int(max_count)
    if limit <= 0:
        return []
    return _impl._probes_from_asset(asset, limit)


def __getattr__(name: str) -> Any:
    """Preserve direct private-symbol compatibility during the module split."""
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))
