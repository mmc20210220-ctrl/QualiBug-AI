from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.db_verifier import DBVerifier, MESDBVerifier, _resolve_default_db_path
from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
from ai_test_asset_center.runtime_verifier import MESRuntimeVerifier, RuntimeVerifier
from ai_test_asset_center.self_improving_loop import _resolve_default_project_doc_path


def test_self_improving_loop_resolves_project_input_docs_without_mes_defaults(tmp_path: Path) -> None:
    input_dir = tmp_path / "platform_workspace" / "benchmark_any" / "input"
    input_dir.mkdir(parents=True)
    expected = input_dir / "API_SPEC.md"
    expected.write_text("# api", encoding="utf-8")

    resolved = _resolve_default_project_doc_path(
        "benchmark_any",
        ["openapi.json", "API_SPEC.md"],
        search_root=tmp_path,
    )

    assert Path(resolved) == expected


def test_runtime_verifier_allows_externalized_role_tokens(monkeypatch) -> None:
    monkeypatch.setenv("QUALIBUG_RUNTIME_TOKENS", '{"admin":"Bearer real-admin","auditor":"Bearer real-auditor"}')

    verifier = RuntimeVerifier(base_url="https://example.test")

    assert verifier.base_url == "https://example.test"
    assert verifier.tokens["admin"] == "Bearer real-admin"
    assert verifier.tokens["auditor"] == "Bearer real-auditor"
    assert MESRuntimeVerifier is RuntimeVerifier


def test_db_verifier_resolves_project_workspace_database(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "platform_workspace" / "benchmark_any" / "data"
    db_dir.mkdir(parents=True)
    expected = db_dir / "app.db"
    expected.write_text("", encoding="utf-8")
    monkeypatch.setattr("ai_test_asset_center.db_verifier.REPO_ROOT", tmp_path)

    resolved = _resolve_default_db_path("benchmark_any")
    verifier = DBVerifier(project_id="benchmark_any")

    assert Path(resolved) == expected
    assert Path(verifier.db_path) == expected
    assert MESDBVerifier is DBVerifier


def test_discovery_engine_resolve_call_matches_prefixed_routes_generically() -> None:
    engine = AutonomousDiscoveryEngine.__new__(AutonomousDiscoveryEngine)
    route_map = {
        "GET /svc/v1/orders/{orderId}": {
            "method": "GET",
            "path_pattern": "/svc/v1/orders/{orderId}",
        }
    }

    resolved = engine._resolve_call("/orders/{id}", "GET", route_map)

    assert resolved is not None
    assert resolved["path_pattern"] == "/svc/v1/orders/{orderId}"
