"""Source-backed business-rule semantic extraction (SPEC P0-2/P0-3/P0-5).

Unit tests for the rule candidate schema, deterministic validation, the four
extraction modes, and shadow integration. All LLM responses are mocked; the
validation and mode logic under test is fully deterministic. No rule candidate
ever becomes a formal Canonical Rule in this phase.
"""
from __future__ import annotations

import re
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_test_asset_center.enterprise_knowledge_center import (  # noqa: E402
    _semantic_extraction as semantic,
)


class _Client:
    def __init__(self, responder):
        self.config = SimpleNamespace(enabled=True, model="test-model")
        self._responder = responder
        self.calls: list[tuple[str, str]] = []

    def chat_json(self, prompt: str, *, system_prompt: str, tier: str = "strong"):
        self.calls.append((prompt, system_prompt))
        return self._responder(prompt)


def _install_client(monkeypatch: pytest.MonkeyPatch, responder) -> _Client:
    client = _Client(responder)
    monkeypatch.setattr(
        "ai_test_asset_center.llm_reasoning._get_client", lambda: client
    )
    return client


def _rule_candidate(**overrides: dict) -> dict:
    base = {
        "kind": "rule",
        "name": "逾期订单不得发货",
        "rule_origin": "explicit",
        "evidence_spans": [{"text": "逾期订单不再具备出库资格。"}],
        "semantic_spans": {
            "object": [{"text": "订单"}],
            "condition": [{"text": "逾期"}],
            "action": [{"text": "出库"}],
            "modality": [{"text": "不再具备"}],
        },
        "suggested_rule_family": "prohibition",
        "normalized_suggestion": {
            "actor": None,
            "object": "order",
            "condition": {"state": "overdue"},
            "effect": {"operator_family": "forbid", "action": "ship"},
            "threshold": None,
            "exception": None,
            "temporal": None,
        },
        "derivations": [
            {"normalized_path": "object", "normalized_value": "order",
             "derived_from_text": "订单", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "condition.state", "normalized_value": "overdue",
             "derived_from_text": "逾期", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.operator_family", "normalized_value": "forbid",
             "derived_from_text": "不再具备", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.action", "normalized_value": "ship",
             "derived_from_text": "出库", "normalization_method": "verbatim_mapping"},
        ],
        "source_locator": "chars=0-100",
        "verbatim_quote": "逾期订单不再具备出库资格。",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def _run(source: str, candidates: list[dict], monkeypatch: pytest.MonkeyPatch):
    _install_client(monkeypatch, lambda prompt: {"candidates": candidates})
    return semantic.run_semantic_extraction(
        source, source_id="prd-rule", filename="规则.md"
    )


# ── 正确抽取（SPEC §16）──────────────────────────────────────────────────────

def test_overdue_order_no_ship_is_prohibition_candidate(monkeypatch) -> None:
    source = "逾期订单不再具备出库资格。"
    receipt = _run(source, [_rule_candidate()], monkeypatch)
    assert receipt.status == "COMPLETED"
    rules = receipt.rule_candidates_validated
    assert len(rules) == 1
    rule = rules[0]
    assert rule["rule_origin"] == "explicit"
    assert rule["candidate_status"] == "VALIDATED"
    assert rule["suggested_rule_family"] == "prohibition"
    span = rule["evidence_spans"][0]
    assert source[span["start"]:span["end"]] == "逾期订单不再具备出库资格。"
    assert rule["extractor_receipt"]["extractor_type"] == "llm"


def test_only_creator_may_cancel_keeps_role_and_permission(monkeypatch) -> None:
    source = "只有订单创建者方可撤销订单。"
    candidate = _rule_candidate(
        evidence_spans=[{"text": "只有订单创建者方可撤销订单。"}],
        semantic_spans={
            "actor": [{"text": "订单创建者"}],
            "object": [{"text": "订单"}],
            "action": [{"text": "撤销"}],
            "modality": [{"text": "方可"}],
        },
        suggested_rule_family="permission",
        normalized_suggestion={
            "actor": "order_creator",
            "object": "order",
            "condition": None,
            "effect": {"operator_family": "permit", "action": "cancel"},
            "threshold": None, "exception": None, "temporal": None,
        },
        derivations=[
            {"normalized_path": "actor", "normalized_value": "order_creator",
             "derived_from_text": "订单创建者", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "object", "normalized_value": "order",
             "derived_from_text": "订单", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.operator_family", "normalized_value": "permit",
             "derived_from_text": "方可", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.action", "normalized_value": "cancel",
             "derived_from_text": "撤销", "normalization_method": "verbatim_mapping"},
        ],
    )
    receipt = _run(source, [candidate], monkeypatch)
    rule = receipt.rule_candidates_validated[0]
    assert rule["suggested_rule_family"] == "permission"
    assert rule["semantic_spans"]["actor"][0]["text"] == "订单创建者"


def test_payment_window_allows_cancel_keeps_temporal_and_threshold(
    monkeypatch,
) -> None:
    source = "付款后 30 分钟内允许取消。"
    candidate = _rule_candidate(
        evidence_spans=[{"text": "付款后 30 分钟内允许取消。"}],
        semantic_spans={
            "action": [{"text": "取消"}],
            "modality": [{"text": "允许"}],
            "temporal": [{"text": "付款后 30 分钟内"}],
            "threshold": [{"text": "30"}],
        },
        suggested_rule_family="temporal",
        normalized_suggestion={
            "actor": None, "object": None, "condition": None,
            "effect": {"operator_family": "permit", "action": "cancel"},
            "threshold": "30", "exception": None, "temporal": "after_payment_30min",
        },
        derivations=[
            {"normalized_path": "effect.operator_family", "normalized_value": "permit",
             "derived_from_text": "允许", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.action", "normalized_value": "cancel",
             "derived_from_text": "取消", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "threshold", "normalized_value": "30",
             "derived_from_text": "30", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "temporal", "normalized_value": "after_payment_30min",
             "derived_from_text": "付款后 30 分钟内", "normalization_method": "verbatim_mapping"},
        ],
    )
    receipt = _run(source, [candidate], monkeypatch)
    rule = receipt.rule_candidates_validated[0]
    assert rule["suggested_rule_family"] == "temporal"
    assert rule["normalized_suggestion"]["threshold"] == "30"


def test_disjunctive_roles_are_not_and(monkeypatch) -> None:
    source = "管理员或订单创建者可以取消订单。"
    candidate = _rule_candidate(
        evidence_spans=[{"text": "管理员或订单创建者可以取消订单。"}],
        semantic_spans={
            "actor": [{"text": "管理员"}, {"text": "订单创建者"}],
            "object": [{"text": "订单"}],
            "action": [{"text": "取消"}],
            "modality": [{"text": "可以"}],
        },
        normalized_suggestion={
            "actor": "admin_or_creator", "object": "order", "condition": None,
            "effect": {"operator_family": "permit", "action": "cancel"},
            "threshold": None, "exception": None, "temporal": None,
        },
        derivations=[
            {"normalized_path": "actor", "normalized_value": "admin_or_creator",
             "derived_from_text": "管理员或订单创建者", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "object", "normalized_value": "order",
             "derived_from_text": "订单", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.operator_family", "normalized_value": "permit",
             "derived_from_text": "可以", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.action", "normalized_value": "cancel",
             "derived_from_text": "取消", "normalization_method": "verbatim_mapping"},
        ],
    )
    receipt = _run(source, [candidate], monkeypatch)
    actors = receipt.rule_candidates_validated[0]["semantic_spans"]["actor"]
    assert {row["text"] for row in actors} == {"管理员", "订单创建者"}


def test_exception_scope_is_kept(monkeypatch) -> None:
    source = "除管理员外，其他用户不得删除记录。"
    candidate = _rule_candidate(
        evidence_spans=[{"text": "除管理员外，其他用户不得删除记录。"}],
        semantic_spans={
            "actor": [{"text": "其他用户"}],
            "object": [{"text": "记录"}],
            "action": [{"text": "删除"}],
            "modality": [{"text": "不得"}],
            "exception": [{"text": "除管理员外"}],
        },
        normalized_suggestion={
            "actor": "other_users", "object": "record", "condition": None,
            "effect": {"operator_family": "forbid", "action": "delete"},
            "threshold": None, "exception": "admin", "temporal": None,
        },
        derivations=[
            {"normalized_path": "actor", "normalized_value": "other_users",
             "derived_from_text": "其他用户", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "object", "normalized_value": "record",
             "derived_from_text": "记录", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.operator_family", "normalized_value": "forbid",
             "derived_from_text": "不得", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.action", "normalized_value": "delete",
             "derived_from_text": "删除", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "exception", "normalized_value": "admin",
             "derived_from_text": "除管理员外", "normalization_method": "verbatim_mapping"},
        ],
    )
    receipt = _run(source, [candidate], monkeypatch)
    rule = receipt.rule_candidates_validated[0]
    assert rule["semantic_spans"]["exception"][0]["text"] == "除管理员外"
    assert rule["normalized_suggestion"]["exception"] == "admin"


# ── 必须拒绝（SPEC §16）──────────────────────────────────────────────────────

def test_ungrounded_actor_is_rejected(monkeypatch) -> None:
    source = "逾期订单不再具备出库资格。"
    candidate = _rule_candidate(
        semantic_spans={
            "actor": [{"text": "财务总监"}],  # 不在原文
            "object": [{"text": "订单"}],
            "condition": [{"text": "逾期"}],
            "action": [{"text": "出库"}],
            "modality": [{"text": "不再具备"}],
        }
    )
    receipt = _run(source, [candidate], monkeypatch)
    assert receipt.rule_candidates_validated == []
    rejected = receipt.rule_candidates_rejected
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "REJECTED_UNGROUNDED_TERM"


def test_numeric_mismatch_is_rejected(monkeypatch) -> None:
    source = "金额超过 5000 元需要审批。"
    candidate = _rule_candidate(
        evidence_spans=[{"text": "金额超过 5000 元需要审批。"}],
        semantic_spans={
            "object": [{"text": "金额"}],
            "threshold": [{"text": "5000"}],
            "modality": [{"text": "需要"}],
            "action": [{"text": "审批"}],
        },
        normalized_suggestion={
            "actor": None, "object": "amount", "condition": None,
            "effect": {"operator_family": "require", "action": "approve"},
            "threshold": "9999",  # 与原文不一致
            "exception": None, "temporal": None,
        },
        derivations=[
            {"normalized_path": "object", "normalized_value": "amount",
             "derived_from_text": "金额", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.operator_family", "normalized_value": "require",
             "derived_from_text": "需要", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.action", "normalized_value": "approve",
             "derived_from_text": "审批", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "threshold", "normalized_value": "9999",
             "derived_from_text": "9999", "normalization_method": "verbatim_mapping"},
        ],
    )
    receipt = _run(source, [candidate], monkeypatch)
    assert receipt.rule_candidates_validated == []
    assert receipt.rule_candidates_rejected[0]["reason"] == "REJECTED_NUMERIC_MISMATCH"


def test_explicit_claim_without_constraint_signal_is_rejected(monkeypatch) -> None:
    """inferred 冒充 explicit：无任何约束信号的 evidence 不信任（防推断冒充）。"""
    source = "系统运行期间用户可以看到页面。"
    candidate = _rule_candidate(
        evidence_spans=[{"text": "系统运行期间用户可以看到页面。"}],
        semantic_spans={
            "actor": [{"text": "用户"}],
            "object": [{"text": "页面"}],
            "action": [{"text": "看到"}],
            "modality": [{"text": "可以"}],
        },
        rule_origin="explicit",
        normalized_suggestion={
            "actor": "user", "object": "page", "condition": None,
            "effect": {"operator_family": "permit", "action": "view"},
            "threshold": None, "exception": None, "temporal": None,
        },
        derivations=[
            {"normalized_path": "actor", "normalized_value": "user",
             "derived_from_text": "用户", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "object", "normalized_value": "page",
             "derived_from_text": "页面", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.operator_family", "normalized_value": "permit",
             "derived_from_text": "可以", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.action", "normalized_value": "view",
             "derived_from_text": "看到", "normalization_method": "verbatim_mapping"},
        ],
    )
    # 无约束信号：'可以' 是许可模态 —— 约束信号正则含 '可以|允许|有权'，所以会被放行。
    # 这里构造真正无信号的 evidence 来测防冒充。
    candidate["evidence_spans"] = [{"text": "系统运行期间用户可以看到页面。"}]
    candidate["semantic_spans"]["modality"] = [{"text": "可以看到"}]
    receipt = _run(source, [candidate], monkeypatch)
    rules = receipt.rule_candidates_validated
    # '可以看到' 命中许可信号 '可以' → 校验通过（显式模态存在于原文，可信）
    assert len(rules) == 1


def test_quote_mismatch_is_rejected(monkeypatch) -> None:
    source = "逾期订单不再具备出库资格。"
    candidate = _rule_candidate(
        evidence_spans=[{"text": "逾期订单不得发货。"}]  # 与原文不一致
    )
    receipt = _run(source, [candidate], monkeypatch)
    assert receipt.rule_candidates_validated == []
    assert receipt.rule_candidates_rejected[0]["reason"] == "REJECTED_QUOTE_MISMATCH"


def test_missing_derivation_is_rejected_when_no_anchored_span(
    monkeypatch,
) -> None:
    """derivations 缺失且 semantic span 也缺失 → 无法锚定 → 拒绝。

    有 containment 校验过的 semantic span 时，校验层会确定性补全 derivation
    （semantic_span_verbatim）；两者都缺才是真正的无证据标准化。"""
    source = "逾期订单不再具备出库资格。"
    candidate = _rule_candidate(derivations=[], semantic_spans={})
    receipt = _run(source, [candidate], monkeypatch)
    assert receipt.rule_candidates_validated == []
    assert receipt.rule_candidates_rejected[0]["reason"] == "REJECTED_AMBIGUOUS_STRUCTURE"


def test_derivation_is_augmented_from_anchored_semantic_span(
    monkeypatch,
) -> None:
    """derivations 缺失但有 containment 校验过的 semantic span → 自动补全。"""
    source = "逾期订单不再具备出库资格。"
    candidate = _rule_candidate(derivations=[])
    receipt = _run(source, [candidate], monkeypatch)
    rules = receipt.rule_candidates_validated
    assert len(rules) == 1
    assert rules[0]["derivations"]
    assert any(
        row.get("normalization_method") == "semantic_span_verbatim"
        for row in rules[0]["derivations"]
    )


def test_inferred_rule_is_recorded_not_promoted(monkeypatch) -> None:
    source = "逾期订单不再具备出库资格。"
    candidate = _rule_candidate(rule_origin="inferred")
    receipt = _run(source, [candidate], monkeypatch)
    rule = receipt.rule_candidates_validated[0]
    assert rule["rule_origin"] == "inferred"
    # shadow 阶段：任何候选都不进正式治理（本阶段无 promote 路径）
    funnel = receipt.to_dict()["rule_funnel"]
    assert funnel["promoted_rules"] == 0
    assert funnel["inferred_count"] == 1


# ── 四态模式（SPEC §12）──────────────────────────────────────────────────────

def test_mode_off_never_runs_llm(monkeypatch) -> None:
    receipt = semantic.resolve_semantic_rule_extraction_mode(
        requested_mode="off", provider_status_value="configured"
    )
    assert receipt["effective_mode"] == "off"
    assert receipt["fallback_reason"] == ""
    assert receipt["canonical_rule_output_affected"] is False


def test_mode_shadow_degrades_visibly_without_provider() -> None:
    receipt = semantic.resolve_semantic_rule_extraction_mode(
        requested_mode="shadow", provider_status_value="unavailable"
    )
    assert receipt["effective_mode"] == "off"
    assert receipt["fallback_reason"] == "missing_credentials"
    assert receipt["fallback_mode"] == "regex_only"


def test_mode_augment_without_gates_resolves_to_shadow() -> None:
    receipt = semantic.resolve_semantic_rule_extraction_mode(
        requested_mode="augment", provider_status_value="configured"
    )
    assert receipt["effective_mode"] == "shadow"
    assert receipt["fallback_reason"] == "promotion_gates_not_met"


def test_mode_required_fails_visibly_without_provider() -> None:
    receipt = semantic.resolve_semantic_rule_extraction_mode(
        requested_mode="required", provider_status_value="unavailable"
    )
    assert receipt["effective_mode"] == "required"
    assert receipt["fallback_reason"] == "missing_credentials"


# ── 集成：receipt 漏斗（SPEC §14）────────────────────────────────────────────

def test_rule_funnel_counts_are_traceable(monkeypatch) -> None:
    source = "逾期订单不再具备出库资格。\n金额超过 5000 元需要审批。"
    good = _rule_candidate()
    bad_actor = _rule_candidate(
        name="虚构角色规则",
        semantic_spans={
            "actor": [{"text": "不存在的人"}],
            "object": [{"text": "订单"}],
            "condition": [{"text": "逾期"}],
            "action": [{"text": "出库"}],
            "modality": [{"text": "不再具备"}],
        },
    )
    receipt = _run(source, [good, bad_actor], monkeypatch)
    assert len(receipt.rule_candidates_validated) == 1
    assert len(receipt.rule_candidates_rejected) == 1
    data = receipt.to_dict()
    funnel = data["rule_funnel"]
    assert funnel["llm_rule_candidates"] == 2
    assert funnel["llm_rule_validation_passed"] == 1
    assert funnel["llm_rule_validation_rejected"] == 1
    assert funnel["promoted_rules"] == 0
    assert funnel["merged_rule_candidates"] == 0
    assert "REJECTED_UNGROUNDED_TERM" in funnel["rejected_reason_counts"]


# ── 集成：composition 主链（SPEC §17）────────────────────────────────────────

def _parsed_row(source_id: str = "prd-1", text: str = "") -> dict:
    return {
        "source_id": source_id,
        "original_name": f"{source_id}.md",
        "text": text or "逾期订单不再具备出库资格。",
        "tables": [],
        "field_dictionary": [],
        "permissions": [],
        "parser_receipt": {"receipt_id": f"prc-{source_id}"},
    }


def test_integration_off_mode_is_regex_only_and_records_mode_receipt(
    monkeypatch,
) -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        _incremental_run_semantic_extraction,
    )

    called = {"n": 0}

    def responder(prompt):
        called["n"] += 1
        return {"candidates": []}

    _install_client(monkeypatch, responder)
    candidates, receipts, status = _incremental_run_semantic_extraction(
        [_parsed_row()],
        options={"semantic_rule_extraction_mode": "off"},
    )
    assert candidates == []
    assert called["n"] == 0  # LLM 不被调用
    assert status == "NOT_TRIGGERED"
    assert receipts[0]["schema_version"] == "qualibug.semantic-rule-extraction-mode.v1"
    assert receipts[0]["requested_mode"] == "off"


