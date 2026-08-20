from __future__ import annotations

import json

from ai_test_asset_center.service_topology_config_guard import (
    BLOCKED_SERVICE_TOPOLOGY_INVALID,
    load_guarded_project_service_topology,
)


def _write_config(tmp_path, project: str, payload: dict) -> None:
    path = tmp_path / "platform_inputs" / project / "real_project_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_absent_multi_service_keeps_single_target_mode(tmp_path) -> None:
    project = "single-target"
    _write_config(
        tmp_path,
        project,
        {
            "base_url": "http://127.0.0.1:39001",
            "environment_type": "test",
        },
    )
    topology, receipt = load_guarded_project_service_topology(project, tmp_path)
    assert topology == {}
    assert receipt["status"] == "NOT_APPLICABLE"


def test_declared_services_must_be_object(tmp_path) -> None:
    project = "invalid-services-shape"
    _write_config(
        tmp_path,
        project,
        {
            "base_url": "http://127.0.0.1:39001",
            "multi_service": {"enabled": True, "services": ["alpha", "beta"]},
        },
    )
    topology, receipt = load_guarded_project_service_topology(project, tmp_path)
    assert topology == {}
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == BLOCKED_SERVICE_TOPOLOGY_INVALID
    assert receipt["detail"] == "multi_service_services_must_be_object"


def test_invalid_service_url_is_not_silently_dropped(tmp_path) -> None:
    project = "invalid-service-url"
    _write_config(
        tmp_path,
        project,
        {
            "base_url": "http://127.0.0.1:39001",
            "multi_service": {
                "enabled": True,
                "services": {
                    "alpha": "http://127.0.0.1:39117",
                    "beta": "not-a-url",
                },
            },
        },
    )
    topology, receipt = load_guarded_project_service_topology(project, tmp_path)
    assert topology == {}
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == BLOCKED_SERVICE_TOPOLOGY_INVALID
    assert "service_url_invalid:beta" in receipt["detail"]


def test_valid_declared_topology_preserves_arbitrary_ports(tmp_path) -> None:
    project = "valid-multi-service"
    _write_config(
        tmp_path,
        project,
        {
            "base_url": "http://127.0.0.1:39001",
            "multi_service": {
                "enabled": True,
                "services": {
                    "alpha": "http://127.0.0.1:39117",
                    "beta": {"approved_base_url": "http://127.0.0.1:48763/api"},
                },
            },
        },
    )
    topology, receipt = load_guarded_project_service_topology(project, tmp_path)
    assert receipt["status"] == "VALID"
    assert receipt["service_refs"] == ["alpha", "beta"]
    assert topology["alpha"]["approved_base_url"] == "http://127.0.0.1:39117"
    assert topology["beta"]["approved_base_url"] == "http://127.0.0.1:48763/api"
