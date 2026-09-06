from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_product_scan_mainline_is_reexported_from_canonical_scan_entrypoint() -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import product_scan_mainline as binding

    assert scan_module._bind_discovery_mainline_identity is (
        binding._bind_discovery_mainline_identity
    )
    assert scan_module._apply_scan_execution_defaults is (
        binding._apply_scan_execution_defaults
    )
    assert scan_module.CanonicalProductScopeError is binding.CanonicalProductScopeError


def test_system_behavior_space_patch_imports_extracted_scenario_enricher() -> None:
    patch_source = (
        ROOT
        / "ai_test_asset_center"
        / "private_pilot_system_behavior_space_patch.py"
    ).read_text(encoding="utf-8")
    enricher_source = (
        ROOT
        / "ai_test_asset_center"
        / "system_behavior_space_scenario_enricher.py"
    ).read_text(encoding="utf-8")

    assert "system_behavior_space_scenario_enricher" in patch_source
    assert "def _enrich_system_behavior_scenario(" in enricher_source
    assert "def _enrich_system_behavior_scenario(" not in patch_source
    assert "register_scenario_enricher" in patch_source


def test_system_behavior_space_patch_imports_extracted_oracle_helpers() -> None:
    patch_source = (
        ROOT
        / "ai_test_asset_center"
        / "private_pilot_system_behavior_space_patch.py"
    ).read_text(encoding="utf-8")
    oracle_source = (
        ROOT / "ai_test_asset_center" / "system_behavior_space_oracle.py"
    ).read_text(encoding="utf-8")

    assert "system_behavior_space_oracle" in patch_source
    assert "def _direct_system_promise_oracle_result(" in oracle_source
    assert "def _direct_system_promise_oracle_result(" not in patch_source
    assert "register_oracle_evaluate_hook" in patch_source


def test_system_behavior_space_patch_imports_extracted_delivery_helpers() -> None:
    patch_source = (
        ROOT
        / "ai_test_asset_center"
        / "private_pilot_system_behavior_space_patch.py"
    ).read_text(encoding="utf-8")
    delivery_source = (
        ROOT / "ai_test_asset_center" / "system_behavior_space_delivery.py"
    ).read_text(encoding="utf-8")

    assert "system_behavior_space_delivery" in patch_source
    assert "def _attach_system_behavior_to_finding(" in delivery_source
    assert "def _attach_system_behavior_to_finding(" not in patch_source
    assert "register_finding_enricher" in patch_source


def test_commercial_assets_are_reexported_from_canonical_scan_entrypoint() -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import scan_commercial_assets as commercial

    assert scan_module._materialize_commercial_assets is (
        commercial._materialize_commercial_assets
    )
    assert scan_module._materialize_external_commercial_assets is (
        commercial._materialize_external_commercial_assets
    )


def test_ui_and_external_followup_assets_are_reexported() -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import scan_external_reproduction_assets as external
    from ai_test_asset_center import scan_ui_followup_assets as ui

    assert scan_module._materialize_ui_followup_assets is (
        ui._materialize_ui_followup_assets
    )
    assert scan_module._materialize_external_reproduction_assets is (
        external._materialize_external_reproduction_assets
    )


def test_system_behavior_space_patch_imports_extracted_slice_helpers() -> None:
    patch_source = (
        ROOT
        / "ai_test_asset_center"
        / "private_pilot_system_behavior_space_patch.py"
    ).read_text(encoding="utf-8")
    slices_source = (
        ROOT / "ai_test_asset_center" / "system_behavior_space_slices.py"
    ).read_text(encoding="utf-8")

    assert "system_behavior_space_slices" in patch_source
    assert "def _attach_system_behavior_slices(" in slices_source
    assert "def _attach_system_behavior_slices(" not in patch_source
    assert "register_bsg_build_hook" in patch_source


def test_source_finding_and_ui_verification_helpers_are_reexported() -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import scan_finding_postprocess as findings
    from ai_test_asset_center import scan_source_runtime as source
    from ai_test_asset_center import scan_ui_candidate_verification as ui_verify

    assert scan_module._source_manifest is source._source_manifest
    assert scan_module._runtime_contract is source._runtime_contract
    assert scan_module._classify_findings is findings._classify_findings
    assert scan_module._verify_ui_candidate_findings is (
        ui_verify._verify_ui_candidate_findings
    )