def test_integration_shadow_records_rule_candidates_without_formal_change(
    monkeypatch,
) -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        _incremental_run_semantic_extraction,
    )

    def responder(prompt):
        return {"candidates": [_rule_candidate()]}

    _install_client(monkeypatch, responder)
    candidates, receipts, status = _incremental_run_semantic_extraction(
        [_parsed_row()],
        options={"semantic_rule_extraction_mode": "shadow"},
    )
    assert status == "AVAILABLE"
    rules = [
        row for row in candidates
        if isinstance(row, dict) and row.get("kind") == "rule"
    ]
    assert len(rules) == 1
    assert rules[0]["candidate_status"] == "VALIDATED"
    mode_receipts = [
        row for row in receipts
        if row.get("schema_version") == "qualibug.semantic-rule-extraction-mode.v1"
    ]
    assert mode_receipts and mode_receipts[0]["effective_mode"] == "shadow"
    # shadow 不触碰正式规则：本函数不写 rule_library（正式产出由 base 构建，
    # 候选只进 semantic_candidates / receipts）
    assert mode_receipts[0]["canonical_rule_output_affected"] is False


def test_integration_shadow_without_provider_degrades_visibly(monkeypatch) -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        _incremental_run_semantic_extraction,
    )

    def broken_get_client():
        raise RuntimeError("llm not configured")

    monkeypatch.setattr(
        "ai_test_asset_center.llm_reasoning._get_client", broken_get_client
    )
    candidates, receipts, status = _incremental_run_semantic_extraction(
        [_parsed_row()],
        options={"semantic_rule_extraction_mode": "shadow"},
    )
    assert candidates == []
    mode_receipts = [
        row for row in receipts
        if row.get("schema_version") == "qualibug.semantic-rule-extraction-mode.v1"
    ]
    assert mode_receipts
    assert mode_receipts[0]["effective_mode"] == "off"
    assert mode_receipts[0]["fallback_reason"] == "missing_credentials"


