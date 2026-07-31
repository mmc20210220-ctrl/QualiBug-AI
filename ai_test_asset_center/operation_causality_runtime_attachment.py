"""Attach the compiled causal slice and clear private runtime-only values."""
from __future__ import annotations

import functools
import sys
from typing import Any

_PREFLIGHT_INSTALL_MARKER = "__qualibug_operation_causality_attachment_v1__"
_PHASE_INSTALL_MARKER = "__qualibug_operation_causality_private_cleanup_v1__"
_CAUSAL_ASSERTIONS_KEY = "operation_causality_assertions"
_CAUSAL_EXPERIMENT_KEY = "operation_causality_experiment"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clear_private_runtime_state(runtime: Any, observations: dict[str, Any]) -> None:
    observations.pop(runtime._PRIVATE_VALUES_KEY, None)
    observations.pop(_CAUSAL_ASSERTIONS_KEY, None)
    observations.pop(_CAUSAL_EXPERIMENT_KEY, None)


def install_operation_causality_attachment() -> None:
    from . import database_observer_experiment_runtime as phase_runtime
    from . import operation_causality_runtime as runtime

    preflight = getattr(runtime, "prepare_operation_causality_preflight", None)
    if callable(preflight) and not getattr(
        preflight, _PREFLIGHT_INSTALL_MARKER, False
    ):
        @functools.wraps(preflight)
        def preflight_wrapped(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            exp = kwargs.get("exp")
            observations = kwargs.get("observations")
            if isinstance(exp, dict) and isinstance(observations, dict):
                runtime.attach_operation_causality_experiment(exp, observations)
            return preflight(*args, **kwargs)

        setattr(preflight_wrapped, _PREFLIGHT_INSTALL_MARKER, True)
        setattr(preflight_wrapped, "__qualibug_original__", preflight)
        runtime.prepare_operation_causality_preflight = preflight_wrapped

    phase = getattr(phase_runtime, "execute_database_observer_phase", None)
    if callable(phase) and not getattr(phase, _PHASE_INSTALL_MARKER, False):
        @functools.wraps(phase)
        def phase_wrapped(
            exp: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            target = _text(
                kwargs.get("phase") or (args[0] if args else "")
            ).upper()
            observations = kwargs.get("observations")
            result = phase(exp, *args, **kwargs)
            if isinstance(observations, dict):
                before_failed = bool(
                    target == "BEFORE"
                    and (
                        _text(result.get("status")).upper() != "OBSERVED"
                        or result.get("blocked") is True
                    )
                )
                if before_failed or target == "AFTER":
                    _clear_private_runtime_state(runtime, observations)
            return result

        setattr(phase_wrapped, _PHASE_INSTALL_MARKER, True)
        setattr(phase_wrapped, "__qualibug_original__", phase)
        phase_runtime.execute_database_observer_phase = phase_wrapped
        executor = sys.modules.get(f"{__package__}.experiment_executor")
        if executor is not None:
            executor.execute_database_observer_phase = phase_wrapped


__all__ = ["install_operation_causality_attachment"]
