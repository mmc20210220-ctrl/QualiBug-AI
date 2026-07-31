from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.probe_policy import (
    build_gated_probes,
)


PLAN_ID = "runtime-plan:event"
MATERIALIZATION_ID = "materialization:event"
OBSERVER_REF = "observer-binding:event"
EVENT_CONTRACT_REF = "event-contract:order-created"


def _asset(*, materialization_observer_ref: str = OBSERVER_REF) -> dict:
    return {
        "scenario_planning_gate": {
            "scenario_planning_allowed": True,
            "entry_allowed": True,
        },
        "scenario_ir_gate": {"entry_allowed": True},
        "binding_identity_gate": {"entry_allowed": True},
        "scenario_execution_contract_gate": {"entry_allowed": True},
        "runtime_plan_gate": {"entry_allowed": True},
        "runtime_materialization_gate": {"entry_allowed": True},
        "runtime_plans": [
            {
                "plan_id": PLAN_ID,
                "binding_identity_refs": {
                    "observer_binding_refs": [OBSERVER_REF]
                },
                "oracle_query_templates": {
                    "templates": [
                        {
                            "template_id": "event-template",
                            "template_kind": "SOURCE_EVENT_DELIVERY_OBSERVATION",
                            "observer_binding_ref": OBSERVER_REF,
                            "event_contract_ref": EVENT_CONTRACT_REF,
                            "expected_event_type": "OrderCreated",
                            "expected_min_count": 1,
                            "expected_max_count": 1,
                            "observation_window_ms": 3000,
                        }
                    ]
                },
            }
        ],
        "runtime_materializations": [
            {
                "materialization_id": MATERIALIZATION_ID,
                "runtime_plan_ref": PLAN_ID,
                "binding_identity_refs": {
                    "observer_binding_refs": [materialization_observer_ref]
                    if materialization_observer_ref
                    else []
                },
            }
        ],
    }


def _compiler(_asset: dict, _limit: int) -> list[dict]:
    return [
        {
            "probe_id": "probe:event",
            "source": "enterprise_understanding_runtime_plan",
            "runtime_plan_ref": PLAN_ID,
            "runtime_materialization_ref": MATERIALIZATION_ID,
            "knowledge_lineage": {},
            "expected": "permission=ALLOW",
            "oracle_assertion": "permission=ALLOW",
            "oracle_family": "business_rule_oracle",
            "bug_signal": "generic",
            "evidence_requirements": ["runtime_plan", "runtime_materialization"],
        }
    ]


def test_formal_probe_carries_event_observer_and_contract_lineage() -> None:
    probes = build_gated_probes(_asset(), compiler=_compiler)

    assert len(probes) == 1
    probe = probes[0]
    assert probe["observer_binding_refs"] == [OBSERVER_REF]
    assert probe["formal_event_contract_refs"] == [EVENT_CONTRACT_REF]
    assert probe["knowledge_lineage"]["observer_binding_refs"] == [OBSERVER_REF]
    assert probe["knowledge_lineage"]["formal_event_contract_refs"] == [
        EVENT_CONTRACT_REF
    ]
    assert probe["knowledge_lineage"]["observer_identity_materialization_match"] is True
    requirement = probe["formal_event_assertion_requirements"][0]
    assert requirement == {
        "observer_binding_ref": OBSERVER_REF,
        "event_contract_ref": EVENT_CONTRACT_REF,
        "expected_event_type": "OrderCreated",
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observation_window_ms": 3000,
        "source_declared": True,
    }
    assert "event=OrderCreated,count=1,window_ms=3000" in probe["expected"]
    assert "event=OrderCreated,count=1,window_ms=3000" in probe[
        "oracle_assertion"
    ]
    assert probe["oracle_family"] == "event_delivery_consistency"
    assert "事件类型" in probe["bug_signal"]
    assert "formal_event_contract" in probe["evidence_requirements"]
    assert "formal_event_observer_binding" in probe["evidence_requirements"]
    assert "event_observation_receipt" in probe["evidence_requirements"]


def test_observer_identity_drift_blocks_probe() -> None:
    probes = build_gated_probes(
        _asset(materialization_observer_ref="observer-binding:other"),
        compiler=_compiler,
    )

    assert probes == []


def test_missing_materialized_observer_identity_blocks_probe() -> None:
    probes = build_gated_probes(
        _asset(materialization_observer_ref=""),
        compiler=_compiler,
    )

    assert probes == []
