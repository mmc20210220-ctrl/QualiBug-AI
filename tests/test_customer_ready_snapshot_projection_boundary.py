from __future__ import annotations

import json

import ai_test_asset_center.__main__ as main_module


def test_persist_customer_ready_snapshot_preserves_discovery_top_level_and_writes_family_shelf(tmp_path, monkeypatch) -> None:
    project = "enterprise-project"
    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan_result_path.parent.mkdir(parents=True, exist_ok=True)
    scan_result_path.write_text(json.dumps({"project": project}, ensure_ascii=False), encoding="utf-8")

    real_project_path = tmp_path / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    real_project_path.parent.mkdir(parents=True, exist_ok=True)
    real_project_path.write_text(
        json.dumps(
            {
                "project_id": project,
                "metrics": {"validated_bug_count": 3},
                "summary": {"validated_bug_count": 3},
                "defects": [{"id": "DISC-001", "title": "original discovery fact"}],
                "clues": [{"id": "DISC-CLUE-001", "title": "original discovery clue"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    snapshot = {
        "project": project,
        "generated_at_utc": "2026-07-08T08:00:00Z",
        "defects": [{"id": "SHELF-001", "title": "customer-ready shelf defect"}],
        "clues": [{"id": "SHELF-CLUE-001", "title": "customer-ready shelf clue"}],
        "risks": [{"id": "SHELF-001", "title": "customer-ready shelf defect"}],
        "value_metrics": {"ready_bug_count": 1},
        "executive_summary": {"ready_bugs": 1},
        "scan_meta": {"ready_bug_count": 1},
        "data_contract": {"display_key": "defects"},
        "commercial_assets": {
            "status": "materialized",
            "delivery_package": {"status": "created"},
        },
    }
    monkeypatch.setattr(main_module, "_customer_ready_static_snapshot", lambda project_id, root: dict(snapshot))

    main_module._persist_customer_ready_static_artifacts(project, tmp_path, {"project": project})

    saved_real_project = json.loads(real_project_path.read_text(encoding="utf-8"))

    assert saved_real_project["defects"][0]["id"] == "DISC-001"
    assert saved_real_project["clues"][0]["id"] == "DISC-CLUE-001"
    assert saved_real_project["customer_ready_snapshot"]["defects"][0]["id"] == "SHELF-001"
    assert saved_real_project["customer_ready_family_shelf"]["defects"][0]["id"] == "SHELF-001"
    assert saved_real_project["customer_ready_family_shelf"]["clues"][0]["id"] == "SHELF-CLUE-001"
    assert saved_real_project["customer_ready_commercial_assets"]["delivery_package"]["status"] == "created"
