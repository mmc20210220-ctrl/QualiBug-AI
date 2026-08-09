"""Task 34: 执行率暴跌根因回归锁——round limit 持久化棘轮 + 轮次早退 receipt。

run16 实测（33 任务叠加后端到端）：selected 1200 只 executed 64；run10 同期
executed 854。根因链：
1. ``_campaign_context`` 用 ``min(persisted, settings)`` 对齐持久化 campaign
   上限——单向棘轮：任何一次以较小 round_limit 持久化的 run（如
   QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT=1 的快速扫描）会让同一 campaign
   之后的所有 run 永远以该小值运行，操作者当前 env 被静默覆盖。
2. round_limit=1 时 ``_consume_pending_obligation_rounds`` 立即返回（round 1
   已完成，无 follow-on 轮次），round-1 批次因每批预算（formal=100）而
   budget_deferred 的 1100/1200 义务从未再被调度 → 手动终结投影为
   OBLIGATION_BUDGET_REACHED（run16 的 terminal_stage=execution 1100 条）。

本测试锁定两个通用机制修复：
- 当前 run 的 settings（env 派生）是本次运行的权威，持久化旧值不得压低本次
  执行容量（assign，不用 min）。
- 轮次早退（round_limit<=1 / plan budget=0）但仍有 pending 时必须写入
  receipt（early_stop_reason / follow_on_round_limit / pending_count），禁止
  静默。
"""
from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.discovery_runtime_execution_support import (
    _consume_pending_obligation_rounds,
)
from ai_test_asset_center.v12_pipeline import _campaign_context


class _FakeCampaign:
    def __init__(self, *, slice_budget: int, automatic_round_limit: int) -> None:
        self.campaign_id = "cmp-ratchet"
        self.project_id = "demo"
        self.scope_id = "scope"
        self.environment_ref = "env"
        self.source_snapshot_hash = "snap"
        self.source_id = ""
        self.source_hash = ""
        self.policy_version = ""
        self.rerun_key = ""
        self.slice_budget = slice_budget
        self.automatic_round_limit = automatic_round_limit


class _FakeStore:
    def __init__(self, persisted: _FakeCampaign) -> None:
        self._persisted = persisted

    def open_or_create(self, candidate):
        return self._persisted, "resumed"


def test_campaign_context_current_settings_win_over_persisted_ratchet(
    monkeypatch,
) -> None:
    """持久化 automatic_round_limit=1 / slice_budget=15 不得压低本次 env=3。

    修复前 ``min(persisted, settings)`` 返回 1 —— 即 run16 实际行为；修复后
    当前 settings（env 权威）生效。
    """
    persisted = _FakeCampaign(
        slice_budget=15, automatic_round_limit=1
    )

    def fake_candidate(*_args, **_kwargs):
        return _FakeCampaign(
            slice_budget=1200, automatic_round_limit=3
        )

    monkeypatch.setattr(
        "ai_test_asset_center.v12_pipeline._campaign_candidate",
        fake_candidate,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.v12_pipeline.EnterpriseCampaignStore",
        lambda root, project: _FakeStore(persisted),
    )
    campaign, store, mode = _campaign_context(
        project="demo",
        prd_text="",
        api_spec_text="",
        db_schema_text="",
        base_url="http://target.invalid",
        settings={"slice_budget": 1200, "round_limit": 3},
        context={},
        root=Path("."),
        submitted_api_spec_text="",
    )
    assert mode == "resumed"
    assert campaign.automatic_round_limit == 3, (
        "current env round_limit must win over persisted 1 (run16 ratchet)"
    )
    assert campaign.slice_budget == 1200
    assert store is not None


def test_campaign_context_fresh_campaign_keeps_settings() -> None:
    """无持久化状态（created）时 settings 原样生效。"""

    def fake_candidate(*_args, **_kwargs):
        return _FakeCampaign(
            slice_budget=1200, automatic_round_limit=3
        )

    class _CreateStore:
        def open_or_create(self, candidate):
            return candidate, "created"

    import ai_test_asset_center.v12_pipeline as _v12
    original = _v12.EnterpriseCampaignStore

    _v12._campaign_candidate = fake_candidate
    _v12.EnterpriseCampaignStore = lambda root, project: _CreateStore()
    try:
        campaign, _store, mode = _campaign_context(
            project="demo",
            prd_text="",
            api_spec_text="",
            db_schema_text="",
            base_url="http://target.invalid",
            settings={"slice_budget": 1200, "round_limit": 3},
            context={},
            root=Path("."),
            submitted_api_spec_text="",
        )
    finally:
        _v12.EnterpriseCampaignStore = original
    assert mode == "created"
    assert campaign.automatic_round_limit == 3
    assert campaign.slice_budget == 1200


