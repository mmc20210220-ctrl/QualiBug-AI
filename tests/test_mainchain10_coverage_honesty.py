"""主链 4/5 覆盖诚实性守卫回归测试。

守卫目标：campaign 判 completed 时，若有高价值切片（permission/isolation/money/
concurrency）因运行时前置缺失而从未执行，必须显式列出 unexecuted_high_value_slices
并把 clean grade（inconclusive/evidence_ready）降级为 partial_coverage —— 绝不静默
把"只执行了一部分切片"报成干净完成。

单测直接验证纯函数 _apply_coverage_honesty_guard（单一真源、无副作用）。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_test_asset_center.__main__ import _apply_coverage_honesty_guard  # noqa: E402


def _v12(slices, slice_status=None, attempted=None, campaign_status=None, next_round=None):
    ledger = {
        "slice_status": slice_status or {},
        "attempted_slice_ids": attempted or [],
    }
    if campaign_status is not None:
        ledger["campaign_status"] = campaign_status
    if next_round is not None:
        ledger["next_round"] = next_round
    return {
        "behavior_slices": slices,
        "behavior_slice_ledger": ledger,
        "campaign": {"campaign_status": campaign_status} if campaign_status is not None else {},
    }


def test_downgrade_when_permission_slices_unexecuted_on_completed_campaign():
    """完成的 campaign 只执行了 invariant，24 个 permission 切片从未执行 → 降级 + 列出。"""
    slices = [
        {"slice_id": "BHV_inv1", "kind": "invariant", "entity": "inventory", "endpoints": ["/api/products"]},
        {"slice_id": "BHV_perm1", "kind": "permission", "entity": "order", "endpoints": ["/api/orders/:id/confirm"]},
        {"slice_id": "BHV_perm2", "kind": "permission", "entity": "order", "endpoints": ["/api/orders/:id/cancel"]},
        {"slice_id": "BHV_money1", "kind": "money", "entity": "refund", "endpoints": ["/api/refunds"]},
    ]
    # 只有 invariant 被执行
    v12 = _v12(slices, slice_status={"BHV_inv1": "running"})
    honesty, grade = _apply_coverage_honesty_guard(v12, "inconclusive", "completed")

    assert honesty["honest"] is False
    assert honesty["downgraded"] is True
    assert honesty["grade_before_guard"] == "inconclusive"
    assert grade == "partial_coverage"
    # 高价值总数=3（2 permission + 1 money），未执行=3
    assert honesty["high_value_total"] == 3
    assert honesty["high_value_unexecuted"] == 3
    kinds = {s["kind"] for s in honesty["unexecuted_high_value_slices"]}
    assert kinds == {"permission", "money"}
    ids = {s["slice_id"] for s in honesty["unexecuted_high_value_slices"]}
    assert ids == {"BHV_perm1", "BHV_perm2", "BHV_money1"}


def test_no_downgrade_when_all_high_value_executed():
    """所有高价值切片都执行过 → honest=True，保持原 grade。"""
    slices = [
        {"slice_id": "BHV_perm1", "kind": "permission", "entity": "order", "endpoints": ["/a"]},
        {"slice_id": "BHV_iso1", "kind": "isolation", "entity": "cart", "endpoints": ["/b"]},
    ]
    v12 = _v12(slices, slice_status={"BHV_perm1": "passed", "BHV_iso1": "failed"})
    honesty, grade = _apply_coverage_honesty_guard(v12, "inconclusive", "completed")

    assert honesty["honest"] is True
    assert honesty["downgraded"] is False
    assert honesty["high_value_unexecuted"] == 0
    assert grade == "inconclusive"  # 未降级


def test_attempted_slice_ids_counts_as_executed():
    """用 attempted_slice_ids 也算已执行（ledger 的另一真源）。"""
    slices = [{"slice_id": "BHV_perm1", "kind": "permission", "entity": "order", "endpoints": ["/a"]}]
    v12 = _v12(slices, slice_status={}, attempted=["BHV_perm1"])
    honesty, grade = _apply_coverage_honesty_guard(v12, "inconclusive", "completed")

    assert honesty["honest"] is True
    assert grade == "inconclusive"


def test_no_downgrade_when_not_completed():
    """非 completed（如 blocked/not_executed）不降级 —— 契约测试依赖这些保持原值。"""
    slices = [{"slice_id": "BHV_perm1", "kind": "permission", "entity": "order", "endpoints": ["/a"]}]
    v12 = _v12(slices, slice_status={})  # permission 未执行

    honesty_blocked, grade_blocked = _apply_coverage_honesty_guard(v12, "blocked", "blocked")
    assert grade_blocked == "blocked"
    assert honesty_blocked["downgraded"] is False

    # execution_status 非 completed（如 not_executed）：即便 grade=inconclusive 也不降级
    honesty_ne, grade_ne = _apply_coverage_honesty_guard(v12, "inconclusive", "not_executed")
    assert grade_ne == "inconclusive"
    assert honesty_ne["downgraded"] is False
    # 但仍然如实报告未执行的高价值切片（信息永远透明）
    assert honesty_ne["high_value_unexecuted"] == 1


def test_evidence_ready_also_downgraded():
    """有真实 findings（evidence_ready）但仍漏执行高价值切片 → 同样降级（覆盖不完整）。"""
    slices = [
        {"slice_id": "BHV_perm1", "kind": "permission", "entity": "order", "endpoints": ["/a"]},
        {"slice_id": "BHV_inv1", "kind": "invariant", "entity": "inv", "endpoints": ["/b"]},
    ]
    v12 = _v12(slices, slice_status={"BHV_inv1": "confirmed"})  # 只有 invariant 执行
    honesty, grade = _apply_coverage_honesty_guard(v12, "evidence_ready", "completed")

    assert grade == "partial_coverage"
    assert honesty["grade_before_guard"] == "evidence_ready"


def test_no_high_value_slices_means_honest():
    """项目根本没有高价值切片（如纯只读接口）→ honest=True，不降级。"""
    slices = [
        {"slice_id": "BHV_inv1", "kind": "invariant", "entity": "inv", "endpoints": ["/a"]},
        {"slice_id": "BHV_src1", "kind": "source_observation", "entity": "coupon", "endpoints": ["/b"]},
    ]
    v12 = _v12(slices, slice_status={})  # 都没执行，但都不是高价值
    honesty, grade = _apply_coverage_honesty_guard(v12, "inconclusive", "completed")

    assert honesty["honest"] is True
    assert honesty["high_value_total"] == 0
    assert grade == "inconclusive"


def test_empty_v12_is_safe():
    """空 v12 不报错，honest=True。"""
    honesty, grade = _apply_coverage_honesty_guard({}, "inconclusive", "completed")
    assert honesty["honest"] is True
    assert honesty["total_slices"] == 0
    assert grade == "inconclusive"


def test_resumable_partial_when_campaign_active_with_next_round():
    """campaign active + next_round → resumable=True（重跑可续），非终态跳过。"""
    slices = [
        {"slice_id": "BHV_perm1", "kind": "permission", "entity": "order", "endpoints": ["/a"]},
        {"slice_id": "BHV_inv1", "kind": "invariant", "entity": "inv", "endpoints": ["/b"]},
    ]
    v12 = _v12(slices, slice_status={"BHV_inv1": "running"}, campaign_status="active", next_round=5)
    honesty, grade = _apply_coverage_honesty_guard(v12, "evidence_ready", "completed")

    assert grade == "partial_coverage"  # 仍降级（本次确实未覆盖全部）
    assert honesty["resumable"] is True
    assert honesty["terminal_skip"] is False
    assert honesty["next_round"] == 5
    assert honesty["campaign_status"] == "active"
    assert "re-run" in honesty["actionable"].lower()


def test_terminal_skip_when_campaign_completed_but_high_value_unexecuted():
    """campaign completed 却漏执行高价值切片 → terminal_skip=True（真风险）。"""
    slices = [
        {"slice_id": "BHV_perm1", "kind": "permission", "entity": "order", "endpoints": ["/a"]},
        {"slice_id": "BHV_inv1", "kind": "invariant", "entity": "inv", "endpoints": ["/b"]},
    ]
    # campaign 报 completed、无 next_round，但 permission 未执行
    v12 = _v12(slices, slice_status={"BHV_inv1": "passed"}, campaign_status="completed", next_round=None)
    honesty, grade = _apply_coverage_honesty_guard(v12, "evidence_ready", "completed")

    assert grade == "partial_coverage"
    assert honesty["terminal_skip"] is True
    assert honesty["resumable"] is False
    assert "coverage gap" in honesty["actionable"].lower()


def test_completed_campaign_all_high_value_executed_is_honest_no_flags():
    """campaign completed 且高价值全执行 → honest，无 terminal_skip，不降级。"""
    slices = [
        {"slice_id": "BHV_perm1", "kind": "permission", "entity": "order", "endpoints": ["/a"]},
    ]
    v12 = _v12(slices, slice_status={"BHV_perm1": "confirmed"}, campaign_status="completed", next_round=None)
    honesty, grade = _apply_coverage_honesty_guard(v12, "evidence_ready", "completed")

    assert honesty["honest"] is True
    assert honesty["terminal_skip"] is False
    assert honesty["resumable"] is False
    assert grade == "evidence_ready"
