from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from ai_test_asset_center.artifact_redactor import write_json_redacted
from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.campaign_api_contract import (
    CampaignContractError,
    build_campaign_view,
    build_evaluation_submission,
)
from ai_test_asset_center.customer_delivery_gate_v2 import (
    CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
    DeliveryGateV2Error,
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
    validate_customer_delivery_gate_bundle,
    validate_customer_delivery_gate_receipt_v2,
)
from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
)
from ai_test_asset_center.discovery_evaluation_contract import (
    EvaluationContractError,
    EvaluationManifest,
    EvaluationTarget,
    aggregate_evaluation_receipts,
    build_paired_evaluation_evidence,
    evaluate_completed_scan,
)
from ai_test_asset_center.discovery_evaluator_projection import (
    build_evaluator_only_projection,
)
from ai_test_asset_center.discovery_quality_projection import (
    build_finding_classification_projection,
    build_formal_count_projection,
)
from ai_test_asset_center.formal_delivery_authority import (
    FORMAL_DELIVERY_AUTHORITY_SCHEMA,
    FormalDeliveryAuthorityError,
    build_formal_delivery_authority_receipt,
    validate_formal_delivery_authority_receipt,
)
from ai_test_asset_center.canonical_defect_registry import (
    CANONICAL_DEFECT_REGISTRY_SCHEMA,
    CanonicalDefectRegistryError,
    build_canonical_defect_identity,
    build_canonical_defect_registry,
    derive_canonical_identity_evidence,
    validate_canonical_defect_registry,
)
from ai_test_asset_center.obligation_attempt_ledger import (
    build_obligation_attempt_ledger,
)
from ai_test_asset_center.observer_contracts import build_observer_receipt
from ai_test_asset_center.operational_receipts import (
    build_execution_operational_receipt,
)
from tests.phase3_gate_support import (
    build_formal_evaluation_scope,
    build_formal_scope_contract,
    build_test_execution_authority,
)


CAMPAIGN_ID = "campaign-1"
EXECUTION_ID = "execution-1"
EXPERIMENT_ID = "experiment-1"
OBLIGATION_ID = "obligation-1"
TEST_EVALUATOR_HMAC_KEY = "phase3-evaluator-test-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def _evaluator_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY",
        TEST_EVALUATOR_HMAC_KEY,
    )


