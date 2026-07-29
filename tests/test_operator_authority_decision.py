from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_authority_decision import (
    ACTION_LEAVE_UNRESOLVED,
    ACTION_SELECT_FACT,
    apply_authority_decisions_to_conflicts,
    record_operator_authority_decision,
    save_authority_decision_ledger,
)
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_conflicts import (
    reconcile_chinese_business_fact_conflicts,
)
from ai_test_asset_center.enterprise_knowledge_center._common import _write_json
from ai_test_asset_center.enterprise_knowledge_center._utils import _paths


def _fact(
    fact_id: str,
    *,
    source_id: str,
    modality: str = "MUST_NOT",
) -> dict:
    return {
        "fact_id": fact_id,
        "kind": "RULE",
        "status": "ACCEPTED",
        "subject": {
            "entity_refs": ["采购申请"],
            "actor_refs": ["普通用户"],
        },
        "conditions": ["已提交"],
        "action": {"canonical": "修改"},
        "scope": {"ownership": "本人"},
        "modality": modality,
        "raw_statement": f"{source_id}:{modality}",
        "state_effects": [],
        "source_spans": [
            {
                "source_id": source_id,
                "locator": f"{source_id}.md#section=规则",
                "quote": f"{source_id}:{modality}",
                "quote_hash": f"hash-{fact_id}",
            }
        ],
    }


def _rule(fact: dict) -> dict:
    return {
        "rule_id": f"rule:{fact['fact_id']}",
        "statement": fact["raw_statement"],
        "derivation": "chinese_first_business_comprehension",
        "semantic_contract": fact,
    }


def _asset(facts: list[dict], *, project_id: str = "authority-demo") -> dict:
    return {
        "project_id": project_id,
        "business_fact_ledger": {"items": facts},
        "rule_library": [_rule(fact) for fact in facts],
        "cross_document_conflicts": [],
        "enterprise_comprehension_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
        "coverage_gaps": [],
        "summary": {},
        "enterprise_understanding_model": {
            "conflicts": [],
            "unknowns": [],
            "business_objects": [],
            "actors": [],
            "operations": [],
            "object_relations": [],
            "lifecycles": [],
            "processes": [],
            "business_behaviors": [],
            "gate": {},
        },
    }


def _persist_asset(asset: dict, project: str, root: Path) -> None:
    paths = _paths(project, root)
    paths["asset"].parent.mkdir(parents=True, exist_ok=True)
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["asset"], asset)


def test_select_fact_resolves_conflict_and_restores_winner_only(tmp_path: Path) -> None:
    project = "authority-select"
    facts = [
        _fact("fact-deny", source_id="policy_v1", modality="MUST_NOT"),
        _fact("fact-allow", source_id="policy_v2", modality="MAY"),
    ]
    asset = reconcile_chinese_business_fact_conflicts(
        _asset(facts, project_id=project),
        project_id=project,
        root=tmp_path,
    )
    conflict = asset["cross_document_conflicts"][0]
    assert conflict["status"] == "UNRESOLVED"
    assert asset["enterprise_comprehension_gate"]["entry_allowed"] is False

    _persist_asset(asset, project, tmp_path)
    result = record_operator_authority_decision(
        project,
        conflict_id=conflict["conflict_id"],
        action=ACTION_SELECT_FACT,
        selected_fact_id="fact-deny",
        actor={"name": "ops-alice", "role": "qa_lead"},
        rationale="operator chose policy_v1 as the governing source",
        root=tmp_path,
        rebuild=False,
    )
    assert result["decision"]["selected_fact_id"] == "fact-deny"
    assert result["audit_receipt"]["actor"]["name"] == "ops-alice"
    assert result["decision"]["rationale"]

    refreshed = result["conflict"]
    assert refreshed is not None
    assert refreshed["status"] == "RESOLVED"
    assert refreshed["authority_decision"]["status"] == "RESOLVED"
    assert refreshed["authority_decision"]["selected_fact_id"] == "fact-deny"
    assert refreshed["authority_decision"]["automatic_resolution_allowed"] is False
    assert result["comprehension_gate"]["entry_allowed"] is True
    assert result["comprehension_gate"]["unresolved_business_fact_conflicts"] == []

    # Reload via reconcile to prove ledger is honored on recompute.
    asset2 = reconcile_chinese_business_fact_conflicts(
        _asset(facts, project_id=project),
        project_id=project,
        root=tmp_path,
    )
    resolved = asset2["cross_document_conflicts"][0]
    assert resolved["status"] == "RESOLVED"
    by_id = {
        row["fact_id"]: row
        for row in asset2["business_fact_ledger"]["items"]
    }
    assert by_id["fact-deny"]["status"] == "ACCEPTED"
    assert by_id["fact-allow"]["status"] == "SUPERSEDED"
    assert any(
        row.get("semantic_contract", {}).get("fact_id") == "fact-deny"
        for row in asset2["rule_library"]
    )
    assert not any(
        row.get("semantic_contract", {}).get("fact_id") == "fact-allow"
        for row in asset2["rule_library"]
    )
    assert asset2["enterprise_comprehension_gate"]["entry_allowed"] is True


