from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ai_test_asset_center.product_intelligence_linkage import compose_requirement_test_linkage
from products.test_intelligence import (
    TEST_DESIGN_PROJECTION_SCHEMA,
    TEST_DESIGN_QUALITY_CLAIM,
    TEST_DESIGN_SCHEMA,
    analyze_test_intelligence,
    get_product_manifest,
    project_test_designs,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = REPO_ROOT / "products" / "test_intelligence"


def _evidence(fact_id: str = "fact:rule") -> list[dict[str, str]]:
    return [
        {
            "source_id": "prd:order",
            "source_locator": "PRD.md#line=20",
            "quote": "订单规则来源证据",
            "fact_id": fact_id,
        }
    ]


def _obligation(
    obligation_id: str,
    kind: str,
    *,
    expected_outcomes: list[dict[str, Any]],
    preconditions: list[Any] | None = None,
    operation_ref: str = "cancel",
    object_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "qualibug.test-obligation.v1",
        "obligation_id": obligation_id,
        "obligation_kind": kind,
        "source_unit_id": f"unit:{obligation_id}",
        "source_behavior_id": "behavior:cancel",
        "source_transition_id": "",
        "title": "订单取消规则",
        "objective": "验证订单取消规则满足来源业务定义。",
        "actor_refs": ["operator"],
        "object_refs": object_refs or ["order"],
        "operation_ref": operation_ref,
        "preconditions": preconditions or [{"kind": "state", "state": "WAIT_PAY"}],
        "expected_outcomes": expected_outcomes,
        "business_constraints": ["condition_combinator=SINGLE_CONDITION"],
        "source_refs": ["fact:rule"],
        "source_ids": ["prd:order"],
        "evidence": _evidence(),
        "derived_from": [{"kind": "business_behavior", "id": "behavior:cancel"}],
        "requirement_finding_ids": [],
        "design_status": "OBLIGATION_ONLY",
        "verification_status": "NOT_MEASURED",
        "runtime_linkage": "NOT_EVALUATED",
        "risk_level": "NOT_ASSESSED",
    }


def test_test_design_projects_semantic_setup_action_observation_and_oracle_only() -> None:
    obligation = _obligation(
        "test-obligation:auth",
        "authorization",
        expected_outcomes=[{"kind": "authorization_decision", "decision": "DENY"}],
    )

    projection = project_test_designs([obligation])

    assert projection["schema"] == TEST_DESIGN_PROJECTION_SCHEMA
    assert projection["status"] == "DESIGNED"
    assert projection["quality_claim"] == TEST_DESIGN_QUALITY_CLAIM
    assert projection["eligible_obligation_count"] == 1
    assert projection["designed_obligation_count"] == 1
    assert projection["undesigned_obligation_count"] == 0
    assert projection["runtime_grounding_status"] == "NOT_GROUNDED"
    assert projection["runtime_execution_status"] == "NOT_EXECUTED"

    design = projection["designs"][0]
    assert design["schema"] == TEST_DESIGN_SCHEMA
    assert design["source_obligation_id"] == "test-obligation:auth"
    assert design["setup"]["preconditions"] == obligation["preconditions"]
    assert design["setup"]["test_data_requirements"] == obligation["preconditions"]
    assert design["setup"]["test_data_materialization_status"] == "NOT_MATERIALIZED"
    assert design["setup"]["environment_status"] == "NOT_SELECTED"
    assert design["action"] == {
        "operation_ref": "cancel",
        "actor_refs": ["operator"],
        "object_refs": ["order"],
        "execution_surface": "NOT_SELECTED",
        "binding_status": "NOT_GROUNDED",
    }
    assert design["observations"] == [
        {
            "observation_kind": "authorization_decision",
            "binding_status": "NOT_GROUNDED",
            "expected": {"kind": "authorization_decision", "decision": "DENY"},
            "target": "operation_authorization_result",
        }
    ]
    assert design["oracle"]["assertions"] == obligation["expected_outcomes"]
    assert design["oracle"]["semantic_status"] == "SOURCE_DERIVED"
    assert design["oracle"]["binding_status"] == "NOT_GROUNDED"
    assert design["evidence"] == obligation["evidence"]
    assert design["design_status"] == "STRUCTURED_DESIGN_ONLY"
    assert design["observer_binding_status"] == "NOT_GROUNDED"
    assert design["oracle_binding_status"] == "NOT_GROUNDED"
    assert design["runtime_handoff_status"] == "NOT_REQUESTED"
    assert design["execution_status"] == "NOT_EXECUTED"
    assert design["safety_review_status"] == "NOT_ASSESSED"

    serialized = repr(design).lower()
    for forbidden in ("http://", "https://", "api_path", "ui_step", "selector", "click("):
        assert forbidden not in serialized


def test_test_design_observation_targets_are_derived_from_existing_outcome_kinds() -> None:
    obligation = _obligation(
        "test-obligation:effects",
        "side_effect",
        expected_outcomes=[
            {"kind": "postcondition", "statement": "库存恢复"},
            {
                "kind": "data_effect",
                "statement": "库存增加订单数量",
                "object": "inventory",
                "field": "quantity",
            },
            {"kind": "compensation", "statement": "释放预占额度"},
        ],
    )

    design = project_test_designs([obligation])["designs"][0]
    assert [item["target"] for item in design["observations"]] == [
        "business_postcondition",
        "business_data_effect",
        "business_compensation_result",
    ]
    assert design["observations"][1]["object_ref"] == "inventory"
    assert design["observations"][1]["field"] == "quantity"


def test_test_design_identity_is_stable_and_malformed_obligations_fail_closed() -> None:
    obligation = _obligation(
        "test-obligation:stable",
        "business_rule",
        expected_outcomes=[{"kind": "business_modality", "modality": "MUST"}],
    )
    first = project_test_designs([obligation])
    second = project_test_designs([obligation])
    assert first["designs"][0]["design_id"] == second["designs"][0]["design_id"]

    malformed = dict(obligation)
    malformed["obligation_id"] = "test-obligation:missing-evidence"
    malformed["evidence"] = []
    projection = project_test_designs([malformed])
    assert projection["status"] == "PARTIAL"
    assert projection["designs"] == []
    assert projection["undesigned_obligation_ids"] == [
        "test-obligation:missing-evidence"
    ]


def test_empty_obligation_universe_is_not_presented_as_designed() -> None:
    projection = project_test_designs([])
    assert projection["status"] == "NOT_MEASURED"
    assert projection["eligible_obligation_count"] == 0
    assert projection["designs"] == []


def _analysis_asset() -> dict[str, Any]:
    return {
        "project_id": "project-a",
        "summary": {"active_source_count": 1},
        "enterprise_understanding_model": {
            "business_behaviors": [
                {
                    "behavior_id": "manual-review",
                    "status": "CONFIRMED",
                    "candidate_only": False,
                    "formal_business_rule": True,
                    "source_kind": "ACCEPTED_BUSINESS_FACT",
                    "source_refs": ["fact:rule"],
                    "actor_refs": ["reviewer"],
                    "operation_ref": "approve",
                    "object_refs": ["refund"],
                    "preconditions": [],
                    "condition_combinator": "",
                    "expected_effects": [],
                    "state_effects": [],
                    "data_effects": [],
                    "permission_decision": "UNSPECIFIED",
                    "authorization_semantics_explicit": False,
                    "authorization_semantic_kind": "NONE",
                    "authorization_semantics_status": "NOT_DECLARED",
                    "business_modality": "MUST",
                    "exceptions": [],
                    "compensations": [],
                    "evidence": _evidence(),
                }
            ],
            "lifecycles": [],
        },
    }


def test_test_intelligence_analysis_exposes_design_projection_without_execution_claim() -> None:
    analysis = analyze_test_intelligence(_analysis_asset())

    assert analysis["summary"]["obligation_count"] == 1
    assert analysis["summary"]["test_design_count"] == 1
    assert analysis["summary"]["undesigned_obligation_count"] == 0
    assert analysis["summary"]["test_design_status"] == "DESIGNED"
    assert analysis["test_design_projection"]["status"] == "DESIGNED"
    assert analysis["test_design_projection"]["runtime_grounding_status"] == "NOT_GROUNDED"
    assert analysis["test_design_projection"]["runtime_execution_status"] == "NOT_EXECUTED"
    assert analysis["test_designs"][0]["execution_status"] == "NOT_EXECUTED"
    assert analysis["obligations"][0]["design_status"] == "OBLIGATION_ONLY"


def test_requirement_links_propagate_to_design_only_from_proven_source_obligation_link() -> None:
    obligation = _obligation(
        "test-obligation:linked",
        "business_rule",
        expected_outcomes=[{"kind": "business_modality", "modality": "MUST"}],
    )
    design_projection = project_test_designs([obligation])
    test_analysis = {
        "product_id": "test_intelligence",
        "project_id": "project-a",
        "summary": {
            "requirement_finding_linked_obligation_count": 0,
            "test_design_count": 1,
        },
        "obligations": [obligation],
        "test_designs": design_projection["designs"],
    }
    requirement_analysis = {
        "product_id": "requirement_intelligence",
        "project_id": "project-a",
        "findings": [
            {
                "finding_id": "requirement:conflict:rule",
                "finding_type": "requirement_conflict",
                "evidence": _evidence(),
            }
        ],
    }

    composed = compose_requirement_test_linkage(requirement_analysis, test_analysis)

    expected = ["requirement:conflict:rule"]
    assert composed["obligations"][0]["requirement_finding_ids"] == expected
    assert composed["test_designs"][0]["requirement_finding_ids"] == expected
    assert composed["summary"]["requirement_finding_linked_design_count"] == 1
    assert composed["requirement_linkage"]["linked_test_design_count"] == 1


def test_manifest_owns_structured_design_but_not_grounding_or_execution() -> None:
    manifest = get_product_manifest()
    assert manifest["structured_test_design_owned"] is True
    assert manifest["runtime_grounding_owned"] is False
    assert manifest["runtime_execution_owned"] is False


def test_test_design_product_layer_does_not_import_runtime_authorities() -> None:
    violations: list[str] = []
    for path in sorted(PRODUCT_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                modules = []
            for module in modules:
                if module.startswith("ai_test_asset_center"):
                    violations.append(f"{path.name} -> {module}")
                if module.startswith("products.requirement_intelligence"):
                    violations.append(f"{path.name} -> {module}")
                if module.startswith("products.bug_discovery"):
                    violations.append(f"{path.name} -> {module}")
    assert violations == []
