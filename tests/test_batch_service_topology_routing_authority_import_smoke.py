from __future__ import annotations


def test_batch_service_topology_facade_imports_preserved_mechanics() -> None:
    import ai_test_asset_center._experiment_batch_executor_single_finding_mechanics as batch

    assert callable(batch.execute_selected_experiments)
    assert callable(batch._check_required_bindings)
    assert callable(batch._operation_coverage_budget)
