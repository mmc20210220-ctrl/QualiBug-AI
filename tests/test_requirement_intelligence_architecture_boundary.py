from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ai_test_asset_center.private_pilot_product_catalog import ProductCatalogHttpMixin
from products.catalog import get_product_catalog
from products.requirement_intelligence import analyze_knowledge_asset, get_product_manifest


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
                        "source_id": "prd:v1",
                        "source_locator": "PRD.md#line=20",
                        "quote": "已支付订单不得取消",
                        "quote_hash": "hash:prd",
                        "fact_id": "fact:prd",
                    },
                    {
                        "source_id": "api:v2",
                        "source_locator": "openapi.yaml#/orders/cancel",
                        "quote": "PAID may cancel",
                        "quote_hash": "hash:api",
                        "fact_id": "fact:api",
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
                    {
                        "source_id": "state:v1",
                        "source_locator": "state.md#line=8",
                        "quote": "PAID -> CANCELLED",
                    }
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


def test_requirement_intelligence_manifest_is_bounded_and_evidence_required() -> None:
    manifest = get_product_manifest()

    assert manifest["product_id"] == "requirement_intelligence"
    assert manifest["status"] == "primary"
    assert manifest["evidence_required"] is True
    assert set(manifest["supported_findings"]) == {
        "requirement_conflict",
        "requirement_missing",
        "requirement_ambiguity",
    }
    assert tuple(manifest["implemented_findings"]) == ("requirement_conflict",)


def test_product_catalog_demotes_bug_discovery_without_importing_runtime() -> None:
    catalog = {item["product_id"]: item for item in get_product_catalog()}

    assert catalog["requirement_intelligence"]["status"] == "primary"
    assert catalog["requirement_intelligence"]["entry_mode"] == "analysis"
    assert catalog["requirement_intelligence"]["implemented_findings"] == (
        "requirement_conflict",
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

    assert analysis["analysis_status"] == "BLOCKED_BY_REQUIREMENT_CONFLICTS"
    assert analysis["summary"] == {
        "source_count": 2,
        "requirement_conflict_count": 1,
        "resolved_conflict_count": 1,
        "suppressed_without_evidence_count": 1,
        "blocking_finding_count": 1,
        "implemented_finding_types": ["requirement_conflict"],
    }
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

    assert analysis["analysis_status"] == "READY_FOR_REVIEW"
    assert analysis["findings"] == []
    assert analysis["summary"]["suppressed_without_evidence_count"] == 1


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


def test_requirement_intelligence_project_route_projects_existing_knowledge_conflicts() -> None:
    handler = _CatalogHarness(
        path="/api/v1/projects/project-a/requirement-intelligence"
    )

    payload = handler.do_GET()

    assert handler.initialized is True
    assert handler.fallback_called is False
    assert payload["ok"] is True
    assert payload["data"]["project_id"] == "project-a"
    assert payload["data"]["summary"]["requirement_conflict_count"] == 1
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
