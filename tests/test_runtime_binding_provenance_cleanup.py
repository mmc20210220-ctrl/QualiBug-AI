from __future__ import annotations

from ai_test_asset_center import experiment_executor_core
from ai_test_asset_center.operational_receipts import (
    build_execution_operational_receipt,
)


def test_provenance_block_preserves_observed_steps_and_reuses_cleanup(monkeypatch) -> None:
    monkeypatch.setattr(
        experiment_executor_core,
        "preflight_experiment_executable",
        lambda *args, **kwargs: (True, "", ""),
    )
    monkeypatch.setattr(
        experiment_executor_core,
        "validate_cleanup_plan",
        lambda *args, **kwargs: {"valid": True},
    )

    observed_step = {
        "phase": "binding_materialization",
        "method": "GET",
        "path": "/api/users/addresses",
        "status_code": 200,
    }
    cleanup_calls = []

    def fake_materialize(**kwargs):
        return {
            "status": "ready",
            "steps_out": [observed_step],
            "fixture_receipts": [],
            "binding_materialization_receipts": [],
            "runtime_bindings": {"address_id": "address-1"},
            "pending_fixture_cleanups": [],
            "cleanup_failures": 0,
            "contract_evidence_receipts": [],
        }

    def fake_cleanup(**kwargs):
        cleanup_calls.append(kwargs)
        return {
            "steps_out": list(kwargs["steps_out"]),
            "observations": dict(kwargs["observations"]),
            "contract_evidence_receipts": [],
            "cleanup_failures": 0,
        }

    monkeypatch.setattr(
        experiment_executor_core,
        "materialize_experiment_fixtures",
        fake_materialize,
    )
    monkeypatch.setattr(
        experiment_executor_core,
        "execute_experiment_cleanup_compensation",
        fake_cleanup,
    )

    result = experiment_executor_core.execute_one_experiment(
        {
            "experiment_id": "exp-provenance",
            "obligation_id": "obl-provenance",
            "binding_plan": [{"target": "address_id"}],
            "binding_coverage_graph": {
                "nodes": [{"semantic_name": "different_target"}],
            },
        },
        behavior_ir={"actors": [], "operations": []},
        root=experiment_executor_core.Path("."),
        project="project",
        base_url="http://localhost:8080",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-provenance",
        execution_id="execution-provenance",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_BINDING_GRAPH_INVALID"
    assert result["steps"] == [observed_step]
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0]["steps_out"] == [observed_step]


def test_runtime_cleanup_block_preserves_binding_request_for_accounting(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        experiment_executor_core,
        "preflight_experiment_executable",
        lambda *args, **kwargs: (True, "", ""),
    )
    monkeypatch.setattr(
        experiment_executor_core,
        "build_proof_fingerprint",
        lambda proof: "proof-fingerprint",
    )

    def fake_validate(*args, **kwargs):
        if kwargs.get("phase") == "runtime":
            return {
                "valid": False,
                "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                "detail": "fingerprint_mismatch",
            }
        return {"valid": True}

    monkeypatch.setattr(
        experiment_executor_core,
        "validate_cleanup_plan",
        fake_validate,
    )
    observed_step = {
        "phase": "binding_materialization",
        "method": "GET",
        "path": "/orders",
        "status_code": 200,
    }
    monkeypatch.setattr(
        experiment_executor_core,
        "materialize_experiment_fixtures",
        lambda **kwargs: {
            "status": "ready",
            "steps_out": [observed_step],
            "fixture_receipts": [],
            "binding_materialization_receipts": [],
            "runtime_bindings": {},
            "pending_fixture_cleanups": [],
            "cleanup_failures": 0,
            "contract_evidence_receipts": [],
        },
    )

    result = experiment_executor_core.execute_one_experiment(
        {
            "experiment_id": "exp-runtime-proof",
            "obligation_id": "obl-runtime-proof",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "operation_ref": "op-update",
                "method": "PATCH",
                "path": "/orders",
            }],
            "safety_contract": {"governed_write": True},
            "write_reversibility_proof": {
                "proof_status": "PROVEN",
                "fingerprint": "proof-fingerprint",
            },
            "compile_receipt": {
                "write_reversibility_fingerprint": "proof-fingerprint",
            },
        },
        behavior_ir={
            "actors": [],
            "operations": [{
                "id": "op-update",
                "method": "PATCH",
                "path": "/orders",
                "read_write": "write",
            }],
        },
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-runtime-proof",
        execution_id="execution-runtime-proof",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_CLEANUP_CONTRACT_DRIFT"
    assert result["steps"] == [observed_step]
    operational = build_execution_operational_receipt(
        receipt_id="operational-runtime-proof",
        execution_status=result["status"],
        steps=result["steps"],
        cleanup_failures=result.get("cleanup_failures", 0),
    )
    assert operational["http_request_attempt_count"] == 1
