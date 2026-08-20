from __future__ import annotations

from pathlib import Path

import ai_test_asset_center._experiment_batch_executor_single_finding_mechanics as batch
import ai_test_asset_center.discovery_runtime_planning as planning
import ai_test_asset_center.service_topology_config_guard as topology_guard


def _fake_batch_observes_legacy_filter(seen: dict[str, str]):
    def _run(*args, **kwargs):
        seen["service"] = planning._target_service_name_from_base_url(
            kwargs.get("base_url") or ""
        )
        return {"schema_version": "test.batch.v1", "results": []}

    return _run


def test_declared_topology_disables_legacy_fixed_port_filter(monkeypatch) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        topology_guard,
        "load_guarded_project_service_topology",
        lambda project, root: (
            {
                "alpha": {"approved_base_url": "http://127.0.0.1:8111"},
                "beta": {"approved_base_url": "http://127.0.0.1:8120"},
            },
            {"status": "VALID"},
        ),
    )
    monkeypatch.setattr(
        batch,
        "_original_execute_selected_experiments",
        _fake_batch_observes_legacy_filter(seen),
    )

    batch.execute_selected_experiments(
        [],
        project="demo",
        root=Path("."),
        base_url="http://127.0.0.1:8111",
    )

    assert seen["service"] == ""


def test_no_topology_preserves_legacy_single_target_filter(monkeypatch) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        topology_guard,
        "load_guarded_project_service_topology",
        lambda project, root: ({}, {"status": "NOT_APPLICABLE"}),
    )
    monkeypatch.setattr(
        batch,
        "_original_execute_selected_experiments",
        _fake_batch_observes_legacy_filter(seen),
    )

    batch.execute_selected_experiments(
        [],
        project="demo",
        root=Path("."),
        base_url="http://127.0.0.1:8111",
    )

    assert seen["service"] == "scm_trade"


def test_invalid_declared_topology_reaches_fail_closed_router(monkeypatch) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        topology_guard,
        "load_guarded_project_service_topology",
        lambda project, root: (
            {},
            {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_SERVICE_TOPOLOGY_INVALID",
            },
        ),
    )
    monkeypatch.setattr(
        batch,
        "_original_execute_selected_experiments",
        _fake_batch_observes_legacy_filter(seen),
    )

    batch.execute_selected_experiments(
        [],
        project="demo",
        root=Path("."),
        base_url="http://127.0.0.1:8111",
    )

    # Invalid topology must not be silently converted into a single-service
    # skip. The public per-experiment router owns the explicit BLOCKED receipt.
    assert seen["service"] == ""
