from pathlib import Path

import pytest

from ai_test_asset_center.reasoner_graph_context import build_reasoner_graph_context


def test_empty_persisted_business_model_cannot_activate_graph_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUALIBUG_GRAPH_CONTEXT_MODE", "active")

    result = build_reasoner_graph_context(
        behavior_ir={},
        project_id="empty-graph",
        environment_id="staging",
        root=tmp_path,
        source_ref="asset-empty",
    )

    assert result["status"] == "EMPTY"
    assert result["pack"] == {}
    assert result["stats"] == {}
    assert result["reason"] == "persisted_business_graph_input_empty"
    assert not (
        tmp_path
        / "platform_workspace"
        / "empty-graph"
        / "cognitive_memory_graph.sqlite3"
    ).exists()
