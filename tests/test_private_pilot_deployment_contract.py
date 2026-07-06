from __future__ import annotations

from pathlib import Path


def test_product_version_matches_pyproject() -> None:
    from ai_test_asset_center.version import PRODUCT_VERSION

    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{PRODUCT_VERSION}"' in pyproject


def test_health_payload_uses_product_version_and_canonical_port(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center.private_pilot_entrypoint import _health_payload
    from ai_test_asset_center.version import CANONICAL_HEALTH_PATH, DEFAULT_PRIVATE_PILOT_PORT, PRODUCT_VERSION

    monkeypatch.delenv("QUALIBUG_PORT", raising=False)

    class DummyHandler:
        def _root(self) -> Path:
            return tmp_path

        def _llm_health(self) -> dict:
            return {"available": True, "status": "online", "label": "online"}

    payload = _health_payload(DummyHandler())

    assert payload["version"] == PRODUCT_VERSION
    assert payload["product_version"] == PRODUCT_VERSION
    assert payload["port"] == DEFAULT_PRIVATE_PILOT_PORT
    assert payload["canonical_health_path"] == CANONICAL_HEALTH_PATH
    assert payload["deployment_contract_patch"]["health_contract"] == CANONICAL_HEALTH_PATH
    assert payload["llm_available"] is True


def test_health_contract_and_deployment_patch_are_extracted_from_entrypoint() -> None:
    entrypoint = Path("ai_test_asset_center/private_pilot_entrypoint.py").read_text(encoding="utf-8")
    health_module = Path("ai_test_asset_center/private_pilot_health_contract.py").read_text(encoding="utf-8")
    deployment_patch = Path("ai_test_asset_center/private_pilot_deployment_patch.py").read_text(encoding="utf-8")

    assert "from ai_test_asset_center.private_pilot_health_contract import build_private_pilot_health_payload" not in entrypoint
    assert "from ai_test_asset_center.private_pilot_deployment_patch import" in entrypoint
    assert "def build_private_pilot_health_payload" in health_module
    assert "def install_deployment_contract_patch" in deployment_patch
    assert "urlparse" not in entrypoint
    assert "def _do_get_with_deployment_contract" not in entrypoint
    assert "def _pattern_library_count" not in entrypoint
    assert "def _browser_ui_status" not in entrypoint


def test_qualibug_server_script_uses_patched_entrypoint() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'qualibug-server = "ai_test_asset_center.private_pilot_entrypoint:run_server"' in pyproject


def test_docker_compose_maps_host_5000_to_container_8088() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "qualibug-ai:95.0.0-private-pilot" in compose
    assert '"5000:8088"' in compose
    assert 'QUALIBUG_PORT: "8088"' in compose
    assert "http://localhost:8088/api/health" in compose


def test_dockerfile_runs_patched_entrypoint_on_8088() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "ENV QUALIBUG_PORT=8088" in dockerfile
    assert "EXPOSE 8088" in dockerfile
    assert "http://localhost:8088/api/health" in dockerfile
    assert 'CMD ["python", "-m", "ai_test_asset_center.private_pilot_entrypoint"]' in dockerfile
