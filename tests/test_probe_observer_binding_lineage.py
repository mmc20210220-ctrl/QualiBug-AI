from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.probe_policy import (
    build_gated_probes,
)


PLAN_ID = "runtime-plan:event"
MATERIALIZATION_ID = "materialization:event"
OBSERVER_REF = "observer-binding:event"
EVENT_CONTRACT_REF = "event-contract:order-created"


def _event_contract(
    *,
    contract_ref: str = EVENT_CONTRACT_REF,
    event_type: str = "OrderCreated",
) -> dict:
    return {
        "contract_id": contract_ref,
        "source_refs": [
            {
                "source_id": "event-doc",
                "locator": f"events.{event_type}",
                "quote_hash": f"sha256:{event_type}",
            }
        ],
        "operation_ref": "api:POST:/orders",
        "actor_ref": "actor:admin",
        "observer_path": "/test/events",
        "events_path": "$.items",
        "event_id_field": "id",
        "event_type_field": "type",
        "correlation_field": "orderId",
        "correlation_query_parameter": "orderId",
        "correlation_source": {
            "location": "treatment_response",
            "path": "$.id",
        },
        "expected_event_type": event_type,
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observation_window_ms": 3000,
    }


def _event_template(
    *,
    template_id: str = "event-template",
    observer_ref: str = OBSERVER_REF,
    contract_ref: str = EVENT_CONTRACT_REF,
    event_type: str = "OrderCreated",
) -> dict:
    return {
        "template_id": template_id,
        "template_kind": "SOURCE_EVENT_DELIVERY_OBSERVATION",
        "observer_binding_ref": observer_ref,
        "event_contract_ref": contract_ref,
        "expected_event_type": event_type,
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observation_window_ms": 3000,
        "event_contract": _event_contract(
            contract_ref=contract_ref,
            event_type=event_type,
        ),
    }


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
                    "templates": [_event_template()]
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
    assert probe["source_event_contract"]["contract_id"] == EVENT_CONTRACT_REF
    assert probe["source_event_contracts"] == [probe["source_event_contract"]]
    assert probe["event_observer_execution_handoff_ready"] is True
    assert probe["event_observer_execution_mode"] == "SINGLE_FORMAL_CONTRACT"
    assert probe["knowledge_lineage"][
        "event_observer_execution_handoff_ready"
    ] is True
    assert "event=OrderCreated,count=1,window_ms=3000" in probe["expected"]
    assert "event=OrderCreated,count=1,window_ms=3000" in probe[
        "oracle_assertion"
    ]
    assert probe["oracle_family"] == "event_delivery_consistency"
    assert "事件类型" in probe["bug_signal"]
    assert "formal_event_contract" in probe["evidence_requirements"]
    assert "formal_event_observer_binding" in probe["evidence_requirements"]
    assert "event_observation_receipt" in probe["evidence_requirements"]


def test_multiple_event_contracts_remain_draft_only() -> None:
    asset = _asset()
    second_observer = "observer-binding:audit-event"
    second_contract = "event-contract:order-audited"
    asset["runtime_plans"][0]["binding_identity_refs"][
        "observer_binding_refs"
    ].append(second_observer)
    asset["runtime_plans"][0]["oracle_query_templates"]["templates"].append(
        _event_template(
            template_id="audit-event-template",
            observer_ref=second_observer,
            contract_ref=second_contract,
            event_type="OrderAudited",
        )
    )
    asset["runtime_materializations"][0]["binding_identity_refs"][
        "observer_binding_refs"
    ].append(second_observer)

    probes = build_gated_probes(asset, compiler=_compiler)

    assert len(probes) == 1
    probe = probes[0]
    assert "source_event_contract" not in probe
    assert len(probe["source_event_contracts"]) == 2
    assert probe["event_observer_execution_handoff_ready"] is False
    assert probe["event_observer_execution_mode"] == "MULTI_CONTRACT_DRAFT_ONLY"
    assert probe["knowledge_lineage"][
        "event_observer_execution_handoff_ready"
    ] is False
    assert probe["formal_event_contract_refs"] == sorted([
        EVENT_CONTRACT_REF,
        second_contract,
    ])


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