def _request_semantics_fingerprint(*, phase: str) -> str:
    payload = {
        "operation_ref": "read-resource",
        "method": "GET",
        "path_template": "/resources/{resourceId}",
        "mutation_class": (
            "positive_control"
            if phase == "control"
            else "actor_relation_treatment"
        ),
        "mutation_selector": "",
        "mutation_operator": "",
        "request_body_fingerprint": "c" * 64,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _chain(
    *,
    expected_status: int = 403,
    actual_status: int = 200,
    evaluation_mode: str = "operational",
    include_request_semantics: bool = True,
    assertion_count: int = 1,
    control_actor_ref: str = "owner",
    treatment_actor_ref: str = "viewer",
    actor_role: str = "public",
    assertion_kind: str = "http_status",
) -> dict:
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-1",
        campaign_id=CAMPAIGN_ID,
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode=evaluation_mode,
    )
    source_refs = [{
        "kind": "api_contract",
        "source_id": "generic-resource-api",
        "locator": "GET /resources/{resourceId}",
    }]
    experiment = {
        "experiment_id": EXPERIMENT_ID,
        "obligation_id": OBLIGATION_ID,
        "campaign_id": CAMPAIGN_ID,
        "execution_id": EXECUTION_ID,
        "source_refs": source_refs,
        "control_plan": [{
            "step_id": "control-1",
            "actor_ref": control_actor_ref,
            "operation_ref": "read-resource",
        }],
        "treatment_plan": [{
            "step_id": "treatment-1",
            "actor_ref": treatment_actor_ref,
            "operation_ref": "read-resource",
        }],
        "fixture_dag": {"nodes": [], "setup_order": []},
        "observers": [{"observer_id": "http_response"}],
        "cleanup_plan": [],
        "assertions": [
            {
                "assertion_id": f"assert-status-{index}",
                "kind": assertion_kind,
                "expected": expected_status,
            }
            for index in range(assertion_count)
        ],
    }
    contract_receipts = [
        build_contract_evidence_receipt(
            kind=kind,
            experiment_id=EXPERIMENT_ID,
            obligation_id=OBLIGATION_ID,
            campaign_id=CAMPAIGN_ID,
            execution_id=EXECUTION_ID,
            subject_id=subject_id,
            status="OBSERVED",
            evidence={
                **evidence,
                **(
                    {
                        "path_template": "/resources/{resourceId}",
                        "request_body_fingerprint": "c" * 64,
                        "request_semantics_fingerprint": (
                            _request_semantics_fingerprint(phase=kind)
                        ),
                        "mutation_class": (
                            "positive_control"
                            if kind == "control"
                            else "actor_relation_treatment"
                        ),
                        "mutation_selector": "",
                        "mutation_operator": "",
                    }
                    if include_request_semantics
                    and kind in {"control", "treatment"}
                    else {}
                ),
            },
        )
        for kind, subject_id, evidence in (
            (
                "control",
                "control-1",
                {
                    "response_observed": True,
                    "status_code": 200,
                    "control_succeeded": True,
                },
            ),
            (
                "treatment",
                "treatment-1",
                {"response_observed": True, "status_code": actual_status},
            ),
            ("actor", control_actor_ref, {"role": actor_role}),
            ("actor", treatment_actor_ref, {"role": actor_role}),
        )
    ]
    observer = build_observer_receipt(
        observer_id="http_response",
        status="OBSERVED",
        campaign_id=CAMPAIGN_ID,
        execution_id=EXECUTION_ID,
        evidence={"statuses": [200, actual_status]},
    )
    evidence = {
        "campaign_id": CAMPAIGN_ID,
        "execution_id": EXECUTION_ID,
        "status_code": actual_status,
        "contract_evidence_receipts": contract_receipts,
        "observer_receipts": [observer],
    }
    oracle = evaluate_contract_oracle(
        experiment=experiment,
        evidence=evidence,
    )
    steps = [
        {
            "phase": "control",
            "step_id": "control-1",
            "actor_ref": control_actor_ref,
            "operation_ref": "read-resource",
            "method": "GET",
            "path": "/resources/r-1",
            "status_code": 200,
            "body": {"id": "r-1"},
            "observation_receipt_id": "observation-control",
            **(
                {
                    "path_template": "/resources/{resourceId}",
                    "request_body_fingerprint": "c" * 64,
                    "request_semantics_fingerprint": (
                        _request_semantics_fingerprint(phase="control")
                    ),
                    "mutation_class": "positive_control",
                    "mutation_selector": "",
                    "mutation_operator": "",
                }
                if include_request_semantics
                else {}
            ),
        },
        {
            "phase": "treatment",
            "step_id": "treatment-1",
            "actor_ref": treatment_actor_ref,
            "operation_ref": "read-resource",
            "method": "GET",
            "path": "/resources/r-1",
            "status_code": actual_status,
            "body": {"id": "r-1"},
            "observation_receipt_id": "observation-treatment",
            **(
                {
                    "path_template": "/resources/{resourceId}",
                    "request_body_fingerprint": "c" * 64,
                    "request_semantics_fingerprint": (
                        _request_semantics_fingerprint(phase="treatment")
                    ),
                    "mutation_class": "actor_relation_treatment",
                    "mutation_selector": "",
                    "mutation_operator": "",
                }
                if include_request_semantics
                else {}
            ),
        },
    ]
    operational = build_execution_operational_receipt(
        receipt_id="operational-1",
        execution_status="EXECUTED",
        steps=steps,
        cleanup_failures=0,
    )
    execution = build_delivery_execution_receipt(
        mainline_run=mainline,
        candidate_id="candidate-1",
        slice_id="slice-1",
        obligation_id=OBLIGATION_ID,
        experiment_id=EXPERIMENT_ID,
        execution_id=EXECUTION_ID,
        evidence_id="evidence-1",
        operational_receipt=operational,
        observation_receipt_ids=[
            "observation-control",
            "observation-treatment",
            observer["receipt_id"],
            *[receipt["receipt_id"] for receipt in contract_receipts],
        ],
        oracle_receipt_id=oracle["receipt_id"],
    )
    reproduction = build_reproduction_receipt(
        execution_receipt=execution,
        steps=steps,
        oracle_receipt=oracle,
        source_refs=source_refs,
    )
    finding = {
        "id": "finding-1",
        "finding_id": "finding-1",
        "candidate_id": "candidate-1",
        "slice_id": "slice-1",
        "obligation_id": OBLIGATION_ID,
        "experiment_id": EXPERIMENT_ID,
        "execution_id": EXECUTION_ID,
        "evidence_id": "evidence-1",
        "campaign_id": CAMPAIGN_ID,
        "mainline_run": {
            "contract_fingerprint": mainline["contract_fingerprint"],
        },
        "title": "Source-backed observed property violation",
        "source_refs": source_refs,
        "failed_assertions": list(oracle.get("assertions") or []),
        "canonical_identity_evidence": {
            "schema_version": "qualibug.canonical-identity-evidence.v1",
            "operation": {
                "operation_ref": "read-resource",
                "method": "GET",
                "path_template": "/resources/{resourceId}",
            },
            "property": {
                "assertion_id": "assert-status",
                "kind": assertion_kind,
                "template": "non_owner_access_must_be_denied",
                "invariant_ref": "",
                "entity_ref": "resource",
            },
            "actor_relation": {
                "control_role": "owner",
                "treatment_role": "viewer",
                "relation": "owner_to_viewer",
            },
            "resource_identity_class": {
                "entity_refs": ["resource"],
                "path_template": "/resources/{resourceId}",
            },
            "mutation": {
                "class": "actor_relation_treatment",
                "selector": "",
                "operator": "",
                "request_body_fingerprint": "c" * 64,
                "request_semantics_fingerprint": (
                    _request_semantics_fingerprint(phase="treatment")
                ),
            },
            "outcome": {
                "assertion_status": "VIOLATION",
                "assertion_kind": assertion_kind,
                "control_http_status_class": 2,
                "treatment_http_status_class": actual_status // 100,
                "viewer_can_access": actual_status == 200,
                "leak_detected": None,
                "invariant_held": None,
            },
        },
    }
    return {
        "mainline": mainline,
        "finding": finding,
        "execution": execution,
        "contract_receipts": contract_receipts,
        "observer_receipts": [observer],
        "oracle": oracle,
        "reproduction": reproduction,
    }


