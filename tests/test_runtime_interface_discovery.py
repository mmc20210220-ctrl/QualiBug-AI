import pytest
import json
from pathlib import Path


def _documented_operations() -> list[dict[str, object]]:
    return [
        {"method": "GET", "path": "/api/orders", "operation_id": "listOrders"},
        {"method": "GET", "path": "/api/orders/{id}", "operation_id": "getOrder"},
        {"method": "POST", "path": "/api/orders/{id}/cancel", "operation_id": "cancelOrder"},
        {"method": "GET", "path": "/api/reports/sales", "operation_id": "salesReport"},
        {"method": "GET", "path": "/api/reports/users", "operation_id": "userReport"},
        {"method": "POST", "path": "/api/products/admin", "operation_id": "createProduct"},
    ]


def test_runtime_interface_candidates_are_source_derived_read_only_and_bounded() -> None:
    from ai_test_asset_center.runtime_interface_discovery import (
        plan_runtime_interface_candidates,
    )

    plan = plan_runtime_interface_candidates(
        _documented_operations(),
        action_markers=["export", "history"],
        max_candidates=10,
    )

    assert plan["schema_version"] == "qualibug.runtime-interface-discovery-plan.v1"
    assert 0 < plan["candidate_count"] <= 10
    assert plan["truncated"] is True
    assert all(row["method"] == "GET" for row in plan["candidates"])
    assert all("{" not in row["path"] and ":" not in row["path"] for row in plan["candidates"])
    assert all(row["source_refs"] for row in plan["candidates"])
    assert "/api/orders" not in {row["path"] for row in plan["candidates"]}
    assert "/api/orders/export" in {row["path"] for row in plan["candidates"]}
    assert "/api/reports/export" in {
        row["path"] for row in plan["candidates"]
    }
    assert "/api/reports/orders/export" in {
        row["path"] for row in plan["candidates"]
    }


def test_general_resource_prefix_collision_is_not_planned() -> None:
    from ai_test_asset_center.runtime_interface_discovery import (
        plan_runtime_interface_candidates,
    )

    documented = [
        *_documented_operations(),
        {"method": "GET", "path": "/api/cart/items", "operation_id": "listCart"},
    ]

    plan = plan_runtime_interface_candidates(
        documented,
        action_markers=["health"],
        max_candidates=500,
    )
    paths = {row["path"] for row in plan["candidates"]}

    assert "/api/cart/health" in paths
    assert "/api/carts/health" not in paths
    assert "carts" in plan["general_resource_shadowed"]
    assert plan["general_resource_shadowed_count"] >= 1


def test_nested_collection_candidates_are_reachable_within_the_existing_budget() -> None:
    from ai_test_asset_center.runtime_interface_discovery import (
        plan_runtime_interface_candidates,
    )

    plan = plan_runtime_interface_candidates(
        _documented_operations(),
        action_markers=["health"],
        max_candidates=800,
    )

    paths = [row["path"] for row in plan["candidates"]]
    assert "/api/users/addresses" in paths
    address_row = plan["candidates"][paths.index("/api/users/addresses")]
    assert address_row["method"] == "GET"
    assert address_row["derivation"] == "nested_resource_collection_lattice"
    assert address_row["source_refs"]


def test_runtime_interface_observation_requires_real_request_receipt() -> None:
    from ai_test_asset_center.runtime_interface_discovery import (
        build_runtime_interface_observation_receipt,
        plan_runtime_interface_candidates,
    )

    candidate = plan_runtime_interface_candidates(
        _documented_operations(),
        action_markers=["export"],
        max_candidates=50,
    )["candidates"][0]

    with pytest.raises(ValueError, match="runtime_interface_request_receipt_missing"):
        build_runtime_interface_observation_receipt(
            candidate,
            {"status_code": 401, "request_receipt_id": ""},
        )


