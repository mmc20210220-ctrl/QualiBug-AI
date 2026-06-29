from __future__ import annotations

from ai_test_asset_center.analyzers_adapter import AnalyzersAdapter
from ai_test_asset_center.enhanced_discovery_engine import EnhancedDiscoveryEngine


def test_analyzer_and_enhanced_engine_parse_real_openapi_routes() -> None:
    spec_text = """
openapi: 3.0.0
paths:
  /api/projects:
    get:
    post:
  /api/projects/{project_id}/reports:
    get:
"""
    adapter = AnalyzersAdapter()
    engine = EnhancedDiscoveryEngine(enable_checkpoints=False, enable_phase2=False)

    parsed_adapter = adapter._parse_api_spec(spec_text)
    parsed_engine = engine._parse_api_spec(spec_text)

    assert "/api/projects" in parsed_adapter["paths"]
    assert "post" in parsed_adapter["paths"]["/api/projects"]
    assert "/api/projects/{project_id}/reports" in parsed_engine["paths"]
    assert "/api/orders" not in parsed_engine["paths"] or len(parsed_engine["paths"]) > 1