def _build_gate(chain: dict) -> dict:
    return build_customer_delivery_gate_receipt_v2(
        finding=chain["finding"],
        execution_receipt=chain["execution"],
        contract_evidence_receipts=chain["contract_receipts"],
        observer_receipts=chain["observer_receipts"],
        oracle_receipt=chain["oracle"],
        reproduction_receipt=chain["reproduction"],
    )


def _build_ledger(chain: dict, gate: dict) -> tuple[dict, dict]:
    finding = copy.deepcopy(chain["finding"])
    finding.update({
        "delivery_gate_receipt": gate,
        "delivery_gate_receipt_id": gate["gate_receipt_id"],
        "gate_passed": gate["status"] == "DELIVERABLE",
        "customer_delivery_status": (
            "defect" if gate["status"] == "DELIVERABLE" else "candidate"
        ),
    })
    execution = chain["execution"]
    execution_result = {
        "status": "EXECUTED",
        "experiment_id": EXPERIMENT_ID,
        "execution_id": EXECUTION_ID,
        "receipt_id": execution["receipt_id"],
        "output_fingerprint": execution["receipt_fingerprint"],
        "observation_receipt_ids": execution["observation_receipt_ids"],
        "oracle_receipt_id": chain["oracle"]["receipt_id"],
        "operational_receipt": execution["operational_receipt"],
        "delivery_execution_receipt": execution,
        "contract_evidence_receipts": chain["contract_receipts"],
        "observer_receipts": chain["observer_receipts"],
        "oracle_receipt": chain["oracle"],
        "reproduction_receipt": chain["reproduction"],
        "finding": finding,
    }
    ledger = build_obligation_attempt_ledger(
        mainline_run=chain["mainline"],
        selected=[{
            "obligation_id": OBLIGATION_ID,
            "experiment_id": EXPERIMENT_ID,
            "candidate_id": "candidate-1",
        }],
        compile_results={
            OBLIGATION_ID: {
                "status": "COMPILED",
                "experiment_id": EXPERIMENT_ID,
                "receipt_id": "compile-1",
            }
        },
        execution_results={OBLIGATION_ID: execution_result},
        gate_results={OBLIGATION_ID: gate},
    )
    return ledger, finding


def test_complete_independent_chain_is_the_only_deliverable_path() -> None:
    chain = _chain()
    gate = _build_gate(chain)

    assert gate["schema_version"] == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
    assert gate["status"] == "DELIVERABLE"
    assert gate["reason_codes"] == []
    assert gate["identity"]["finding_id"] == "finding-1"
    assert gate["adjudication"] == {
        "execution": "EXECUTED",
        "activation": "ACTIVE",
        "assertion": "VIOLATION",
        "oracle": "VIOLATION",
        "reproduction": "REPRODUCED",
        "cleanup": "NOT_REQUIRED",
        "lineage": "CONSISTENT",
    }
    assert validate_customer_delivery_gate_receipt_v2(
        gate,
        finding=chain["finding"],
    ) == gate
    assert validate_customer_delivery_gate_bundle(
        gate,
        finding=chain["finding"],
        execution_receipt=chain["execution"],
        contract_evidence_receipts=chain["contract_receipts"],
        observer_receipts=chain["observer_receipts"],
        oracle_receipt=chain["oracle"],
        reproduction_receipt=chain["reproduction"],
    ) == gate


def test_multiple_violations_are_blocked_until_split_into_occurrences() -> None:
    gate = _build_gate(_chain(assertion_count=2))

    assert gate["status"] == "BLOCKED"
    assert gate["reason_codes"] == [
        "AMBIGUOUS_MULTI_ASSERTION_OCCURRENCE"
    ]
    assert gate["identity"]["finding_id"] == ""
    assert validate_customer_delivery_gate_receipt_v2(
        gate,
        finding=None,
    ) == gate


def test_gate_requires_execution_proven_request_semantics() -> None:
    with pytest.raises(
        DeliveryGateV2Error,
        match="request_semantics",
    ):
        _chain(include_request_semantics=False)


@pytest.mark.parametrize(
    "field",
    [
        "execution",
        "contract_receipts",
        "observer_receipts",
        "oracle",
        "reproduction",
    ],
)
def test_missing_or_foreign_chain_component_fails_closed(field: str) -> None:
    chain = _chain()
    if field in {"contract_receipts", "observer_receipts"}:
        chain[field] = []
    else:
        chain[field] = {}

    with pytest.raises(DeliveryGateV2Error):
        _build_gate(chain)


def test_gate_is_bound_to_finding_payload_and_execution_lineage() -> None:
    chain = _chain()
    gate = _build_gate(chain)

    mutated = copy.deepcopy(chain["finding"])
    mutated["title"] = "mutated after adjudication"
    with pytest.raises(DeliveryGateV2Error, match="finding_payload_fingerprint"):
        validate_customer_delivery_gate_receipt_v2(gate, finding=mutated)

    foreign = _chain()
    foreign["execution"]["execution_id"] = "execution-foreign"
    with pytest.raises(DeliveryGateV2Error):
        _build_gate(foreign)