def test_runtime_interface_observation_requires_authenticated_confirmation_for_auth_boundary() -> None:
    from ai_test_asset_center.runtime_interface_discovery import (
        build_runtime_interface_observation_receipt,
        merge_runtime_discovered_operations,
        plan_runtime_interface_candidates,
    )

    candidates = plan_runtime_interface_candidates(
        _documented_operations(),
        action_markers=["export"],
        max_candidates=50,
    )["candidates"]
    candidate = next(row for row in candidates if row["path"] == "/api/orders/export")

    auth_boundary_only = build_runtime_interface_observation_receipt(
        candidate,
        {
            "status_code": 401,
            "request_receipt_id": "request-receipt-1",
            "response_fingerprint": "a" * 64,
        },
    )
    discovered = build_runtime_interface_observation_receipt(
        candidate,
        {
            "status_code": 401,
            "request_receipt_id": "request-receipt-1",
            "response_fingerprint": "a" * 64,
            "confirmation_status_code": 200,
            "confirmation_request_receipt_id": "request-receipt-confirmation",
            "confirmation_response_fingerprint": "d" * 64,
        },
    )
    absent = build_runtime_interface_observation_receipt(
        candidate,
        {
            "status_code": 404,
            "request_receipt_id": "request-receipt-2",
            "response_fingerprint": "b" * 64,
        },
    )

    assert auth_boundary_only["status"] == "INDETERMINATE"
    assert "operation" not in auth_boundary_only
    assert discovered["status"] == "DISCOVERED"
    assert discovered["operation"]["method"] == "GET"
    assert discovered["operation"]["path"] == "/api/orders/export"
    assert discovered["operation"]["derivation"] == "runtime-observed"
    assert absent["status"] == "NOT_FOUND"
    assert "operation" not in absent

    merged = merge_runtime_discovered_operations(
        _documented_operations(),
        [discovered, absent],
    )
    assert len(merged) == len(_documented_operations()) + 1
    assert sum(row["path"] == "/api/orders/export" for row in merged) == 1


def test_runtime_interface_observation_rejects_write_probe_candidates() -> None:
    from ai_test_asset_center.runtime_interface_discovery import (
        build_runtime_interface_observation_receipt,
    )

    with pytest.raises(ValueError, match="runtime_interface_candidate_not_read_only"):
        build_runtime_interface_observation_receipt(
            {
                "candidate_id": "surface-candidate-1",
                "method": "POST",
                "path": "/api/resources/export",
                "source_refs": [{"source_id": "api_spec"}],
            },
            {
                "status_code": 404,
                "request_receipt_id": "request-receipt-1",
                "response_fingerprint": "c" * 64,
            },
        )


def test_runtime_interface_default_actions_come_from_policy_asset() -> None:
    import ai_test_asset_center.runtime_interface_discovery as discovery_module

    from ai_test_asset_center.runtime_interface_discovery import (
        load_runtime_interface_discovery_actions,
        load_runtime_interface_discovery_budget,
        plan_runtime_interface_candidates,
    )

    actions = load_runtime_interface_discovery_actions()
    budget = load_runtime_interface_discovery_budget()
    assert "export" in actions
    assert "all" in actions
    policy = json.loads(
        (Path(discovery_module.__file__).parent / "policies" / "semantic_lexicon.json")
        .read_text(encoding="utf-8")
    )
    assert budget == policy["runtime_interface_discovery_max_candidates"]
    plan = plan_runtime_interface_candidates(
        _documented_operations(),
        action_markers=None,
        max_candidates=10,
    )
    assert plan["policy_action_count"] == len(actions)


def test_confirmation_tokens_are_unique_active_declared_accounts_only(
    tmp_path,
) -> None:
    from ai_test_asset_center.runtime_interface_discovery import (
        load_runtime_interface_confirmation_tokens,
    )

    account_dir = tmp_path / "platform_inputs" / "sample"
    account_dir.mkdir(parents=True)
    (account_dir / "test_accounts.json").write_text(
        json.dumps({
            "active-a": {"role": "reader", "status": "active", "token": "token-a"},
            "disabled": {"role": "reader", "status": "disabled", "token": "token-disabled"},
            "active-duplicate": {"role": "operator", "status": "active", "token": "token-a"},
            "active-b": {"role": "admin", "authenticated_status": "active", "token": "token-b"},
        }),
        encoding="utf-8",
    )

    assert load_runtime_interface_confirmation_tokens(tmp_path, "sample") == [
        "token-a",
        "token-b",
    ]


