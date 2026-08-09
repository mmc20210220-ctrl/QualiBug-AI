# -*- coding: utf-8 -*-
"""Unit tests for finding_source_contract: source-contract + runtime-evidence
paragraphs on delivered findings.

Synthetic industry-neutral data only (no benchmark vocabulary).

Evidence paragraphs are stored on dedicated fields (``contract_evidence`` /
``runtime_observation``) — never appended to ``description``/``title`` — so the
delivery gate's ``finding_payload_fingerprint`` (bound to the description/title
payload at gate-build time) stays stable across later enrichment passes.
"""
import pytest

from ai_test_asset_center.finding_source_contract import (
    attach_evidence_paragraphs,
    build_rule_statement_index,
    collect_experiment_rule_statements,
    enrich_governed_result,
    resolve_source_ref_statements,
)


def _finding(**overrides):
    finding = {
        "title": "[ContractOracle] domain_rule: operator PUT /v1/items/{id}",
        "description": "control=admin succeeded; treatment=operator violated the typed assertion",
        "reproduction": {
            "actor": "operator",
            "method": "PUT",
            "path": "/v1/items/a1",
            "reproduction_steps": ["PUT /v1/items/a1 -> HTTP 200"],
        },
        "evidence": {"actor": "operator", "control_succeeded": True},
        "failed_assertions": [{"kind": "domain_rule"}],
        "raw_evidence": {
            "response_raw": {"status_code": 200, "body": {"accepted": True}}
        },
    }
    finding.update(overrides)
    return finding


def test_all_bound_rule_statements_are_carried():
    exp = {
        "assertions": [
            {"property": {"expression": {"raw": "库存不得为负"}}},
            {"property": {"expression": {"raw": "同一幂等键不得重复扣款"}}},
            {"property": {"expression": {"raw": "同一幂等键不得重复扣款"}}},  # dup
            {"property": {"description": "金额关系必须保持一致"}},
        ]
    }
    statements = collect_experiment_rule_statements(exp)
    assert statements == ["库存不得为负", "同一幂等键不得重复扣款", "金额关系必须保持一致"]


def test_paragraphs_stored_on_dedicated_fields_description_untouched():
    finding = _finding()
    out = attach_evidence_paragraphs(finding, statements=["库存不得为负"])
    # Title and description are the fingerprinted payload: byte-identical.
    assert out["description"] == finding["description"]
    assert out["title"] == finding["title"]
    # Evidence paragraphs live on dedicated fields.
    assert "源契约: 库存不得为负" in out["contract_evidence"]
    assert "运行时证据: " in out["runtime_observation"]
    assert "角色 operator 对照实验" in out["runtime_observation"]
    assert "PUT /v1/items/a1" in out["runtime_observation"]
    assert "control=成功" in out["runtime_observation"]
    assert "复现: PUT /v1/items/a1 -> HTTP 200" in out["runtime_observation"]
    assert "观察HTTP状态=200" in out["runtime_observation"]


def test_exp_derived_statements_without_explicit_list():
    exp = {"assertions": [{"property": {"expression": {"raw": "订单必须处于待支付状态"}}}]}
    out = attach_evidence_paragraphs(_finding(), exp=exp)
    assert "源契约: 订单必须处于待支付状态" in out["contract_evidence"]
    assert "运行时证据: " in out["runtime_observation"]
    assert out["description"] == _finding()["description"]


def test_no_rules_no_injection():
    # no bound rules -> no contract_evidence; runtime evidence still present
    finding = _finding(description="")
    out = attach_evidence_paragraphs(finding, statements=[])
    assert not out.get("contract_evidence")
    assert "运行时证据: " in out["runtime_observation"]
    # no runtime evidence at all -> completely unchanged
    bare = {"title": "t", "description": "nothing observed"}
    out_bare = attach_evidence_paragraphs(bare, statements=[])
    assert out_bare == bare


def test_idempotent_merge():
    finding = _finding()
    once = attach_evidence_paragraphs(finding, statements=["规则一"])
    twice = attach_evidence_paragraphs(once, statements=["规则二"])
    assert twice["contract_evidence"].count("源契约:") == 1
    assert "规则一" in twice["contract_evidence"]
    assert "规则二" in twice["contract_evidence"]
    assert twice["runtime_observation"].count("运行时证据:") == 1
    assert twice["description"] == finding["description"]
    # Re-applying the same statements is a no-op (byte-identical finding).
    assert attach_evidence_paragraphs(twice, statements=["规则一"]) == twice


def test_governed_result_enrichment():
    governed = {"findings": [_finding(), _finding()]}
    out = enrich_governed_result(
        governed,
        exp={"assertions": [{"property": {"expression": {"raw": "必须保持守恒"}}}]},
    )
    assert len(out["findings"]) == 2
    for row in out["findings"]:
        assert "源契约: 必须保持守恒" in row["contract_evidence"]
        assert "运行时证据: " in row["runtime_observation"]
        assert row["description"] == _finding()["description"]


def test_asset_statement_index_and_source_ref_resolution():
    asset = {
        "permission_matrix": [
            {
                "role": "operator",
                "evidence": "operator 只能修改自己负责的对象",
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:1",
                "statement": "对象余额不得为负",
                "source_locator": "PUT /v1/items/{id}",
            }
        ],
    }
    index = build_rule_statement_index(asset)
    statements = resolve_source_ref_statements(
        [
            {"kind": "permission_matrix", "locator": "operator"},
            {"kind": "api_operation", "locator": "PUT /v1/items/a1"},
        ],
        index,
    )
    assert "operator 只能修改自己负责的对象" in statements
    assert "对象余额不得为负" in statements


def test_no_fabrication_for_unbound_obligations():
    asset = {"permission_matrix": [], "rule_library": []}
    index = build_rule_statement_index(asset)
    statements = resolve_source_ref_statements(
        [{"kind": "api_operation", "locator": "PUT /v1/other"}], index
    )
    assert statements == []
    out = attach_evidence_paragraphs(_finding(), statements=[])
    assert not out.get("contract_evidence")


def test_legacy_description_paragraphs_kept_and_merged_into_fields():
    # Pre-fix persisted findings carry the paragraphs inside description and
    # their gate fingerprint includes that text: the fixed injector must leave
    # description byte-identical and only merge new statements into the fields.
    finding = _finding()
    finding["description"] = (
        "control=admin succeeded; treatment=operator violated the typed assertion\n"
        "源契约: 旧规则一\n"
        "运行时证据: 角色 operator 对照实验"
    )
    out = attach_evidence_paragraphs(finding, statements=["新规则二"])
    assert out["description"] == finding["description"]
    assert "源契约: 旧规则一; 新规则二" == out["contract_evidence"]
    # Legacy 运行时证据 paragraph stays in description; no duplicate field.
    assert not out.get("runtime_observation")
