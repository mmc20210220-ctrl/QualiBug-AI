from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.deep_experiment_protocol_adapter import (
    adapt_deep_experiments_for_execution,
)
from ai_test_asset_center.experiment_runtime_support import (
    preflight_experiment_executable,
)


def test_discovery_mainline_does_not_replace_blocked_experiments_with_deep_heuristics() -> None:
    planning_source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "discovery_runtime_planning.py"
    ).read_text(encoding="utf-8")

    assert "plan_deep_experiments" not in planning_source
    assert "adapt_deep_experiments_for_execution" not in planning_source


def test_deep_adapter_rejects_missing_actor_without_substitution() -> None:
    result = adapt_deep_experiments_for_execution(
        [{
            "experiment_id": "deep-missing-actor",
            "obligation_id": "obl-missing-actor",
            "compile_receipt": {"status": "COMPILED"},
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "operation_ref": "op-read",
                "actor_ref": "actor-source-declared-but-missing",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "http_status_class", "expected": 2}],
        }],
        {
            "actors": [{
                "id": "actor-admin",
                "role": "admin",
                "credential_secret_ref": "secret_ref:test_accounts:admin",
            }],
            "operations": [{
                "id": "op-read",
                "method": "GET",
                "path": "/api/resources",
            }],
        },
    )

    assert result["adapted"] == []
    assert result["blocked_reasons"] == {"BLOCKED_MISSING_ACTOR": 1}


def test_deep_adapter_binds_bare_path_id_via_collection_parent() -> None:
    behavior_ir = {
        "actors": [{
            "id": "actor-admin",
            "role": "admin",
            "credential_secret_ref": "secret_ref:test_accounts:admin",
        }],
        "operations": [{
            "id": "op-list-orders",
            "method": "GET",
            "path": "/api/orders",
            "read_write": "read",
        }, {
            "id": "op-ship",
            "method": "POST",
            "path": "/api/orders/:id/ship",
            "read_write": "write",
        }],
    }
    adapted = adapt_deep_experiments_for_execution(
        [{
            "experiment_id": "deep-ship",
            "obligation_id": "obl-ship",
            "compile_receipt": {"status": "COMPILED"},
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "operation_ref": "op-ship",
                "actor_ref": "actor-admin",
                "path": "/api/orders/:id/ship",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "http_status_class", "expected": 4}],
        }],
        behavior_ir,
    )

    assert adapted["adapted"] == []
    assert adapted["blocked_reasons"] == {"BLOCKED_NON_REVERSIBLE_WRITE": 1}


def test_deep_adapter_binds_body_order_id_placeholder() -> None:
    behavior_ir = {
        "actors": [{
            "id": "actor-buyer",
            "role": "buyer",
            "credential_secret_ref": "secret_ref:test_accounts:buyer",
        }],
        "operations": [{
            "id": "op-list-orders",
            "method": "GET",
            "path": "/api/orders",
            "read_write": "read",
        }, {
            "id": "op-pay",
            "method": "POST",
            "path": "/api/payments/pay",
            "read_write": "write",
            "request_example": {
                "orderId": "<order_id>",
                "amount": 100,
                "channel": "BALANCE",
            },
        }],
    }
    adapted = adapt_deep_experiments_for_execution(
        [{
            "experiment_id": "deep-pay",
            "obligation_id": "obl-pay",
            "compile_receipt": {"status": "COMPILED"},
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "operation_ref": "op-pay",
                "actor_ref": "actor-buyer",
                "body": {
                    "orderId": "<order_id>",
                    "amount": 100,
                    "channel": "BALANCE",
                },
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "http_status_class", "expected": 4}],
        }],
        behavior_ir,
    )

    assert adapted["adapted"] == []
    assert adapted["blocked_reasons"] == {"BLOCKED_NON_REVERSIBLE_WRITE": 1}


def test_deep_adapter_uses_fixture_create_when_no_list_read() -> None:
    behavior_ir = {
        "actors": [{
            "id": "actor-finance",
            "role": "finance",
            "credential_secret_ref": "secret_ref:test_accounts:finance",
        }],
        "operations": [{
            "id": "op-create-refund",
            "method": "POST",
            "path": "/api/refunds",
            "read_write": "write",
            "request_example": {"orderId": "<order_id>", "amount": 100},
        }, {
            "id": "op-list-orders",
            "method": "GET",
            "path": "/api/orders",
            "read_write": "read",
        }, {
            "id": "op-approve",
            "method": "POST",
            "path": "/api/refunds/:id/approve",
            "read_write": "write",
        }, {
            "id": "op-reject",
            "method": "POST",
            "path": "/api/refunds/{id}/reject",
            "read_write": "write",
        }],
    }
    adapted = adapt_deep_experiments_for_execution(
        [{
            "experiment_id": "deep-approve",
            "obligation_id": "obl-approve",
            "compile_receipt": {"status": "COMPILED"},
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "operation_ref": "op-approve",
                "actor_ref": "actor-finance",
            }],
            "observers": [{"observer_id": "http_response"}],
            "assertions": [{"kind": "http_status_class", "expected": 4}],
        }],
        behavior_ir,
    )

    assert adapted["adapted"] == []
    assert adapted["blocked_reasons"] == {"BLOCKED_NON_REVERSIBLE_WRITE": 1}


def test_preflight_accepts_fixture_create_only_path_binding() -> None:
    experiment = {
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": "actor-finance",
            "operation_ref": "op-get-refund",
            "path": "/api/refunds/{id}",
        }],
        "binding_plan": [{
            "target": "id",
            "status": "runtime_resolvable",
            "source_priority": "fixture_create_only",
            "resolver_operations": [],
            "fixture_setup": {
                "operation_ref": "op-create-refund",
                "method": "POST",
                "path": "/api/refunds",
                "actor_refs": ["actor-finance"],
                "body_template": {"orderId": "<order_id>", "amount": 100},
                "cleanup_operations": [{
                    "operation_ref": "op-reject",
                    "method": "POST",
                    "path": "/api/refunds/{id}/reject",
                }],
            },
        }],
        "fixture_dag": {
            "status": "READY",
            "nodes": [{
                "node_id": "bind-id",
                "kind": "runtime_read_binding",
                "target": "id",
            }],
            "setup_order": ["bind-id"],
        },
        "observers": [{"observer_id": "http_response"}],
        "assertions": [{"kind": "http_status_class", "expected": 2}],
        "safety_contract": {"governed_write": False},
    }
    behavior_ir = {
        "actors": [{
            "id": "actor-finance",
            "role": "finance",
            "credential_secret_ref": "secret_ref:test_accounts:finance",
        }],
        "operations": [{
            "id": "op-get-refund",
            "method": "GET",
            "path": "/api/refunds/{id}",
            "read_write": "read",
        }, {
            "id": "op-create-refund",
            "method": "POST",
            "path": "/api/refunds",
            "read_write": "write",
        }, {
            "id": "op-reject",
            "method": "POST",
            "path": "/api/refunds/{id}/reject",
            "read_write": "write",
        }],
    }

    ok, reason, detail = preflight_experiment_executable(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={"secret_ref:test_accounts:finance": "tok"},
    )

    assert ok is True, (reason, detail)