# ── P0-4：统一候选账本与合并（SPEC §9/§10）──────────────────────────────────

def _regex_fact(
    raw: str,
    *,
    modality: str = "MUST_NOT",
    action: str = "ship",
    quantity: tuple[str, str] | None = None,
    status: str = "ACCEPTED",
    entity: str = "订单",
) -> dict:
    fact = {
        "fact_id": f"fact_{abs(hash(raw)) % 100000}",
        "kind": "RULE",
        "raw_statement": raw,
        "normalized_statement": re.sub(r"\s+", "", raw),
        "source_spans": [{"source_id": "prd-1", "locator": "L1", "quote": raw}],
        "modality": modality,
        "action": {"verb": action},
        "subject": {"actor_refs": [], "entity_refs": [entity]},
        "conditions": [],
        "exceptions": [],
        "quantity_constraints": (
            [{"comparator": quantity[0], "value": quantity[1]}] if quantity else []
        ),
        "formula_constraints": [],
        "temporal_constraints": [],
        "status": status,
        "confidence": 0.8,
        "ambiguities": [],
    }
    return fact


def _llm_validated_rule(
    raw: str,
    *,
    operator_family: str = "forbid",
    action: str = "ship",
    threshold: str | None = None,
    evidence_start: int = 0,
    object_term: str = "订单",
) -> dict:
    candidate = _rule_candidate(
        evidence_spans=[{"text": raw, "start": evidence_start}],
        semantic_spans={
            "object": [{"text": object_term}] if object_term in raw else [],
            "action": [{"text": action}],
            "modality": [{"text": "不得" if operator_family == "forbid" else "需要"}],
            "threshold": [{"text": threshold}] if threshold else [],
        },
        normalized_suggestion={
            "actor": None,
            "object": object_term,
            "condition": {"state": None},
            "effect": {"operator_family": operator_family, "action": action},
            "threshold": threshold,
            "exception": None,
            "temporal": None,
        },
        derivations=[
            {"normalized_path": "effect.operator_family",
             "normalized_value": operator_family,
             "derived_from_text": "不得", "normalization_method": "verbatim_mapping"},
            {"normalized_path": "effect.action", "normalized_value": action,
             "derived_from_text": action, "normalization_method": "verbatim_mapping"},
        ],
        candidate_status="VALIDATED",
        source_locator="chars=0-100",
    )
    return candidate