def test_gate_validation_allows_separately_sealed_canonical_projection_fields() -> None:
    chain = _chain()
    gate = _build_gate(chain)
    projected = copy.deepcopy(chain["finding"])
    projected.update({
        "canonical_defect_id": "cdef_" + "a" * 32,
        "canonical_identity_fingerprint": "b" * 64,
        "delivery_occurrence_finding_id": projected["finding_id"],
        "delivery_occurrence_count": 1,
        "delivery_occurrence_finding_ids": [projected["finding_id"]],
    })

    assert validate_customer_delivery_gate_receipt_v2(
        gate,
        finding=projected,
    ) == gate


def test_property_held_is_rejected_and_never_self_promotes_from_flags() -> None:
    chain = _chain(expected_status=200, actual_status=200)
    chain["finding"].update({
        "gate_passed": True,
        "bug_status": "reproduced",
        "customer_delivery_status": "defect",
    })

    gate = _build_gate(chain)

    assert chain["oracle"]["status"] == "PROPERTY_HELD"
    assert gate["status"] == "REJECTED"
    assert gate["reason_code"] == "ORACLE_NOT_VIOLATED"
    assert gate["identity"]["finding_id"] == ""


def test_tampered_oracle_or_reproduction_receipt_is_rejected() -> None:
    chain = _chain()
    chain["oracle"]["status"] = "PROPERTY_HELD"
    with pytest.raises(DeliveryGateV2Error):
        _build_gate(chain)


def test_formal_projection_requires_validated_gate_and_attempt_ledger() -> None:
    chain = _chain()
    gate = _build_gate(chain)
    ledger, finding = _build_ledger(chain, gate)
    registry = build_canonical_defect_registry(
        mainline_run=chain["mainline"],
        deliverable_occurrences=[finding],
        obligation_attempt_ledger=ledger,
    )

    formal = build_formal_count_projection(
        findings=[finding],
        candidate_findings=[],
        obligation_attempt_ledger=ledger,
        mainline_run=chain["mainline"],
        canonical_defect_registry=registry,
    )
    classification = build_finding_classification_projection(
        findings=[finding],
        candidate_findings=[],
        obligation_attempt_ledger=ledger,
    )

    assert formal["formal_customer_deliverable_count"] == 1
    assert formal["canonical_defect_ids"] == registry["canonical_defect_ids"]
    assert formal["delivery_occurrence_finding_ids"] == ["finding-1"]
    assert classification["counts"]["deliverable"] == 1

    legacy_flags_only = {
        **chain["finding"],
        "gate_passed": True,
        "bug_status": "reproduced",
        "customer_delivery_status": "defect",
    }
    blocked = build_formal_count_projection(
        findings=[legacy_flags_only],
        candidate_findings=[],
        obligation_attempt_ledger=None,
    )
    assert blocked["formal_customer_deliverable_count"] == 0

    chain = _chain()
    chain["reproduction"]["status"] = "BLOCKED"
    with pytest.raises(DeliveryGateV2Error):
        _build_gate(chain)


def _evaluation_manifest() -> EvaluationManifest:
    target = EvaluationTarget(
        target_id="target-1",
        project_id="project-1",
        industry="industry-neutral",
        split="held_in",
        expectation="clean",
        environment_ref="environment-1",
        environment_type="test",
        input_bundle_ref="runtime/input.json",
        fixture_snapshot_ref="runtime/fixture.json",
        context_artifact_ref="runtime/context.json",
    )
    return EvaluationManifest(
        dataset_id="dataset-1",
        dataset_version="v1",
        targets=(target,),
        manifest_path=Path("evaluation-manifest.json"),
        manifest_fingerprint="manifest-fingerprint",
        target_fingerprints={
            "target-1": {
                "runtime_fingerprint": "runtime-fingerprint",
                "input_fingerprint": "input-fingerprint",
                "fixture_fingerprint": "fixture-fingerprint",
                "context_fingerprint": "context-fingerprint",
                "ground_truth_fingerprint": "",
            }
        },
    )


def _phase3_evaluation_receipt(
    *,
    policy_id: str = "policy-1",
    evaluation_mode: str = "shadow",
) -> dict:
    chain = _chain(evaluation_mode=evaluation_mode)
    gate = _build_gate(chain)
    ledger, finding = _build_ledger(chain, gate)
    formal_scope = build_formal_scope_contract(
        mainline_run=chain["mainline"],
        findings=[finding],
        obligation_attempt_ledger=ledger,
    )
    strategy = hashlib.sha256(
        f"{policy_id}:policy-1".encode("utf-8")
    ).hexdigest()
    execution_authority = build_test_execution_authority(
        mainline_run=chain["mainline"],
        obligation_attempt_ledger=ledger,
        policy_id=policy_id,
        strategy_fingerprint=strategy,
    )
    return evaluate_completed_scan(
        _evaluation_manifest(),
        "target-1",
        run_id="run-1",
        policy_id=policy_id,
        evaluation_mode=evaluation_mode,
        findings=list(
            formal_scope["formal_count_projection"][
                "canonical_representative_findings"
            ]
        ),
        candidates=[],
        pipeline_health={"status": "OK"},
        operational_metrics={
            "wall_clock_seconds": 1.0,
            "estimated_cost_usd": 0.1,
            "request_count": 2,
            "production_http_requests": 0,
            "cleanup_failures": 0,
            "safety_incidents": 0,
            "dirty_test_environments": 0,
            "execution_success_rate": 1.0,
            "engine_success_rate": 1.0,
            "duplicate_rate": 0.0,
        },
        obligation_attempt_ledger=ledger,
        mainline_run=chain["mainline"],
        evaluator_policy_identity={
            "policy_id": policy_id,
            "policy_version": "policy-1",
            "strategy_fingerprint": strategy,
        },
        **execution_authority,
        **formal_scope,
    )


