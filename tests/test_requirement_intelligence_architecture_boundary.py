from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ai_test_asset_center.private_pilot_product_catalog import ProductCatalogHttpMixin
from products.catalog import get_product_catalog
from products.requirement_intelligence import (
    READINESS_SCHEMA,
    analyze_knowledge_asset,
    get_product_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = REPO_ROOT / "products" / "requirement_intelligence"
SERVICE_PATH = REPO_ROOT / "ai_test_asset_center" / "private_pilot_service.py"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _is_forbidden(module: str) -> bool:
    if module.startswith("products.bug_discovery"):
        return True
    if module == "ai_test_asset_center.v12_pipeline" or module.startswith(
        "ai_test_asset_center.v12_pipeline."
    ):
        return True
    if module.startswith("ai_test_asset_center.private_pilot_") and "_patch" in module:
        return True
    if module.startswith("ai_test_asset_center.discovery_runtime_semantic_binding"):
        return True
    return False


def _source_evidence(
    source_id: str,
    locator: str,
    quote: str,
    *,
    fact_id: str = "",
) -> dict[str, str]:
    row = {
        "source_id": source_id,
        "source_locator": locator,
        "quote": quote,
    }
    if fact_id:
        row["fact_id"] = fact_id
    return row


def _analysis_asset() -> dict[str, Any]:
    return {
        "project_id": "project-a",
        "summary": {"active_source_count": 2},
        "cross_document_conflicts": [
            {
                "conflict_id": "conflict:business:1",
                "kind": "BUSINESS_MODALITY_CONTRADICTION",
                "status": "UNRESOLVED",
                "reason": "同一取消订单规则同时声明 MUST_NOT 与 MAY。",
                "operator_action": "请选择被批准的来源版本。",
                "evidence": [
                    {
                        **_source_evidence(
                            "prd:v1",
                            "PRD.md#line=20",
                            "已支付订单不得取消",
                            fact_id="fact:prd",
                        ),
                        "quote_hash": "hash:prd",
                    },
                    {
                        **_source_evidence(
                            "api:v2",
                            "openapi.yaml#/orders/cancel",
                            "PAID may cancel",
                            fact_id="fact:api",
                        ),
                        "quote_hash": "hash:api",
                    },
                ],
                "authority_decision": {
                    "status": "UNRESOLVED",
                    "automatic_resolution_allowed": False,
                },
            },
            {
                "conflict_id": "conflict:resolved:1",
                "kind": "STATE_TRANSITION_TARGET_CONTRADICTION",
                "status": "RESOLVED",
                "evidence": [
                    _source_evidence(
                        "state:v1",
                        "state.md#line=8",
                        "PAID -> CANCELLED",
                    )
                ],
            },
            {
                "conflict_id": "conflict:no-evidence:1",
                "kind": "CROSS_SOURCE_CONFLICT",
                "status": "UNRESOLVED",
                "reason": "内部候选缺少可交付来源证据。",
            },
        ],
    }


def _missing_asset() -> dict[str, Any]:
    return {
        "project_id": "project-missing",
        "summary": {"active_source_count": 1},
        "enterprise_understanding_model": {
            "unknowns": [
                {
                    "unknown_id": "understanding_unknown:to-state",
                    "kind": "LIFECYCLE_TO_STATE_UNKNOWN",
                    "reason_code": "LIFECYCLE_TO_STATE_UNKNOWN",
                    "question": "订单执行“取消”后的目标状态未定义。",
                    "related_object_refs": ["order"],
                    "related_operation_refs": ["cancel"],
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "resolution_status": "UNRESOLVED",
                    "automatic_inference_allowed": False,
                    "details": {"from_state": "PAID"},
                    "evidence": [
                        _source_evidence(
                            "prd:order",
                            "PRD.md#line=31",
                            "已支付订单允许取消。",
                        )
                    ],
                },
                {
                    "unknown_id": "understanding_unknown:from-state",
                    "kind": "LIFECYCLE_FROM_STATE_UNKNOWN",
                    "reason_code": "LIFECYCLE_FROM_STATE_UNKNOWN",
                    "question": "退款完成前的起始状态未定义。",
                    "related_object_refs": ["refund"],
                    "related_operation_refs": ["complete"],
                    "severity": "P1",
                    "blocks_formal_understanding": False,
                    "resolution_status": "RESOLVED",
                    "automatic_inference_allowed": False,
                    "evidence": [
                        _source_evidence(
                            "prd:refund",
                            "PRD.md#line=80",
                            "系统完成退款。",
                        )
                    ],
                },
                {
                    "unknown_id": "understanding_unknown:no-evidence",
                    "kind": "LIFECYCLE_DISCONNECTED",
                    "reason_code": "LIFECYCLE_DISCONNECTED",
                    "question": "订单生命周期存在未连接片段。",
                    "severity": "P1",
                    "blocks_formal_understanding": False,
                    "resolution_status": "UNRESOLVED",
                    "automatic_inference_allowed": False,
                    "evidence": [],
                },
                {
                    "unknown_id": "understanding_unknown:technical",
                    "kind": "DOCUMENT_STRUCTURE_CONTENT_UNPARSED",
                    "reason_code": "DOCUMENT_STRUCTURE_CONTENT_UNPARSED",
                    "question": "parser did not expose structure",
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "resolution_status": "UNRESOLVED",
                    "evidence": [
                        _source_evidence(
                            "prd:technical",
                            "PRD.md#line=1",
                            "Document title",
                        )
                    ],
                },
                {
                    "unknown_id": "understanding_unknown:contradiction",
                    "kind": "LIFECYCLE_TARGET_CONTRADICTION",
                    "reason_code": "LIFECYCLE_TARGET_CONTRADICTION",
                    "question": "同一状态操作存在多个目标。",
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "resolution_status": "UNRESOLVED",
                    "evidence": [
                        _source_evidence(
                            "prd:state",
                            "state.md#line=9",
                            "PAID -> CANCELLED or REFUNDING",
                        )
                    ],
                },
            ]
        },
    }


def _ambiguity_asset() -> dict[str, Any]:
    return {
        "project_id": "project-ambiguity",
        "summary": {"active_source_count": 2},
        "enterprise_identity_structural_review_queue": {
            "schema": "qualibug.enterprise-identity-structural-review-queue.v1",
            "tasks": [
                {
                    "review_task_id": "enterprise_identity_structural_review_task:1",
                    "candidate_id": "candidate:customer",
                    "candidate_entity_ids": ["entity:customer", "entity:customer-profile"],
                    "canonical_labels": {
                        "entity:customer": "客户",
                        "entity:customer-profile": "CustomerProfile",
                    },
                    "matched_dimensions": ["operation", "lifecycle"],
                    "review_status": "PENDING_REVIEW",
                    "requires_explicit_canonical_entity_selection": True,
                    "automatic_resolution_allowed": False,
                    "automatic_entity_union_allowed": False,
                    "evidence": [
                        _source_evidence(
                            "prd:legacy",
                            "legacy.md#line=5",
                            "CustomerProfile（客户）",
                        ),
                        _source_evidence(
                            "prd:current",
                            "current.md#line=5",
                            "CustomerAccount（客户）",
                        ),
                    ],
                },
                {
                    "review_task_id": "enterprise_identity_structural_review_task:confirmed",
                    "candidate_id": "candidate:contract",
                    "review_status": "CONFIRMED",
                    "evidence": [
                        _source_evidence(
                            "prd:contract",
                            "PRD.md#line=12",
                            "合同 Contract",
                        )
                    ],
                },
                {
                    "review_task_id": "enterprise_identity_structural_review_task:no-evidence",
                    "candidate_id": "candidate:no-evidence",
                    "review_status": "PENDING_REVIEW",
                    "evidence": [],
                    "automatic_resolution_allowed": False,
                    "automatic_entity_union_allowed": False,
                },
            ],
        },
    }


def test_requirement_intelligence_manifest_is_bounded_and_evidence_required() -> None:
    manifest = get_product_manifest()

    assert manifest["product_id"] == "requirement_intelligence"
    assert manifest["status"] == "primary"
    assert manifest["evidence_required"] is True
    expected = (
        "requirement_conflict",
        "requirement_missing",
        "requirement_ambiguity",
    )
    assert tuple(manifest["supported_findings"]) == expected
    assert tuple(manifest["implemented_findings"]) == expected


def test_product_catalog_demotes_bug_discovery_without_importing_runtime() -> None:
    catalog = {item["product_id"]: item for item in get_product_catalog()}

    assert catalog["requirement_intelligence"]["status"] == "primary"
    assert catalog["requirement_intelligence"]["entry_mode"] == "analysis"
    assert catalog["requirement_intelligence"]["implemented_findings"] == (
        "requirement_conflict",
        "requirement_missing",
        "requirement_ambiguity",
    )
    assert catalog["bug_discovery"]["status"] == "experimental"
    assert catalog["bug_discovery"]["entry_mode"] == "advanced_runtime"


def test_requirement_intelligence_does_not_import_bug_discovery_authorities() -> None:
    violations: list[str] = []
    for path in sorted(PRODUCT_ROOT.rglob("*.py")):
        for module in sorted(_imported_modules(path)):
            if _is_forbidden(module):
                violations.append(f"{path.relative_to(PRODUCT_ROOT)} -> {module}")

    assert violations == [], (
        "Requirement Intelligence must remain upstream of Bug Discovery execution/patch "
        "authorities. Forbidden imports: " + ", ".join(violations)
    )


def test_requirement_conflict_projection_uses_existing_conflict_identity_and_evidence() -> None:
    analysis = analyze_knowledge_asset(_analysis_asset())

    assert analysis["analysis_status"] == "NOT_READY"
    assert analysis["summary"]["source_count"] == 2
    assert analysis["summary"]["requirement_conflict_count"] == 1
    assert analysis["summary"]["requirement_missing_count"] == 0
    assert analysis["summary"]["requirement_ambiguity_count"] == 0
    assert analysis["summary"]["resolved_conflict_count"] == 1
    assert analysis["summary"]["suppressed_without_evidence_count"] == 1
    assert analysis["summary"]["blocking_finding_count"] == 1
    assert analysis["readiness"]["schema"] == READINESS_SCHEMA
    assert analysis["readiness"]["status"] == "NOT_READY"
    assert analysis["readiness"]["ready"] is False

    finding = analysis["findings"][0]
    assert finding["finding_id"] == "requirement:conflict:business:1"
    assert finding["source_conflict_id"] == "conflict:business:1"
    assert finding["finding_type"] == "requirement_conflict"
    assert finding["title"] == "业务规则约束冲突"
    assert finding["source_ids"] == ["api:v2", "prd:v1"]
    assert [row["fact_id"] for row in finding["evidence"]] == [
        "fact:prd",
        "fact:api",
    ]
    assert finding["authority_decision"]["automatic_resolution_allowed"] is False


def test_requirement_conflict_projection_never_promotes_unsupported_conflict() -> None:
    asset = {
        "project_id": "project-a",
        "cross_document_conflicts": [
            {
                "conflict_id": "conflict:unsupported",
                "status": "UNRESOLVED",
                "reason": "no source evidence",
            }
        ],
    }

    analysis = analyze_knowledge_asset(asset)

    assert analysis["analysis_status"] == "READY"
    assert analysis["findings"] == []
    assert analysis["summary"]["suppressed_without_evidence_count"] == 1
    assert analysis["readiness"]["ready"] is True


def test_requirement_missing_projects_only_source_backed_business_lifecycle_gaps() -> None:
    analysis = analyze_knowledge_asset(_missing_asset())

    assert analysis["analysis_status"] == "NOT_READY"
    assert analysis["summary"]["requirement_missing_count"] == 1
    assert analysis["summary"]["resolved_missing_count"] == 1
    assert analysis["summary"]["suppressed_missing_without_evidence_count"] == 1

    finding = analysis["findings"][0]
    assert finding["finding_id"] == "requirement:understanding_unknown:to-state"
    assert finding["finding_type"] == "requirement_missing"
    assert finding["missing_kind"] == "LIFECYCLE_TO_STATE_UNKNOWN"
    assert finding["title"] == "生命周期目标状态定义缺失"
    assert finding["blocking"] is True
    assert finding["blocks_formal_understanding"] is True
    assert finding["source_ids"] == ["prd:order"]
    assert finding["automatic_inference_allowed"] is False

    projected_source_ids = {item.get("source_unknown_id") for item in analysis["findings"]}
    assert "understanding_unknown:technical" not in projected_source_ids
    assert "understanding_unknown:contradiction" not in projected_source_ids


def test_nonblocking_missing_requirement_requires_review_without_becoming_hard_blocker() -> None:
    asset = {
        "enterprise_understanding_model": {
            "unknowns": [
                {
                    "unknown_id": "understanding_unknown:from-state-review",
                    "kind": "LIFECYCLE_FROM_STATE_UNKNOWN",
                    "reason_code": "LIFECYCLE_FROM_STATE_UNKNOWN",
                    "question": "发货前的起始状态未定义。",
                    "blocks_formal_understanding": False,
                    "resolution_status": "UNRESOLVED",
                    "automatic_inference_allowed": False,
                    "evidence": [
                        _source_evidence(
                            "prd:ship",
                            "PRD.md#line=44",
                            "系统执行发货。",
                        )
                    ],
                }
            ]
        }
    }

    analysis = analyze_knowledge_asset(asset)

    assert analysis["analysis_status"] == "REVIEW_REQUIRED"
    assert analysis["readiness"]["blocking_finding_count"] == 0
    assert analysis["readiness"]["review_required_finding_count"] == 1
    assert analysis["readiness"]["ready"] is False
    assert analysis["findings"][0]["blocking"] is False


def test_requirement_ambiguity_projects_only_pending_evidence_backed_identity_review() -> None:
    analysis = analyze_knowledge_asset(_ambiguity_asset())

    assert analysis["analysis_status"] == "REVIEW_REQUIRED"
    assert analysis["summary"]["requirement_ambiguity_count"] == 1
    assert analysis["summary"]["inactive_ambiguity_count"] == 1
    assert analysis["summary"]["suppressed_ambiguity_without_evidence_count"] == 1
    assert analysis["readiness"]["blocking_finding_count"] == 0
    assert analysis["readiness"]["review_required_finding_count"] == 1

    finding = analysis["findings"][0]
    assert finding["finding_id"] == (
        "requirement:enterprise_identity_structural_review_task:1"
    )
    assert finding["finding_type"] == "requirement_ambiguity"
    assert finding["source_review_task_id"] == (
        "enterprise_identity_structural_review_task:1"
    )
    assert finding["review_status"] == "PENDING_REVIEW"
    assert finding["blocking"] is False
    assert finding["candidate_entity_ids"] == [
        "entity:customer",
        "entity:customer-profile",
    ]
    assert finding["source_ids"] == ["prd:current", "prd:legacy"]
    assert finding["automatic_resolution_allowed"] is False
    assert finding["automatic_entity_union_allowed"] is False


def test_requirement_readiness_is_deterministic_and_never_claims_completeness_or_recall() -> None:
    ready = analyze_knowledge_asset({"project_id": "empty"})
    review = analyze_knowledge_asset(_ambiguity_asset())
    blocked = analyze_knowledge_asset(_analysis_asset())

    assert ready["readiness"]["status"] == "READY"
    assert ready["readiness"]["ready"] is True
    assert ready["readiness"]["finding_count"] == 0

    assert review["readiness"]["status"] == "REVIEW_REQUIRED"
    assert review["readiness"]["ready"] is False

    assert blocked["readiness"]["status"] == "NOT_READY"
    assert blocked["readiness"]["ready"] is False
    assert blocked["readiness"]["quality_claim"] == (
        "DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL"
    )
    assert "score" not in blocked["readiness"]
    assert "completeness" not in blocked["readiness"]
    assert "recall" not in blocked["readiness"]


def test_product_catalog_mixin_precedes_legacy_http_router_in_composition_root() -> None:
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"), filename=str(SERVICE_PATH))
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivatePilotHandler"
    )
    base_names = [base.id for base in handler.bases if isinstance(base, ast.Name)]

    assert "ProductCatalogHttpMixin" in base_names
    assert "HttpRoutingMixin" in base_names
    assert base_names.index("ProductCatalogHttpMixin") < base_names.index("HttpRoutingMixin")