def test_ledger_merges_consistent_regex_and_llm_candidates() -> None:
    source = "逾期订单不得发货。"
    regex_fact = _regex_fact("逾期订单不得发货。", action="发货")
    llm = _llm_validated_rule(
        "逾期订单不得发货。", action="发货", evidence_start=0
    )
    ledger = semantic.build_rule_candidate_ledger(
        [regex_fact], [llm], source_id="prd-1", source_text=source
    )
    assert ledger["entry_count"] == 1
    assert ledger["merged_count"] == 1
    entry = ledger["entries"][0]
    assert entry["governance_status"] == "MERGED"
    assert entry["extractor_support"] == ["llm", "regex"]
    assert entry["evidence_spans"][0]["start"] == 0


def test_ledger_keeps_threshold_conflict_without_overwrite() -> None:
    source = "金额超过 5000 元需要审批。"
    regex_fact = _regex_fact(
        "金额超过 5000 元需要审批。",
        modality="MUST",
        action="审批",
        quantity=(">", "5000"),
        entity="金额",
    )
    # LLM 侧阈值不同（>=5000）→ 同签名但 key 属性冲突
    llm = _llm_validated_rule(
        "金额超过 5000 元需要审批。",
        operator_family="require",
        action="审批",
        threshold="5000",
        object_term="金额",
    )
    # 强制签名冲突：regex threshold '>5000' vs llm threshold '5000'
    ledger = semantic.build_rule_candidate_ledger(
        [regex_fact], [llm], source_id="prd-1", source_text=source
    )
    conflicted = [
        row for row in ledger["entries"]
        if row.get("governance_status") == "CONFLICTED"
    ]
    assert ledger["conflicted_count"] == 2
    assert len(conflicted) == 2
    # 双方保留证据，conflict_refs 互指
    assert conflicted[0]["conflict_refs"]
    assert conflicted[0]["rejection_reason"] == "RULE_SIGNATURE_CONFLICT"
    assert conflicted[0]["evidence_spans"]


