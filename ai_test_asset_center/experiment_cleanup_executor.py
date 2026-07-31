"""Public cleanup executor facade.

The core module retains the existing compensation implementation.  The
lifecycle adapter adds precondition-write visibility without changing cleanup
algorithms or public call sites.
"""
from .experiment_cleanup_executor_core import *  # noqa: F401,F403
from .experiment_cleanup_lifecycle_adapter import (
    execute_experiment_cleanup_compensation,
)
