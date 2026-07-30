"""Compatibility helpers for explicit Probe admission.

This module no longer rewrites ``_api._probes_from_asset`` or
``_linking._probes_from_asset``. The single composition root calls the pure policy
in :mod:`probe_policy` after all runtime gates are known.
"""
from __future__ import annotations

from typing import Any, Callable

from .probe_policy import build_gated_probes, probe_generation_allowed


def guard_probe_builder(
    builder: Callable[[dict[str, Any], int], list[dict[str, Any]]]
) -> Callable[[dict[str, Any], int], list[dict[str, Any]]]:
    """Return a local guarded callable without modifying the supplied module."""

    def guarded(asset: dict[str, Any], max_count: int = 140) -> list[dict[str, Any]]:
        return build_gated_probes(asset, max_count, compiler=builder)

    return guarded


def install_scenario_execution_probe_guard() -> None:
    """Deprecated compatibility no-op; global Probe patching has been removed."""
    return None


__all__ = [
    "probe_generation_allowed",
    "guard_probe_builder",
    "install_scenario_execution_probe_guard",
]
