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


def _function(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _understanding_builder() -> tuple[str, ast.FunctionDef]:
    source = Path(
        "ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/"
        "builder/__init__.py"
    ).read_text(encoding="utf-8")
    return source, _function(source, "build_enterprise_understanding_model")


def test_identity_benchmark_repository_precedes_first_understanding_pass() -> None:
    source = Path(
        "ai_test_asset_center/enterprise_knowledge_center/composition.py"
    ).read_text(encoding="utf-8")
    function = _function(source, "build_enterprise_business_knowledge_asset")
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


def test_structural_candidates_run_only_after_final_identity_model_projection() -> None:
    source, function = _understanding_builder()
    calls = _named_calls(function)
    legacy_line = next(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "build_enterprise_understanding_model"
    )
    identity_line = next(
        line for name, line in calls if name == "apply_identity_resolution_to_model"
    )
    recognition_line = next(
        line for name, line in calls if name == "apply_recognition_to_model"
    )
    structural_line = next(
        line
        for name, line in calls
        if name == "project_distinctness_structural_candidates"
    )
    receipt_line = next(
        line for name, line in calls if name == "_attach_identity_audit_receipts"
    )

    assert legacy_line < identity_line < recognition_line < structural_line < receipt_line
    assert source.count("project_distinctness_structural_candidates(") == 1
    assert (
        "project_distinctness_structural_candidates(asset, model, resolution)"
        in source
    )
    assert 'model["identity_structural_evidence"]' in source
    assert '"enterprise_identity_structural_evidence"' in source


def test_structural_review_confirmation_rebuilds_through_same_identity_builder() -> None:
    source, function = _understanding_builder()
    calls = _named_calls(function)
    resolve_line = next(
        line for name, line in calls if name == "resolve_enterprise_identities"
    )
    scrub_line = next(
        line
        for name, line in calls
        if name == "scrub_operator_structural_review_mentions"
    )
    registry_line = next(
        line for name, line in calls if name == "govern_identity_registry"
    )
    candidate_line = next(
        line
        for name, line in calls
        if name == "project_distinctness_structural_candidates"
    )
    admission_line = next(
        line
        for name, line in calls
        if name == "govern_identity_structural_review_decision_admission"
    )
    apply_line = next(
        line
        for name, line in calls
        if name == "apply_identity_structural_review_decisions"
    )
    attach_lines = sorted(
        line
        for name, line in calls
        if name == "attach_identity_structural_review_admission"
    )
    preserve_line = next(
        line
        for name, line in calls
        if name == "preserve_identity_structural_review_registry_merges"
    )
    begin_line = next(
        line
        for name, line in calls
        if name == "begin_identity_structural_review_rebuild"
    )
    finalize_line = next(
        line
        for name, line in calls
        if name == "finalize_identity_structural_review_measurement"
    )

    assert resolve_line < scrub_line < registry_line
    assert candidate_line < admission_line < apply_line
    assert len(attach_lines) == 2
    assert apply_line < attach_lines[-1] < preserve_line < begin_line
    assert finalize_line > candidate_line
    assert attach_lines[0] > finalize_line
    assert source.count("return build_enterprise_understanding_model(asset)") == 1
    assert "identity_structural_review_rebuild_in_progress(asset)" in source
    assert "compile_and_import_identity_annotations" not in source
    assert "import_identity_ground_truth" not in source


def test_structural_review_decisions_load_after_final_fact_conflict_governance() -> None:
    source = Path(
        "ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/"
        "post_compile_fact_governance.py"
    ).read_text(encoding="utf-8")
    function = _function(source, "govern_compiled_business_facts")
    calls = _named_calls(function)
    typed_conflict_line = next(
        line for name, line in calls if name == "reconcile_typed_fact_conflicts"
    )
    structural_review_line = next(
        line
        for name, line in calls
        if name == "_project_structural_review_decisions"
    )

    assert typed_conflict_line < structural_review_line
    assert "load_authority_decision_ledger" in source
    assert "parallel_decision_ledger_created\": False" in source
    assert "identity_structural_review_uses_blind_ground_truth\": False" in source


def test_structural_review_public_facade_reuses_one_operator_entrypoint() -> None:
    from ai_test_asset_center import enterprise_knowledge_center as public
    from ai_test_asset_center.enterprise_knowledge_center import (
        enterprise_understanding as understanding,
    )

    assert public.get_identity_structural_review_queue is (
        understanding.get_identity_structural_review_queue
    )
    assert public.record_identity_structural_review_decision is (
        understanding.record_identity_structural_review_decision
    )
    assert public.ACTION_CONFIRM_ALIAS == understanding.ACTION_CONFIRM_ALIAS
    assert public.ACTION_REJECT_CANDIDATE == understanding.ACTION_REJECT_CANDIDATE


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
        "gate": {
            "status": "PARTIAL_ENTERPRISE_IDENTITY_BINDING",
            "entry_allowed": True,
        },
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


def test_two_understanding_passes_share_previous_finalized_identity_registry() -> None:
    source = Path(
        "ai_test_asset_center/enterprise_knowledge_center/composition.py"
    ).read_text(encoding="utf-8")
    function = _function(source, "build_enterprise_business_knowledge_asset")
    calls = _named_calls(function)
    understanding_lines = sorted(
        line
        for name, line in calls
        if name == "enrich_asset_with_enterprise_understanding"
    )
    restore_lines = sorted(
        line for name, line in calls if name == "_restore_previous_identity_registry"
    )

    assert len(understanding_lines) == 2
    assert len(restore_lines) == 2
    assert restore_lines[0] < understanding_lines[0]
    assert understanding_lines[0] < restore_lines[1] < understanding_lines[1]
    assert "same_finalized_baseline_used_for_all_passes" in source
    assert '"provisional_first_pass_registry_promoted": False' in source
