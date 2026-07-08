from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.real_project_onboarding import load_real_project_config


def test_load_real_project_config_falls_back_to_connector_registry(tmp_path: Path) -> None:
    project = "benchmark_like_project"
    connector_path = tmp_path / "platform_workspace" / project / "enterprise_pilot_runtime" / "connector_registry.json"
    connector_path.parent.mkdir(parents=True, exist_ok=True)
    connector_path.write_text(
        json.dumps(
            {
                "project_id": project,
                "connectors": [
                    {
                        "connector_id": "gateway",
                        "enabled": True,
                        "endpoint_ref": "http://127.0.0.1:8080",
                    }
                ],
                "test_profile": {
                    "api_base_url": "http://127.0.0.1:8080",
                    "ui_base_url": "http://localhost:3001",
                    "scope_id": "benchmark-scope",
                    "environment_ref": "local-benchmark",
                    "test_credentials": {
                        "buyer": {
                            "email": "buyer01@example.com",
                            "password": "Test@123456",
                        }
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    cfg = load_real_project_config(project, tmp_path)

    assert cfg["project_id"] == project
    assert cfg["base_url"] == "http://127.0.0.1:8080"
    assert cfg["ui_base_url"] == "http://localhost:3001"
    assert cfg["deployment_scope_id"] == "benchmark-scope"
    assert cfg["environment_ref"] == "local-benchmark"
    assert cfg["test_credentials"]["buyer"]["email"] == "buyer01@example.com"
