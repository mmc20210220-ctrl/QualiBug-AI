"""Reject incomplete semantic rule frames at every parsing authority boundary.

The public ``_parsing`` facade copies symbols from ``_parsing_mechanics`` during
import.  Guarding only that copied facade function leaves direct mechanics
callers unprotected, so marker-only lines such as OpenAPI ``required:`` or a
403 response description can still enter the knowledge asset with an empty
behavior and later abort the whole Behavior IR build.

Install the same fail-closed wrapper on both the facade and its mechanics
authority.  No behavior is invented: an incomplete frame is simply rejected.
"""
from __future__ import annotations

from typing import Any


def _install_one(target: Any) -> None:
    current = getattr(target, "_semantic_rule_frame", None)
    if not callable(current):
        return
    if getattr(current, "_qualibug_rejects_empty_behavior", False):
        return

    def _semantic_rule_frame(statement: str) -> dict[str, Any]:
        frame = current(statement)
        if frame and not target._norm(frame.get("behavior")):
            return {}
        return frame

    _semantic_rule_frame._qualibug_rejects_empty_behavior = True
    target._semantic_rule_frame = _semantic_rule_frame


def install_semantic_rule_frame_guard(parsing: Any) -> None:
    """Install the guard on the facade and the underlying mechanics module."""

    _install_one(parsing)
    core = getattr(parsing, "_core", None)
    if core is not None:
        _install_one(core)