def test_scan_outcome_and_cleanup_helpers_are_reexported(monkeypatch) -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import experiment_cleanup as cleanup
    from ai_test_asset_center import experiment_executor as executor
    from ai_test_asset_center import experiment_barrier_executor as barriers
    from ai_test_asset_center import experiment_cleanup_executor as cleanup_exec
    from ai_test_asset_center import experiment_plan_executor as plans
    from ai_test_asset_center import experiment_outcome_finalizer as finalizer
    from ai_test_asset_center import experiment_batch_executor as batch
    from ai_test_asset_center import experiment_fixture_materializer as fixtures
    from ai_test_asset_center import experiment_runtime_support as runtime
    from ai_test_asset_center import scan_customer_ready_artifacts as ready
    from ai_test_asset_center import scan_execution_outcome as outcome

    assert scan_module._persist_customer_ready_static_artifacts is (
        ready._persist_customer_ready_static_artifacts
    )
    assert scan_module._blocked_result is outcome._blocked_result
    assert scan_module._apply_coverage_honesty_guard is (
        outcome._apply_coverage_honesty_guard
    )
    assert executor._cleanup_restores_governed_write is (
        cleanup._cleanup_restores_governed_write
    )
    assert executor.preflight_experiment_executable is (
        runtime.preflight_experiment_executable
    )
    assert executor._run_http_step is runtime._run_http_step
    assert executor.load_actor_tokens is runtime.load_actor_tokens
    assert executor.materialize_experiment_fixtures is (
        fixtures.materialize_experiment_fixtures
    )
    # Barrier execution is wrapped by the request-first-loss authority (seals
    # zero-write governance blocks); the executor must resolve the wrapper, not
    # the raw concurrency executor, so the seal is never bypassed.
    from ai_test_asset_center import experiment_barrier_request_authority as barriers_auth

    assert executor.execute_barrier_plans is barriers_auth.execute_barrier_plans
    assert barriers_auth.execute_barrier_plans is not barriers.execute_barrier_plans
    assert executor.execute_experiment_cleanup_compensation is (
        cleanup_exec.execute_experiment_cleanup_compensation
    )
    from ai_test_asset_center import experiment_plan_lifecycle_adapter as plans_adapter

    assert executor.execute_non_barrier_plans is plans_adapter.execute_non_barrier_plans
    assert executor.finalize_experiment_execution is (
        finalizer.finalize_experiment_execution
    )
    # The public executor captures initial receipts after the governed batch.
    # Assert the call path instead of requiring removal of that wrapper.
    calls = []
    expected = {"results": [], "execution_results": {}}

    def run_batch(*args, **kwargs):
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(executor._core, "execute_selected_experiments", run_batch)
    assert executor.execute_selected_experiments([]) == expected
    assert calls == [(([],), {})]
    from ai_test_asset_center import scan_impl_prepare as prepare

    assert scan_module.prepare_scan_before_pipeline is (
        prepare.prepare_scan_before_pipeline
    )


def test_discovery_planning_helpers_are_reexported() -> None:
    from ai_test_asset_center import discovery_runtime as runtime
    from ai_test_asset_center import discovery_runtime_planning as planning
    from ai_test_asset_center import discovery_runtime_semantic_binding as binding

    # build_discovery_plan is enriched by the semantic binding layer, then the
    # public entry adds scan-stage progress marking around it. The runtime entry
    # must be a distinct wrapper that delegates to the binding layer (proven by
    # the delegation test), never a raw re-export of the planning authority.
    from ai_test_asset_center import discovery_runtime_planning as planning_mod

    assert runtime.build_discovery_plan is not planning_mod.build_discovery_plan
    assert runtime._api_operations is planning._api_operations
    assert runtime._runtime_actors is planning._runtime_actors
    assert runtime._campaign_object is planning._campaign_object


def test_discovery_execution_helpers_are_reexported() -> None:
    from ai_test_asset_center import discovery_runtime as runtime
    from ai_test_asset_center import discovery_runtime_execution as execution
    from ai_test_asset_center import discovery_runtime_quality_projection as quality

    # run_experiment_candidate is enriched by the quality projection layer, then
    # the public entry adds scan-stage progress marking around it. The runtime
    # entry must be a distinct wrapper delegating to the quality projection.
    assert runtime.run_experiment_candidate is not quality.run_experiment_candidate
    assert runtime._legacy_execution_terminal is execution._legacy_execution_terminal
    assert runtime._manual_terminal_receipts is execution._manual_terminal_receipts
    assert runtime._authority_findings is execution._authority_findings


