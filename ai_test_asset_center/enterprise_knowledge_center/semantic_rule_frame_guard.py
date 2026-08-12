"""Reject incomplete semantic rule frames before Behavior IR validation."""
from __future__ import annotations

from typing import Any, Callable


def _guarded_semantic_rule_frame(
    current: Callable[[str], dict[str, Any]],
    *,
    norm: Callable[[Any], str],
) -> Callable[[str], dict[str, Any]]:
    if getattr(current, "_qualibug_rejects_empty_behavior", False):
        return current

    def _semantic_rule_frame(statement: str) -> dict[str, Any]:
        frame = current(statement)
        if frame and not norm(frame.get("behavior")):
            return {}
        return frame

    _semantic_rule_frame._qualibug_rejects_empty_behavior = True  # type: ignore[attr-defined]
    return _semantic_rule_frame


def install_semantic_rule_frame_guard(parsing: Any) -> None:
    """Install the guard on both the facade and its mechanics globals.

    ``_parsing`` re-exports functions from ``_parsing_mechanics``. Replacing only
    ``_parsing._semantic_rule_frame`` protects direct callers but does not alter
    the global looked up by ``_parsing_mechanics._rules_from_text``. That import
    split let empty frames from OpenAPI YAML (for example ``required:``) bypass
    the guard and later abort the entire Behavior IR build.
    """

    norm = parsing._norm
    parsing._semantic_rule_frame = _guarded_semantic_rule_frame(
        parsing._semantic_rule_frame,
        norm=norm,
    )

    rules_from_text = getattr(parsing, "_rules_from_text", None)
    rule_globals = getattr(rules_from_text, "__globals__", None)
    if not isinstance(rule_globals, dict):
        return
    mechanics_current = rule_globals.get("_semantic_rule_frame")
    if not callable(mechanics_current):
        return
    rule_globals["_semantic_rule_frame"] = _guarded_semantic_rule_frame(
        mechanics_current,
        norm=norm,
    )