def _pending_plan(count: int = 4):
    return {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "budget": 2,
        "selected": [
            {"obligation_id": f"obl-{index}"} for index in range(1, 3)
        ],
        "pending_next_round": [
            {"obligation_id": f"obl-{index}"} for index in range(3, count + 1)
        ],
        "selected_count": 2,
        "pending_count": count - 2,
    }


def test_round_limit_one_early_return_is_receipted() -> None:
    """round_limit=1 + 有 pending：立即返回，但必须写 early_stop_reason。

    修复前静默返回 []，1100 条 budget-deferred 义务被手动终结投影成
    OBLIGATION_BUDGET_REACHED，无任何 receipt 说明真实原因（run16）。
    """
    _, updated = _consume_pending_obligation_rounds(
        obligation_plan=_pending_plan(),
        obligations=[],
        experiments_by_obligation={},
        behavior_ir={},
        root=".",
        project="demo",
        base_url="http://target.invalid",
        runtime_contract={"status": "approved"},
        mainline_run={"campaign_id": "cmp", "run_id": "run"},
        campaign_id="cmp",
        automatic_round_limit=1,
        execute_batch=lambda *a, **k: {"executed_count": 0},
    )
    assert (
        updated.get("early_stop_reason")
        == "PENDING_NEXT_ROUND_SKIPPED_ROUND_LIMIT_ONE"
    )
    assert updated.get("follow_on_round_limit") == 1
    assert updated.get("pending_count") == 2
    # pending 队列原样保留——手动终结投影仍能对它们做 DEFERRED 收尾。
    assert len(updated.get("pending_next_round") or []) == 2


def test_plan_budget_zero_with_pending_is_receipted() -> None:
    """plan budget=0 但仍有 pending：同样显式 receipt，不静默。"""
    _, updated = _consume_pending_obligation_rounds(
        obligation_plan={
            **_pending_plan(),
            "budget": 0,
        },
        obligations=[],
        experiments_by_obligation={},
        behavior_ir={},
        root=".",
        project="demo",
        base_url="http://target.invalid",
        runtime_contract={"status": "approved"},
        mainline_run={"campaign_id": "cmp", "run_id": "run"},
        campaign_id="cmp",
        automatic_round_limit=3,
        execute_batch=lambda *a, **k: {"executed_count": 0},
    )
    assert (
        updated.get("early_stop_reason")
        == "PENDING_NEXT_ROUND_SKIPPED_PLAN_BUDGET_ZERO"
    )
    assert len(updated.get("pending_next_round") or []) == 2


def test_operation_coverage_budget_counts_intent_operation_refs() -> None:
    """batch 预算 operation floor 必须识别意图行的 operation_refs。

    主链调度传入的是 agent-intent 行（只有 operation_refs，没有
    operation_key）；floor 只读 operation_key 时静默失效（run16：预算停在
    phase 默认 100，1100/1200 selected 被 budget_deferred）。修复后按
    operation_refs 兜底计数。
    """
    from ai_test_asset_center import (
        _experiment_batch_executor_single_finding_mechanics as batch_core,
    )

    intent_rows = [
        {"obligation_id": f"o{i}", "operation_refs": [f"op-{i}"]}
        for i in range(5)
    ]
    assert batch_core._operation_coverage_budget(intent_rows, budget=1) == 5

    # 混合形态：operation_key 与 operation_refs 各自计数，取并集。
    mixed = [
        {"obligation_id": "a", "operation_key": "OP-A"},
        {"obligation_id": "b", "operation_refs": ["op-B"]},
        {"obligation_id": "c"},
    ]
    assert batch_core._operation_coverage_budget(mixed, budget=1) == 2

    # 无任何操作标识的行不抬升预算（原有语义保持）。
    assert batch_core._operation_coverage_budget(
        [{"obligation_id": "a"}], budget=1
    ) == 1


def test_family_coverage_budget_union_floor_with_operation_refs() -> None:
    """family 覆盖 floor 的 union 上界同样识别 operation_refs。"""
    from ai_test_asset_center import (
        _experiment_batch_executor_single_finding_mechanics as batch_core,
    )

    rows = [
        {"obligation_id": "a", "operation_refs": ["op-1"], "risk_family": "authorization"},
        {"obligation_id": "b", "operation_refs": ["op-2"], "risk_family": "state"},
    ]
    assert batch_core._family_coverage_budget(rows, budget=1) == 4  # 2 ops + 2 fams
    assert batch_core._family_coverage_budget(rows, budget=50) == 50
