"""Reject incomplete semantic rule frames before Behavior IR validation."""
from __future__ import annotations

from typing import Any


def install_semantic_rule_frame_guard(parsing: Any) -> None:
    current = parsing._semantic_rule_frame
    if getattr(current, "_qualibug_rejects_empty_behavior", False):
        return

    def _semantic_rule_frame(statement: str) -> dict[str, Any]:
        frame = current(statement)
        if frame and not parsing._norm(frame.get("behavior")):
            return {}
        return frame

    _semantic_rule_frame._qualibug_rejects_empty_behavior = True
    parsing._semantic_rule_frame = _semantic_rule_frame
