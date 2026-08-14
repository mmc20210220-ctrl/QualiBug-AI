from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center._parsing import (
    _markdown_api_operations,
    _markdown_required_roles,
)
from ai_test_asset_center.experiment_plan_step_executor_core import (
    execute_non_barrier_plans,
)
from ai_test_asset_center.experiment_runtime_support import _unauthorized_actor_role

_SPEC = Path("projects/benchmark_mall/input/API_SPEC.md")


def _ops_by_key() -> dict[tuple[str, str], dict]:
    ops = _markdown_api_operations(_SPEC.read_text(encoding="utf-8"), "benchmark_mall")
    return {(op["method"], op["path"]): op for op in ops}


def test_markdown_required_roles_phrasings() -> None:
    # "seller/admin 可用" informal phrasing (set order is unspecified)
    assert set(_markdown_required_roles("后台创建商品。seller/admin 可用。")) == {
        "seller",
        "admin",
    }
    # explicit "所需角色" line (with Markdown emphasis markers)
    assert set(_markdown_required_roles("**所需角色**：seller, admin")) == {
        "seller",
        "admin",
    }
    # admin-only phrase
    assert _markdown_required_roles("后台调整用户余额，仅 admin 可用。") == ["admin"]
    # no restriction
    assert _markdown_required_roles("查询商品详情。") == []


def test_products_admin_declares_seller_admin() -> None:
    ops = _ops_by_key()
    op = ops[("POST", "/api/products/admin")]
    assert "seller" in op["required_roles"]
    assert "admin" in op["required_roles"]
    # PATCH variant too
    op2 = ops[("PATCH", "/api/products/admin/:sku")]
    assert set(op2["required_roles"]) == {"seller", "admin"}


def test_balance_endpoint_declares_admin() -> None:
    ops = _ops_by_key()
    op = ops[("PATCH", "/api/users/admin/users/:id/balance")]
    assert op["required_roles"] == ["admin"]


def test_no_restriction_endpoint_has_no_required_roles() -> None:
    ops = _ops_by_key()
    # cart/items has no role restriction in the contract
    assert "required_roles" not in ops[("POST", "/api/cart/items")]


def test_unauthorized_actor_helper() -> None:
    op = {"required_roles": ["seller", "admin"]}
    # permitted
    assert _unauthorized_actor_role(op, {"role": "admin"}) is None
    assert _unauthorized_actor_role(op, {"role": "SELLER"}) is None
    # wrong role
    assert _unauthorized_actor_role(op, {"role": "buyer"}) == "buyer"
    # missing role
    assert _unauthorized_actor_role(op, {"actor_id": "a1"}) == "missing_role"
    # no restriction => no-op
    assert _unauthorized_actor_role({"method": "GET", "path": "/x"}, {"role": "buyer"}) is None


def _run_admin_post(actor: dict) -> dict:
    op = {
        "method": "POST",
        "path": "/api/products/admin",
        "required_roles": ["seller", "admin"],
    }
    return execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=[
            {
                "step_id": "treatment_1",
                "method": "POST",
                "operation_ref": "op-1",
                "actor_ref": "actor-1",
                "path": "/api/products/admin",
                "body": {"sku": "SKU-1", "name": "x"},
            }
        ],
        consumed_barrier_steps=set(),
        actors={"actor-1": actor},
        ops={"op-1": op},
        tokens={},
        runtime_bindings={},
        activation_requirements={"control": [], "treatment": ["treatment_1"]},
        observations={},
        eid="exp-1",
        oid="obl-1",
        resolved_campaign_id="CMP-1",
        resolved_execution_id="exec-1",
        campaign_id="CMP-1",
        root=Path("/tmp"),
        project="proj-1",
        base_url="http://target.invalid",
        runtime_contract={},
    )


def _block_reasons(result: dict) -> list[str]:
    return [
        step.get("skipped_reason", "")
        for step in result.get("steps", [])
        if step.get("skipped_reason")
    ]


def test_executor_blocks_unauthorized_actor() -> None:
    result = _run_admin_post({"actor_id": "actor-1", "role": "buyer"})
    reasons = _block_reasons(result)
    assert any(r.startswith("BLOCKED_UNAUTHORIZED_ACTOR") for r in reasons), reasons
    assert "buyer" in reasons[0]


def test_executor_blocks_missing_role() -> None:
    result = _run_admin_post({"actor_id": "actor-1"})
    reasons = _block_reasons(result)
    assert any(r.startswith("BLOCKED_UNAUTHORIZED_ACTOR") for r in reasons), reasons
    assert "missing_role" in reasons[0]


def test_executor_allows_permitted_actor() -> None:
    result = _run_admin_post({"actor_id": "actor-1", "role": "admin"})
    reasons = _block_reasons(result)
    assert not any(r.startswith("BLOCKED_UNAUTHORIZED_ACTOR") for r in reasons), reasons
    # should proceed past the permission guard (transport failure is a separate concern)
    assert result.get("pre_transport_block_reasons") == [] or not any(
        r.startswith("unauthorized_actor") for r in result.get("pre_transport_block_reasons", [])
    )
