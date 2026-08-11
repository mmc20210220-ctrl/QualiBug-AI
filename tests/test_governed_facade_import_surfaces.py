from __future__ import annotations


def test_evaluator_facades_preserve_public_and_private_import_surfaces() -> None:
    import benchmark_evaluator.benchmark_compute as benchmark
    import ai_test_asset_center.discovery_evaluation_contract as evaluation

    assert callable(benchmark.compute_benchmark)
    assert callable(benchmark.compute_stage_loss_matrix)
    assert callable(benchmark._risk_family_for_item)
    assert getattr(
        benchmark.compute_benchmark,
        "_qualibug_commercial_scoring_contract",
        False,
    ) is True

    assert callable(evaluation.load_evaluation_manifest)
    assert callable(evaluation.aggregate_evaluation_receipts)
    assert callable(evaluation.policy_metrics_from_evaluation_reports)
    assert callable(evaluation._ratio)


def test_discovery_facades_preserve_existing_mechanics_surface() -> None:
    import ai_test_asset_center.blocker_attribution as blockers
    import ai_test_asset_center.cleanup_equivalence_core as cleanup
    import ai_test_asset_center.experiment_outcome_finalizer_core as finalizer
    import ai_test_asset_center.experiment_protocols as protocols
    import ai_test_asset_center.fact_first_loss_ledger as lineage

    assert callable(blockers.profile_reason_code)
    assert isinstance(blockers.REASON_CODE_REGISTRY, dict)

    assert callable(cleanup.evaluate_cleanup_equivalence)
    assert callable(cleanup._canonical_json)

    assert callable(finalizer.finalize_experiment_execution)
    assert callable(finalizer._classify_harness_failure)

    assert callable(protocols.compile_family_protocol)
    assert callable(protocols._validation_protocol_material)

    assert callable(lineage.build_fact_first_loss_ledger)
    assert callable(lineage.attach_fact_refs_to_planning_artifacts)
    assert callable(lineage.extract_fact_refs)
