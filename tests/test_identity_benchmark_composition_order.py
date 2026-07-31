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
