from __future__ import annotations

import hashlib
import json


def _sealed_plan() -> dict:
    plan = {
        "schema_version": "qualibug.actor-execution-plan.v1",
        "mode": "permission_exploration",
        "source_actor_id": "actor-a",
        "candidate_ids": ["actor-a", "actor-b"],
        "authorization_oracle_enabled": False,
        "max_attempts": 2,
        "reason": "source_permission_unknown",
        "authority": "compiled_actor_execution_plan",
    }
    plan["plan_hash"] = hashlib.sha256(
        json.dumps(
            plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return plan


def test_multiple_required_operations_have_no_source_order_primary() -> None:
    from ai_test_asset_center.experiment_executor import (
        _unique_primary_operation_ref,
    )

    assert _unique_primary_operation_ref(
        {"required_operations": ["op-a", "op-b"]}
    ) == ""
    assert _unique_primary_operation_ref(
        {"required_operations": ["op-b", "op-a"]}
    ) == ""


def test_property_can_explicitly_select_one_required_operation() -> None:
    from ai_test_asset_center.experiment_executor import (
        _unique_primary_operation_ref,
    )

    assert _unique_primary_operation_ref(
        {
            "required_operations": ["op-a", "op-b"],
            "property": {"operation_ref": "op-b"},
        }
    ) == "op-b"


def test_property_cannot_select_operation_outside_required_set() -> None:
    from ai_test_asset_center.experiment_executor import (
        _unique_primary_operation_ref,
    )

    assert _unique_primary_operation_ref(
        {
            "required_operations": ["op-a", "op-b"],
            "property": {"operation_ref": "op-c"},
        }
    ) == ""


def test_sealed_exploration_plan_blocks_when_primary_operation_is_ambiguous() -> None:
    from ai_test_asset_center.experiment_executor import _actor_execution_plan

    plan, problem = _actor_execution_plan(
        {
            "actor_execution_plan": _sealed_plan(),
            "required_operations": ["op-a", "op-b"],
        }
    )

    assert plan == {}
    assert problem == "actor_exploration_primary_operation_ambiguous"


def test_single_operation_exploration_remains_valid() -> None:
    from ai_test_asset_center.experiment_executor import _actor_execution_plan

    plan, problem = _actor_execution_plan(
        {
            "actor_execution_plan": _sealed_plan(),
            "required_operations": ["op-a"],
        }
    )

    assert problem == ""
    assert plan["source_actor_id"] == "actor-a"
