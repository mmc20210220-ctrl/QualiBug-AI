"""Stage-3 S1/S2: permit-only preflight align + pending-round consumption."""
from __future__ import annotations

from ai_test_asset_center.discovery_runtime_execution_support import (
    _consume_pending_obligation_rounds,
)
from ai_test_asset_center.experiment_runtime_support import (
    preflight_experiment_executable,
)


def _permit_only_write_experiment() -> dict:
    return {
        "compile_receipt": {"status": "COMPILED", "reason_code": ""},
        "fixture_dag": {"status": "READY", "nodes": []},
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": "actor-buyer",
            "operation_ref": "op-create",
            "intent": "permitted_operation_invocation",
            "property_template": "permitted_operation_invocation",
        }],
        "assertions": [{
            "assertion_id": "assert_auth",
            "kind": "http_status_class",
            "template": "permitted_operation_invocation",
            "property": {"template": "permitted_operation_invocation"},
        }],
        "observers": [{
            "observer_id": "http_response",
            "adapter": "http_api",
        }],
        "cleanup_plan": [{
            "operation_ref": "op-delete",
            "actor_ref": "actor-buyer",
        }],
        "safety_contract": {
            "environment_type": "test",
            "governed_write": True,
        },
        "binding_plan": [],
    }


def _behavior_ir() -> dict:
    return {
        "actors": [{
            "id": "actor-buyer",
            "role": "buyer",
            "credential_secret_ref": "secret_ref:buyer",
        }],
        "operations": [
            {
                "id": "op-create",
                "method": "POST",
                "path": "/api/cart/items",
            },
            {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/api/cart/items/{id}",
            },
        ],
    }


def test_preflight_allows_permit_only_write_without_effect_read() -> None:
    ok, reason, detail = preflight_experiment_executable(
        _permit_only_write_experiment(),
        behavior_ir=_behavior_ir(),
        actor_tokens={"secret_ref:buyer": "token"},
    )
    assert ok is True, (reason, detail)
    assert reason == ""
    assert detail == ""


def test_preflight_still_blocks_non_permit_write_without_effect_read() -> None:
    experiment = _permit_only_write_experiment()
    experiment["treatment_plan"][0]["intent"] = "treatment"
    experiment["treatment_plan"][0].pop("property_template", None)
    experiment["assertions"][0]["template"] = ""
    experiment["assertions"][0]["property"] = {}
    ok, reason, detail = preflight_experiment_executable(
        experiment,
        behavior_ir=_behavior_ir(),
        actor_tokens={"secret_ref:buyer": "token"},
    )
    assert ok is False
    assert reason == "BLOCKED_MISSING_OBSERVER"
    assert detail.startswith("write_observer:")


def test_consume_pending_rounds_reselects_without_raising_budget() -> None:
    obligations = [
        {
            "obligation_id": f"obl-{index}",
            "risk_family": "authorization",
            "required_operations": [f"op-{index}"],
            "required_actors": ["actor-1"],
            "confidence": 0.8,
            "property": {"operation_ref": f"op-{index}"},
            "source_refs": [{"id": f"src-{index}", "type": "api"}],
        }
        for index in range(1, 5)
    ]
    experiments = {
        f"obl-{index}": {
            "obligation_id": f"obl-{index}",
            "experiment_id": f"exp-{index}",
            "compile_receipt": {"status": "COMPILED"},
            "observers": [{"observer_id": "http_response", "adapter": "http_api"}],
        }
        for index in range(1, 5)
    }
    behavior_ir = {
        "actors": [{"id": "actor-1", "role": "buyer"}],
        "operations": [
            {"id": f"op-{index}", "method": "GET", "path": f"/api/items/{index}"}
            for index in range(1, 5)
        ],
        "relations": [],
    }
    obligation_plan = {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "budget": 2,
        "selected": [{"obligation_id": "obl-1"}, {"obligation_id": "obl-2"}],
        "pending_next_round": [
            {"obligation_id": "obl-3"},
            {"obligation_id": "obl-4"},
        ],
        "selected_count": 2,
        "pending_count": 2,
    }
    calls: list[list[str]] = []

    def _fake_execute(scheduled, **_kwargs):
        ids = [str(row.get("obligation_id") or "") for row in scheduled]
        calls.append(ids)
        return {
            "executed_count": len(ids),
            "findings": [],
            "compile_results": {
                oid: {"status": "COMPILED", "experiment_id": f"exp-{oid}"}
                for oid in ids
            },
            "execution_results": {
                oid: {"status": "EXECUTED", "execution_id": f"exec-{oid}"}
                for oid in ids
            },
            "gate_results": {},
        }

    batches, updated = _consume_pending_obligation_rounds(
        obligation_plan=obligation_plan,
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir=behavior_ir,
        root=".",
        project="demo",
        base_url="http://target.invalid",
        runtime_contract={"status": "approved"},
        mainline_run={"campaign_id": "cmp", "run_id": "run"},
        campaign_id="cmp",
        automatic_round_limit=3,
        execute_batch=_fake_execute,
    )
    assert len(batches) >= 1
    assert int(updated.get("budget") or 0) == 2
    assert calls
    assert all(len(batch_ids) <= 2 for batch_ids in calls)
    selected_follow_on = {oid for batch_ids in calls for oid in batch_ids}
    assert selected_follow_on <= {"obl-3", "obl-4"}
    assert int(updated.get("pending_count") or 0) == 0
