from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center.private_pilot_product_catalog import ProductCatalogHttpMixin
from ai_test_asset_center.product_intelligence_linkage import (
    LINKAGE_QUALITY_CLAIM,
    LINKAGE_SCHEMA,
    compose_requirement_test_linkage,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _evidence(source_id: str, locator: str, quote: str, fact_id: str = "") -> dict[str, str]:
    row = {"source_id": source_id, "source_locator": locator, "quote": quote}
    if fact_id:
        row["fact_id"] = fact_id
    return row


def _obligation(
    obligation_id: str,
    kind: str,
    *,
    object_refs: list[str],
    operation_ref: str,
    source_refs: list[str] | None = None,
    evidence: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "obligation_id": obligation_id,
        "obligation_kind": kind,
        "object_refs": object_refs,
        "operation_ref": operation_ref,
        "source_refs": source_refs or [],
        "evidence": evidence or [],
        "requirement_finding_ids": [],
    }


def _test_analysis(obligations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "product_id": "test_intelligence",
        "project_id": "project-a",
        "summary": {
            "requirement_finding_linked_obligation_count": 0,
        },
        "obligations": obligations,
    }


def _requirement_analysis(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "product_id": "requirement_intelligence",
        "project_id": "project-a",
        "findings": findings,
    }


def test_exact_linkage_uses_fact_identity_object_identity_and_lifecycle_coordinates() -> None:
    requirement_analysis = _requirement_analysis(
        [
            {
                "finding_id": "requirement:conflict-1",
                "finding_type": "requirement_conflict",
                "evidence": [_evidence("prd:a", "a.md#1", "规则 A", "fact:rule-1")],
            },
            {
                "finding_id": "requirement:ambiguity-1",
                "finding_type": "requirement_ambiguity",
                "candidate_entity_ids": ["entity:customer", "entity:customer-profile"],
                "evidence": [_evidence("prd:b", "b.md#2", "客户定义")],
            },
            {
                "finding_id": "requirement:missing-1",
                "finding_type": "requirement_missing",
                "related_object_refs": ["order"],
                "related_operation_refs": ["cancel"],
                "evidence": [_evidence("prd:c", "c.md#3", "订单允许取消")],
            },
            {
                "finding_id": "requirement:missing-unlinked",
                "finding_type": "requirement_missing",
                "related_object_refs": ["refund"],
                "related_operation_refs": ["complete"],
                "evidence": [_evidence("prd:d", "d.md#4", "退款完成")],
            },
        ]
    )
    test_analysis = _test_analysis(
        [
            _obligation(
                "test:rule",
                "business_rule",
                object_refs=["order"],
                operation_ref="cancel",
                source_refs=["fact:rule-1"],
            ),
            _obligation(
                "test:identity",
                "authorization",
                object_refs=["entity:customer"],
                operation_ref="edit",
            ),
            _obligation(
                "test:lifecycle",
                "lifecycle_transition",
                object_refs=["order"],
                operation_ref="cancel",
            ),
            _obligation(
                "test:unrelated",
                "side_effect",
                object_refs=["invoice"],
                operation_ref="issue",
            ),
        ]
    )

    composed = compose_requirement_test_linkage(requirement_analysis, test_analysis)

    by_id = {item["obligation_id"]: item for item in composed["obligations"]}
    assert by_id["test:rule"]["requirement_finding_ids"] == ["requirement:conflict-1"]
    assert by_id["test:identity"]["requirement_finding_ids"] == ["requirement:ambiguity-1"]
    assert by_id["test:lifecycle"]["requirement_finding_ids"] == ["requirement:missing-1"]
    assert by_id["test:unrelated"]["requirement_finding_ids"] == []

    receipt = composed["requirement_linkage"]
    assert receipt["schema"] == LINKAGE_SCHEMA
    assert receipt["quality_claim"] == LINKAGE_QUALITY_CLAIM
    assert receipt["requirement_finding_count"] == 4
    assert receipt["linked_requirement_finding_count"] == 3
    assert receipt["unlinked_requirement_finding_count"] == 1
    assert receipt["linked_test_obligation_count"] == 3
    assert receipt["link_count"] == 3
    assert receipt["unlinked_findings"] == [
        {
            "finding_id": "requirement:missing-unlinked",
            "finding_type": "requirement_missing",
            "reason_code": "NO_EXACT_LINKAGE_PROOF",
        }
    ]
    assert composed["summary"]["requirement_finding_linked_obligation_count"] == 3
    assert composed["summary"]["linked_requirement_finding_count"] == 3
    assert composed["summary"]["unlinked_requirement_finding_count"] == 1
    assert composed["summary"]["requirement_finding_link_count"] == 3


def test_linkage_never_uses_same_source_or_nearby_business_coordinates_as_proof() -> None:
    requirement_analysis = _requirement_analysis(
        [
            {
                "finding_id": "requirement:conflict-no-shared-fact",
                "finding_type": "requirement_conflict",
                "evidence": [_evidence("prd:shared", "prd.md#1", "冲突规则", "fact:other")],
            },
            {
                "finding_id": "requirement:missing-nearby",
                "finding_type": "requirement_missing",
                "related_object_refs": ["order"],
                "related_operation_refs": ["cancel"],
                "evidence": [_evidence("prd:shared", "prd.md#2", "目标状态缺失")],
            },
        ]
    )
    test_analysis = _test_analysis(
        [
            _obligation(
                "test:nearby",
                "business_rule",
                object_refs=["order"],
                operation_ref="cancel",
                source_refs=["fact:rule"],
                evidence=[_evidence("prd:shared", "prd.md#3", "取消规则", "fact:rule")],
            )
        ]
    )

    composed = compose_requirement_test_linkage(requirement_analysis, test_analysis)

    assert composed["obligations"][0]["requirement_finding_ids"] == []
    assert composed["requirement_linkage"]["link_count"] == 0
    assert composed["requirement_linkage"]["unlinked_requirement_finding_count"] == 2


def test_composition_rejects_cross_project_or_wrong_product_inputs() -> None:
    test_analysis = _test_analysis([])
    wrong_project = _requirement_analysis([])
    wrong_project["project_id"] = "project-b"
    try:
        compose_requirement_test_linkage(wrong_project, test_analysis)
    except ValueError as exc:
        assert str(exc) == "requirement_test_project_mismatch"
    else:
        raise AssertionError("cross-project linkage must fail closed")

    wrong_product = _requirement_analysis([])
    wrong_product["product_id"] = "other"
    try:
        compose_requirement_test_linkage(wrong_product, test_analysis)
    except ValueError as exc:
        assert str(exc) == "requirement_analysis_product_mismatch"
    else:
        raise AssertionError("wrong product linkage must fail closed")


def _route_asset() -> dict[str, Any]:
    return {
        "project_id": "project-a",
        "summary": {"active_source_count": 2},
        "cross_document_conflicts": [
            {
                "conflict_id": "conflict:cancel-rule",
                "kind": "BUSINESS_MODALITY_CONTRADICTION",
                "status": "UNRESOLVED",
                "reason": "取消规则冲突",
                "evidence": [
                    _evidence("prd:v1", "prd.md#1", "订单必须允许取消", "fact:cancel-rule"),
                    _evidence("prd:v2", "prd.md#2", "订单不得取消", "fact:cancel-rule-opposite"),
                ],
            }
        ],
        "enterprise_understanding_model": {
            "business_behaviors": [
                {
                    "behavior_id": "cancel-rule",
                    "status": "CONFIRMED",
                    "candidate_only": False,
                    "formal_business_rule": True,
                    "source_refs": ["fact:cancel-rule"],
                    "actor_refs": [],
                    "operation_ref": "cancel",
                    "object_refs": ["order"],
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
                    "evidence": [
                        _evidence("prd:v1", "prd.md#1", "订单必须允许取消", "fact:cancel-rule")
                    ],
                }
            ],
            "lifecycles": [],
        },
    }


class _FallbackGet:
    def do_GET(self) -> Any:  # noqa: N802
        return "fallback"


class _ProductHarness(ProductCatalogHttpMixin, _FallbackGet):
    def __init__(self, path: str) -> None:
        self.path = path

    def _init_request_context(self) -> None:
        return None

    def _root(self) -> Path:
        return REPO_ROOT

    def _require_actor(self) -> dict[str, str]:
        return {"role": "viewer"}

    def _require_tenant(self, root: Path) -> str:
        assert root == REPO_ROOT
        return "tenant-test"

    def _require_project_scope(self, project: str) -> bool:
        return project == "project-a"

    def _require_known_project(self, project: str, root: Path) -> bool:
        return project == "project-a" and root == REPO_ROOT

    def _load_merged_knowledge_asset(self, project: str, root: Path, actor: dict[str, str]) -> dict[str, Any]:
        assert project == "project-a"
        assert root == REPO_ROOT
        assert actor["role"] == "viewer"
        return _route_asset()

    def _json(self, payload: dict[str, Any], status: int = 200, **_: Any) -> dict[str, Any]:
        assert status == 200
        return payload


def test_test_intelligence_http_route_returns_composed_requirement_linkage() -> None:
    payload = _ProductHarness("/api/v1/projects/project-a/test-intelligence").do_GET()

    data = payload["data"]
    assert data["product_id"] == "test_intelligence"
    assert data["summary"]["obligation_count"] == 1
    assert data["summary"]["requirement_finding_linked_obligation_count"] == 1
    assert data["obligations"][0]["requirement_finding_ids"] == [
        "requirement:conflict:cancel-rule"
    ]
    assert data["requirement_linkage"]["link_count"] == 1
    assert data["requirement_linkage"]["links"][0]["reason_code"] == "SHARED_SOURCE_FACT_ID"