class _FallbackGet:
    fallback_called = False

    def do_GET(self) -> Any:  # noqa: N802
        self.fallback_called = True
        return "fallback"


class _CatalogHarness(ProductCatalogHttpMixin, _FallbackGet):
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

    def _load_merged_knowledge_asset(
        self,
        project: str,
        root: Path,
        actor: dict[str, str],
    ) -> dict[str, Any]:
        assert project == "project-a"
        assert root == REPO_ROOT
        assert actor["role"] == "viewer"
        return _analysis_asset()

    def _json(self, payload: dict[str, Any], status: int = 200, **_: Any) -> dict[str, Any]:
        self.response = (payload, status)
        return payload


def test_product_catalog_route_is_authenticated_and_returns_catalog() -> None:
    handler = _CatalogHarness(path="/api/v1/products")

    payload = handler.do_GET()

    assert handler.initialized is True
    assert handler.fallback_called is False
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    products = {item["product_id"]: item for item in payload["products"]}
    assert products["requirement_intelligence"]["status"] == "primary"
    assert products["bug_discovery"]["status"] == "experimental"


def test_requirement_intelligence_project_route_projects_existing_knowledge_findings() -> None:
    handler = _CatalogHarness(
        path="/api/v1/projects/project-a/requirement-intelligence"
    )

    payload = handler.do_GET()

    assert handler.initialized is True
    assert handler.fallback_called is False
    assert payload["ok"] is True
    assert payload["data"]["project_id"] == "project-a"
    assert payload["data"]["summary"]["requirement_conflict_count"] == 1
    assert payload["data"]["readiness"]["status"] == "NOT_READY"
    assert payload["data"]["findings"][0]["source_conflict_id"] == (
        "conflict:business:1"
    )


def test_product_catalog_route_rejects_unauthenticated_request() -> None:
    handler = _CatalogHarness(path="/api/v1/products", authenticated=False)

    assert handler.do_GET() is None
    assert handler.fallback_called is False
    assert handler.response is None


def test_product_catalog_mixin_delegates_unrelated_get_routes() -> None:
    handler = _CatalogHarness(path="/api/health")

    assert handler.do_GET() == "fallback"
    assert handler.fallback_called is True