def test_leave_unresolved_stays_blocked(tmp_path: Path) -> None:
    project = "authority-leave"
    facts = [
        _fact("fact-a", source_id="doc-a", modality="MUST_NOT"),
        _fact("fact-b", source_id="doc-b", modality="MAY"),
    ]
    asset = reconcile_chinese_business_fact_conflicts(
        _asset(facts, project_id=project),
        project_id=project,
        root=tmp_path,
    )
    conflict_id = asset["cross_document_conflicts"][0]["conflict_id"]
    _persist_asset(asset, project, tmp_path)

    result = record_operator_authority_decision(
        project,
        conflict_id=conflict_id,
        action=ACTION_LEAVE_UNRESOLVED,
        actor={"name": "ops-bob", "role": "knowledge_admin"},
        root=tmp_path,
        rebuild=False,
    )
    assert result["decision"]["status"] == "UNRESOLVED"
    assert result["conflict"]["status"] == "UNRESOLVED"
    assert result["conflict"]["authority_decision"]["explicit_leave_unresolved"] is True
    assert result["comprehension_gate"]["entry_allowed"] is False
    assert (
        result["comprehension_gate"]["status"]
        == "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS"
    )

    refreshed = reconcile_chinese_business_fact_conflicts(
        _asset(facts, project_id=project),
        project_id=project,
        root=tmp_path,
    )
    assert refreshed["cross_document_conflicts"][0]["status"] == "UNRESOLVED"
    assert refreshed["enterprise_comprehension_gate"]["entry_allowed"] is False
    assert (
        refreshed["cross_document_conflicts"][0]["authority_decision"][
            "explicit_leave_unresolved"
        ]
        is True
    )


def test_never_auto_picks_by_confidence_or_recency(tmp_path: Path) -> None:
    project = "authority-no-auto"
    facts = [
        {
            **_fact("old-low", source_id="old", modality="MUST_NOT"),
            "confidence": 0.1,
            "updated_at": "2010-01-01T00:00:00Z",
        },
        {
            **_fact("new-high", source_id="new", modality="MAY"),
            "confidence": 0.99,
            "updated_at": "2035-01-01T00:00:00Z",
        },
    ]
    asset = reconcile_chinese_business_fact_conflicts(
        _asset(facts, project_id=project),
        project_id=project,
        root=tmp_path,
    )
    conflict = asset["cross_document_conflicts"][0]
    assert conflict["authority_decision"]["selected_fact_id"] == ""
    assert conflict["status"] == "UNRESOLVED"


