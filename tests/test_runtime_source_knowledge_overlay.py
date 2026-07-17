from __future__ import annotations

import pytest

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center import (
    build_runtime_source_knowledge_overlay,
    merge_knowledge_asset_overlay,
)
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)


@pytest.mark.parametrize(
    ("project", "resource", "api_text", "schema_text"),
    [
        (
            "asset-lifecycle",
            "assets",
            """
### PATCH /assets/{id}
Asset state must follow the declared transition rule.
""",
            "CREATE TABLE assets (id TEXT PRIMARY KEY, state TEXT NOT NULL);",
        ),
        (
            "support-lifecycle",
            "cases",
            """
### POST /cases/{id}/close
Case status must follow the declared transition rule.
""",
            "CREATE TABLE cases (id TEXT PRIMARY KEY, status TEXT NOT NULL);",
        ),
    ],
)
def test_runtime_sources_use_one_domain_neutral_ir_and_obligation_path(
    project: str,
    resource: str,
    api_text: str,
    schema_text: str,
) -> None:
    overlay = build_runtime_source_knowledge_overlay(
        prd_text=f"# {resource} requirements\nThe operator must review every state change.",
        api_spec_text=api_text,
        db_schema_text=schema_text,
    )
    asset = merge_knowledge_asset_overlay({}, overlay)

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id=project,
        runtime_actors=[{
            "role": "operator",
            "account_ref": "operator-a",
            "secret_ref": "secret_ref:test_accounts:operator-a",
        }],
    )
    obligations = compile_obligations_from_behavior_ir(behavior_ir)

    assert any(entity.get("name") == resource for entity in behavior_ir["entities"])
    assert any(
        relation.get("source_relationship_ref")
        and relation.get("operation_ref")
        for relation in behavior_ir["relations"]
    )
    assert obligations["obligation_count"] > 0
    assert {source.get("source_type") for source in overlay["source_inventory"]} == {
        "prd",
        "markdown_api",
        "database_schema",
    }
    assert "ground_truth" not in str(overlay).lower()


def test_unbound_runtime_rule_is_preserved_as_stable_coverage_gap() -> None:
    overlay = build_runtime_source_knowledge_overlay(
        prd_text="A reviewer must approve every published record.",
        api_spec_text="### GET /records\nList records.",
        db_schema_text="CREATE TABLE records (id TEXT PRIMARY KEY);",
    )
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        merge_knowledge_asset_overlay({}, overlay),
        project_id="unbound-rule",
    )

    gap = next(
        row
        for row in behavior_ir["coverage_gaps"]
        if row.get("gap_type") == "source_invariant_operation_unbound"
    )
    assert gap["invariant_ref"]
    assert gap["source_rule_refs"]
