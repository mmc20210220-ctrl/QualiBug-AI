from __future__ import annotations


def test_cleanup_observation_authority_is_wired_into_all_fixture_entrypoints() -> None:
    import inspect
    import ai_test_asset_center.runtime_binding_graph as binding
    import ai_test_asset_center.experiment_fixture_materializer as fixtures
    import ai_test_asset_center.multi_level_dependency_chain as dependencies

    modules = [binding, fixtures, dependencies]
    for module in modules:
        source = inspect.getsource(module)
        assert "resolve_cleanup_observation" in source, module.__name__