def test_participant_drift_fails_closed(tmp_path: Path) -> None:
    project = "authority-drift"
    facts = [
        _fact("fact-1", source_id="s1", modality="MUST_NOT"),
        _fact("fact-2", source_id="s2", modality="MAY"),
    ]
    asset = reconcile_chinese_business_fact_conflicts(
        _asset(facts, project_id=project),
        project_id=project,
        root=tmp_path,
    )
    conflict = asset["cross_document_conflicts"][0]
    save_authority_decision_ledger(
        {
            "schema": "qualibug.operator-authority-decision-ledger.v1",
            "project_id": project,
            "decisions": [
                {
                    "schema": "qualibug.operator-authority-decision.v1",
                    "decision_id": "decision:stale",
                    "conflict_id": conflict["conflict_id"],
                    "action": ACTION_SELECT_FACT,
                    "selected_fact_id": "fact-1",
                    "participant_fact_ids": ["fact-1", "fact-999"],
                    "participant_fingerprint": "stale",
                    "actor": {"name": "ops", "role": "admin", "tenant_id": ""},
                    "decided_at_utc": "2020-01-01T00:00:00Z",
                    "rationale": "",
                    "audit_receipt_id": "audit:stale",
                }
            ],
            "audit_receipts": [],
        },
        project,
        tmp_path,
    )
    drifted = apply_authority_decisions_to_conflicts(
        asset,
        project_id=project,
        root=tmp_path,
    )
    assert drifted["cross_document_conflicts"][0]["status"] == "UNRESOLVED"
    assert drifted["enterprise_comprehension_gate"]["entry_allowed"] is False


def test_field_required_mismatch_select_fact_no_auto_pick(tmp_path: Path) -> None:
    from ai_test_asset_center.enterprise_knowledge_center._api import (
        _detect_cross_document_conflicts,
    )
    from ai_test_asset_center.enterprise_knowledge_center._chinese_business_conflicts import (
        TECHNICAL_CONFLICT_SCHEMA,
    )

    conflicts = _detect_cross_document_conflicts(
        [
            {
                "field": "warehouse_id",
                "table": "orders",
                "required": True,
                "source_id": "schema_a.sql",
                "field_id": "a",
            },
            {
                "field": "warehouse_id",
                "table": "orders",
                "required": False,
                "source_id": "schema_b.sql",
                "field_id": "b",
            },
        ],
        [],
        [],
        [],
    )
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["schema"] == TECHNICAL_CONFLICT_SCHEMA
    assert conflict["kind"] == "FIELD_REQUIRED_MISMATCH"
    assert conflict["authority_decision"]["automatic_resolution_allowed"] is False
    assert conflict["authority_decision"]["selected_fact_id"] == ""
    participants = sorted(
        row["fact_id"] for row in conflict["facts"] if row.get("fact_id")
    )
    assert len(participants) == 2

    project = "tech-field-auth"
    asset = {
        "project_id": project,
        "business_fact_ledger": {"items": []},
        "rule_library": [],
        "cross_document_conflicts": conflicts,
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "summary": {},
        "enterprise_understanding_model": {"conflicts": [], "gate": {}},
    }
    applied = apply_authority_decisions_to_conflicts(
        asset, project_id=project, root=tmp_path
    )
    assert applied["cross_document_conflicts"][0]["status"] == "UNRESOLVED"
    assert applied["enterprise_comprehension_gate"]["entry_allowed"] is False

    _persist_asset(applied, project, tmp_path)
    result = record_operator_authority_decision(
        project,
        conflict_id=conflict["conflict_id"],
        action=ACTION_SELECT_FACT,
        selected_fact_id=participants[0],
        actor={"name": "ops-tech", "role": "qa_lead"},
        rationale="operator chose required=true source",
        root=tmp_path,
        rebuild=False,
    )
    assert result["conflict"]["status"] == "RESOLVED"
    assert result["conflict"]["authority_decision"]["selected_fact_id"] == participants[0]
    assert result["conflict"]["authority_decision"]["automatic_resolution_allowed"] is False
    assert result["comprehension_gate"]["entry_allowed"] is True


