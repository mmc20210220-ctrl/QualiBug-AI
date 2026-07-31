"""Attach the compiled causal slice before the existing plan wrapper executes."""
from __future__ import annotations

import functools
from typing import Any

_INSTALL_MARKER = "__qualibug_operation_causality_attachment_v1__"


def install_operation_causality_attachment() -> None:
    from . import operation_causality_runtime as runtime

    original = getattr(runtime, "prepare_operation_causality_preflight", None)
    if not callable(original) or getattr(original, _INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        exp = kwargs.get("exp")
        observations = kwargs.get("observations")
        if isinstance(exp, dict) and isinstance(observations, dict):
            runtime.attach_operation_causality_experiment(exp, observations)
        return original(*args, **kwargs)

    setattr(wrapped, _INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    runtime.prepare_operation_causality_preflight = wrapped


__all__ = ["install_operation_causality_attachment"]
