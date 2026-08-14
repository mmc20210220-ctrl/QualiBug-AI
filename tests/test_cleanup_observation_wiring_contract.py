from __future__ import annotations


def test_cleanup_observation_authority_is_live_and_wired_into_runtime() -> None:
    """Pin the post-cleanup observation contract without source-text guessing.

    The ``resolve_cleanup_observation`` authority module must be importable and
    resolve a source-declared GET/HEAD readback for an identity-bound cleanup
    route (the module is live, not a broken import). The runtime post-cleanup
    observation resolver (``_declared_observation_path``) is provided by
    ``experiment_runtime_support`` and consumed by ``experiment_cleanup_executor_core``
    — the fixture-entrypoint path that actually executes cleanup — so cleanup
    observation is wired end-to-end, not a dead symbol in a facade's source.
    """
    import inspect

    import ai_test_asset_center.cleanup_observation_authority as authority
    import ai_test_asset_center.experiment_cleanup_executor_core as cleanup_core
    import ai_test_asset_center.experiment_runtime_support as runtime_support

    # 1. The authority module is live and functional.
    assert callable(authority.resolve_cleanup_observation)
    receipt = authority.resolve_cleanup_observation(
        {"id": "create-order", "method": "POST", "path": "/api/orders"},
        {"id": "delete-order", "method": "DELETE", "path": "/api/orders/{id}"},
        behavior_ir={
            "operations": [
                {"id": "create-order", "method": "POST", "path": "/api/orders"},
                {"id": "delete-order", "method": "DELETE", "path": "/api/orders/{id}"},
                {"id": "get-order", "method": "GET", "path": "/api/orders/{id}"},
            ]
        },
    )
    assert receipt["status"] == "RESOLVED"
    assert receipt["authority"] == "exact_cleanup_identity_read"

    # 2. The runtime resolver is the same object consumed by the cleanup executor.
    assert callable(runtime_support._declared_observation_path)
    assert cleanup_core._declared_observation_path is runtime_support._declared_observation_path
    assert "_declared_observation_path(" in inspect.getsource(cleanup_core)