def test_term_alias_identity_conflict_select_fact(tmp_path: Path) -> None:
    project = "term-alias-auth"
    facts = [
        {
            "fact_id": "alias-a",
            "kind": "TERM_ALIAS",
            "status": "ACCEPTED",
            "alias": "SO",
            "canonical_term": "销售订单",
            "raw_statement": "SO：销售订单",
            "source_spans": [
                {
                    "source_id": "glossary_a.md",
                    "locator": "glossary_a.md#L1",
                    "quote": "SO：销售订单",
                    "quote_hash": "h1",
                }
            ],
        },
        {
            "fact_id": "alias-b",
            "kind": "TERM_ALIAS",
            "status": "ACCEPTED",
            "alias": "SO",
            "canonical_term": "服务工单",
            "raw_statement": "SO：服务工单",
            "source_spans": [
                {
                    "source_id": "glossary_b.md",
                    "locator": "glossary_b.md#L1",
                    "quote": "SO：服务工单",
                    "quote_hash": "h2",
                }
            ],
        },
    ]
    asset = reconcile_chinese_business_fact_conflicts(
        _asset(facts, project_id=project),
        project_id=project,
        root=tmp_path,
    )
    alias_conflicts = [
        row
        for row in asset["cross_document_conflicts"]
        if row.get("kind") == "TERM_ALIAS_IDENTITY_CONFLICT"
    ]
    assert len(alias_conflicts) == 1
    conflict = alias_conflicts[0]
    assert conflict["authority_decision"]["automatic_resolution_allowed"] is False
    assert conflict["status"] == "UNRESOLVED"
    assert asset["enterprise_comprehension_gate"]["entry_allowed"] is False

    _persist_asset(asset, project, tmp_path)
    result = record_operator_authority_decision(
        project,
        conflict_id=conflict["conflict_id"],
        action=ACTION_SELECT_FACT,
        selected_fact_id="alias-a",
        actor={"name": "ops-alias", "role": "knowledge_admin"},
        rationale="glossary_a is the governing terminology source",
        root=tmp_path,
        rebuild=False,
    )
    assert result["conflict"]["status"] == "RESOLVED"
    assert result["conflict"]["authority_decision"]["selected_fact_id"] == "alias-a"

    refreshed = reconcile_chinese_business_fact_conflicts(
        _asset(facts, project_id=project),
        project_id=project,
        root=tmp_path,
    )
    by_id = {row["fact_id"]: row for row in refreshed["business_fact_ledger"]["items"]}
    assert by_id["alias-a"]["status"] == "ACCEPTED"
    assert by_id["alias-b"]["status"] == "SUPERSEDED"
    assert refreshed["enterprise_comprehension_gate"]["entry_allowed"] is True


def test_term_alias_leave_unresolved_stays_blocked(tmp_path: Path) -> None:
    project = "term-alias-leave"
    facts = [
        {
            "fact_id": "alias-1",
            "kind": "TERM_ALIAS",
            "status": "ACCEPTED",
            "alias": "PO",
            "canonical_term": "采购订单",
            "raw_statement": "PO：采购订单",
            "source_spans": [
                {
                    "source_id": "a.md",
                    "locator": "a.md#1",
                    "quote": "PO：采购订单",
                    "quote_hash": "x1",
                }
            ],
        },
        {
            "fact_id": "alias-2",
            "kind": "TERM_ALIAS",
            "status": "ACCEPTED",
            "alias": "PO",
            "canonical_term": "生产订单",
            "raw_statement": "PO：生产订单",
            "source_spans": [
                {
                    "source_id": "b.md",
                    "locator": "b.md#1",
                    "quote": "PO：生产订单",
                    "quote_hash": "x2",
                }
            ],
        },
    ]
    asset = reconcile_chinese_business_fact_conflicts(
        _asset(facts, project_id=project),
        project_id=project,
        root=tmp_path,
    )
    conflict = next(
        row
        for row in asset["cross_document_conflicts"]
        if row.get("kind") == "TERM_ALIAS_IDENTITY_CONFLICT"
    )
    _persist_asset(asset, project, tmp_path)
    result = record_operator_authority_decision(
        project,
        conflict_id=conflict["conflict_id"],
        action=ACTION_LEAVE_UNRESOLVED,
        actor={"name": "ops-leave", "role": "admin"},
        root=tmp_path,
        rebuild=False,
    )
    assert result["conflict"]["status"] == "UNRESOLVED"
    assert result["conflict"]["authority_decision"]["explicit_leave_unresolved"] is True
    assert result["comprehension_gate"]["entry_allowed"] is False