def test_confirmation_tokens_reuse_configured_credential_authority(
    tmp_path,
    monkeypatch,
) -> None:
    from ai_test_asset_center.runtime_interface_discovery import (
        load_runtime_interface_confirmation_tokens,
    )

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._configured_credential_tokens",
        lambda root, project, *, base_url="": {
            "buyer@example.com": "token-buyer",
            "buyer": "token-buyer",
            "admin@example.com": "token-admin",
        },
    )

    assert load_runtime_interface_confirmation_tokens(tmp_path, "sample") == [
        "token-buyer",
        "token-admin",
    ]


def test_confirmation_token_catalog_fails_fast_when_malformed(tmp_path) -> None:
    from ai_test_asset_center.runtime_interface_discovery import (
        load_runtime_interface_confirmation_tokens,
    )

    account_dir = tmp_path / "platform_inputs" / "sample"
    account_dir.mkdir(parents=True)
    (account_dir / "test_accounts.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="runtime_interface_actor_catalog_invalid"):
        load_runtime_interface_confirmation_tokens(tmp_path, "sample")


def test_runtime_interface_execution_is_ledger_ready_and_never_emits_findings(
    monkeypatch,
) -> None:
    import ai_test_asset_center.runtime_interface_discovery as discovery

    plan = discovery.plan_runtime_interface_candidates(
        _documented_operations(),
        action_markers=["export"],
        max_candidates=2,
    )
    responses = iter(
        [
            {"status": 401, "body": {"error": "unauthorized"}, "headers": {}},
            {"status": 200, "body": {"items": []}, "headers": {}},
            {"status": 404, "body": {"error": "missing"}, "headers": {}},
        ]
    )
    monkeypatch.setattr(discovery, "_http_request", lambda *args, **kwargs: next(responses))

    result = discovery.execute_runtime_interface_discovery(
        plan,
        base_url="http://127.0.0.1:8080",
        mainline_run={
            "run_id": "RUN-1",
            "campaign_id": "CMP-1",
            "target_id": "TARGET-1",
        },
        confirmation_tokens=["declared-test-actor-token"],
    )

    assert result["selected_count"] == 2
    assert result["executed_count"] == 2
    assert len(result["selected_rows"]) == 2
    assert len(result["compile_results"]) == 2
    assert len(result["execution_results"]) == 2
    assert len(result["gate_results"]) == 2
    assert len(result["observation_receipts"]) == 2
    assert len(result["discovered_operations"]) == 1
    assert result["findings"] == []
    assert all(
        receipt["operational_receipt"]["http_request_attempt_count"]
        == (2 if receipt["runtime_interface_observation"]["status_code"] == 401 else 1)
        for receipt in result["execution_results"].values()
    )
    assert all(
        receipt["status"] == "REJECTED"
        and receipt["reason_code"] == "SURFACE_DISCOVERY_OBSERVATION_ONLY"
        for receipt in result["gate_results"].values()
    )


def test_authenticated_confirmation_stops_after_interface_is_proven(monkeypatch) -> None:
    import ai_test_asset_center.runtime_interface_discovery as discovery

    plan = discovery.plan_runtime_interface_candidates(
        _documented_operations(),
        action_markers=["export"],
        max_candidates=1,
    )
    calls: list[str] = []
    responses = iter([
        {"status": 401, "body": {}, "headers": {}},
        {"status": 403, "body": {}, "headers": {}},
        {"status": 200, "body": {}, "headers": {}},
    ])

    def request(*args, **kwargs):
        calls.append(str(kwargs.get("token") or "anonymous"))
        return next(responses)

    monkeypatch.setattr(discovery, "_http_request", request)
    result = discovery.execute_runtime_interface_discovery(
        plan,
        base_url="http://127.0.0.1:8080",
        mainline_run={
            "run_id": "RUN-1",
            "campaign_id": "CMP-1",
            "target_id": "TARGET-1",
        },
        confirmation_tokens=["token-1", "token-2", "token-never-used"],
    )

    assert calls == ["anonymous", "token-1", "token-2"]
    assert result["observation_receipts"][0]["status"] == "DISCOVERED"
    operational = next(iter(result["execution_results"].values()))["operational_receipt"]
    assert operational["http_request_attempt_count"] == 3
