import json
from pathlib import Path

from ai_test_asset_center.behavior_learning_memory import (
    apply_learning_to_probe_candidates,
    build_behavior_learning_memory,
    persist_behavior_learning_memory,
)


def test_behavior_learning_memory_extracts_confirmed_defect_signals(tmp_path: Path) -> None:
    project = "demo"
    ws = tmp_path / "platform_workspace" / project / "defect_discovery"
    ws.mkdir(parents=True)
    (ws / "confirmed_findings.json").write_text(json.dumps({
        "ev1": {
            "title": "普通用户跨租户读取订单接口返回 HTTP 200",
            "severity": "P1",
            "risk_type": "tenant_isolation",
            "repro_path": "/api/orders/1",
            "reproduction": {"method": "GET", "path": "/api/orders/1"},
        }
    }, ensure_ascii=False), encoding="utf-8")
    chain_dir = ws / "evidence_chains"
    chain_dir.mkdir()
    (chain_dir / "ev1.json").write_text(json.dumps({
        "layers": [{"raw_probe": {"request": "GET /api/orders/1", "response": "HTTP 200"}}]
    }, ensure_ascii=False), encoding="utf-8")

    memory = build_behavior_learning_memory(project, tmp_path)

    assert memory["version"] == "behavior_learning_memory.v1"
    assert memory["summary"]["signal_count"] == 1
    assert memory["summary"]["top_dimensions"]["tenant"] > 0
    assert memory["summary"]["top_dimensions"]["authorization"] > 0
    assert memory["summary"]["top_surfaces"]["api"] > 0
    assert memory["signals"][0]["entity"] == "orders"


def test_behavior_learning_memory_persists_and_boosts_future_probes(tmp_path: Path) -> None:
    project = "demo"
    ws = tmp_path / "platform_workspace" / project / "defect_discovery"
    ws.mkdir(parents=True)
    (ws / "confirmed_findings.json").write_text(json.dumps({
        "ev1": {"title": "金额退款重复提交导致余额异常", "severity": "P0", "risk_type": "money", "repro_path": "/api/refunds"}
    }, ensure_ascii=False), encoding="utf-8")

    memory = persist_behavior_learning_memory(project, tmp_path)
    assert (tmp_path / "platform_workspace" / project / "defect_discovery" / "behavior_learning_memory.json").exists()
    assert (tmp_path / "platform_outputs" / project / "behavior_learning_memory.json").exists()

    space = {
        "summary": {},
        "probe_candidates": [
            {
                "probe_id": "p1",
                "entity": "refunds",
                "priority": 0.5,
                "surface_plan": ["api", "db"],
                "oracle_intent": ["promise_violation:money", "promise_violation:idempotency"],
            },
            {
                "probe_id": "p2",
                "entity": "profile",
                "priority": 0.5,
                "surface_plan": ["ui"],
                "oracle_intent": ["promise_violation:visibility"],
            },
        ],
    }
    boosted = apply_learning_to_probe_candidates(space, memory)

    assert boosted["summary"]["learning_signal_count"] == 1
    assert boosted["summary"]["learning_boosted_probe_count"] >= 1
    assert boosted["probe_candidates"][0]["probe_id"] == "p1"
    assert boosted["probe_candidates"][0]["priority"] > 0.5
    assert boosted["probe_candidates"][0]["learning_memory_version"] == "behavior_learning_memory.v1"