def _reseal_plain_fingerprint(payload: dict, *, field: str) -> None:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {field, "receipt_authentication", "report_authentication"}
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload[field] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_evaluator_receipt_authentication_binds_policy_identity() -> None:
    receipt = _phase3_evaluation_receipt()

    assert receipt["policy_identity"] == {
        "policy_id": "policy-1",
        "policy_version": "policy-1",
        "strategy_fingerprint": hashlib.sha256(
            b"policy-1:policy-1"
        ).hexdigest(),
        "mainline_contract_fingerprint": receipt[
            "formal_delivery_authority"
        ]["mainline_contract_fingerprint"],
    }
    assert receipt["receipt_authentication"]["algorithm"] == "HMAC-SHA256"
    assert receipt["receipt_authentication"]["key_id"]
    assert receipt["receipt_authentication"]["signature"]


def test_evaluator_receipt_rejects_policy_relabel_after_plain_rehash() -> None:
    forged = copy.deepcopy(_phase3_evaluation_receipt())
    forged["policy_id"] = "policy-forged"
    forged["policy_identity"]["policy_id"] = "policy-forged"
    _reseal_plain_fingerprint(forged, field="receipt_fingerprint")

    with pytest.raises(EvaluationContractError, match="authentication"):
        aggregate_evaluation_receipts(_evaluation_manifest(), [forged])


def test_evaluator_fails_closed_without_hmac_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _phase3_evaluation_receipt()
    monkeypatch.delenv("QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY", raising=False)

    with pytest.raises(EvaluationContractError, match="HMAC key"):
        aggregate_evaluation_receipts(_evaluation_manifest(), [receipt])


def test_paired_evaluation_rebuilds_authenticated_report() -> None:
    manifest = _evaluation_manifest()
    champion_replay = aggregate_evaluation_receipts(
        manifest,
        [_phase3_evaluation_receipt(policy_id="champion", evaluation_mode="replay")],
    )
    challenger_replay = aggregate_evaluation_receipts(
        manifest,
        [_phase3_evaluation_receipt(policy_id="challenger", evaluation_mode="replay")],
    )
    champion_shadow = aggregate_evaluation_receipts(
        manifest,
        [_phase3_evaluation_receipt(policy_id="champion", evaluation_mode="shadow")],
    )
    challenger_shadow = aggregate_evaluation_receipts(
        manifest,
        [_phase3_evaluation_receipt(policy_id="challenger", evaluation_mode="shadow")],
    )
    forged = copy.deepcopy(champion_replay)
    forged["clean"]["customer_deliverable_false_positives"] = 0
    _reseal_plain_fingerprint(forged, field="report_fingerprint")

    with pytest.raises(EvaluationContractError, match="authentication|rebuild"):
        build_paired_evaluation_evidence(
            manifest,
            champion_replay=forged,
            challenger_replay=challenger_replay,
            champion_shadow=champion_shadow,
            challenger_shadow=challenger_shadow,
        )


def test_private_evaluator_projects_only_gate_v2_ledger_authority() -> None:
    chain = _chain(evaluation_mode="shadow")
    gate = _build_gate(chain)
    ledger, finding = _build_ledger(chain, gate)
    formal_scope = build_formal_scope_contract(
        mainline_run=chain["mainline"],
        findings=[finding],
        obligation_attempt_ledger=ledger,
    )

    projection = build_evaluator_only_projection({
        "mainline_run": chain["mainline"],
        "shadow_findings": [finding],
        "obligation_attempt_ledger": ledger,
        "evaluator_canonical_findings": list(
            formal_scope["formal_count_projection"][
                "canonical_representative_findings"
            ]
        ),
        **formal_scope,
    })

    assert [item["finding_id"] for item in projection["findings"]] == [
        "finding-1"
    ]
    assert projection["obligation_attempt_ledger"] == ledger
    assert projection["defect_identity_consistency"]["consistent"] is True