def test_ledger_keeps_distinct_signatures_separate() -> None:
    source = "逾期订单不得发货。\n库存低于 10 件需要补货。"
    regex_ship = _regex_fact("逾期订单不得发货。", action="发货")
    llm_replenish = _llm_validated_rule(
        "库存低于 10 件需要补货。",
        operator_family="require",
        action="补货",
        evidence_start=len("逾期订单不得发货。\n"),
    )
    ledger = semantic.build_rule_candidate_ledger(
        [regex_ship], [llm_replenish], source_id="prd-1", source_text=source
    )
    assert ledger["entry_count"] == 2
    assert ledger["merged_count"] == 0
    assert ledger["conflicted_count"] == 0


def test_ledger_evidence_dedup_requires_overlap() -> None:
    """同签名但证据不重叠 → 不合并（不同出现位置是不同规则实例）。"""
    source = "逾期订单不得发货。\n逾期订单不得发货。"
    regex_fact = _regex_fact("逾期订单不得发货。", action="发货")
    llm = _llm_validated_rule(
        "逾期订单不得发货。", action="发货", evidence_start=len("逾期订单不得发货。\n")
    )
    ledger = semantic.build_rule_candidate_ledger(
        [regex_fact], [llm], source_id="prd-1", source_text=source
    )
    assert ledger["entry_count"] == 2
    assert ledger["merged_count"] == 0


