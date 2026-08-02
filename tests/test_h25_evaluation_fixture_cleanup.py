# -*- coding: utf-8 -*-
"""H25: evaluation API_SPEC must declare cleanup so empty collections can fixture-create."""
from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.adapter_capability import (
    observation_surfaces_for_adapters,
    resolve_available_adapters,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.discovery_runtime_planning import _api_operations
from ai_test_asset_center.enterprise_knowledge_center import (
    build_runtime_source_knowledge_overlay,
    merge_knowledge_asset_overlay,
)
from ai_test_asset_center.runtime_binding_graph import (
    _declared_cleanup_operations,
    _declared_fixture_setup,
)

ROOT = Path(__file__).resolve().parents[1]
EVAL_API = ROOT / "platform_inputs/evaluation-benchmark-mall-held-in-131/API_SPEC.md"
ASSET = (
    ROOT
    / "platform_outputs/benchmark_mall/enterprise_knowledge_center"
    / "enterprise_business_knowledge_asset.json"
)


def _bir_from_eval_api():
    api = EVAL_API.read_text(encoding="utf-8")
    asset = json.loads(ASSET.read_text(encoding="utf-8"))
    merged = merge_knowledge_asset_overlay(
        asset, build_runtime_source_knowledge_overlay(api_spec_text=api)
    )
    adapters = resolve_available_adapters(ROOT, "benchmark_mall", {})
    return build_behavior_ir_from_knowledge_asset(
        merged,
        project_id="benchmark_mall",
        api_operations=_api_operations(api),
        available_surfaces=observation_surfaces_for_adapters(adapters),
    )


def test_evaluation_api_declares_cart_and_address_delete_cleanup() -> None:
    text = EVAL_API.read_text(encoding="utf-8")
    assert "### DELETE /api/cart/items/:id" in text
    assert "### DELETE /api/users/addresses/:id" in text


def test_evaluation_api_generates_cart_and_address_fixture_setup() -> None:
    bir = _bir_from_eval_api()
    ops = [o for o in (bir.get("operations") or []) if isinstance(o, dict)]
    cart_get = next(
        o
        for o in ops
        if str(o.get("method") or "").upper() == "GET"
        and str(o.get("path") or "") == "/api/cart/items"
    )
    addr_get = next(
        o
        for o in ops
        if str(o.get("method") or "").upper() == "GET"
        and str(o.get("path") or "") == "/api/users/addresses"
    )
    assert _declared_cleanup_operations("/api/cart/items", behavior_ir=bir)
    assert _declared_cleanup_operations("/api/users/addresses", behavior_ir=bir)
    cart_setup = _declared_fixture_setup(cart_get, target="id", behavior_ir=bir)
    addr_setup = _declared_fixture_setup(addr_get, target="address_id", behavior_ir=bir)
    assert cart_setup.get("method") == "POST"
    assert cart_setup.get("path") == "/api/cart/items"
    assert cart_setup.get("body_template")
    assert addr_setup.get("method") == "POST"
    assert addr_setup.get("path") == "/api/users/addresses"
    assert addr_setup.get("body_template")


def test_source_backed_dependency_fixture_setup_for_address_id() -> None:
    """Order create body FK addressId can resolve via disposable address create."""
    from ai_test_asset_center.experiment_fixture_materializer_core import (
        _source_backed_dependency_fixture_setup,
    )

    bir = _bir_from_eval_api()
    ops = {
        str(o["id"]): o
        for o in (bir.get("operations") or [])
        if isinstance(o, dict) and o.get("id")
    }
    actors = {
        str(a["id"]): a
        for a in (bir.get("actors") or [])
        if isinstance(a, dict) and a.get("id")
    }
    # Ensure at least one executable actor is present for validation.
    if not actors:
        actors = {
            "actor_buyer": {
                "id": "actor_buyer",
                "role": "buyer",
                "credential_secret_ref": "secret_ref:test_accounts:buyer01",
            }
        }
    addr_get = next(
        o
        for o in ops.values()
        if str(o.get("method") or "").upper() == "GET"
        and str(o.get("path") or "") == "/api/users/addresses"
    )
    setup = _source_backed_dependency_fixture_setup(
        dependency_leaf="address_id",
        resolver_operations=[
            {
                "operation_ref": addr_get["id"],
                "method": "GET",
                "path": "/api/users/addresses",
            }
        ],
        ops=ops,
        actors=actors,
        behavior_ir=bir,
        binding_plan={},
    )
    assert setup.get("method") == "POST"
    assert setup.get("path") == "/api/users/addresses"
    assert setup.get("body_template")
    assert setup.get("cleanup_operations")
    assert not setup.get("body_bindings"), setup
