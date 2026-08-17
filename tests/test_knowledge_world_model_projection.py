"""Unit tests for the knowledge-asset -> Reasoner world-model projection.

The 11-engine Reasoner grounds hypotheses in a structured business world
(entities / documented_rules / state_machines / roles / relationships).  The
projection adapts the source-derived knowledge asset to that contract; these
tests pin the contract: source-grounded values, bounded sizes, deterministic
output, and never any content the asset does not carry.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest import mock

from ai_test_asset_center.enterprise_knowledge_center import (
    project_knowledge_world_model,
)


@contextmanager
def _env_patch(key: str, value: str) -> Any:
    with mock.patch.dict("os.environ", {key: value}):
        yield


def _asset() -> dict[str, Any]:
    return {
        "business_objects": [
            {
                "object": "orders",
                "source": "database_schema",
                "source_id": "src:db",
                "confidence": 0.9,
                "aliases": ["sales_orders"],
                "key_identifiers": ["order_id"],
                "key_business_fields": ["status", "amount"],
                "source_refs": [{"source_id": "src:db", "locator": "schema.orders"}],
            },
            {"object": "", "source": "database_schema", "confidence": 0.5},
            {"object": "users", "source": "api_spec", "confidence": 0.3},
            {"object": "balance", "source": "database_schema", "confidence": 0.7},
        ],
        "rule_library": [
            {
                "rule_id": "rule:src:1",
                "source_id": "src:prd",
                "source_locator": "PRD.md#订单",
                "statement": "订单支付前不得发货",
                "severity": "P0",
                "tokens": ["订单", "支付", "发货"],
                "operation_refs": ["op:ship-order"],
                "binding_readiness": "READY",
            },
            {
                "rule_id": "rule:src:2",
                "source_id": "src:prd",
                "source_locator": "PRD.md#退款",
                "statement": "退款金额不得超过实付金额",
                "severity": "P1",
                "tokens": ["退款", "金额"],
            },
            {
                "rule_id": "rule:src:3",
                "source_id": "src:api",
                "statement": "订单支付前不得发货",
                "severity": "P2",
            },
            {"rule_id": "rule:src:4", "source_id": "src:api", "statement": "", "severity": "P0"},
        ],
        "semantic_candidates": [
            {
                "candidate_id": "candidate_implicit_return_sequence",
                "kind": "rule",
                "name": "仓库验收入库",
                "rule_origin": "inferred",
                "candidate_status": "VALIDATED",
                "source_id": "src:prd",
                "source_locator": "PRD.md#退货流程",
                "verbatim_quote": "客服登记退货，仓库验收入库，财务原路退款。",
                "evidence_spans": [{
                    "text": "客服登记退货，仓库验收入库，财务原路退款。",
                    "start": 0,
                    "end": 24,
                }],
                "semantic_spans": {
                    "actor": [{"text": "仓库"}],
                    "object": [{"text": "退货"}],
                    "action": [{"text": "验收入库"}],
                },
                "suggested_rule_family": "cross_entity",
                "normalized_suggestion": {
                    "effect": {"operator_family": "sequence_hypothesis", "action": "验收入库"},
                },
            },
            {
                "candidate_id": "candidate_explicit_not_advisory",
                "kind": "rule",
                "name": "订单支付前不得发货",
                "rule_origin": "explicit",
                "candidate_status": "VALIDATED",
                "source_id": "src:prd",
                "verbatim_quote": "订单支付前不得发货",
                "evidence_spans": [{"text": "订单支付前不得发货"}],
            },
        ],
        "state_machines": [
            {
                "state_machine_id": "state:src:1",
                "source_id": "src:prd",
                "object": "order",
                "states": ["CREATED", "PAID", "SHIPPED"],
                "transitions": [
                    {"from": "CREATED", "to": "PAID", "trigger": "pay"},
                    {"from": "PAID", "to": "SHIPPED", "trigger": "ship"},
                ],
            }
        ],
        "roles": [
            {"role_id": "role:src:1", "source_id": "src:prd", "role": "buyer"},
            {"role_id": "role:src:2", "source_id": "src:prd", "role": "buyer"},
            {"role_id": "role:src:3", "source_id": "src:prd", "role": "seller"},
        ],
        "permission_matrix": [
            {
                "permission_id": "permission:buyer:list",
                "source_id": "src:prd",
                "source_locator": "PRD.md#权限",
                "role": "buyer",
                "interface_id": "op:list-orders",
                "action": "list",
                "resource": "orders",
                "decision": "allow",
                "scope": "own",
            }
        ],
        "cross_document_conflicts": [
            {
                "conflict_id": "conflict:shipping-state",
                "kind": "STATE_CONTRADICTION",
                "summary": "发货允许状态在两份资料中不一致",
                "source_refs": [
                    {"source_id": "src:prd", "locator": "PRD.md#发货"},
                    {"source_id": "src:api", "locator": "API.md#ship"},
                ],
            }
        ],
        "parse_coverage_gaps": [
            {
                "kind": "UNPARSED_VISUAL",
                "gap_type": "visual_semantics_unavailable",
                "source_id": "src:diagram",
            }
        ],
        "entity_relations": [
            {
                "from_entity": "orders",
                "to_entity": "users",
                "relation_type": "belongs_to",
                "source_id": "src:db",
            },
            {"from_entity": "", "to_entity": "users", "relation_type": "x", "source_id": "s"},
        ],
    }


def test_projection_carries_source_grounded_content() -> None:
    world = project_knowledge_world_model(_asset())

    assert world["insufficient_evidence"] is False
    # Duplicate statements are collapsed; empty statements dropped.
    assert len(world["documented_rules"]) == 2
    assert world["documented_rules"][0]["rule"] == "订单支付前不得发货"
    assert world["documented_rules"][0]["severity"] == "P0"
    assert world["documented_rules"][0]["source"] == "src:prd@PRD.md#订单"
    assert world["documented_rules"][0]["entities_involved"] == ["订单", "支付", "发货"]

    # Empty names are dropped; is_core follows confidence.
    assert len(world["entities"]) == 3
    by_name = {e["name"]: e for e in world["entities"]}
    assert by_name["orders"]["is_core"] is True
    assert by_name["users"]["is_core"] is False
    assert by_name["orders"]["aliases"] == ["sales_orders"]
    assert by_name["orders"]["key_identifiers"] == ["order_id"]
    assert by_name["orders"]["key_business_fields"] == ["status", "amount"]
    assert by_name["orders"]["source_refs"][0]["source_id"] == "src:db"

    assert world["documented_rules"][0]["is_verifiable"] is True
    assert world["documented_rules"][1]["is_verifiable"] is False

    # Inferred Chinese semantics are carried as explicitly unverified reasoning
    # hypotheses, never mixed into documented_rules or formal rule authority.
    assert len(world["semantic_hypotheses"]) == 1
    semantic_hypothesis = world["semantic_hypotheses"][0]
    assert semantic_hypothesis["candidate_id"] == "candidate_implicit_return_sequence"
    assert semantic_hypothesis["statement"] == "客服登记退货，仓库验收入库，财务原路退款。"
    assert semantic_hypothesis["authority"] == "UNVERIFIED_SEMANTIC_HYPOTHESIS"
    assert semantic_hypothesis["formal_rule_authority"] is False

    assert len(world["state_machines"]) == 1
    sm = world["state_machines"][0]
    assert sm["entity"] == "order"
    assert sm["transitions"][0]["trigger"] == "pay"

    # Roles are deduplicated by name.
    assert {r["name"] for r in world["roles"]} == {"buyer", "seller"}
    buyer = next(row for row in world["roles"] if row["name"] == "buyer")
    assert buyer["permissions"] == [{
        "permission_id": "permission:buyer:list",
        "operation_ref": "op:list-orders",
        "action": "list",
        "resource": "orders",
        "decision": "allow",
        "scope": "own",
        "source": "src:prd@PRD.md#权限",
    }]

    # Relationships drop rows without a complete triple.
    assert len(world["relationships"]) == 1
    assert world["relationships"][0]["relationship_type"] == "belongs_to"
    assert world["contradictions"][0]["conflict_id"] == "conflict:shipping-state"
    assert world["gaps"][0]["gap_type"] == "visual_semantics_unavailable"


def test_projection_is_bounded_and_empty_safe() -> None:
    world = project_knowledge_world_model(
        _asset(), max_rules=1, max_relationships=0, max_roles=1
    )
    assert len(world["documented_rules"]) == 1
    assert len(world["relationships"]) == 0
    assert len(world["roles"]) == 1

    empty = project_knowledge_world_model(None)
    assert empty["insufficient_evidence"] is True
    assert empty["documented_rules"] == []
    assert empty["entities"] == []
    assert empty["state_machines"] == []
    assert empty["semantic_hypotheses"] == []
    assert empty["gaps"] == []


def test_projection_receipt_reports_truncation_instead_of_silent_cap() -> None:
    # A caller-supplied explicit bound is used exactly; the truncation is
    # receipted with a named reason code, never silently discarded.
    world = project_knowledge_world_model(
        _asset(), max_rules=1, max_relationships=1, max_roles=1
    )
    receipt = world["projection_receipt"]
    assert receipt["schema_version"] == "qualibug.world-model-projection-receipt.v1"
    assert receipt["budgets"]["max_rules"] == 1
    assert receipt["budgets"]["max_relationships"] == 1
    assert receipt["budgets"]["max_roles"] == 1
    # 2 unique rules, 1 relationship, 2 unique roles in the fixture.
    assert receipt["counts"]["rules_total"] == 2
    assert receipt["counts"]["rules_projected"] == 1
    assert receipt["counts"]["relationships_total"] == 1
    assert receipt["counts"]["roles_total"] == 2
    assert receipt["counts"]["roles_projected"] == 1
    assert receipt["counts"]["semantic_hypotheses_total"] == 1
    assert receipt["counts"]["semantic_hypotheses_projected"] == 1
    assert "world_model_rules_truncated:1/2" in receipt["reason_codes"]
    assert "world_model_roles_truncated:1/2" in receipt["reason_codes"]


def test_projection_default_budget_raises_breadth_floor_over_legacy_cap() -> None:
    # The historical default of 40 rules was a code-level breadth ceiling. The
    # default now projects a strictly larger rule set, and the
    # receipt carries no truncation reason for this fixture.
    world = project_knowledge_world_model(_asset())
    receipt = world["projection_receipt"]
    assert receipt["budgets"]["max_rules"] >= 200
    assert receipt["counts"]["rules_projected"] == 2
    assert "world_model_rules_truncated" not in receipt["reason_codes"]


def test_projection_env_budget_cannot_narrow_below_floor() -> None:
    # An operator env override that would collapse comprehension below the
    # legacy baseline is floored, so breadth can never be silently narrowed.
    with _env_patch("QUALIBUG_WORLD_MODEL_MAX_RULES", "5"):
        world = project_knowledge_world_model(_asset())
    receipt = world["projection_receipt"]
    assert receipt["budgets"]["max_rules"] >= 40
    assert receipt["counts"]["rules_projected"] == 2


def test_semantic_hypothesis_budget_is_receipted_not_silent() -> None:
    asset = _asset()
    second = dict(asset["semantic_candidates"][0])
    second.update({
        "candidate_id": "candidate_implicit_second",
        "name": "第二条隐含语义",
        "verbatim_quote": "第二条来源锚定的隐含语义。",
        "evidence_spans": [{"text": "第二条来源锚定的隐含语义。"}],
    })
    asset["semantic_candidates"].append(second)

    world = project_knowledge_world_model(
        asset,
        max_semantic_hypotheses=1,
    )
    receipt = world["projection_receipt"]

    assert len(world["semantic_hypotheses"]) == 1
    assert receipt["counts"]["semantic_hypotheses_total"] == 2
    assert receipt["counts"]["semantic_hypotheses_projected"] == 1
    assert "world_model_semantic_hypotheses_truncated:1/2" in receipt["reason_codes"]


def test_projection_budgets_are_fair_across_enterprise_sources() -> None:
    """One verbose document must not erase every other source before reasoning."""
    asset = _asset()
    asset["rule_library"] = [
        {
            "rule_id": f"rule:verbose:{index}",
            "source_id": "src:verbose-prd",
            "statement": f"长文档显式规则 {index}",
            "severity": "P0",
        }
        for index in range(6)
    ] + [{
        "rule_id": "rule:short:1",
        "source_id": "src:short-api",
        "statement": "短文档显式规则",
        "severity": "P2",
    }]
    template = asset["semantic_candidates"][0]
    asset["semantic_candidates"] = [
        {
            **template,
            "candidate_id": f"candidate_verbose_{index}",
            "source_id": "src:verbose-prd",
            "verbatim_quote": f"长文档隐含语义 {index}",
            "evidence_spans": [{"text": f"长文档隐含语义 {index}"}],
        }
        for index in range(6)
    ] + [{
        **template,
        "candidate_id": "candidate_short_source",
        "source_id": "src:short-api",
        "verbatim_quote": "短文档隐含语义",
        "evidence_spans": [{"text": "短文档隐含语义"}],
    }]

    world = project_knowledge_world_model(
        asset,
        max_rules=2,
        max_semantic_hypotheses=2,
    )
    receipt = world["projection_receipt"]

    assert {row["source"].split("@", 1)[0] for row in world["documented_rules"]} == {
        "src:verbose-prd",
        "src:short-api",
    }
    assert {row["source"].split("@", 1)[0] for row in world["semantic_hypotheses"]} == {
        "src:verbose-prd",
        "src:short-api",
    }
    assert receipt["counts"]["rule_sources_total"] == 2
    assert receipt["counts"]["rule_sources_projected"] == 2
    assert receipt["counts"]["semantic_hypothesis_sources_total"] == 2
    assert receipt["counts"]["semantic_hypothesis_sources_projected"] == 2

    limited = project_knowledge_world_model(
        asset,
        max_rules=1,
        max_semantic_hypotheses=1,
    )["projection_receipt"]
    assert "world_model_rule_sources_truncated:1/2" in limited["reason_codes"]
    assert (
        "world_model_semantic_hypothesis_sources_truncated:1/2"
        in limited["reason_codes"]
    )
