"""Public fixture materializer facade.

The core module owns fixture DAG/data binding. The composed wrapper adds
compiled state-precondition execution before measured business steps.
"""
from .experiment_fixture_materializer_core import *  # noqa: F401,F403
from .experiment_fixture_materializer_with_preconditions import (
    materialize_experiment_fixtures,
)