def test_external_evaluator_requires_exact_gate_v2_ledger_scope() -> None:
    chain = _chain(evaluation_mode="shadow")
    gate = _build_gate(chain)
    ledger, finding = _build_ledger(chain, gate)
    formal_scope = build_formal_scope_contract(
        mainline_run=chain["mainline"],
        findings=[finding],
        obligation_attempt_ledger=ledger,
    )
    formal = formal_scope["formal_count_projection"]
    common = {
        "run_id": "run-1",
        "policy_id": "policy-1",
        "evaluation_mode": "shadow",
        "candidates": [],
        "pipeline_health": {"status": "BLOCKED"},
        "operational_metrics": {},
        "mainline_run": chain["mainline"],
        "evaluator_policy_identity": {
            "policy_id": "policy-1",
            "policy_version": "policy-1",
            "strategy_fingerprint": hashlib.sha256(
                b"policy-1:policy-1"
            ).hexdigest(),
        },
        **formal_scope,
    }

    receipt = evaluate_completed_scan(
        _evaluation_manifest(),
        "target-1",
        findings=list(formal["canonical_representative_findings"]),
        obligation_attempt_ledger=ledger,
        **common,
    )
    assert receipt["measurement_status"] == "NOT_MEASURED"
    assert receipt["schema_version"] == "qualibug.discovery-evaluation-receipt.v3"
    validate_formal_delivery_authority_receipt(
        receipt["formal_delivery_authority"]
    )

    with pytest.raises(
        EvaluationContractError,
        match="obligation_attempt_ledger_required",
    ):
        evaluate_completed_scan(
            _evaluation_manifest(),
            "target-1",
            findings=list(formal["canonical_representative_findings"]),
            obligation_attempt_ledger=None,
            **common,
        )

    tampered_projection = dict(formal)
    tampered_projection["canonical_defect_ids"] = []
    with pytest.raises(EvaluationContractError, match="formal_count_projection"):
        evaluate_completed_scan(
            _evaluation_manifest(),
            "target-1",
            findings=list(formal["canonical_representative_findings"]),
            obligation_attempt_ledger=ledger,
            **{
                **common,
                "formal_count_projection": tampered_projection,
            },
        )

    with pytest.raises(
        EvaluationContractError,
        match="obligation_attempt_ledger_required",
    ):
        evaluate_completed_scan(
            _evaluation_manifest(),
            "target-1",
            findings=[],
            obligation_attempt_ledger=None,
            **common,
        )

    foreign = copy.deepcopy(ledger)
    foreign["run_id"] = "run-foreign"
    with pytest.raises(EvaluationContractError):
        evaluate_completed_scan(
            _evaluation_manifest(),
            "target-1",
            findings=[finding],
            obligation_attempt_ledger=foreign,
            **common,
        )

    legacy = copy.deepcopy(receipt)
    legacy["schema_version"] = "qualibug.discovery-evaluation-receipt.v1"
    legacy.pop("formal_delivery_authority", None)
    with pytest.raises(EvaluationContractError):
        aggregate_evaluation_receipts(_evaluation_manifest(), [legacy])


def test_campaign_api_exports_the_exact_validated_formal_authority(
    tmp_path: Path,
) -> None:
    chain = _chain()
    gate = _build_gate(chain)
    ledger, finding = _build_ledger(chain, gate)
    formal_scope = build_formal_scope_contract(
        mainline_run=chain["mainline"],
        findings=[finding],
        obligation_attempt_ledger=ledger,
    )
    candidate = {
        "finding_id": "candidate-1",
        "title": "Runtime clue without a formal delivery receipt",
        "mainline_run": {
            "contract_fingerprint": chain["mainline"]["contract_fingerprint"],
        },
    }
    formal_scope["formal_count_projection"] = build_formal_count_projection(
        findings=[finding],
        candidate_findings=[candidate],
        obligation_attempt_ledger=ledger,
        mainline_run=chain["mainline"],
        canonical_defect_registry=formal_scope["canonical_defect_registry"],
    )
    output = tmp_path / "platform_outputs" / "project-1"
    output.mkdir(parents=True)
    scan_result = {
        "scan_id": "run-1",
        "policy_id": "policy-1",
        "mainline_run": chain["mainline"],
        "campaign": {
            "campaign_id": CAMPAIGN_ID,
            "campaign_status": "completed",
            "project_id": "project-1",
            "environment_ref": "environment-1",
        },
        "pipeline_health": {"status": "OK", "cleanup_failure_count": 0},
        "operational_metrics": {
            "wall_clock_seconds": 1.0,
            "estimated_cost_usd": 0.1,
            "request_count": 2,
            "production_http_requests": 0,
            "cleanup_failures": 0,
            "safety_incidents": 0,
            "dirty_test_environments": 0,
            "execution_success_rate": 1.0,
            "engine_success_rate": 1.0,
            "duplicate_rate": 0.0,
        },
        "findings": list(
            formal_scope["formal_count_projection"][
                "canonical_representative_findings"
            ]
        ),
        "delivery_occurrences": [finding],
        "candidate_findings": [candidate],
        "obligation_attempt_ledger": ledger,
        **formal_scope,
        "v12": {
            "mainline_run": chain["mainline"],
            "experiment_execution": {
                "selected_count": 1,
                "executed_count": 1,
                "blocked_count": 0,
                "harness_failure_count": 0,
                "results": [{
                    "candidate_id": "candidate-1",
                    "slice_id": "slice-1",
                    "obligation_id": OBLIGATION_ID,
                    "experiment_id": EXPERIMENT_ID,
                    "execution_id": EXECUTION_ID,
                    "evidence_id": "evidence-1",
                    "campaign_id": CAMPAIGN_ID,
                    "status": "EXECUTED",
                    "reason_code": "",
                    "execution_receipt": chain["execution"],
                    "finding": finding,
                }],
            },
        },
    }
    (output / "scan_result.json").write_text(
        json.dumps(scan_result),
        encoding="utf-8",
    )

    view = build_campaign_view(tmp_path, "project-1", CAMPAIGN_ID)
    submission = build_evaluation_submission(
        tmp_path,
        "project-1",
        {"evaluation_mode": "operational"},
    )

    assert view["defect_identity_consistency"]["consistent"] is True
    assert {
        "delivery_gate_ids",
        "registry_occurrence_ids",
        "trace_ledger_occurrence_ids",
    }.issubset(
        view["defect_identity_consistency"]["occurrence_scopes"]
    )
    assert {
        "canonical_registry_ids",
        "formal_projection_ids",
        "product_projection_ids",
    }.issubset(
        view["defect_identity_consistency"]["canonical_scopes"]
    )
    assert submission["scan_result"]["obligation_attempt_ledger"] == ledger
    assert submission["scan_result"]["formal_count_projection"] == (
        submission["formal_count_projection"]
    )
    assert submission["formal_count_projection"]["candidate_count"] == 0
    assert validate_formal_delivery_authority_receipt(
        submission["formal_delivery_authority"]
    ) == submission["formal_delivery_authority"]
    assert "evaluator_submission_occurrence_ids" in submission[
        "defect_identity_consistency"
    ]["occurrence_scopes"]
    assert "evaluator_submission_ids" in submission[
        "defect_identity_consistency"
    ]["canonical_scopes"]
    persisted = json.loads(Path(submission["artifact_ref"]).read_text("utf-8"))
    persisted_authority = validate_formal_delivery_authority_receipt(
        persisted["formal_delivery_authority"]
    )
    assert persisted_authority == submission["formal_delivery_authority"]
    persisted_formal = build_formal_count_projection(
        findings=persisted["scan_result"]["delivery_occurrences"],
        candidate_findings=[],
        obligation_attempt_ledger=persisted["scan_result"][
            "obligation_attempt_ledger"
        ],
        mainline_run=persisted["mainline_run"],
        canonical_defect_registry=persisted[
            "canonical_defect_registry"
        ],
    )
    assert persisted_formal["canonical_defect_ids"] == persisted[
        "canonical_defect_registry"
    ]["canonical_defect_ids"]

    with pytest.raises(CampaignContractError, match="run_id.*mismatch"):
        build_evaluation_submission(
            tmp_path,
            "project-1",
            {"evaluation_mode": "operational", "run_id": "run-foreign"},
        )
    with pytest.raises(CampaignContractError, match="policy_id.*mismatch"):
        build_evaluation_submission(
            tmp_path,
            "project-1",
            {"evaluation_mode": "operational", "policy_id": "policy-foreign"},
        )


