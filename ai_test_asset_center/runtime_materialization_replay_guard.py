"""Install Runtime Materialization checks for persisted Experiment replay.

A replay/regression process may load ``experiment_executor`` without compiling a new plan first.
This explicit runtime-composition hook installs only the existing preflight and Finalizer wrappers;
it does not import/build an enterprise knowledge asset and does not create another executor.
"""
from __future__ import annotations

from . import runtime_materialization_experiment_bridge as _bridge


def install_runtime_materialization_replay_guard() -> None:
    _bridge._install_preflight_guard()
    _bridge._install_finalizer_receipt()


__all__ = ["install_runtime_materialization_replay_guard"]
