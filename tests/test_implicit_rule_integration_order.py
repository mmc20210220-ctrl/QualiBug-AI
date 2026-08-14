from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import integration
from ai_test_asset_center.enterprise_knowledge_center import (
    _chinese_business_comprehension,
    _chinese_business_conflicts,
    _chinese_document_context,
    _document_ir_context,
    _document_ir_fact_evidence,
    implicit_rule_projection,
)


def test_rule_projection_runs_after_fact_conflicts_and_before_model(monkeypatch):
    order = []

    # The integration package facade loads its implementation into
    # ``integration._legacy`` (a distinct module instance). Patching the facade
    # itself is a silent no-op; the observable call sites must be patched there.
    legacy = integration._legacy
    monkeypatch.setattr(
        legacy,
        "_attach_document_structure_assets",
        lambda asset, rows: order.append("structure"),
    )
    monkeypatch.setattr(
        _chinese_business_comprehension,
        "build_chinese_first_comprehension",
        lambda asset, rows: order.append("facts") or asset,
    )
    monkeypatch.setattr(
        _document_ir_fact_evidence,
        "align_business_facts_to_document_ir",
        lambda asset, rows: order.append("evidence") or asset,
    )
    monkeypatch.setattr(
        _document_ir_context,
        "apply_document_ir_context",
        lambda asset, rows: order.append("ir_context") or asset,
    )
    monkeypatch.setattr(
        _chinese_document_context,
        "apply_chinese_document_context",
        lambda asset, rows: order.append("text_context") or asset,
    )
    monkeypatch.setattr(
        _chinese_business_conflicts,
        "reconcile_chinese_business_fact_conflicts",
        lambda asset: order.append("conflicts") or asset,
    )
    monkeypatch.setattr(
        implicit_rule_projection,
        "enrich_asset_with_implicit_rule_projection",
        lambda asset: order.append("implicit_rules") or asset,
    )

    def build_model(asset):
        order.append("model")
        return {
            "model_id": "model:test",
            "gate": {
                "status": "PASS",
                "entry_allowed": True,
                "critical_unknowns": [],
                "unresolved_conflicts": [],
            },
            "metrics": {},
            "business_objects": [],
            "actors": [],
            "operations": [],
            "object_relations": [],
            "lifecycles": [],
            "processes": [],
            "unknowns": [],
        }

    monkeypatch.setattr(legacy, "build_enterprise_understanding_model", build_model)
    monkeypatch.setattr(
        legacy,
        "apply_minimum_understanding_closure",
        lambda model, asset: model,
    )
    monkeypatch.setattr(
        legacy,
        "project_final_scenario_planning_gate",
        lambda asset, model: None,
    )

    integration.enrich_asset_with_enterprise_understanding(
        {
            "coverage_gaps": [],
            "summary": {},
            "governance": {},
            "enterprise_comprehension_gate": {
                "status": "PASS",
                "entry_allowed": True,
            },
        },
        parsed_sources=[],
    )

    assert order.index("conflicts") < order.index("implicit_rules")
    assert order.index("implicit_rules") < order.index("model")
