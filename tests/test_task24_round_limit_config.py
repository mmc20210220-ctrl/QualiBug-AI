"""Task 24: round 机制效率——automatic_round_limit 配置传递修复。

run10 实测（round_limit=3）：轮次循环实际跑到 round 16（持久化 obligation_plan
follow_on_round_receipts 有 15 条，planning_round 2..16），与配置的 3 不符——
主链把 campaign 句柄以 dict 包装器（campaign_id/campaign/store/mode）传入
``run_experiment_candidate``，而轮次循环用 ``getattr(campaign_handle,
"automatic_round_limit", 16)`` 直接读包装器，恒得默认 16，配置被静默丢弃。

本测试锁定修复：``_campaign_automatic_round_limit`` 必须从包装器内的
EnterpriseCampaign 对象读取 ``automatic_round_limit``（与 ``_finalize_campaign``
解包方式一致），并验证轮次循环按该上限截止。
"""
from __future__ import annotations

from ai_test_asset_center.discovery_runtime_execution_support import (
    _campaign_automatic_round_limit,
    _consume_pending_obligation_rounds,
)


class _FakeCampaign:
    def __init__(self, round_limit: int, campaign_id: str = "cmp-1") -> None:
        self.automatic_round_limit = round_limit
        self.campaign_id = campaign_id


def _dict_wrapper(campaign) -> dict:
    """主链 ``_build_mainline_campaign`` 返回的句柄形态。"""
    return {
        "campaign_id": campaign.campaign_id,
        "campaign": campaign,
        "store": None,
        "mode": "open",
    }


def test_round_limit_reads_campaign_object_not_dict_wrapper() -> None:
    # 修复前：getattr(dict_wrapper, "automatic_round_limit", 16) 恒为 16，
    # 配置的 3 被静默丢弃（run10 实测 15 条 follow-on receipts 即此根因）。
    campaign = _FakeCampaign(round_limit=3)
    assert _campaign_automatic_round_limit(_dict_wrapper(campaign)) == 3


def test_round_limit_bare_object_still_supported() -> None:
    campaign = _FakeCampaign(round_limit=5)
    assert _campaign_automatic_round_limit(campaign) == 5


def test_round_limit_wrapper_without_campaign_falls_back() -> None:
    assert _campaign_automatic_round_limit({"campaign_id": "x"}) == 16
    assert _campaign_automatic_round_limit(object()) == 16


def test_round_limit_malformed_value_falls_back() -> None:
    campaign = _FakeCampaign(round_limit="not-an-int")
    assert _campaign_automatic_round_limit(campaign) == 16


def _minimal_round_inputs(count: int = 6):
    obligations = [
        {
            "obligation_id": f"obl-{index}",
            "risk_family": "authorization",
            "required_operations": [f"op-{index}"],
            "required_actors": ["actor-1"],
            "confidence": 0.8,
            "property": {"operation_ref": f"op-{index}"},
            "source_refs": [{"id": f"src-{index}", "type": "api"}],
        }
        for index in range(1, count + 1)
    ]
    experiments = {
        f"obl-{index}": {
            "obligation_id": f"obl-{index}",
            "experiment_id": f"exp-{index}",
            "compile_receipt": {"status": "COMPILED"},
            "observers": [{"observer_id": "http_response", "adapter": "http_api"}],
        }
        for index in range(1, count + 1)
    }
    behavior_ir = {
        "actors": [{"id": "actor-1", "role": "buyer"}],
        "operations": [
            {"id": f"op-{index}", "method": "GET", "path": f"/api/items/{index}"}
            for index in range(1, count + 1)
        ],
        "relations": [],
    }
    obligation_plan = {
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
    return obligations, experiments, behavior_ir, obligation_plan


def _fake_execute(scheduled, **_kwargs):
    ids = [str(row.get("obligation_id") or "") for row in scheduled]
    return {
        "executed_count": len(ids),
        "findings": [],
        "compile_results": {
            oid: {"status": "COMPILED", "experiment_id": f"exp-{oid}"}
            for oid in ids
        },
        "execution_results": {
            oid: {"status": "EXECUTED", "execution_id": f"exec-{oid}"}
            for oid in ids
        },
        "gate_results": {},
    }


def test_round_loop_stops_at_configured_limit() -> None:
    """automatic_round_limit=3 → 只消费 rounds 2..3，receipts 不超 2 条。"""
    obligations, experiments, behavior_ir, obligation_plan = _minimal_round_inputs(
        count=10
    )
    _, updated = _consume_pending_obligation_rounds(
        obligation_plan=obligation_plan,
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir=behavior_ir,
        root=".",
        project="demo",
        base_url="http://target.invalid",
        runtime_contract={"status": "approved"},
        mainline_run={"campaign_id": "cmp", "run_id": "run"},
        campaign_id="cmp",
        automatic_round_limit=3,
        execute_batch=_fake_execute,
    )
    receipts = updated.get("follow_on_round_receipts") or []
    rounds = [int(r.get("planning_round") or 0) for r in receipts]
    assert rounds == [2, 3]


def test_round_loop_caps_at_default_when_handle_is_bare() -> None:
    """极限保护：无对象可读时仍回退 16（历史默认），不崩溃。"""
    obligations, experiments, behavior_ir, obligation_plan = _minimal_round_inputs(
        count=4
    )
    _, updated = _consume_pending_obligation_rounds(
        obligation_plan=obligation_plan,
        obligations=obligations,
        experiments_by_obligation=experiments,
        behavior_ir=behavior_ir,
        root=".",
        project="demo",
        base_url="http://target.invalid",
        runtime_contract={"status": "approved"},
        mainline_run={"campaign_id": "cmp", "run_id": "run"},
        campaign_id="cmp",
        automatic_round_limit=_campaign_automatic_round_limit(object()),
        execute_batch=_fake_execute,
    )
    receipts = updated.get("follow_on_round_receipts") or []
    assert all(
        int(r.get("planning_round") or 0) >= 2 for r in receipts
    )
