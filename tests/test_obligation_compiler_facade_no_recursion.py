from __future__ import annotations

from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


def test_behavior_ir_v2_facade_delegates_once_without_recursive_rebinding() -> None:
    ir = {
        "schema_version": "qualibug.behavior-ir.v2",
        "project_id": "facade-no-recursion",
        "source_snapshot_hash": "snapshot",
        "sources": [],
        "entities": [],
        "operations": [],
        "actors": [],
        "states": [],
        "invariants": [],
        "relations": [],
        "ui_surfaces": [],
        "observation_surfaces": [],
        "capabilities": [],
        "conflicts": [],
        "coverage_gaps": [],
    }
    result = compile_obligations_from_behavior_ir(ir)
    assert isinstance(result, dict)
    assert ir["ui_specs"] == []
