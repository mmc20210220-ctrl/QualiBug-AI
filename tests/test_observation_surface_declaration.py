"""The IR's observation surfaces must reflect declared capability, not a literal.

``build_behavior_ir_from_knowledge_asset`` set surface availability with

    "available": surface_id == "http_api"

so ``db_snapshot`` and ``ui_browser`` were unavailable unconditionally. Meanwhile
``adapter_capability.resolve_available_adapters`` was already returning ``db_sql`` for
any project whose config declares ``services[].db`` -- and feeding it to the experiment
compiler. Two parts of one run disagreed about the same capability.

The IR is the copy the observer gate reads. Measured on a live target whose Postgres
was configured, reachable and queried directly during the same session,
``BLOCKED_MISSING_OBSERVER`` was the largest terminal reason at 463 of 1218
obligations -- every assertion needing a data-layer read, which is most of the
conservation and state-integrity families.

The fix keeps the failure direction. ``available_surfaces`` is a declaration passed in
by the caller and never inferred inside the IR builder, and omitting it reproduces the
old http-only behaviour exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.adapter_capability import (
    ADAPTER_TO_CAPABILITY,
    ADAPTER_TO_OBSERVATION_SURFACE,
    capabilities_for_adapters,
    observation_surfaces_for_adapters,
    resolve_available_adapters,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset


def _surfaces(model: dict) -> dict[str, bool]:
    return {
        str(node.get("surface")): bool(node.get("available"))
        for node in model.get("observation_surfaces") or []
    }


def _capabilities(model: dict) -> set[str]:
    return {str(node.get("capability")) for node in model.get("capabilities") or []}


# ── the mapping ─────────────────────────────────────────────────────────────

def test_every_known_surface_is_reported_even_when_unavailable() -> None:
    """An absent key reads as "not considered"; False reads as "considered and no"."""
    surfaces = observation_surfaces_for_adapters({"http_api"})
    assert set(surfaces) == set(ADAPTER_TO_OBSERVATION_SURFACE.values())
    assert surfaces["http_api"] is True
    assert surfaces["db_snapshot"] is False


def test_declared_database_adapter_enables_the_db_surface() -> None:
    surfaces = observation_surfaces_for_adapters({"http_api", "db_sql"})
    assert surfaces["db_snapshot"] is True
    assert surfaces["ui_browser"] is False


def test_no_adapters_enables_nothing() -> None:
    assert not any(observation_surfaces_for_adapters(None).values())
    assert not any(observation_surfaces_for_adapters(set()).values())


def test_capabilities_track_the_same_adapters() -> None:
    assert capabilities_for_adapters({"http_api"}) == ["http_execute"]
    assert capabilities_for_adapters({"http_api", "db_sql"}) == ["db_read", "http_execute"]
    for adapter in ADAPTER_TO_OBSERVATION_SURFACE:
        assert adapter in ADAPTER_TO_CAPABILITY, adapter


# ── the IR honours the declaration ──────────────────────────────────────────

def test_omitting_the_declaration_reproduces_the_old_behaviour() -> None:
    """The failure direction must stay "fewer surfaces".

    Any caller that has not been updated must keep getting http-only, never a
    surface it did not declare.
    """
    model = build_behavior_ir_from_knowledge_asset({"sources": []}, project_id="p")
    assert _surfaces(model) == {"http_api": True, "ui_browser": False, "db_snapshot": False}
    assert _capabilities(model) == {"http_execute"}


def test_declared_surfaces_reach_the_ir() -> None:
    model = build_behavior_ir_from_knowledge_asset(
        {"sources": []},
        project_id="p",
        available_surfaces=observation_surfaces_for_adapters({"http_api", "db_sql"}),
    )
    assert _surfaces(model)["db_snapshot"] is True
    assert "db_read" in _capabilities(model)


def test_a_declaration_can_also_withhold_http() -> None:
    """A declaration is authoritative in both directions.

    If the caller says http is not available, the IR must not re-add it from the old
    hardcoded assumption.
    """
    model = build_behavior_ir_from_knowledge_asset(
        {"sources": []},
        project_id="p",
        available_surfaces={"http_api": False, "db_snapshot": True, "ui_browser": False},
    )
    assert _surfaces(model)["http_api"] is False
    assert _capabilities(model) == {"db_read"}


def test_declared_surfaces_are_marked_as_declared_not_schema_derived() -> None:
    """A reader must be able to tell a declaration from a default."""
    model = build_behavior_ir_from_knowledge_asset(
        {"sources": []},
        project_id="p",
        available_surfaces=observation_surfaces_for_adapters({"http_api", "db_sql"}),
    )
    basis = {
        str(node.get("surface")): str(node.get("availability_basis"))
        for node in model["observation_surfaces"]
    }
    assert basis["db_snapshot"] == "declared_adapter_capability"
    # derivation is a closed vocabulary; an operator declaration is "explicit".
    assert {str(n.get("derivation")) for n in model["observation_surfaces"]} == {"explicit"}

    default_model = build_behavior_ir_from_knowledge_asset({"sources": []}, project_id="p")
    assert default_model["observation_surfaces"][0]["derivation"] == "schema-derived"
    assert default_model["observation_surfaces"][0]["availability_basis"] == "builder_default"


# ── end to end from a project config ────────────────────────────────────────

def test_a_project_declaring_a_database_gets_the_db_surface(tmp_path: Path) -> None:
    """The whole chain: config file -> adapters -> surfaces -> IR."""
    config = tmp_path / "platform_workspace" / "proj" / "multi_service_config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"services": [{
            "name": "gateway",
            "base_url": "http://localhost:8080",
            "db": {"host": "localhost", "port": 5432, "name": "app", "user": "u"},
        }]}),
        encoding="utf-8",
    )
    adapters = resolve_available_adapters(tmp_path, "proj", {})
    assert "db_sql" in adapters

    model = build_behavior_ir_from_knowledge_asset(
        {"sources": []},
        project_id="proj",
        available_surfaces=observation_surfaces_for_adapters(adapters),
    )
    assert _surfaces(model)["db_snapshot"] is True


def test_a_project_without_a_database_does_not_get_the_db_surface(tmp_path: Path) -> None:
    config = tmp_path / "platform_workspace" / "proj" / "multi_service_config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"services": [{"name": "gateway", "base_url": "http://x"}]}),
        encoding="utf-8",
    )
    adapters = resolve_available_adapters(tmp_path, "proj", {})
    assert "db_sql" not in adapters
    model = build_behavior_ir_from_knowledge_asset(
        {"sources": []},
        project_id="proj",
        available_surfaces=observation_surfaces_for_adapters(adapters),
    )
    assert _surfaces(model)["db_snapshot"] is False


# ── one resolution point ────────────────────────────────────────────────────

def test_the_planner_resolves_adapters_once_and_reuses_the_result() -> None:
    """Two computations of the same declaration can drift.

    This pair drifting is exactly what let the IR and the compiler disagree, so the
    planner must resolve once, before the IR is built, and reuse the value.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "discovery_runtime_planning.py"
    ).read_text(encoding="utf-8")

    assert source.count("_available_adapters = resolve_available_adapters(") == 1
    resolve_at = source.index("_available_adapters = resolve_available_adapters(")
    ir_build_at = source.index("build_behavior_ir_from_knowledge_asset(")
    compile_at = source.index("available_adapters=_available_adapters")
    assert resolve_at < ir_build_at, "adapters must be resolved before the IR is built"
    assert ir_build_at < compile_at
    assert "available_surfaces=observation_surfaces_for_adapters(_available_adapters)" in source
