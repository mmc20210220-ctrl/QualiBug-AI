"""First-class project/root binder for System Behavior Space loading.

Owned by the discovery compatibility entrypoint. Runtime monkey-patches must
not replace ``run_v12_pipeline`` to set this context.
"""
from __future__ import annotations

import contextvars
from pathlib import Path
from typing import Any

BEHAVIOR_SPACE_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_system_behavior_space_context",
    default={},
)

# Set when run_v12_pipeline binds context natively (health/readiness marker).
FIRST_CLASS_CONTEXT_BINDER = True


def set_behavior_space_context(project: str, root: Path | str) -> contextvars.Token:
    return BEHAVIOR_SPACE_CONTEXT.set({
        "project": str(project or "").strip(),
        "root": Path(root),
    })


def reset_behavior_space_context(token: contextvars.Token) -> None:
    BEHAVIOR_SPACE_CONTEXT.reset(token)


def get_behavior_space_context() -> dict[str, Any]:
    value = BEHAVIOR_SPACE_CONTEXT.get({})
    return dict(value) if isinstance(value, dict) else {}