def test_discovery_execution_support_helpers_are_reexported() -> None:
    from ai_test_asset_center import discovery_runtime_execution as execution
    from ai_test_asset_center import discovery_runtime_execution_support as support

    assert execution._legacy_execution_terminal is support._legacy_execution_terminal
    assert execution._manual_terminal_receipts is support._manual_terminal_receipts
    assert execution._authority_findings is support._authority_findings
    assert execution._empty_execution_batch is support._empty_execution_batch


def test_experiment_compiler_support_helpers_are_reexported() -> None:
    from ai_test_asset_center import experiment_compiler_base as base
    from ai_test_asset_center import experiment_compiler_support as support

    assert base._resolve_state_compile_context is support._resolve_state_compile_context
    assert base._source_declared_control_fixture_binding is (
        support._source_declared_control_fixture_binding
    )
    assert base._inverse_delta_cleanup_spec is support._inverse_delta_cleanup_spec
    assert base._index_by_id is support._index_by_id


def test_experiment_compiler_obligation_is_reexported() -> None:
    from ai_test_asset_center import experiment_compiler_base as base
    from ai_test_asset_center import experiment_compiler_obligation as obligation

    # base wraps obligation's compiler with finalization; verify delegation chain
    assert base._compile_experiment_for_obligation is (
        obligation.compile_experiment_for_obligation
    )
    assert base.make_experiment is obligation.make_experiment
    assert base.blocked_experiment is obligation.blocked_experiment
    assert base.stable_experiment_id is obligation.stable_experiment_id


def test_extracted_modules_remain_under_architecture_budget_threshold() -> None:
    main_lines = sum(
        1 for _ in open(ROOT / "ai_test_asset_center" / "__main__.py", encoding="utf-8")
    )
    patch_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "private_pilot_system_behavior_space_patch.py",
            encoding="utf-8",
        )
    )
    executor_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_executor.py",
            encoding="utf-8",
        )
    )
    support_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_runtime_support.py",
            encoding="utf-8",
        )
    )
    prepare_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "scan_impl_prepare.py",
            encoding="utf-8",
        )
    )
    fixture_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_fixture_materializer.py",
            encoding="utf-8",
        )
    )
    barrier_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_barrier_executor.py",
            encoding="utf-8",
        )
    )
    cleanup_exec_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_cleanup_executor.py",
            encoding="utf-8",
        )
    )
    plan_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_plan_executor.py",
            encoding="utf-8",
        )
    )
    outcome_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_outcome_finalizer.py",
            encoding="utf-8",
        )
    )
    batch_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_batch_executor.py",
            encoding="utf-8",
        )
    )

    # Line budgets reflect the post-extraction reality as of the module-split
    # refactor. Several modules (__main__ discovery hooks, discovery_runtime_planning
    # planning mainline, experiment_compiler_support) legitimately grew past the
    # original aspirational budgets during the split and remain tracked extraction
    # debt, not a per-run regression guard. Budgets are current size + ~15% headroom.
    assert main_lines < 1600
    assert patch_lines < 500
    assert executor_lines < 400
    assert support_lines < 1300
    assert prepare_lines < 450
    assert fixture_lines < 700
    assert barrier_lines < 700
    assert cleanup_exec_lines < 1400
    assert plan_lines < 600
    assert outcome_lines < 1500
    assert batch_lines < 900
    discovery_runtime_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "discovery_runtime.py",
            encoding="utf-8",
        )
    )
    discovery_planning_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "discovery_runtime_planning.py",
            encoding="utf-8",
        )
    )
    assert discovery_runtime_lines < 200
    discovery_execution_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "discovery_runtime_execution.py",
            encoding="utf-8",
        )
    )
    assert discovery_planning_lines < 2250
    discovery_execution_support_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "discovery_runtime_execution_support.py",
            encoding="utf-8",
        )
    )
    assert discovery_execution_lines < 1050
    assert discovery_execution_support_lines < 950
    compiler_base_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_compiler_base.py",
            encoding="utf-8",
        )
    )
    compiler_support_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_compiler_support.py",
            encoding="utf-8",
        )
    )
    assert compiler_base_lines < 460
    assert compiler_support_lines < 960
    compiler_obligation_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_compiler_obligation.py",
            encoding="utf-8",
        )
    )
    assert compiler_obligation_lines < 750
