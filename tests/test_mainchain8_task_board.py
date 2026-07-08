"""主链 8 回归测试：测试任务看板数据从 v12 报告准确透传（前端零变换渲染）。"""
import ast
import os
import sys
from pathlib import Path

import pytest

# private_pilot_service 在导入期校验 JWT 密钥，单测需预置（仅开发占位值）
os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_test_asset_center.private_pilot_service import PrivatePilotHandler  # noqa: E402

_build_test_task_board = PrivatePilotHandler._build_test_task_board  # noqa: E402


def test_board_empty_when_no_task_data():
    """未生成行为路径计划时无任务数据 → 返回 None（前端显示空态）。"""
    assert _build_test_task_board({}) is None
    assert _build_test_task_board({"findings": [], "phases": {}}) is None


def test_board_surfaces_ledger_and_slices_with_status():
    """Fix: ledger + slices（含主链 4 的 status）原样透传。"""
    report = {
        "behavior_slice_ledger": {
            "campaign_id": "c1",
            "campaign_status": "running",
            "attempted_slice_ids": ["BHV_1", "BHV_2"],
            "confirmed_slice_ids": ["BHV_2"],
            "slice_status": {"BHV_1": "running", "BHV_2": "passed"},
        },
        "behavior_slices": [
            {"slice_id": "BHV_1", "entity": "Order", "kind": "api", "priority": "P0", "status": "running"},
            {"slice_id": "BHV_2", "entity": "Payment", "kind": "db", "priority": "P1", "status": "passed"},
        ],
        "phases": {"execution": {"production_data_blocked": 3}, "oracle": {"evidence_chains_saved": 2}},
    }
    board = _build_test_task_board(report)
    assert board is not None
    assert board["ledger"]["slice_status"] == {"BHV_1": "running", "BHV_2": "passed"}
    assert board["slices"][0]["status"] == "running"
    assert board["slices"][1]["status"] == "passed"
    # 主链 5/6 安全边界拦截计数
    assert board["execution"]["production_data_blocked"] == 3
    # 主链 7 证据链落地计数
    assert board["evidence_chains_saved"] == 2


def test_board_handles_missing_phases_gracefully():
    """报告缺失 phases 时安全指标默认为 0，不抛错。"""
    report = {
        "behavior_slice_ledger": {"attempted_slice_ids": ["BHV_1"], "slice_status": {"BHV_1": "blocked"}},
        "behavior_slices": [{"slice_id": "BHV_1", "status": "blocked"}],
    }
    board = _build_test_task_board(report)
    assert board is not None
    assert board["execution"]["production_data_blocked"] == 0
    assert board["evidence_chains_saved"] == 0
    assert board["ledger"]["slice_status"]["BHV_1"] == "blocked"


def test_board_returns_none_on_non_dict_report():
    assert _build_test_task_board(None) is None
    assert _build_test_task_board("not-a-dict") is None
