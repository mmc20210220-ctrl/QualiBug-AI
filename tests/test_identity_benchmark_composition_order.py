from __future__ import annotations

import ast
from pathlib import Path


def _named_calls(function: ast.FunctionDef) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            calls.append((target.id, node.lineno))
    return calls


def _understanding_builder() -> tuple[str, ast.FunctionDef]:
    source = Path(
        "ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/"
        "builder/__init__.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_enterprise_understanding_model"
    )
    return source, function


def test_identity_benchmark_repository_precedes_first_understanding_pass() -> None:
    source = Path(
        "ai_test_asset_center/enterprise_knowledge_center/composition.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_enterprise_business_knowledge_asset"
    )
    calls = _named_calls(function)
    repository_line = min(
        line for name, line in calls if name == "apply_identity_benchmark_repository"
    )
    understanding_lines = sorted(
        line for name, line in calls if name == "enrich_asset_with_enterprise_understanding"
    )

    assert len(understanding_lines) == 2
    assert repository_line < understanding_lines[0] < understanding_lines[1]
    assert (
        "identity_benchmark_repository_precedes_enterprise_understanding"
        in source
    )


def test_field_key_evidence_extends_the_single_identity_authority_before_measurement() -> None:
    source, function = _understanding_builder()
    calls = _named_calls(function)
    technical_line = next(
        line
        for name, line in calls
        if name == "augment_technical_identity_projection"
    )
    field_line = next(
        line for name, line in calls if name == "augment_identity_field_evidence"
    )
    authority_line = next(
        line for name, line in calls if name == "project_identity_authority_receipt"
    )
    manifest_line = next(
        line for name, line in calls if name == "project_identity_annotation_manifest"
    )
    benchmark_line = next(
        line for name, line in calls if name == "project_identity_benchmark"
    )

    assert technical_line < field_line < authority_line < manifest_line < benchmark_line
    assert source.count("augment_identity_field_evidence(") == 1
    assert 'model["identity_field_evidence"]' in source
    assert '"enterprise_identity_field_evidence"' in source


def test_key_backed_bound_field_can_surface_name_only_candidate_without_binding() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_field_evidence import (
        augment_identity_field_evidence,
    )

    asset = {
        "data_tables": [
            {
                "table_id": "table:orders",
                "columns": [
                    {
                        "column_id": "orders.order_id",
                        "name": "order_id",
                        "primary_key": True,
                        "identity_key_ref": "sales-order-id",
                    }
                ],
            }
        ],
        "interfaces": [
            {
                "interface_id": "api:legacy-order",
                "parameter_contracts": [
                    {
                        "field_id": "path.order_id",
                        "name": "order_id",
                        "is_identifier": True,
                    }
                ],
            }
        ],
    }
    result = {
        "bindings": [
            {
                "schema": "qualibug.enterprise-identity-binding.v1",
                "binding_id": "binding:orders",
                "entity_id": "entity:sales-order",
                "artifact_type": "DATABASE_TABLE",
                "artifact_ref": "table:orders",
                "status": "RESOLVED",
                "identity_field_bindings": [],
                "evidence": [],
            }
        ],
        "unknowns": [
            {
                "unknown_id": "unknown:api:legacy-order",
                "reason_code": "CROSS_SOURCE_IDENTITY_UNRESOLVED",
                "details": {"artifact_ref": "api:legacy-order"},
            }
        ],
        "conflicts": [],
        "gate": {"status": "PARTIAL_ENTERPRISE_IDENTITY_BINDING", "entry_allowed": True},
    }

    projected = augment_identity_field_evidence(asset, result)

    assert not any(
        row.get("artifact_ref") == "api:legacy-order"
        for row in projected["bindings"]
    )
    candidates = projected["identity_field_evidence"]["candidate_bindings"]
    assert len(candidates) == 1
    assert candidates[0]["candidate_entity_ids"] == ["entity:sales-order"]
    assert candidates[0]["automatic_resolution_allowed"] is False
    assert projected["unknowns"]


def test_regression_projection_extends_the_existing_identity_benchmark_gate() -> None:
    source, function = _understanding_builder()
    calls = _named_calls(function)
    benchmark_line = next(
        line for name, line in calls if name == "project_identity_benchmark"
    )
    regression_line = next(
        line
        for name, line in calls
        if name == "project_identity_benchmark_regression"
    )
    legacy_line = next(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "build_enterprise_understanding_model"
    )

    assert benchmark_line < regression_line < legacy_line
    assert source.count("project_identity_benchmark_regression(") == 1
