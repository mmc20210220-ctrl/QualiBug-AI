from __future__ import annotations

from ai_test_asset_center import behavior_ir as bir
from ai_test_asset_center.behavior_ir_surface_reconciliation import (
    reconcile_declared_observation_surfaces,
)


def _surface_map(model: dict) -> dict[str, dict]:
    return {
        row["surface"]: row
        for row in model.get("observation_surfaces", [])
        if isinstance(row, dict) and row.get("surface")
    }


def _capabilities(model: dict) -> set[str]:
    return {
        str(row.get("capability") or "")
        for row in model.get("capabilities", [])
        if isinstance(row, dict) and row.get("capability")
    }


def test_event_and_process_surfaces_survive_behavior_ir_reconciliation() -> None:
    model = bir.empty_behavior_ir(
        project_id="surface-project",
        source_snapshot_hash="surface-source",
    )
    # Reproduce the historical builder shape: only HTTP/UI/DB were materialized.
    for surface, available in (
        ("http_api", True),
        ("ui_browser", False),
        ("db_snapshot", False),
    ):
        model["observation_surfaces"].append(bir._fact_node(
            node_id=bir._stable_id("surface", surface),
            typed_fields={
                "surface": surface,
                "label": surface,
                "available": available,
                "availability_basis": "builder_default",
            },
            confidence=1.0 if available else 0.3,
            derivation="schema-derived",
            status="accepted" if available else "unknown",
        ))
    model["capabilities"].append(bir._fact_node(
        node_id=bir._stable_id("cap", "http_execute"),
        typed_fields={"capability": "http_execute", "adapter": "http_api"},
        confidence=1.0,
        derivation="schema-derived",
        status="accepted",
    ))
    model["model_id"] = bir._content_addressed_id(model)

    reconciled, receipt = reconcile_declared_observation_surfaces(
        model,
        {
            "http_api": True,
            "ui_browser": False,
            "db_snapshot": False,
            "process_timeline": True,
            "event_stream": True,
        },
    )

    surfaces = _surface_map(reconciled)
    assert set(surfaces) == {
        "http_api",
        "ui_browser",
        "db_snapshot",
        "process_timeline",
        "event_stream",
    }
    assert surfaces["event_stream"]["available"] is True
    assert surfaces["process_timeline"]["available"] is True
    assert surfaces["ui_browser"]["available"] is False
    assert all(
        row["availability_basis"] == "declared_adapter_capability"
        for row in surfaces.values()
    )

    capabilities = _capabilities(reconciled)
    assert "http_execute" in capabilities
    assert "process_timeline_observe" in capabilities
    assert "event_stream_read" in capabilities
    assert "ui_execute" not in capabilities
    assert "db_read" not in capabilities

    assert receipt["status"] == "RECONCILED"
    assert receipt["surface_added_count"] == 2
    assert receipt["surface_updated_count"] == 3
    assert receipt["capability_added_count"] == 2
    assert bir.validate_behavior_ir(
        reconciled,
        require_explicit_relations=True,
    ) == []


def test_future_declared_surface_is_visible_without_inventing_a_capability() -> None:
    model = bir.empty_behavior_ir(
        project_id="future-surface-project",
        source_snapshot_hash="future-source",
    )

    reconciled, receipt = reconcile_declared_observation_surfaces(
        model,
        {"future_observer_surface": True},
    )

    surfaces = _surface_map(reconciled)
    assert surfaces["future_observer_surface"]["available"] is True
    assert surfaces["future_observer_surface"]["label"].startswith(
        "Declared observation surface:"
    )
    assert _capabilities(reconciled) == set()
    assert receipt["capability_added_count"] == 0
    assert bir.validate_behavior_ir(
        reconciled,
        require_explicit_relations=True,
    ) == []


def test_missing_declaration_map_does_not_widen_surfaces() -> None:
    model = bir.empty_behavior_ir(
        project_id="no-surface-project",
        source_snapshot_hash="no-surface-source",
    )

    reconciled, receipt = reconcile_declared_observation_surfaces(model, None)

    assert reconciled["observation_surfaces"] == []
    assert reconciled["capabilities"] == []
    assert receipt["status"] == "NOT_REQUESTED"
