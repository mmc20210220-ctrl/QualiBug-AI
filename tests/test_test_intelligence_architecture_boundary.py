from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ai_test_asset_center.private_pilot_product_catalog import ProductCatalogHttpMixin
from products.catalog import get_product_catalog
from products.test_intelligence import (
    COVERAGE_QUALITY_CLAIM,
    analyze_test_intelligence,
    get_product_manifest,
    project_test_obligations,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = REPO_ROOT / "products" / "test_intelligence"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _source_evidence(source_id: str, locator: str, quote: str, *, fact_id: str = "") -> dict[str, str]:
    row = {"source_id": source_id, "source_locator": locator, "quote": quote}
    if fact_id:
        row["fact_id"] = fact_id
    return row


def _behavior(
    behavior_id: str,
    *,
    operation: str,
    object_ref: str,
    evidence: list[dict[str, str]],
    modality: str = "MUST",
    actors: list[str] | None = None,
    permission: str = "UNSPECIFIED",
    auth_explicit: bool = False,
    auth_kind: str = "NONE",
    auth_status: str = "NOT_DECLARED",
    expected_effects: list[str] | None = None,
    data_effects: list[dict[str, str]] | None = None,
    compensations: list[str] | None = None,
    state_effects: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "behavior_id": behavior_id,
        "status": "CONFIRMED",
        "candidate_only": False,
        "formal_business_rule": True,
        "source_kind": "ACCEPTED_BUSINESS_FACT",
        "source_refs": [f"fact:{behavior_id}"],
        "actor_refs": actors or [],
        "operation_ref": operation,
        "object_refs": [object_ref],
        "preconditions": [{"field_candidate": "status", "operator_candidate": "EQUALS", "value_candidate": {"raw": "WAIT_PAY"}}],
        "condition_combinator": "SINGLE_CONDITION",
        "expected_effects": expected_effects or [],
        "state_effects": state_effects or [],
        "data_effects": data_effects or [],
        "permission_decision": permission,
        "authorization_semantics_explicit": auth_explicit,
        "authorization_semantic_kind": auth_kind,
        "authorization_semantics_status": auth_status,
        "business_modality": modality,
        "exceptions": [],
        "compensations": compensations or [],
        "evidence": evidence,
    }


def _test_asset() -> dict[str, Any]:
    return {
        "project_id": "project-a",
        "summary": {"active_source_count": 3},
        "enterprise_understanding_model": {
            "business_behaviors": [
                _behavior(
                    "auth-deny", operation="cancel", object_ref="order", actors=["non_owner"],
                    permission="DENY", auth_explicit=True, auth_kind="AUTHORIZATION", auth_status="RESOLVED",
                    modality="MUST_NOT",
                    evidence=[_source_evidence("prd:auth", "PRD.md#line=20", "非订单所有者不得取消订单。", fact_id="fact:auth-deny")],
                ),
                _behavior(
                    "restore-stock", operation="cancel", object_ref="order",
                    expected_effects=["取消订单后恢复库存"],
                    data_effects=[{"statement": "库存增加订单数量", "field": "inventory.quantity", "object": "inventory"}],
                    evidence=[_source_evidence("prd:inventory", "PRD.md#line=32", "取消订单后恢复库存。", fact_id="fact:restore-stock")],
                ),
                _behavior(
                    "manual-review", operation="approve", object_ref="refund", modality="MUST",
                    evidence=[_source_evidence("policy:refund", "refund.md#line=11", "退款必须经过人工审核。", fact_id="fact:manual-review")],
                ),
                {**_behavior(
                    "unsupported-formal", operation="archive", object_ref="order", modality="",
                    evidence=[_source_evidence("prd:archive", "PRD.md#line=90", "订单归档。", fact_id="fact:unsupported-formal")],
                ), "expected_effects": [], "state_effects": []},
                {**_behavior(
                    "candidate-only", operation="delete", object_ref="order",
                    evidence=[_source_evidence("table:candidate", "matrix.md#row=3", "候选规则。")],
                ), "status": "CANDIDATE", "candidate_only": True, "formal_business_rule": False},
            ],
            "lifecycles": [{
                "lifecycle_id": "lifecycle:order",
                "object_ref": "order",
                "transitions": [
                    {
                        "transition_id": "transition:cancel", "from_state": "WAIT_PAY", "operation_ref": "cancel",
                        "to_state": "CANCELLED", "transition_kind": "ALLOWED", "completeness": "COMPLETE",
                        "conditions": [], "exceptions": [], "fact_refs": ["fact:state-cancel"],
                        "evidence": [_source_evidence("prd:state", "state.md#line=10", "WAIT_PAY 订单取消后进入 CANCELLED。", fact_id="fact:state-cancel")],
                    },
                    {
                        "transition_id": "transition:no-evidence", "from_state": "PAID", "operation_ref": "cancel",
                        "to_state": "CANCELLED", "transition_kind": "FORBIDDEN", "completeness": "COMPLETE",
                        "conditions": [], "exceptions": [], "fact_refs": ["fact:no-evidence"], "evidence": [],
                    },
                    {
                        "transition_id": "transition:incomplete", "from_state": "", "operation_ref": "refund",
                        "to_state": "REFUNDED", "transition_kind": "ALLOWED", "completeness": "INCOMPLETE",
                        "evidence": [_source_evidence("prd:refund", "refund.md#line=30", "退款完成后进入 REFUNDED。")],
                    },
                ],
            }],
        },
    }


def test_manifest_is_bounded_and_does_not_claim_runtime_execution() -> None:
    manifest = get_product_manifest()
    assert manifest["product_id"] == "test_intelligence"
    assert manifest["status"] == "experimental"
    assert manifest["evidence_required"] is True
    assert manifest["runtime_execution_owned"] is False
    assert manifest["supported_obligation_kinds"] == (
        "business_rule", "lifecycle_transition", "authorization", "side_effect", "requirement_risk",
    )
    assert manifest["implemented_obligation_kinds"] == (
        "business_rule", "lifecycle_transition", "authorization", "side_effect",
    )


def test_product_catalog_exposes_test_intelligence_between_primary_and_runtime() -> None:
    catalog = list(get_product_catalog())
    ids = [item["product_id"] for item in catalog]
    assert ids == ["requirement_intelligence", "test_intelligence", "bug_discovery"]
    by_id = {item["product_id"]: item for item in catalog}
    assert by_id["requirement_intelligence"]["status"] == "primary"
    assert by_id["test_intelligence"]["status"] == "experimental"
    assert by_id["test_intelligence"]["entry_mode"] == "analysis"
    assert by_id["test_intelligence"]["runtime_execution_owned"] is False
    assert by_id["bug_discovery"]["entry_mode"] == "advanced_runtime"


def test_test_intelligence_product_has_no_runtime_or_requirement_product_dependency() -> None:
    violations: list[str] = []
    for path in sorted(PRODUCT_ROOT.rglob("*.py")):
        for module in sorted(_imported_modules(path)):
            if module.startswith("ai_test_asset_center"):
                violations.append(f"{path.name} -> {module}")
            if module.startswith("products.requirement_intelligence"):
                violations.append(f"{path.name} -> {module}")
            if module.startswith("products.bug_discovery"):
                violations.append(f"{path.name} -> {module}")
    assert violations == []


def test_source_backed_business_semantics_project_to_obligations_without_execution_claims() -> None:
    analysis = analyze_test_intelligence(_test_asset())
    assert analysis["analysis_status"] == "PARTIAL"
    assert analysis["summary"]["source_count"] == 3
    assert analysis["summary"]["obligation_count"] == 4
    assert analysis["summary"]["eligible_supported_semantic_unit_count"] == 5
    assert analysis["summary"]["uncovered_supported_semantic_unit_count"] == 1
    assert analysis["summary"]["suppressed_without_evidence_count"] == 1
    assert analysis["summary"]["unsupported_formal_behavior_count"] == 1
    assert analysis["summary"]["requirement_finding_linked_obligation_count"] == 0

    by_kind = {item["obligation_kind"]: item for item in analysis["obligations"]}
    assert set(by_kind) == {"authorization", "side_effect", "business_rule", "lifecycle_transition"}

    authorization = by_kind["authorization"]
    assert authorization["source_behavior_id"] == "auth-deny"
    assert authorization["actor_refs"] == ["non_owner"]
    assert authorization["object_refs"] == ["order"]
    assert authorization["expected_outcomes"] == [{"kind": "authorization_decision", "decision": "DENY"}]
    assert authorization["source_ids"] == ["prd:auth"]

    lifecycle = by_kind["lifecycle_transition"]
    assert lifecycle["source_transition_id"] == "transition:cancel"
    assert lifecycle["expected_outcomes"] == [{"kind": "lifecycle_transition", "transition_kind": "ALLOWED", "from_state": "WAIT_PAY", "to_state": "CANCELLED"}]

    side_effect = by_kind["side_effect"]
    assert side_effect["source_behavior_id"] == "restore-stock"
    assert any(item["kind"] == "postcondition" and item["statement"] == "取消订单后恢复库存" for item in side_effect["expected_outcomes"])

    business_rule = by_kind["business_rule"]
    assert business_rule["source_behavior_id"] == "manual-review"
    assert business_rule["expected_outcomes"] == [{"kind": "business_modality", "modality": "MUST"}]

    for obligation in analysis["obligations"]:
        assert obligation["evidence"]
        assert obligation["design_status"] == "OBLIGATION_ONLY"
        assert obligation["verification_status"] == "NOT_MEASURED"
        assert obligation["runtime_linkage"] == "NOT_EVALUATED"
        assert obligation["requirement_finding_ids"] == []


def test_projection_identity_is_stable_and_missing_evidence_is_fail_closed() -> None:
    first = project_test_obligations(_test_asset())
    second = project_test_obligations(_test_asset())
    assert [item["obligation_id"] for item in first["obligations"]] == [item["obligation_id"] for item in second["obligations"]]
    assert first["suppressed_without_evidence_count"] == 1
    assert first["uncovered_source_unit_ids"] == ["lifecycle:transition:no-evidence"]
    assert all(item.get("source_transition_id") != "transition:no-evidence" for item in first["obligations"])


def test_coverage_is_supported_semantic_projection_not_total_test_completeness() -> None:
    coverage = analyze_test_intelligence(_test_asset())["coverage"]
    assert coverage["status"] == "PARTIAL"
    assert coverage["eligible_supported_semantic_unit_count"] == 5
    assert coverage["obligated_supported_semantic_unit_count"] == 4
    assert coverage["uncovered_supported_semantic_unit_count"] == 1
    assert coverage["execution_coverage_status"] == "NOT_MEASURED"
    assert coverage["quality_claim"] == COVERAGE_QUALITY_CLAIM
    assert "percentage" not in coverage
    assert "score" not in coverage
    assert "recall" not in coverage
    assert coverage["counts_by_obligation_kind"]["requirement_risk"] == 0


def test_empty_understanding_is_not_presented_as_complete_coverage() -> None:
    analysis = analyze_test_intelligence({"project_id": "empty"})
    assert analysis["analysis_status"] == "NOT_MEASURED"
    assert analysis["coverage"]["status"] == "NOT_MEASURED"
    assert analysis["coverage"]["eligible_supported_semantic_unit_count"] == 0
    assert analysis["coverage"]["execution_coverage_status"] == "NOT_MEASURED"
    assert analysis["obligations"] == []


class _FallbackGet:
    fallback_called = False
    def do_GET(self) -> Any:  # noqa: N802
        self.fallback_called = True
        return "fallback"


class _ProductHarness(ProductCatalogHttpMixin, _FallbackGet):
    def __init__(self, *, path: str, authenticated: bool = True) -> None:
        self.path = path
        self.authenticated = authenticated
        self.initialized = False
        self.fallback_called = False
        self.response: tuple[dict[str, Any], int] | None = None

    def _init_request_context(self) -> None:
        self.initialized = True

    def _root(self) -> Path:
        return REPO_ROOT

    def _require_actor(self) -> dict[str, str] | None:
        return {"role": "viewer"} if self.authenticated else None

    def _require_tenant(self, root: Path) -> str | None:
        assert root == REPO_ROOT
        return "tenant-test" if self.authenticated else None

    def _require_project_scope(self, project: str) -> bool:
        return project == "project-a"

    def _require_known_project(self, project: str, root: Path) -> bool:
        return project == "project-a" and root == REPO_ROOT

    def _load_merged_knowledge_asset(self, project: str, root: Path, actor: dict[str, str]) -> dict[str, Any]:
        assert project == "project-a"
        assert root == REPO_ROOT
        assert actor["role"] == "viewer"
        return _test_asset()

    def _json(self, payload: dict[str, Any], status: int = 200, **_: Any) -> dict[str, Any]:
        self.response = (payload, status)
        return payload


def test_project_test_intelligence_route_reuses_authenticated_project_asset() -> None:
    handler = _ProductHarness(path="/api/v1/projects/project-a/test-intelligence")
    payload = handler.do_GET()
    assert handler.initialized is True
    assert handler.fallback_called is False
    assert payload["ok"] is True
    assert payload["data"]["project_id"] == "project-a"
    assert payload["data"]["product_id"] == "test_intelligence"
    assert payload["data"]["summary"]["obligation_count"] == 4
    assert payload["data"]["coverage"]["status"] == "PARTIAL"


def test_test_intelligence_route_rejects_unauthenticated_request() -> None:
    handler = _ProductHarness(path="/api/v1/projects/project-a/test-intelligence", authenticated=False)
    assert handler.do_GET() is None
    assert handler.fallback_called is False
    assert handler.response is None


def test_product_mixin_still_delegates_unrelated_routes() -> None:
    handler = _ProductHarness(path="/api/health")
    assert handler.do_GET() == "fallback"
    assert handler.fallback_called is True
