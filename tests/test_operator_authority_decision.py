from __future__ import annotations

from pathlib import Path
import runpy

_LEGACY_TARGET = "test_field_required_mismatch_select_fact_no_auto_pick"
_legacy = runpy.run_path(
    str(Path(__file__).with_name("_operator_authority_decision_legacy.py"))
)
for _name, _value in _legacy.items():
    if _name.startswith("test_") and _name != _LEGACY_TARGET:
        globals()[_name] = _value

ACTION_SELECT_FACT = _legacy["ACTION_SELECT_FACT"]
apply_authority_decisions_to_conflicts = _legacy[
    "apply_authority_decisions_to_conflicts"
]
record_operator_authority_decision = _legacy["record_operator_authority_decision"]
_persist_asset = _legacy["_persist_asset"]


def test_field_required_mismatch_select_fact_no_auto_pick(tmp_path: Path) -> None:
    from ai_test_asset_center.enterprise_knowledge_center._api import (
        _detect_cross_document_conflicts,
    )
    from ai_test_asset_center.enterprise_knowledge_center._chinese_business_conflicts import (
        TECHNICAL_CONFLICT_SCHEMA,
    )
    from ai_test_asset_center.enterprise_knowledge_center._parsing import (
        _field_dictionary_entries,
    )

    required_rows = _field_dictionary_entries(
        '{"fields":[{"table":"orders","field":"warehouse_id","required":true}]}',
        {
            "fields": [
                {"table": "orders", "field": "warehouse_id", "required": True}
            ]
        },
        "schema_a.json",
    )
    optional_rows = _field_dictionary_entries(
        '{"fields":[{"table":"orders","field":"warehouse_id","required":false}]}',
        {
            "fields": [
                {"table": "orders", "field": "warehouse_id", "required": False}
            ]
        },
        "schema_b.json",
    )
    conflicts = _detect_cross_document_conflicts(
        [*required_rows, *optional_rows],
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

    expected_evidence = {
        "table=orders; field=warehouse_id; required=true",
        "table=orders; field=warehouse_id; required=false",
    }
    facts = conflict.get("facts") or []
    evidence = conflict.get("evidence") or []
    assert {row.get("normalized_evidence") for row in facts} == expected_evidence
    assert {row.get("normalized_evidence") for row in evidence} == expected_evidence
    assert all(
        row.get("evidence_kind") == "NORMALIZED_STRUCTURED_DECLARATION"
        for row in [*facts, *evidence]
    )
    assert all(
        row.get("evidence_derivation")
        == "normalized_field_dictionary_projection"
        for row in [*facts, *evidence]
    )
    assert all(not str(row.get("quote") or "").strip() for row in [*facts, *evidence])

    participants = sorted(row["fact_id"] for row in facts if row.get("fact_id"))
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
        rationale="operator chose one declared field contract",
        root=tmp_path,
        rebuild=False,
    )
    assert result["conflict"]["status"] == "RESOLVED"
    assert result["conflict"]["authority_decision"]["selected_fact_id"] == participants[0]
    assert result["conflict"]["authority_decision"]["automatic_resolution_allowed"] is False
    assert result["comprehension_gate"]["entry_allowed"] is True