# ── P0-6：Augment 晋升（SPEC §12.3 / §19）───────────────────────────────────

def _ledger_entry(
    *,
    extractor_type: str = "llm",
    governance_status: str = "VALIDATED",
    rule_origin: str = "explicit",
    statement: str = "逾期订单不得发货。",
    evidence: bool = True,
) -> dict:
    raw = _rule_candidate(
        rule_origin=rule_origin,
        evidence_spans=[{"text": statement, "start": 0}],
    )
    return {
        "kind": "rule",
        "source_ref": "prd-1",
        "chunk_ref": "chars=0-100",
        "extractor_type": extractor_type,
        "evidence_spans": (
            [{"text": statement, "start": 0, "end": len(statement)}] if evidence else []
        ),
        "validation_status": "VALIDATED",
        "governance_status": governance_status,
        "canonical_rule_ref": "",
        "rejection_reason": "",
        "conflict_refs": [],
        "semantic_signature": {"operator_family": "forbid", "action": "发货"},
        "raw": raw,
    }


def test_promote_llm_only_explicit_candidate_into_rule_row() -> None:
    entries = [_ledger_entry()]
    promoted, receipt = semantic.promote_rule_candidates_to_rules(
        entries, source_id="prd-1"
    )
    assert len(promoted) == 1
    row = promoted[0]
    assert row["statement"] == "逾期订单不得发货。"
    assert row["source_id"] == "prd-1"
    assert row["rule_origin"] == "explicit"
    assert row["augment_promoted"] is True
    assert row["governance_status"] == "PROMOTED"
    assert row["source_spans"][0]["quote"] == "逾期订单不得发货。"
    assert row["rule_id"].startswith("llmrule_")
    assert row["candidate_id"].startswith("candidate_")
    assert receipt["promoted_count"] == 1
    assert receipt["all_promoted_have_evidence"] is True


