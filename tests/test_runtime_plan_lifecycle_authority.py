from __future__ import annotations

import sys

from ai_test_asset_center import experiment_plan_executor as raw_plan
from ai_test_asset_center import experiment_plan_lifecycle_adapter as lifecycle
from ai_test_asset_center.job_async_runtime import install_job_async_execution_adapter
from ai_test_asset_center.operation_causality_runtime import (
    install_operation_causality_runtime,
)
from ai_test_asset_center.operation_causality_runtime_attachment import (
    install_operation_causality_attachment,
)


def _install_all() -> None:
    install_operation_causality_runtime()
    install_operation_causality_attachment()
    install_job_async_execution_adapter()


def test_runtime_plugins_wrap_only_the_private_transport_delegate() -> None:
    _install_all()

    assert raw_plan.execute_non_barrier_plans is lifecycle.execute_non_barrier_plans
    assert raw_plan.execute_non_barrier_plans.__module__ == (
        "ai_test_asset_center.experiment_plan_lifecycle_adapter"
    )
    delegate = lifecycle.current_raw_plan_delegate()
    assert delegate is not lifecycle.execute_non_barrier_plans
    assert getattr(delegate, "_qualibug_job_async_adapter", False) is True


def test_loaded_executor_aliases_are_republished_to_lifecycle_authority() -> None:
    from ai_test_asset_center import experiment_executor
    from ai_test_asset_center import experiment_executor_core
    from ai_test_asset_center import experiment_executor_governance

    _install_all()
    lifecycle.install_raw_plan_delegate(lifecycle.current_raw_plan_delegate())

    for module in (
        experiment_executor_core,
        experiment_executor_governance,
        experiment_executor,
    ):
        assert module.execute_non_barrier_plans is lifecycle.execute_non_barrier_plans
    assert sys.modules[
        "ai_test_asset_center.experiment_plan_executor"
    ].execute_non_barrier_plans is lifecycle.execute_non_barrier_plans