def test_formal_delivery_authority_is_compact_exact_and_content_addressed() -> None:
    chain = _chain()
    gate = _build_gate(chain)
    ledger, finding = _build_ledger(chain, gate)

    authority = build_formal_delivery_authority_receipt(
        mainline_run=chain["mainline"],
        findings=[finding],
        obligation_attempt_ledger=ledger,
    )

    assert authority["schema_version"] == FORMAL_DELIVERY_AUTHORITY_SCHEMA
    assert authority["status"] == "VERIFIED"
    assert authority["delivery_occurrence_finding_ids"] == ["finding-1"]
    assert authority["delivery_occurrence_count"] == 1
    assert authority["deliverable_attempts"][0]["gate_receipt_id"] == (
        gate["gate_receipt_id"]
    )
    assert "delivery_evidence_bundle" not in authority
    assert validate_formal_delivery_authority_receipt(authority) == authority

    tampered = copy.deepcopy(authority)
    tampered["delivery_occurrence_count"] = 0
    with pytest.raises(FormalDeliveryAuthorityError):
        validate_formal_delivery_authority_receipt(tampered)


def test_redacted_signed_artifact_is_revalidated_before_persistence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "signed.json"

    def require_unchanged(redacted: object) -> None:
        if redacted != {"authorization": "Bearer secret-value-123456"}:
            raise ValueError("signed payload changed during redaction")

    with pytest.raises(ValueError, match="signed payload changed"):
        write_json_redacted(
            output,
            {"authorization": "Bearer secret-value-123456"},
            post_redaction_validator=require_unchanged,
        )
    assert not output.exists()


def test_canonical_identity_is_industry_neutral_and_preserves_route_version() -> None:
    chain = _chain()
    gate = _build_gate(chain)
    ledger, _ = _build_ledger(chain, gate)
    evidence = derive_canonical_identity_evidence(ledger["attempts"][0])

    identity_v1 = build_canonical_defect_identity(
        target_id="target-1",
        evidence=evidence,
    )
    renamed = copy.deepcopy(evidence)
    renamed["operation"]["operation_ref"] = "another-occurrence-operation-id"
    assert build_canonical_defect_identity(
        target_id="target-1",
        evidence=renamed,
    )["canonical_defect_id"] == identity_v1["canonical_defect_id"]

    version_v2 = copy.deepcopy(evidence)
    version_v2["operation"]["source_locator"] = "/v2/resources/{resourceId}"
    version_v2["resource_identity_class"]["source_locators"] = [
        "/v2/resources/{param}"
    ]
    assert build_canonical_defect_identity(
        target_id="target-1",
        evidence=version_v2,
    )["canonical_defect_id"] != identity_v1["canonical_defect_id"]

    incomplete = copy.deepcopy(evidence)
    incomplete["proof"]["request_semantics_fingerprint"] = ""
    with pytest.raises(
        CanonicalDefectRegistryError,
        match="CANONICAL_IDENTITY_INCOMPLETE",
    ):
        build_canonical_defect_identity(
            target_id="target-1",
            evidence=incomplete,
        )


