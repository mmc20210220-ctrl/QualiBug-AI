"""Public fixture materializer facade.

The core module owns fixture DAG/data binding. The composed wrapper proves the
frozen FlowDataRequirement and establishes compiled state preconditions before
measured business steps. Every prior non-dunder core symbol remains available
from this public module.
"""
from . import experiment_fixture_materializer_core as _core

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

from .experiment_fixture_materializer_with_preconditions import (
    materialize_experiment_fixtures,
)

__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