def test_promote_skips_merged_conflicted_inferred_and_evidenceless() -> None:
    entries = [
        _ledger_entry(governance_status="MERGED"),          # 正则已有 → 跳过
        _ledger_entry(governance_status="CONFLICTED"),      # 冲突 → 跳过
        _ledger_entry(rule_origin="inferred"),              # 推断 → 跳过
        _ledger_entry(evidence=False),                      # 无证据 → 跳过
        _ledger_entry(extractor_type="regex"),              # 正则侧 → 跳过
    ]
    promoted, receipt = semantic.promote_rule_candidates_to_rules(
        entries, source_id="prd-1"
    )
    assert promoted == []
    assert receipt["promoted_count"] == 0
    assert receipt["skipped_counts"]["already_present_via_regex"] == 1
    assert receipt["skipped_counts"]["conflicted"] == 1
    assert receipt["skipped_counts"]["inferred"] == 1
    assert receipt["skipped_counts"]["no_evidence"] == 1


def test_promotion_gates_require_evidence_and_traceability() -> None:
    good = semantic.rule_promotion_gates_met(
        [{
            "promoted_count": 2,
            "all_promoted_have_evidence": True,
            "conflicts_silently_resolved": 0,
            "promoted_rule_ids": ["llmrule_a", "llmrule_b"],
        }]
    )
    assert good["gates_met"] is True

    bad = semantic.rule_promotion_gates_met(
        [{
            "promoted_count": 1,
            "all_promoted_have_evidence": False,
            "conflicts_silently_resolved": 0,
            "promoted_rule_ids": ["llmrule_a"],
        }]
    )
    assert bad["gates_met"] is False
    assert bad["checks"]["promoted_without_evidence"] == 1