def test_canonical_registry_ignores_executor_authored_identity_hint() -> None:
    first = _chain()
    first["finding"]["canonical_identity_evidence"]["property"][
        "template"
    ] = "executor-hint-a"
    first_gate = _build_gate(first)
    first_ledger, first_finding = _build_ledger(first, first_gate)
    first_registry = build_canonical_defect_registry(
        mainline_run=first["mainline"],
        deliverable_occurrences=[first_finding],
        obligation_attempt_ledger=first_ledger,
    )

    second = _chain()
    second["finding"]["canonical_identity_evidence"]["property"][
        "template"
    ] = "executor-hint-b"
    second_gate = _build_gate(second)
    second_ledger, second_finding = _build_ledger(second, second_gate)
    second_registry = build_canonical_defect_registry(
        mainline_run=second["mainline"],
        deliverable_occurrences=[second_finding],
        obligation_attempt_ledger=second_ledger,
    )

    assert first_registry["canonical_defect_ids"] == second_registry[
        "canonical_defect_ids"
    ]


def test_canonical_identity_uses_receipted_actor_class_not_account_ref() -> None:
    identities = []
    for suffix in ("a", "b"):
        chain = _chain(
            control_actor_ref=f"buyer-control-{suffix}",
            treatment_actor_ref=f"buyer-treatment-{suffix}",
            actor_role="buyer",
        )
        gate = _build_gate(chain)
        ledger, _ = _build_ledger(chain, gate)
        evidence = derive_canonical_identity_evidence(ledger["attempts"][0])
        identities.append(build_canonical_defect_identity(
            target_id="target-1",
            evidence=evidence,
        ))

    assert identities[0]["canonical_defect_id"] == identities[1][
        "canonical_defect_id"
    ]
    assert identities[0]["identity"]["actor_relation"] == {
        "control_actor_class": "buyer",
        "treatment_actor_class": "buyer",
        "relation": "same_actor_class",
    }


def test_canonical_validation_identity_does_not_split_by_actor_class() -> None:
    identities = []
    for role in ("buyer", "admin"):
        chain = _chain(
            expected_status=4,
            actual_status=500,
            assertion_kind="http_status_class",
            control_actor_ref=f"{role}-control",
            treatment_actor_ref=f"{role}-treatment",
            actor_role=role,
        )
        gate = _build_gate(chain)
        ledger, _ = _build_ledger(chain, gate)
        evidence = derive_canonical_identity_evidence(ledger["attempts"][0])
        identities.append(build_canonical_defect_identity(
            target_id="target-1",
            evidence=evidence,
        ))

    assert identities[0]["canonical_defect_id"] == identities[1][
        "canonical_defect_id"
    ]
    assert identities[0]["identity"]["actor_relation"] == {
        "control_actor_class": "not_identity_defining",
        "treatment_actor_class": "not_identity_defining",
        "relation": "actor_insensitive_property",
    }


def test_canonical_registry_maps_every_gate_occurrence_exactly_once() -> None:
    chain = _chain()
    gate = _build_gate(chain)
    ledger, finding = _build_ledger(chain, gate)

    registry = build_canonical_defect_registry(
        mainline_run=chain["mainline"],
        deliverable_occurrences=[finding],
        obligation_attempt_ledger=ledger,
    )

    assert registry["schema_version"] == CANONICAL_DEFECT_REGISTRY_SCHEMA
    assert registry["canonical_defect_count"] == 1
    assert registry["delivery_occurrence_count"] == 1
    assert registry["delivery_occurrence_finding_ids"] == ["finding-1"]
    assert registry["canonical_defects"][0]["occurrence_finding_ids"] == [
        "finding-1"
    ]
    assert validate_canonical_defect_registry(
        registry,
        mainline_run=chain["mainline"],
        deliverable_occurrences=[finding],
        obligation_attempt_ledger=ledger,
    ) == registry


def test_canonical_registry_deduplicates_repeated_formal_occurrences() -> None:
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-dedupe",
        campaign_id="campaign-dedupe",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode="operational",
    )
    findings, ledger = build_formal_evaluation_scope(
        [
            {"finding_id": "occurrence-1", "title": "first title"},
            {"finding_id": "occurrence-2", "title": "renamed title"},
        ],
        run_id="run-dedupe",
        campaign_id="campaign-dedupe",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode="operational",
    )

    registry = build_canonical_defect_registry(
        mainline_run=mainline,
        deliverable_occurrences=findings,
        obligation_attempt_ledger=ledger,
    )

    assert registry["canonical_defect_count"] == 1
    assert registry["delivery_occurrence_count"] == 2
    assert registry["canonical_defects"][0]["occurrence_finding_ids"] == [
        "occurrence-1",
        "occurrence-2",
    ]

    tampered = copy.deepcopy(registry)
    tampered["canonical_defects"][0]["occurrence_count"] = 3
    with pytest.raises(CanonicalDefectRegistryError):
        validate_canonical_defect_registry(
            tampered,
            mainline_run=mainline,
            deliverable_occurrences=findings,
            obligation_attempt_ledger=ledger,
        )
