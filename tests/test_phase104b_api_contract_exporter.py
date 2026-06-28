from __future__ import annotations

import json

from ai_test_asset_center.phase104_api_contract_exporter import (
    build_openapi_spec,
    export_api_contract,
    render_contract_markdown,
    render_frontend_api_client,
    route_contracts,
)


def test_phase104b_openapi_contract_contains_v1_routes_and_schemas() -> None:
    spec = build_openapi_spec()
    paths = spec["paths"]

    assert spec["openapi"] == "3.0.3"
    assert "/api/v1/projects" in paths
    assert paths["/api/v1/projects"]["post"]["operationId"] == "createProject"
    assert "/api/v1/projects/{project_id}/command-center" in paths
    assert "/api/v1/projects/{project_id}/risks/{risk_id}" in paths
    assert "ProjectCreateRequest" in spec["components"]["schemas"]
    assert "EnvironmentPreflightRequest" in spec["components"]["schemas"]
    assert len(route_contracts()) >= 20

    serialized = json.dumps(spec, ensure_ascii=False)
    assert "raw-token" not in serialized
    assert "SESSION=raw" not in serialized
    assert "client_secret=" not in serialized


def test_phase104b_exports_contract_artifacts(tmp_path) -> None:
    manifest = export_api_contract(tmp_path)

    openapi_path = tmp_path / "openapi.json"
    md_path = tmp_path / "API_CONTRACT.md"
    client_path = tmp_path / "frontend_api_client.ts"
    manifest_path = tmp_path / "contract_manifest.json"

    assert openapi_path.exists()
    assert md_path.exists()
    assert client_path.exists()
    assert manifest_path.exists()
    assert manifest["route_count"] >= 20
    assert manifest["redaction_status"] == "safe"

    openapi = json.loads(openapi_path.read_text(encoding="utf-8"))
    assert openapi["paths"]["/api/v1/projects/{project_id}/reports/generate"]["post"]["operationId"] == "generateExecutiveReport"
    assert "QualiBugCommandCenterClient" in client_path.read_text(encoding="utf-8")
    assert "前端集成顺序" in md_path.read_text(encoding="utf-8")

    combined = "\n".join(path.read_text(encoding="utf-8") for path in [openapi_path, md_path, client_path, manifest_path])
    assert "raw-password" not in combined
    assert "Bearer raw" not in combined


def test_phase104b_markdown_and_frontend_client_are_useful() -> None:
    markdown = render_contract_markdown()
    client = render_frontend_api_client()

    assert "POST /api/v1/projects" in markdown
    assert "GET /command-center" in markdown
    assert "generateTestPlan" in client
    assert "getCommandCenter" in client
    assert "getRiskDetail" in client
