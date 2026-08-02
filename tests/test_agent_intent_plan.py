from __future__ import annotations

import pytest

from ai_test_asset_center.adaptive_discovery_planner import (
    AgentIntentError,
    build_agent_intent_plan,
)


def _source(locator: str) -> dict[str, str]:
    return {
        "source_id": "api-source",
        "kind": "api_operation",
        "locator": locator,
    }


def test_agent_intent_is_constrained_to_behavior_ir_and_compiled_experiment() -> None:
    operation = {
        "id": "op-read-resource",
        "method": "GET",
        "path": "/resources/{id}",
        "source_refs": [_source("GET /resources/{id}")],
    }
    obligation = {
        "obligation_id": "OBL-1",
        "risk_family": "authorization",
        "required_operations": [operation["id"]],
        "required_actors": ["actor-owner", "actor-viewer"],
        "required_observers": ["http_response", "authorization_comparison"],
        "relation_refs": ["relation-permission"],
        "source_refs": [_source("GET /resources/{id}")],
    }
    experiment = {
        "experiment_id": "EXP-1",
        "obligation_id": obligation["obligation_id"],
        "compile_receipt": {"status": "COMPILED"},
        "observers": [
            {"observer_id": "http_response", "adapter": "http_api"},
            {"observer_id": "authorization_comparison", "adapter": "http_api"},
        ],
        "source_refs": [_source("GET /resources/{id}")],
    }
    adaptive_plan = {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "selected": [{
            "obligation_id": obligation["obligation_id"],
            "experiment_id": experiment["experiment_id"],
            "risk_family": "authorization",
            "score": 0.8,
        }],
        "pending_next_round": [],
    }

    receipt = build_agent_intent_plan(
        adaptive_plan,
        obligations=[obligation],
        experiments_by_obligation={obligation["obligation_id"]: experiment},
        behavior_ir={
            "model_id": "BIR-1",
            "operations": [operation],
            "actors": [{"id": "actor-owner"}, {"id": "actor-viewer"}],
            "relations": [{"id": "relation-permission"}],
        },
    )

    assert receipt["schema_version"] == "qualibug.agent-intent-plan.v1"
    assert receipt["status"] == "VERIFIED"
    assert receipt["intent_count"] == 1
    intent = receipt["intents"][0]
    assert intent["semantic_authority"] == "behavior_ir"
    assert intent["operation_refs"] == [operation["id"]]
    assert intent["actor_refs"] == ["actor-owner", "actor-viewer"]
    assert intent["relation_refs"] == ["relation-permission"]
    assert intent["execution_adapters"] == ["http_api"]
    assert intent["observer_refs"] == [
        "authorization_comparison",
        "http_response",
    ]
    assert intent["source_refs"] == [_source("GET /resources/{id}")]


def test_agent_intent_rejects_an_invented_obligation_identity() -> None:
    with pytest.raises(AgentIntentError, match="unknown_obligation"):
        build_agent_intent_plan(
            {
                "schema_version": "qualibug.adaptive-obligation-plan.v1",
                "selected": [{
                    "obligation_id": "OBL-INVENTED",
                    "experiment_id": "EXP-INVENTED",
                }],
                "pending_next_round": [],
            },
            obligations=[],
            experiments_by_obligation={},
            behavior_ir={
                "model_id": "BIR-1",
                "operations": [],
                "actors": [],
                "relations": [],
            },
        )


def test_agent_intent_rejects_missing_compiled_observer_contract() -> None:
    with pytest.raises(AgentIntentError, match="observer_contract_missing:OBL-1"):
        build_agent_intent_plan(
            {
                "schema_version": "qualibug.adaptive-obligation-plan.v1",
                "selected": [{
                    "obligation_id": "OBL-1",
                    "experiment_id": "EXP-1",
                }],
                "pending_next_round": [],
            },
            obligations=[{
                "obligation_id": "OBL-1",
                "risk_family": "validation",
                "required_operations": ["op-read"],
                "required_actors": ["actor-public"],
                "source_refs": [_source("GET /resources")],
            }],
            experiments_by_obligation={
                "OBL-1": {
                    "experiment_id": "EXP-1",
                    "compile_receipt": {"status": "COMPILED"},
                    "source_refs": [_source("GET /resources")],
                    "observers": [],
                }
            },
            behavior_ir={
                "model_id": "BIR-1",
                "operations": [{
                    "id": "op-read",
                    "method": "GET",
                    "path": "/resources",
                    "source_refs": [_source("GET /resources")],
                }],
                "actors": [{"id": "actor-public"}],
                "relations": [],
            },
        )
