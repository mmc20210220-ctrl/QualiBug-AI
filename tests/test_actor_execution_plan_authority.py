from __future__ import annotations

import hashlib
import json


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def test_unsealed_legacy_actor_plan_is_not_runtime_authority() -> None:
    from ai_test_asset_center.experiment_executor import _actor_execution_plan

    plan, problem = _actor_execution_plan(
        {
            "assertions": [
                {
                    "property": {
                        "_actor_exploration_plan": {
                            "mode": "permission_exploration",
                            "candidate_ids": ["actor-a", "actor-b"],
                            "max_attempts": 2,
                        }
                    }
                }
            ]
        }
    )

    assert plan == {}
    assert problem == "legacy_actor_execution_plan_not_authoritative"


def test_compiler_sealed_top_level_actor_plan_remains_valid() -> None:
    from ai_test_asset_center.experiment_executor import _actor_execution_plan

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
    plan["plan_hash"] = _hash(plan)

    resolved, problem = _actor_execution_plan({"actor_execution_plan": plan})

    assert problem == ""
    assert resolved == plan


def test_legacy_candidate_order_can_never_select_source_actor() -> None:
    from ai_test_asset_center.experiment_executor import _actor_execution_plan

    first, first_problem = _actor_execution_plan(
        {
            "property": {
                "_actor_exploration_plan": {
                    "mode": "permission_exploration",
                    "candidate_ids": ["actor-a", "actor-b"],
                    "max_attempts": 2,
                }
            }
        }
    )
    second, second_problem = _actor_execution_plan(
        {
            "property": {
                "_actor_exploration_plan": {
                    "mode": "permission_exploration",
                    "candidate_ids": ["actor-b", "actor-a"],
                    "max_attempts": 2,
                }
            }
        }
    )

    assert first == second == {}
    assert first_problem == second_problem == (
        "legacy_actor_execution_plan_not_authoritative"
    )