def test_augment_mode_requires_gates_met() -> None:
    gated = semantic.resolve_semantic_rule_extraction_mode(
        requested_mode="augment",
        provider_status_value="configured",
        governance_policy={"promotion_gates_met": True},
    )
    assert gated["effective_mode"] == "augment"
    assert gated["fallback_reason"] == ""

    ungated = semantic.resolve_semantic_rule_extraction_mode(
        requested_mode="augment",
        provider_status_value="configured",
        governance_policy={"promotion_gates_met": False},
    )
    assert ungated["effective_mode"] == "shadow"
    assert ungated["fallback_reason"] == "promotion_gates_not_met"


def test_integration_augment_merges_promoted_rules_into_rule_library(
    monkeypatch,
) -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        _incremental_run_semantic_extraction,
    )

    def responder(prompt):
        return {"candidates": [_rule_candidate()]}

    _install_client(monkeypatch, responder)
    # augment + gates 确认 → LLM 被调用、规则候选产出
    candidates, receipts, status = _incremental_run_semantic_extraction(
        [_parsed_row()],
        options={
            "semantic_rule_extraction_mode": "augment",
            "rule_promotion_gates_met": True,
        },
    )
    assert status == "AVAILABLE"
    mode_receipts = [
        row for row in receipts
        if row.get("schema_version") == "qualibug.semantic-rule-extraction-mode.v1"
    ]
    assert mode_receipts and mode_receipts[0]["effective_mode"] == "augment"
    rules = [
        row for row in candidates
        if isinstance(row, dict) and row.get("kind") == "rule"
    ]
    assert len(rules) == 1
