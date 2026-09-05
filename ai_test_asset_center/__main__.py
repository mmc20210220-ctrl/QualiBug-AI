"""QualiBug unified, source-grounded enterprise scan entry point.

A scan may only be driven by an immutable, attributable source asset. Sources
are resolved from the enterprise source registry first, then from a project-owned
asset mirror, or from an explicitly supplied SHA-256 manifest. Any confirmed
finding must also have a persisted, integrity-verifiable evidence bundle.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional, Callable

from .scan_diagnostics import increment_scan_counter
from .enterprise_test_data_plan import build_campaign_test_data_plan
from .test_data_receipt_bootstrap import bootstrap_test_data_receipts_for_campaign


_LOGGER = logging.getLogger(__name__)


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        configure = getattr(stream, "reconfigure", None)
        if callable(configure):
            try:
                configure(errors="replace")
            except Exception as exc:
                _LOGGER.warning(
                    "console_encoding_configuration_failed stream=%s error_type=%s",
                    getattr(stream, "name", "unknown"),
                    type(exc).__name__,
                    exc_info=True,
                )


_configure_console_encoding()

from .product_scan_mainline import (  # noqa: F401
    CanonicalProductScopeError,
    _apply_scan_execution_defaults,
    _as_dict,
    _bind_discovery_mainline_identity,
    _bind_scan_rows_to_mainline,
    _canonical_product_scope,
    _first_text,
    _gap,
    _reject_evaluator_private_context,
    _safe_project,
    _scan_campaign_context_defaults,
    _sha256,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist JSON only after unified recursive redaction + secret scan."""
    from .artifact_redactor import ArtifactSecretLeakError, write_json_redacted

    try:
        write_json_redacted(path, payload)
    except ArtifactSecretLeakError as exc:
        # Fail closed: do not leave a secret-bearing artifact on disk.
        _LOGGER.error(
            "artifact_persistence_blocked_secret_scan path=%s error=%s",
            path,
            exc,
        )
        raise


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}




from .scan_customer_ready_artifacts import (  # noqa: F401
    _customer_ready_static_snapshot,
    _persist_customer_ready_static_artifacts,
)


from .scan_ui_followup_assets import (  # noqa: F401
    _ui_candidate_target_path,
    _ui_candidate_method,
    _normalize_ui_verification_http_path,
    _candidate_followup_verification_template,
    _source_bound_followup_verification_template,
    _ui_followup_execution_template,
    _source_bound_ui_followup_templates,
    _source_bound_ui_test_data_templates,
    _ui_test_data_browser_plan_draft,
    _ui_execution_evidence_summary,
    _load_candidate_items,
    _merge_candidate_items,
    _materialize_ui_followup_assets,
)


from .scan_external_reproduction_assets import (  # noqa: F401
    _external_candidate_id,
    _external_reproduction_observation,
    _render_external_repro_ps1,
    _render_external_regression_pytest,
    _materialize_external_reproduction_assets,
)

from .scan_commercial_assets import (  # noqa: F401
    _external_priority,
    _write_markdown,
    _commercial_priority,
    _commercial_finding_customer_ready,
    _commercial_candidate_id,
    _commercial_finding_reason,
    _commercial_runtime_observation,
    _build_materialized_commercial_assets,
    _materialize_commercial_assets,
    _materialize_external_commercial_assets,
)


from .scan_source_runtime import (  # noqa: F401
    _load_schema_assets,
    _project_requirement_input_dirs,
    _requirement_doc_score,
    _load_project_prd_text,
    _registry_manifest,
    _load_registered_source,
    _find_project_asset,
    _source_manifest,
    _source_contract,
    _runtime_contract,
    _scan_preflight_guide,
    _source_catalog,
)


from .scan_finding_postprocess import (  # noqa: F401
    _classify_findings,
    _dedupe_findings,
    _filter_http_status_class_quality,
    _has_verified_db_evidence,
    _is_external_signal_finding,
    _snapshot_entry_from_external,
    _external_finding_snapshots,
    _external_finding_runtime_observation,
    _attach_external_evidence_packages,
    _adjudicate_external_evidence_backed_candidates,
)


from .scan_ui_candidate_verification import (  # noqa: F401
    _ui_candidate_gate,
    _template_string,
    _ui_verification_context,
    _verify_ui_candidate_http,
    _verify_ui_candidate_sqlite,
    _verify_ui_candidate_execution_evidence,
    _verify_ui_candidate_findings,
    _mark_high_confidence_ui_candidates,
)


from .scan_execution_outcome import (  # noqa: F401
    _test_data_receipt_verifier,
    _persist_execution_evidence,
    _evaluate_release_gate,
    _blocked_result,
    _compute_scan_score,
    _apply_coverage_honesty_guard,
    _discovery_verdict,
)

from .scan_impl_prepare import prepare_scan_before_pipeline  # noqa: F401
from .scan_result_store import write_scan_result  # noqa: F401


def _phase_time(result: dict[str, Any], key: str, started: float) -> None:
    """P1 收尾打点：段耗时并入 scan_phase_timings（纯观测，绝不影响结果）。"""
    try:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        timings = result.get("scan_phase_timings")
        if not isinstance(timings, dict):
            timings = {}
            result["scan_phase_timings"] = timings
        timings[key] = elapsed_ms
        # [wrapup-trace] 实时分段账本：收尾挂住必须能仅凭日志归因。
        # 2026-08-25 事故：产物全部落盘后进程仍烧 ~1h CPU 且零日志输出，
        # 因为各段耗时只写入 result、运行结束才可见——挂住时什么都没有。
        _LOGGER.warning(
            "[wrapup-trace] phase=%s ms=%s",
            key,
            elapsed_ms,
        )
    except Exception:
        pass


def _verified_archive_chain(
    project: str,
    root: Optional[Path],
    *,
    v12: dict[str, Any],
    campaign: dict[str, Any],
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verified Discovery Archive 主链收尾：merge + apply + save + receipt。

    scan 收尾的单一调用点（无论 save_report 与否都执行）：本 run 交付并入
    档案，输出 findings = 本 run 新交付 ∪ 档案未退休历史发现。异常绝不
    静默：失败时返回 FAILED receipt（含原因）并记录 error 日志，扫描继续
    ——档案永不阻塞扫描，但断链必须可见、可追溯。
    """
    try:
        from .verified_discovery_archive import (
            apply_verified_discovery_archive_to_run,
        )

        run_identity = _first_text(
            _as_dict(v12.get("mainline_run")).get("run_id")
        ) or _first_text(campaign.get("campaign_id"))
        return apply_verified_discovery_archive_to_run(
            project,
            root,
            run_id=run_identity,
            campaign_id=_first_text(campaign.get("campaign_id")),
            findings=findings,
        )
    except Exception as _archive_exc:
        _LOGGER.error(
            "verified_discovery_archive_merge_failed error_type=%s error=%s",
            type(_archive_exc).__name__,
            str(_archive_exc)[:200],
            exc_info=True,
        )
        return findings, {
            "schema_version": "qualibug.verified-discovery-archive.v1",
            "status": "FAILED",
            "reason": f"{type(_archive_exc).__name__}:{str(_archive_exc)[:160]}",
        }


def _scan_impl(project: str, root: Optional[Path] = None, *, prd_text: str = "", api_doc_path: str = "", api_doc_text: str = "", base_url: str = "", ci_gate: bool = False, multi_layer: bool = True, output_dir: Optional[Path] = None, save_report: bool = True, campaign_context: Optional[dict[str, Any]] = None, on_started: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """Run the single enterprise-safe discovery and evidence pipeline."""
    prepared = prepare_scan_before_pipeline(
        project,
        root,
        prd_text=prd_text,
        api_doc_path=api_doc_path,
        api_doc_text=api_doc_text,
        base_url=base_url,
        output_dir=output_dir,
        save_report=save_report,
        campaign_context=campaign_context,
    )
    if prepared.get("status") == "early":
        return dict(prepared.get("result") or {"success": False, "error": "scan_prepare_failed"})

    project = str(prepared["project"])
    root = Path(prepared["root"])

    # Project identity for stages that resolve per-project runtime state via
    # env (e.g. learned risk memory consumed by the reasoner). Mirrors the
    # bug_engine_autorun convention so the direct-scan path behaves the same.
    os.environ["QUALIBUG_PROJECT"] = project

    context = dict(prepared["context"])
    prd_text = str(prepared.get("prd_text") or "")
    api_doc_text = str(prepared.get("api_doc_text") or "")
    base_url = str(prepared.get("base_url") or "")
    approved_base_url = str(prepared.get("approved_base_url") or "")
    started = float(prepared["started"])
    manifest = dict(prepared["manifest"])
    initial_runtime_contract = dict(prepared["initial_runtime_contract"])
    input_gaps = list(prepared.get("input_gaps") or [])
    diagnostics = dict(prepared.get("diagnostics") or {"ready": True, "checks": []})
    schema_text = str(prepared.get("schema_text") or "")
    output_dir = prepared.get("output_dir")
    save_report = bool(prepared.get("save_report", save_report))

    # ── Closed-Loop Learning READ side: load knowledge learned in previous
    # scans from the SQLite knowledge base and hand it to the pipeline.
    # Failures stay visible via the load_failure field instead of silently
    # degrading the scan to zero-memory mode.
    try:
        from .closed_loop_feedback import load_learned_scan_context

        learned_context = load_learned_scan_context(project)
        if learned_context.get("pattern_count") or learned_context.get("load_failure"):
            context["learned_knowledge"] = learned_context
            _LOGGER.info(
                "closed_loop_learned_context_loaded project=%s patterns=%s failure=%s",
                project,
                learned_context.get("pattern_count", 0),
                learned_context.get("load_failure", "none"),
            )
        # Cross-round knowledge transfer READ: attach per-round history
        # insights to the learned payload (observational only — planning
        # consumes the same risk_pattern entries as before; round history
        # adds provenance context, never new evidence sources).
        try:
            from .cross_round_knowledge_transfer import CrossRoundKnowledgeTransfer

            transfer = CrossRoundKnowledgeTransfer(project)
            insights = transfer.get_cross_round_insights()
            if isinstance(learned_context, dict) and isinstance(insights, dict):
                learned_context["cross_round_insights"] = insights
        except Exception as exc:
            _LOGGER.warning(
                "cross_round_insights_load_failed: %s:%s",
                type(exc).__name__,
                str(exc)[:120],
            )
        # Binding-experience READ: verified resolver mappings from prior
        # scans (source-declared resolver identities only, never resolved
        # values) ride along for the planning-time resolver reorder.
        try:
            from .learning_pattern_bridge import LearningPatternBridge

            binding_resolvers = LearningPatternBridge(project=project).load_binding_experience()
            if isinstance(learned_context, dict) and binding_resolvers:
                learned_context["binding_resolvers"] = binding_resolvers
                _LOGGER.info(
                    "binding_experience_loaded project=%s resolvers=%s",
                    project,
                    len(binding_resolvers),
                )
        except Exception as exc:
            _LOGGER.warning(
                "binding_experience_load_failed: %s:%s",
                type(exc).__name__,
                str(exc)[:120],
            )
    except Exception as exc:
        _LOGGER.exception("closed_loop_learned_context_load_failed")
        context["learned_knowledge"] = {
            "source": "sqlite_knowledge_base",
            "pattern_count": 0,
            "learned_patterns": [],
            "load_failure": f"{type(exc).__name__}:{str(exc)[:200]}",
        }

    scan_id = f"scan_{_safe_project(project)}_{int(started * 1000)}"
    if on_started is not None:
        on_started(scan_id)

    try:
        from .v12_pipeline import run_v12_pipeline
        v12 = run_v12_pipeline(project=project, root=root, prd_text=prd_text, api_spec_text=api_doc_text, db_schema_text=schema_text, base_url=approved_base_url, campaign_context=context)
    except Exception as exc:
        import traceback as _tb
        _tb.print_exc()
        return {"success": False, "scan_id": scan_id, "error": f"v12_pipeline_failed:{type(exc).__name__}:{exc}"}

    runtime_contract = _as_dict(v12.get("runtime_contract")) or initial_runtime_contract
    phases = _as_dict(v12.get("phases"))
    execution = _as_dict(phases.get("execution"))
    campaign = _as_dict(v12.get("campaign"))

    # Resolve a pre-registered execution approval by the campaign's stable
    # bindings (scope / environment / source hash / target origin). Each scan
    # run receives a fresh campaign_id, so matching must ignore campaign_id and
    # rely on the immutable binding tuple. This backfills runtime_contract so a
    # scan that omitted an explicit execution_approval_id still surfaces the
    # governing approval for audit, WITHOUT silently auto-issuing a new one.
    if not isinstance(runtime_contract.get("execution_approval"), dict) or not runtime_contract.get("execution_approval"):
        _rc_scope = str(campaign.get("scope_id") or context.get("scope_id") or "").strip()
        _rc_env = str(
            runtime_contract.get("environment_ref")
            or context.get("environment_ref")
            or context.get("target_environment")
            or ""
        ).strip()
        _rc_source = str(
            _as_dict(runtime_contract.get("source_manifest")).get("source_hash")
            or manifest.get("source_hash")
            or ""
        ).strip().lower()
        _rc_target = str(runtime_contract.get("approved_base_url") or approved_base_url or "").strip()
        if _rc_scope and _rc_env and _rc_source and _rc_target:
            try:
                from .execution_approvals import resolve_execution_approval_for_campaign

                _resolved = resolve_execution_approval_for_campaign(
                    project,
                    root=root,
                    scope_id=_rc_scope,
                    environment_ref=_rc_env,
                    source_hash=_rc_source,
                    target_base_url=_rc_target,
                )
                if _resolved.get("found"):
                    runtime_contract = dict(runtime_contract)
                    runtime_contract["execution_approval"] = _resolved["approval"]
            except Exception as exc:
                _LOGGER.warning(
                    "execution_approval_resolution_failed scope=%s environment=%s "
                    "source_hash=%s target=%s error_type=%s",
                    _rc_scope,
                    _rc_env,
                    _rc_source,
                    _rc_target,
                    type(exc).__name__,
                    exc_info=True,
                )

    from .discovery_funnel import effective_execution_status

    execution_status = effective_execution_status(v12)
    canonical_scope = _canonical_product_scope(v12)
    if canonical_scope["status"] != "VERIFIED":
        v12["formal_count_projection"] = dict(
            canonical_scope["formal_count_projection"]
        )
    confirmed = list(canonical_scope["findings"])
    candidates = list(canonical_scope["candidates"])
    # ── FP quality filter: demote low-confidence http_status_class findings ──
    confirmed, candidates = _filter_http_status_class_quality(confirmed, candidates)
    # ── Verified Discovery Archive: 已验证发现跨 run 单调保持 ──
    # 只要目标系统未修复，已交付的 defect finding（gate_passed + 复现成功）
    # 不得因单次扫描的覆盖波动而丢失：本 run 交付并入档案，输出 findings =
    # 本 run 新交付 ∪ 档案中未退休的历史发现（archive_entry 标记）。只有
    # 「目标已修复」信号（连续多 run 确认）才退休——届时没发现才是正常的。
    # 无论 save_report 与否都执行；失败时 receipt 为 FAILED（带原因，绝不静默）。
    _t_archive = time.perf_counter()
    confirmed, verified_archive_receipt = _verified_archive_chain(
        project,
        root,
        v12=v12,
        campaign=campaign,
        findings=confirmed,
    )
    # result 在此尚未创建（首个赋值在 v12 汇总之后）——先存局部，
    # 待 result 诞生后再并入 scan_phase_timings。
    _archive_merge_ms = int((time.perf_counter() - _t_archive) * 1000)
    # ── Report split: current formal / archive / candidate ──
    # Score, coverage, the headline findings count and the grade may only
    # reflect THIS run's formal deliveries. Verified-archive hold-overs
    # (archive_entry=True) are reported separately — counting them made a
    # delivery-less run show score=100 / coverage=1 / findings=120.
    archive_findings = [
        finding
        for finding in confirmed
        if isinstance(finding, dict) and finding.get("archive_entry") is True
    ]
    current_formal_findings = [
        finding
        for finding in confirmed
        if not (isinstance(finding, dict) and finding.get("archive_entry") is True)
    ]
    delivery_occurrences = list(canonical_scope["delivery_occurrences"])
    canonical_registry = dict(canonical_scope["canonical_defect_registry"])
    dedupe_input_count = int(
        canonical_registry.get("delivery_occurrence_count")
        if canonical_registry
        else len(delivery_occurrences)
    )
    dedupe_output_count = int(
        canonical_registry.get("canonical_defect_count")
        if canonical_registry
        else len(confirmed)
    )
    dedupe_report = {
        "schema_version": "qualibug.canonical-dedupe-report.v1",
        "authority": "canonical_defect_registry",
        "status": canonical_scope["status"],
        "input_count": dedupe_input_count,
        "output_count": dedupe_output_count,
        "unique_count": dedupe_output_count,
        "delivery_occurrence_count": dedupe_input_count,
        "collapsed_count": max(0, dedupe_input_count - dedupe_output_count),
        "title_or_path_dedupe_used": False,
    }
    external_findings = v12.get("external_findings") if isinstance(v12.get("external_findings"), list) else []
    external_findings = _bind_scan_rows_to_mainline(
        [dict(item) for item in external_findings if isinstance(item, dict)],
        v12,
    )
    if external_findings:
        external_findings = _adjudicate_external_evidence_backed_candidates(external_findings)
        external_findings = _attach_external_evidence_packages(external_findings)
        _, external_candidates = _classify_findings(external_findings)
        candidates.extend(external_candidates)
    state_graph = _as_dict(phases.get("state_graph"))
    incremental = _as_dict(phases.get("incremental_discovery"))
    external_findings, external_reproduction_assets = _materialize_external_reproduction_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        items=external_findings,
    )
    refreshed_candidates = [item for item in candidates if not (isinstance(item, dict) and _is_external_signal_finding(item))]
    if external_findings:
        _, refreshed_external_candidates = _classify_findings(external_findings)
        refreshed_candidates.extend(refreshed_external_candidates)
    candidates = refreshed_candidates
    v12["external_findings"] = external_findings
    ui_execution = _as_dict(v12.get("ui_execution"))
    p4_ui_evidence_bridge: dict[str, Any] = {"status": "not_requested"}
    try:
        evidence_bundle = _persist_execution_evidence(project, root, scan_id, campaign, runtime_contract, execution_status, v12)

        # ── Phase 108Q: Bridge browser execution HAR/screenshot to evidence bundle ──
        # Acceptance Criterion 10: end-to-end P4 UI evidence chain.
        if isinstance(ui_execution, dict) and ui_execution.get("har_ref"):
            try:
                from .har_bridge import bridge_browser_har_to_findings
                all_findings = confirmed + candidates
                har_enriched = bridge_browser_har_to_findings(
                    ui_execution, all_findings, root=root,
                )
                p4_ui_evidence_bridge = _as_dict(har_enriched.get("har_summary"))
            except Exception as exc:
                p4_ui_evidence_bridge = {
                    "status": "failed",
                    "reason": f"har_bridge_error:{type(exc).__name__}",
                    "error": str(exc)[:300],
                }

    except Exception as exc:
        _LOGGER.exception("scan evidence persistence failed")
        failure = {
            "success": False,
            "scan_id": scan_id,
            "project": project,
            "execution_status": "FAILED_SAFE",
            "customer_output_status": "BLOCKED_EVIDENCE_PERSISTENCE",
            "failure_stage": "evidence_persistence",
            "error": "evidence_bundle_persistence_failed",
            "error_type": type(exc).__name__,
            "reason": str(exc)[:500],
            "findings": [],
            "candidate_findings": [
                *candidates,
                *[
                    {
                        **dict(item),
                        "finding_class": "candidate",
                        "customer_delivery_status": "blocked",
                        "customer_delivery_gate_reasons": [
                            "EVIDENCE_BUNDLE_PERSISTENCE_FAILED"
                        ],
                    }
                    for item in confirmed
                ],
            ],
            "delivery_occurrence_count": len(delivery_occurrences),
            "canonical_defect_count_blocked": len(confirmed),
            "canonical_registry_fingerprint": str(
                canonical_registry.get("registry_fingerprint") or ""
            ),
            "pipeline_health": {
                "status": "FAILED_SAFE",
                "empty_findings_means_no_bugs": False,
                "stage_failures": ["EVIDENCE_BUNDLE_PERSISTENCE_FAILED"],
            },
        }
        try:
            from .llm_reasoning import build_llm_observability_receipt

            failure["llm_observability_receipt"] = build_llm_observability_receipt()
        except Exception:
            failure["llm_observability_receipt"] = {
                "schema_version": "qualibug.llm-observability.v1",
                "status": "FAILED",
            }
        failure_path = (
            root
            / "platform_outputs"
            / _safe_project(project)
            / "scan_result.json"
        )
        _write_json(failure_path, failure)
        return failure

    if str(runtime_contract.get("status") or "") == "blocked":
        requirements = runtime_contract.get("missing_requirements") if isinstance(runtime_contract.get("missing_requirements"), list) else []
        for code in requirements:
            if not any(gap.get("code") == str(code) for gap in input_gaps):
                input_gaps.append(_gap(str(code), "Runtime execution approval or contract requirement is not satisfied."))
    graph_gaps = state_graph.get("coverage_gaps", []) if isinstance(state_graph.get("coverage_gaps"), list) else []
    selected_slices = incremental.get("selected_slices") if isinstance(incremental.get("selected_slices"), list) else []
    test_data_bootstrap = bootstrap_test_data_receipts_for_campaign(
        project=project,
        root=root,
        base_url=approved_base_url,
        api_doc_text=api_doc_text,
        campaign=campaign,
        selected_slices=selected_slices,
        contract=_as_dict(context.get("test_data_contract")),
        environment_kind=_first_text(
            runtime_contract.get("environment_kind"),
            runtime_contract.get("environment_type"),
            context.get("environment_kind"),
            context.get("target_environment"),
        ),
    )
    ui_test_data_bootstrap: dict[str, Any] = {"status": "not_requested"}
    if test_data_bootstrap.get("status") != "ready":
        try:
            from .ui_test_data_bootstrap import bootstrap_ui_test_data_receipts_for_campaign

            ui_test_data_bootstrap = bootstrap_ui_test_data_receipts_for_campaign(
                project=project,
                root=root,
                campaign=campaign,
                contract=_as_dict(context.get("test_data_contract")),
                runtime_contract=runtime_contract,
                requests=context.get("ui_test_data_requests"),
                execution_context=context,
            )
            if isinstance(ui_test_data_bootstrap.get("contract"), dict) and ui_test_data_bootstrap.get("status") == "ready":
                test_data_bootstrap = ui_test_data_bootstrap
        except Exception as exc:
            ui_test_data_bootstrap = {"status": "failed", "reason": f"ui_test_data_bootstrap_error:{type(exc).__name__}"}
    if isinstance(test_data_bootstrap.get("contract"), dict) and test_data_bootstrap.get("status") == "ready":
        context["test_data_contract"] = dict(test_data_bootstrap.get("contract") or {})
    test_data_plan = build_campaign_test_data_plan(campaign, selected_slices, _as_dict(context.get("test_data_contract")), receipt_verifier=_test_data_receipt_verifier(root, project))
    coverage_gaps = input_gaps + [item for item in graph_gaps if isinstance(item, dict)] + list(test_data_plan.get("coverage_gaps") or [])
    release_gate = _evaluate_release_gate(project=project, root=root, campaign=campaign, execution_status=execution_status, runtime_contract=runtime_contract, evidence_bundle=evidence_bundle, test_data_plan=test_data_plan, findings=confirmed, coverage_gaps=coverage_gaps, policy=_as_dict(context.get("release_policy")))
    commercial_assets = _materialize_commercial_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        items=confirmed,
        scan_result={
            "project": project,
            "scan_id": scan_id,
            "campaign": campaign,
            "runtime_contract": runtime_contract,
            "release_gate": release_gate,
            "evidence_bundle": evidence_bundle,
            "total_findings": len(confirmed),
        },
    )
    external_commercial_assets = _materialize_external_commercial_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        items=external_findings,
        external_reproduction_assets=external_reproduction_assets,
        scan_result={
            "project": project,
            "scan_id": scan_id,
            "campaign": campaign,
            "runtime_contract": runtime_contract,
            "release_gate": release_gate,
            "evidence_bundle": evidence_bundle,
            "total_findings": len(confirmed),
        },
    )
    preflight_guide = _scan_preflight_guide(
        context=context,
        base_url=base_url,
        manifest=manifest,
        runtime_contract=runtime_contract,
        test_data_plan=test_data_plan,
        diagnostics=diagnostics,
        runtime_observed=str(_as_dict(v12.get("auto_har")).get("status") or "") == "captured",
    )
    # A scan whose execution status is "plan_only" (no runtime target supplied,
    # so nothing was attempted) cannot complete and grades as "blocked", not a
    # clean "inconclusive". Note we key on the derived *execution_status*, not
    # runtime_contract.status: a discovery that planned-but-skipped (e.g. a
    # stubbed pipeline returning phases.execution.status == "skipped") still
    # grades "inconclusive" unless execution was genuinely blocked/plan_only.
    _rc_status = str(runtime_contract.get("status") or "")
    grade = "blocked" if _rc_status == "blocked" or execution_status == "blocked" or execution_status == "plan_only" else ("inconclusive" if not current_formal_findings else "evidence_ready")
    # ── 主链 4/5 覆盖诚实性守卫: never report a clean completion while high-value
    # (permission/isolation/money/concurrency) slices were silently unexecuted. ──
    coverage_honesty, grade = _apply_coverage_honesty_guard(v12, grade, execution_status)
    duration_ms = int((time.time() - started) * 1000)
    # result 尚未创建：先存局部，待 result 诞生后并入 scan_phase_timings。
    _planning_execution_ms = duration_ms
    # ── Honest data-layer verification summary aggregated from real findings ──
    _db_backed = [f for f in confirmed if isinstance(f, dict) and isinstance(f.get("db_evidence"), dict) and f["db_evidence"].get("status") == "captured"]
    _db_changed = [f for f in _db_backed if f["db_evidence"].get("any_change")]
    if _db_backed:
        db_verification = {
            "status": "captured",
            "reason": "runtime_before_after_db_snapshot",
            "findings_with_db_evidence": len(_db_backed),
            "findings_with_db_change": len(_db_changed),
            "findings": [
                {"title": f.get("title"), "changed_tables": f["db_evidence"].get("changed_tables", [])}
                for f in _db_changed
            ],
        }
    else:
        db_verification = {"status": "plan_only" if schema_text else "blocked", "reason": "source_bound_observation_contract_required" if schema_text else "database_schema_source_missing", "findings": []}
    # ── Score/coverage wired to real findings instead of a constant 0.0 ──
    # Only THIS run's formal deliveries may feed the headline score — verified
    # archive hold-overs are counted separately (total_archive_findings).
    score, coverage = _compute_scan_score(
        current_formal_findings, candidates, execution_status
    )
    # Product runtime may expose only GT-free coverage. Hidden-ground-truth
    # scoring belongs to the evaluator process and must never run in scan().
    benchmark_metrics: dict[str, Any] = {}
    try:
        from .risk_coverage_projection import (
            compute_product_coverage_projection,
            persist_product_coverage_projection,
        )
        benchmark_metrics = compute_product_coverage_projection(
            confirmed,
            candidates=candidates,
        )
        if benchmark_metrics:
            persist_product_coverage_projection(
                project,
                benchmark_metrics,
                root=root,
            )
    except Exception as benchmark_error:
        # Coverage computation failures remain explicit, but never trigger a
        # fallback to evaluator-private scoring.
        benchmark_metrics = {
            "benchmark_active": False,
            "ground_truth_available": False,
            "status": "FAILED_SAFE",
            "reason": "benchmark_compute_failed",
            "error": str(benchmark_error)[:400],
        }
        _LOGGER.warning(
            "benchmark_compute_failed status=FAILED_SAFE error=%s",
            benchmark_error,
        )
    ui_findings = v12.get("ui_findings") if isinstance(v12.get("ui_findings"), list) else []
    ui_candidate_findings = _ui_candidate_gate(ui_findings)
    ui_candidate_findings = _verify_ui_candidate_findings(ui_candidate_findings, root=root, runtime_contract=runtime_contract)
    ui_candidate_findings = _mark_high_confidence_ui_candidates(ui_candidate_findings)
    ui_candidate_findings = _bind_scan_rows_to_mainline(
        [dict(item) for item in ui_candidate_findings if isinstance(item, dict)],
        v12,
    )
    if ui_candidate_findings:
        candidates.extend(ui_candidate_findings)
    ui_execution = _as_dict(v12.get("ui_execution"))
    ui_execution_summary = _ui_execution_evidence_summary(ui_execution)
    external_signal_execution = _as_dict(v12.get("external_signal_execution"))
    ui_verified_candidates = [item for item in ui_candidate_findings if isinstance(item, dict) and isinstance(item.get("ui_verification"), dict) and item["ui_verification"].get("status") == "verified"]
    ui_high_confidence_candidates = [item for item in ui_candidate_findings if isinstance(item, dict) and item.get("high_confidence_candidate") is True]
    ui_followup_assets = _materialize_ui_followup_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        campaign=campaign,
        items=ui_high_confidence_candidates,
        selected_slices=selected_slices,
        plan_only_scenarios=v12.get("plan_only_scenarios") if isinstance(v12.get("plan_only_scenarios"), list) else [],
    )
    from .discovery_funnel import (
        build_funnel,
        build_funnel_report,
        reconcile_product_pipeline_health,
        write_funnel_report_files,
    )

    discovery_funnel = build_funnel(v12)
    discovery_funnel_report = build_funnel_report(
        v12,
        funnel=discovery_funnel,
    )
    pipeline_health = reconcile_product_pipeline_health(
        _as_dict(discovery_funnel.get("pipeline_health")),
        execution_status=execution_status,
        preflight_diagnostics=diagnostics,
    )
    discovery_funnel["pipeline_health"] = pipeline_health
    result: dict[str, Any] = {
        "success": True, "scan_id": scan_id, "project": project, "grade": grade, "score": score, "coverage": coverage,
        "total_findings": len(current_formal_findings),
        "total_archive_findings": len(archive_findings),
        "total_candidates": len(candidates), "total_ms": duration_ms,
        "layers": {
            "source_grounded_discovery": {"tool": "V12 enterprise campaign", "findings": len(confirmed), "candidates": len(candidates), "ms": int(v12.get("total_duration_ms") or duration_ms), "execution_status": execution_status, "campaign_id": campaign.get("campaign_id", "")},
            "external_signals": {
                "tool": "explicit_external_signal_requests",
                "findings": 0,
                "candidates": len(external_findings),
                "ms": int(external_signal_execution.get("duration_ms") or 0),
                "execution_status": str(external_signal_execution.get("status") or "not_requested"),
                "provider_distribution": dict(external_signal_execution.get("provider_distribution") or {}),
            },
            "ui_execution": {
                "tool": "explicit_ui_execution_requests",
                "findings": len(ui_findings),
                "candidates": len(ui_candidate_findings),
                "ms": int(ui_execution.get("duration_ms") or 0),
                "execution_status": str(ui_execution.get("status") or "not_requested"),
                "provider_distribution": dict(ui_execution.get("provider_distribution") or {}),
                "artifact_count": len(ui_execution.get("artifacts") or []),
                "evidence_captured_count": int(ui_execution_summary.get("evidence_captured_count") or 0),
                "created_data_count": int(ui_execution_summary.get("created_data_count") or 0),
                "verified_candidates": len(ui_verified_candidates),
                "high_confidence_candidates": len(ui_high_confidence_candidates),
            },
            "legacy_domain_layers": {"tool": "disabled", "findings": 0, "candidates": 0, "ms": 0, "reason": "source_bound_scope_fixture_actor_cleanup_contract_required" if multi_layer else "not_requested"},
        },
        "findings": confirmed, "candidate_findings": candidates, "db_findings": [], "e2e_findings": [], "ui_findings": ui_findings, "ui_candidate_findings": ui_candidate_findings, "ui_high_confidence_candidates": ui_high_confidence_candidates, "external_findings": external_findings, "deep_findings": [], "spectrum": {},
        # Strict current-formal / archive split: the merged ``findings`` list
        # keeps archive_entry markers for consumers that need the hold-over
        # view; the headline counts and score never mix them.
        "current_formal_findings": current_formal_findings,
        "archive_findings": archive_findings,
        "mainline_run": v12.get("mainline_run"),
        "obligation_attempt_ledger": v12.get("obligation_attempt_ledger"),
        # Fact ledger is promoted by discovery_mainline onto the v12 result;
        # expose it top-level so the fact-tracking report generator (and any
        # consumer of the composite scan result) reads the same SSOT instead
        # of an empty ledger nested under "v12".
        "fact_experimentability_ledger": v12.get("fact_experimentability_ledger"),
        "canonical_defect_registry": canonical_registry,
        "formal_delivery_authority": v12.get("formal_delivery_authority"),
        "formal_count_projection": canonical_scope["formal_count_projection"],
        "defect_identity_consistency": canonical_scope[
            "defect_identity_consistency"
        ],
        "delivery_occurrences": delivery_occurrences,
        "ui_followup_assets": ui_followup_assets,
        "p4_ui_evidence_bridge": p4_ui_evidence_bridge,
        "commercial_assets": commercial_assets,
        "external_reproduction_assets": external_reproduction_assets,
        "external_commercial_assets": external_commercial_assets,
        "preflight_diagnostics": diagnostics, "input_gaps": input_gaps, "coverage_gaps": coverage_gaps,
        "scan_preflight_guide": preflight_guide,
        "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "campaign": campaign, "test_data_bootstrap": test_data_bootstrap,
        "ui_test_data_bootstrap": ui_test_data_bootstrap,
        "behavior_slice_ledger": v12.get("behavior_slice_ledger", {}), "incremental_discovery": incremental,
        "execution_status": execution_status,
        "coverage_honesty": coverage_honesty,
        "db_verification": db_verification,
        "benchmark_metrics": benchmark_metrics,
        "dedupe_report": dedupe_report,
        "verified_archive_receipt": verified_archive_receipt,
        "discovery_verdict": _discovery_verdict(current_formal_findings, db_verification),
        "discovery_funnel": discovery_funnel,
        "discovery_funnel_report": discovery_funnel_report,
        "pipeline_health": pipeline_health,
        "ci_gate": {"status": "not_evaluated" if ci_gate else "not_requested", "reason": "confirmed_receipts_and_approved_baseline_required" if ci_gate else ""},
        "auto_har": v12.get("auto_har", {}), "evidence_bundle": evidence_bundle, "release_gate": release_gate, "ui_execution": ui_execution, "ui_execution_summary": ui_execution_summary, "execution_evidence_summary": ui_execution_summary, "external_signal_execution": external_signal_execution, "v12": v12,
    }
    # Closed-loop READ side observability: surface what knowledge this scan
    # consumed from the SQLite knowledge base (or why it failed to load).
    if context.get("learned_knowledge") is not None:
        result["learned_knowledge"] = context["learned_knowledge"]
    from .discovery_quality_projection import (
        attach_quality_projection_to_scan_result,
        suppress_benchmark_quality_when_not_measured,
    )

    result = attach_quality_projection_to_scan_result(result)
    # ── Authorization evidence gate ──
    # Only downgrade findings from permission-matrix-inferred operations
    # that lack real API endpoints. Original API findings are preserved.
    _downgraded = 0
    for _finding in (result.get("findings") or []):
        if _finding.get("risk_family") != "authorization":
            continue
        _evidence = _finding.get("evidence") or {}
        _control_ok = _evidence.get("control_succeeded")
        if _control_ok is False or str(_control_ok).lower() == "false":
            # Only downgrade if ALL source_refs are from inferred operations
            _srcs = _finding.get("source_refs") or []
            _all_inferred = all(
                str(s.get("kind", "")).startswith("permission_")
                for s in _srcs if isinstance(s, dict)
            ) if _srcs else False
            if _all_inferred:
                _finding["customer_delivery_status"] = "candidate"
                _finding["confirmation_status"] = "candidate"
                _finding["gate_override_reason"] = "INFERRED_OP_CONTROL_FAILED"
                _downgraded += 1
    if _downgraded:
        # The headline count may only reflect THIS run's formal deliveries.
        # Verified-archive hold-overs (archive_entry=True) are historical
        # rows from previous runs — counting them here made a delivery-less
        # run report total_findings=42 while current_formal_findings was 0.
        _formal = [
            f
            for f in (result.get("current_formal_findings") or [])
            if f.get("customer_delivery_status") == "defect"
        ]
        result["total_findings"] = len(_formal)
        # Keep the canonical projection self-consistent after demotion: the
        # count must equal the surviving canonical defect id set, otherwise
        # discovery_funnel._formal_projection fails the whole scan.
        _formal_canonical_ids = sorted({
            str(f.get("canonical_defect_id") or "").strip()
            for f in _formal
            if str(f.get("canonical_defect_id") or "").strip()
        })
        for _proj_key in ("formal_count_projection", "formal_delivery_authority"):
            _proj = _as_dict(result.get(_proj_key))
            if _proj:
                _proj["formal_customer_deliverable_count"] = len(_formal)
                _proj["canonical_defect_count"] = len(_formal)
                if _formal_canonical_ids:
                    _proj["canonical_defect_ids"] = _formal_canonical_ids
    result["benchmark_metrics"] = suppress_benchmark_quality_when_not_measured(
        _as_dict(result.get("benchmark_metrics")),
        _as_dict(result.get("external_evaluation")),
    )
    # Evidence-driven Harness evolution observability. This is derived only
    # from the completed real V12 run and persists redacted lineage/status
    # summaries; raw bodies, credentials, and benchmark ground truth are never
    # copied into the evolution artifacts.
    try:
        from .discovery_trace_ledger import build_discovery_trace_ledger
        from .discovery_weakness_miner import mine_discovery_weaknesses, persist_weakness_report
        from .discovery_harness_proposer import propose_harness_candidates, persist_harness_proposals
        from .enterprise_project_config import MultiServiceProject
        from .policy_wiring import get_effective_policy_strategy

        # Candidate evaluation runs use a ContextVar strategy override without
        # mutating the product registry.  The run context is the policy identity
        # authority; the global active registry would mislabel candidate traces.
        _policy_id = str(context.get("policy_id") or "").strip()
        if not _policy_id:
            raise RuntimeError("scan_policy_id_missing_from_campaign_context")
        _effective_policy_strategy = get_effective_policy_strategy()
        _industry = str(context.get("industry") or "").strip()
        if not _industry:
            _industry = str(MultiServiceProject(project, root).project_metadata().get("industry") or "").strip()
        if not _industry:
            _industry = "unclassified"
        _target_id = str(
            context.get("target_id")
            or context.get("scope_id")
            or context.get("environment_ref")
            or project
        ).strip()
        _evaluation_mode = str(context.get("evaluation_mode") or "operational").strip()
        _trace_ledger = build_discovery_trace_ledger(
            v12,
            run_id=str(context["run_id"]),
            policy_id=_policy_id,
            target_id=_target_id,
            project_id=project,
            industry=_industry,
            evaluation_mode=_evaluation_mode,
        )
        result["trace_ledger"] = _trace_ledger
        v12["trace_ledger"] = _trace_ledger
        _weakness_report = mine_discovery_weaknesses([_trace_ledger])
        _proposal_report = propose_harness_candidates(
            _weakness_report,
            _effective_policy_strategy,
        )
        _evolution_root = root / "platform_outputs" / _safe_project(project) / "discovery_evolution"
        # P0-4 Phase 4: Single Write — artifact store when enabled (trace
        # payload → TRACE_EVENT artifacts + TRACE_LEDGER metadata ref),
        # legacy trace_ledgers file only in the store-disabled fallback mode.
        from .trace_artifactization import persist_trace_ledger_output

        _trace_output = persist_trace_ledger_output(
            _trace_ledger,
            _evolution_root,
            root=root,
        )
        _weakness_path = persist_weakness_report(_weakness_report, _evolution_root / "weakness_reports")
        _proposal_path = persist_harness_proposals(_proposal_report, _evolution_root / "harness_proposals")
        result["discovery_evolution"] = {
            "status": "observed",
            "policy_id": _policy_id,
            "industry": _industry,
            "evaluation_mode": _evaluation_mode,
            "trace_count": int(_trace_ledger.get("trace_count") or 0),
            "outcome_counts": dict(_trace_ledger.get("outcome_counts") or {}),
            "failure_signature_counts": dict(_trace_ledger.get("failure_signature_counts") or {}),
            "weakness_pattern_count": int(_weakness_report.get("pattern_count") or 0),
            "proposal_eligible_pattern_count": int(_weakness_report.get("proposal_eligible_pattern_count") or 0),
            "selected_patterns_for_proposal": list(_weakness_report.get("selected_patterns_for_proposal") or []),
            "harness_proposal_count": int(_proposal_report.get("proposal_count") or 0),
            "blocked_proposal_pattern_count": int(_proposal_report.get("blocked_pattern_count") or 0),
            "trace_ledger_ref": _trace_output["ref"],
            "trace_ledger_mode": _trace_output["mode"],
            "weakness_report_ref": str(_weakness_path.relative_to(root)).replace("\\", "/"),
            "harness_proposals_ref": str(_proposal_path.relative_to(root)).replace("\\", "/"),
        }
    except Exception as evolution_error:
        # This feature is not allowed to disappear silently. An empty plan is
        # explicitly BLOCKED; a trace/mining failure is FAILED_SAFE. Neither may
        # claim readiness or promote a policy from this run.
        _attempt_ledger = _as_dict(v12.get("obligation_attempt_ledger"))
        _no_selected_obligations = int(
            _attempt_ledger.get("selected_count") or 0
        ) == 0
        result["discovery_evolution"] = {
            "status": "BLOCKED" if _no_selected_obligations else "FAILED_SAFE",
            "error_type": type(evolution_error).__name__,
            "error": str(evolution_error)[:500],
            "reason": (
                "NO_OBLIGATIONS_SELECTED"
                if _no_selected_obligations
                else "DISCOVERY_EVOLUTION_OBSERVABILITY_FAILED"
            ),
            "promotion_allowed": False,
        }
        coverage_gaps.append(
            _gap(
                (
                    "NO_OBLIGATIONS_SELECTED"
                    if _no_selected_obligations
                    else "DISCOVERY_EVOLUTION_OBSERVABILITY_FAILED"
                ),
                (
                    "No source-grounded obligations were selected; planning and policy promotion are blocked."
                    if _no_selected_obligations
                    else f"Trace ledger or weakness mining failed ({type(evolution_error).__name__}); policy promotion is blocked."
                ),
            )
        )
        _LOGGER.error(
            "discovery_evolution_failed status=%s error_type=%s error=%s",
            "BLOCKED" if _no_selected_obligations else "FAILED_SAFE",
            type(evolution_error).__name__,
            evolution_error,
        )
    result["discovery_funnel_report"] = build_funnel_report(
        result,
        funnel=_as_dict(result.get("discovery_funnel")),
    )
    # Chain positioning: one view answering 走到哪/卡在哪/损失在哪一步. Pure
    # projection of the receipts above; diagnostic guidance is synthetic and
    # never satisfies the delivery gate. Fail-soft: any missing receipt
    # degrades a stage with a visible note instead of failing the run.
    try:
        from .chain_positioning import build_chain_positioning

        _t_chain = time.perf_counter()
        result["discovery_chain_positioning"] = build_chain_positioning(result)
        _phase_time(result, "chain_positioning_ms", _t_chain)
    except Exception as _chain_exc:
        result["discovery_chain_positioning"] = {
            "schema_version": "qualibug.discovery-chain-positioning.v1",
            "status": "FAILED",
            "reason": f"{type(_chain_exc).__name__}:{str(_chain_exc)[:160]}",
            "stages": [],
        }
    if save_report:
        output = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = output / "intelligence_report.json"
        # P0-4 Phase 6: Single Write — the report is a logical read model.
        # Heavy payloads (obligation_attempt_ledger, delivery_occurrences, …)
        # and finding raw evidence are stored as artifacts and referenced;
        # only summary + artifact_refs land in the file (SPEC §25/§43).
        from .intelligence_report_artifactization import write_intelligence_report
        from .artifact_redactor import redact_and_validate as _redact_payload

        _report_bundle_ref = (
            evidence_bundle.get("artifact_manifest_ref")
            if isinstance(evidence_bundle, dict)
            else None
        )
        _report_payload = {
            "project": project,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "real_findings": confirmed,
            "findings": confirmed,
            "candidate_findings": candidates,
            "risk_clues": candidates,
            "mainline_run": v12.get("mainline_run"),
            "obligation_attempt_ledger": v12.get("obligation_attempt_ledger"),
            "canonical_defect_registry": canonical_registry,
            "formal_delivery_authority": v12.get("formal_delivery_authority"),
            "formal_count_projection": result.get("formal_count_projection"),
            "run_delivery_readiness": result.get("run_delivery_readiness"),
            "commercial_readiness": result.get("commercial_readiness"),
            "external_evaluation": result.get("external_evaluation"),
            "defect_identity_consistency": result.get("defect_identity_consistency"),
            "delivery_occurrences": delivery_occurrences,
            "campaign": campaign,
            "coverage_gaps": coverage_gaps,
            "scan_preflight_guide": preflight_guide,
            "runtime_contract": runtime_contract,
            "test_data_plan": test_data_plan,
            "test_data_bootstrap": test_data_bootstrap,
            "behavior_slice_ledger": result["behavior_slice_ledger"],
            "execution_status": execution_status,
            "coverage_honesty": coverage_honesty,
            "verified_archive_receipt": verified_archive_receipt,
            "evidence_bundle": evidence_bundle,
            "release_gate": result.get("release_gate"),
            "ui_execution_summary": ui_execution_summary,
            "execution_evidence_summary": ui_execution_summary,
            "ui_followup_assets": ui_followup_assets,
            "external_reproduction_assets": external_reproduction_assets,
            "external_commercial_assets": external_commercial_assets,
            "discovery_funnel": result.get("discovery_funnel"),
            "discovery_funnel_report": result.get("discovery_funnel_report"),
        }
        # SPEC §46: the report payload must be redacted before it enters the
        # write path — compact_intelligence_report fails closed on unredacted
        # material (report_payload_unredacted). The obligation-attempt ledger
        # carries password-assignment observation refs (before_ref/after_ref);
        # redact_and_validate rewrites them, reseals the ledger fingerprint
        # and re-derives the fingerprint-bound authority artifacts so the
        # persisted envelope stays self-consistent.
        _t_redact = time.perf_counter()
        _report_payload, _redaction_receipt = _redact_payload(_report_payload)
        _phase_time(result, "report_redact_ms", _t_redact)
        _t_report_write = time.perf_counter()
        result["report_artifactization"] = write_intelligence_report(
            report_path,
            _report_payload,
            root=root,
            bundle_manifest_ref=_report_bundle_ref,
        )
        _phase_time(result, "report_write_ms", _t_report_write)
        result["report_path"] = str(report_path)
        _t_aux = time.perf_counter()
        result["discovery_funnel_report_paths"] = write_funnel_report_files(
            result,
            output,
            funnel=_as_dict(result.get("discovery_funnel")),
        )
        try:
            from .fact_first_loss_ledger import write_fact_tracking_report_files

            result["fact_tracking_report_paths"] = write_fact_tracking_report_files(
                result,
                output,
            )
            result["fact_first_loss_ledger"] = result.get("fact_first_loss_ledger")
            result["fact_experimentability_report"] = result.get(
                "fact_experimentability_report"
            )
        except Exception as exc:
            _LOGGER.exception("fact_tracking_report_write_failed")
            result.setdefault("stage_failures", []).append(
                f"FACT_TRACKING_REPORT_FAILED:{type(exc).__name__}"
            )
        finally:
            _phase_time(result, "aux_reports_ms", _t_aux)
    # LLM call observability: aggregated per-call stats mounted on the scan
    # result (qualibug.llm-observability.v1). Metadata only — never prompt or
    # model-output content. Fail-soft: an observability failure must never
    # break the scan result.
    try:
        from .llm_reasoning import build_llm_observability_receipt

        result["llm_observability_receipt"] = build_llm_observability_receipt()
    except Exception as _llm_obs_exc:
        _LOGGER.exception("llm_observability_receipt_build_failed")
        result["llm_observability_receipt"] = {
            "schema_version": "qualibug.llm-observability.v1",
            "status": "FAILED",
            "reason": f"{type(_llm_obs_exc).__name__}:{str(_llm_obs_exc)[:160]}",
        }
    output_root = root / "platform_outputs" / _safe_project(project)
    _persist_started = time.time()
    _persist_receipt = write_scan_result(
        output_root / "scan_result.json",
        result,
        # 编译产物（v12 实验/义务计划快照）占 result ~90% 且与执行结果无关：
        # 落盘只保留身份行（findings/ledger/执行结果全量），落盘从 ~30min
        # 压到 ~2min。内存 result 不变（剪枝只发生在持久化副本）。
        prune_compiled_experiments=True,
    )
    # 阶段计时可观测（WARNING 级：INFO 会被产品日志根级别静默丢弃）。
    # 定位回测慢点必须能看到各阶段真实耗时，否则优化只能靠猜。
    _persist_timing = _as_dict(_persist_receipt).get("timing") or {}
    _LOGGER.warning(
        "scan_stage_timing project=%s planning_execution_ms=%s persist_ms=%s "
        "persist_stages=%s",
        project,
        duration_ms,
        int((time.time() - _persist_started) * 1000),
        _persist_timing,
    )
    result.setdefault("scan_phase_timings", {})["archive_merge_ms"] = _archive_merge_ms
    _LOGGER.warning(
        "[wrapup-trace] phase=archive_merge ms=%s",
        _archive_merge_ms,
    )
    result.setdefault("scan_phase_timings", {})["planning_execution_ms"] = _planning_execution_ms
    result.setdefault("scan_phase_timings", {})["persist_result_ms"] = int(
        (time.time() - _persist_started) * 1000
    )
    _LOGGER.warning(
        "[wrapup-trace] phase=persist_result ms=%s",
        int((time.time() - _persist_started) * 1000),
    )
    increment_scan_counter(output_root / "scan_counter.json")
    _t_customer = time.perf_counter()
    _persist_customer_ready_static_artifacts(project, root, result)
    _phase_time(result, "customer_static_artifacts_ms", _t_customer)

    # ── Phase 108R: Auto-generate Issue Lifecycle Center after scan ──
    # Acceptance Criterion 12: lifecycle center aggregates discovery + regression
    # states and auto-migrates bug status based on evidence.
    try:
        from .issue_lifecycle_center import build_issue_lifecycle_center
        _t_lifecycle = time.perf_counter()
        lifecycle = build_issue_lifecycle_center(project, root, options={"auto_generate_missing": False})
        _phase_time(result, "issue_lifecycle_ms", _t_lifecycle)
        result["lifecycle_center"] = {
            "ref": f"platform_outputs/{_safe_project(project)}/issue_lifecycle/issue_lifecycle.json",
            "summary": lifecycle.get("summary", {}),
            "active_issue_count": lifecycle.get("summary", {}).get("active_issue_count", 0),
        }
    except Exception as exc:
        _LOGGER.exception("issue_lifecycle_projection_failed")
        failure_code = f"ISSUE_LIFECYCLE_PROJECTION_FAILED:{type(exc).__name__}"
        result.setdefault("stage_failures", []).append(failure_code)
        result["lifecycle_center"] = {
            "status": "FAILED_SAFE",
            "error_type": type(exc).__name__,
            "stage": "issue_lifecycle_projection",
            "retryability": "after_operator_action",
        }
        health = _as_dict(result.get("pipeline_health"))
        if str(health.get("status") or "").upper() != "FAILED_SAFE":
            health["status"] = "DEGRADED"
        health.setdefault("stage_failures", []).append(failure_code)
        health["empty_findings_means_no_bugs"] = False
        result["pipeline_health"] = health

    # ── Closed-Loop Learning: extract patterns, persist to SQLite knowledge ──
    # The mainline consumes learned knowledge as a bounded ranking boost at
    # planning time (learning_knowledge_consumption.py); probe-pool files
    # without a mainline consumer are no longer produced.
    # Learning is gated on THIS run's formal deliveries (current_formal_findings),
    # never on the archive-merged ``confirmed`` list. Verified-archive hold-overs
    # were already learned in the run that first delivered them; re-running the
    # whole closed-loop over them every scan re-learns the same 120 defects and
    # bloats the SQLite KB, adding ~20min of post-processing to a regression run
    # that discovered nothing new. A regression run with zero new formal
    # deliveries therefore skips learning entirely.
    _t_closed_loop = time.perf_counter()
    try:
        from .closed_loop_feedback import build_closed_loop_context
        from .auto_learning_trigger import AutoLearningTrigger, LearningTriggerConfig

        if current_formal_findings:
            feedback = build_closed_loop_context(
                project, root, current_formal_findings, consumed_context=context.get("learned_knowledge")
            )
            result["closed_loop"] = {
                "patterns": feedback.get("total_patterns", 0),
                "new_patterns": feedback.get("new_this_scan", 0),
                "sqlite_storage": feedback.get("sqlite_storage", {}),
            }

            # ── Binding-experience WRITE: persist verified resolver mappings
            # (source-declared identities only) so the next scan reorders its
            # resolver candidates by verified success. This is the execution-
            # changing surface of the learning loop.
            try:
                from .binding_experience_learning import (
                    build_binding_experience_context,
                )

                binding_experience = build_binding_experience_context(
                    project, root, result
                )
                result["binding_experience"] = binding_experience
            except Exception as exc:
                result["binding_experience"] = {
                    "schema_version": "qualibug.binding-experience-write.v1",
                    "status": "FAILED",
                    "failure": f"{type(exc).__name__}:{str(exc)[:200]}",
                    "stored_count": 0,
                }

            # ── Cross-round knowledge transfer WRITE: record this round's
            # knowledge so the round history / insights are persisted for the
            # next scan's READ (observational provenance, not new sources).
            try:
                from .closed_loop_feedback import load_learned_scan_context
                from .cross_round_knowledge_transfer import CrossRoundKnowledgeTransfer

                transfer = CrossRoundKnowledgeTransfer(project)
                round_patterns = list(
                    (load_learned_scan_context(project) or {}).get("learned_patterns") or []
                )
                transfer.record_round_completion(
                    round_id=str(result.get("run_id") or result.get("scan_id") or "scan"),
                    risk_patterns=round_patterns,
                    effective_probes=[],
                    failure_patterns=[],
                    domains=[],
                    avg_confidence=0.8,
                )
                result["cross_round_transfer"] = {
                    "status": "recorded",
                    "patterns": len(round_patterns),
                }
            except Exception as exc:
                result["cross_round_transfer"] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}:{str(exc)[:200]}",
                }

            # ── Confirmed-bug flywheel signals → SQLite KB (loop closure):
            # signals extracted by the flywheel's pattern memory enter the
            # same knowledge base the planning boost / reasoner memory block
            # consume, instead of dying in an unread manifest file.
            try:
                from .confirmed_bug_flywheel import load_confirmed_bug_flywheel_profile
                from .learning_pattern_bridge import LearningPatternBridge

                flywheel_profile = load_confirmed_bug_flywheel_profile(project, root)
                flywheel_signals = (
                    (flywheel_profile.get("pattern_memory") or {}).get("extracted_signals")
                    if isinstance(flywheel_profile, dict) else None
                )
                if isinstance(flywheel_signals, list) and flywheel_signals:
                    flywheel_patterns = []
                    for signal in flywheel_signals:
                        if not isinstance(signal, dict):
                            continue
                        category = str(signal.get("category") or "uncategorized")
                        pattern_name = str(
                            signal.get("pattern_name") or f"learned_{category}"
                        )
                        flywheel_patterns.append({
                            "signature": f"flywheel:{pattern_name}",
                            "type": f"flywheel:{category}",
                            "entity": "",
                            "mutation_hint": "",
                            "count": int(signal.get("frequency") or 1),
                        })
                    stored = LearningPatternBridge(project=project).store_patterns(
                        flywheel_patterns,
                        scan_id="confirmed_bug_flywheel",
                        confidence=0.8,
                    )
                    result["flywheel_loopback"] = {
                        "status": "stored",
                        "patterns_stored": stored,
                    }
                else:
                    result["flywheel_loopback"] = {
                        "status": "no_signals",
                        "patterns_stored": 0,
                    }
            except Exception as exc:
                result["flywheel_loopback"] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}:{str(exc)[:200]}",
                }

            # ── V2.0: Auto-learning trigger (automated) ──
            try:
                trigger = AutoLearningTrigger(
                    project=project,
                    root=root,
                    config=LearningTriggerConfig(
                        min_confirmed_bugs=3,
                        schedule="after_each_scan",
                        dry_run=False,
                    ),
                )
                
                # Check conditions and execute
                should_learn, reason = trigger.should_trigger(result)
                _LOGGER.info("Auto-learning decision: %s (reason: %s)", 
                           "YES" if should_learn else "NO", reason)
                
                if should_learn:
                    learn_result = trigger.execute(scan_result=result)
                    result["auto_learning"] = {
                        "success": learn_result.success,
                        "rounds_analyzed": learn_result.rounds_analyzed,
                        "patterns_extracted": learn_result.patterns_extracted,
                        "new_probes_generated": learn_result.new_probes_generated,
                        "execution_time_seconds": learn_result.execution_time_seconds,
                        "risk_weights_updated": learn_result.risk_weights_updated,
                    }
                    _LOGGER.info("Auto-learning completed: %s", 
                               "SUCCESS" if learn_result.success else "FAILED")
            except Exception as e:
                _LOGGER.warning("Auto-learning trigger failed: %s", e)
                result.setdefault("stage_failures", []).append(
                    f"AUTO_LEARNING_FAILED:{type(e).__name__}"
                )
                
    except Exception as e:
        _LOGGER.exception("closed_loop_learning_failed")
        failure_code = f"CLOSED_LOOP_LEARNING_FAILED:{type(e).__name__}:{str(e)[:200]}"
        result.setdefault("stage_failures", []).append(failure_code)
        result["closed_loop"] = {
            "status": "failed_safe",
            "stage": "closed_loop_learning",
            "code": "CLOSED_LOOP_LEARNING_FAILED",
            "identity": {"project_id": project, "scan_id": result.get("scan_id")},
            "retryability": "after_operator_action",
            "operator_action": "Inspect closed-loop history and generated probe inputs, then rerun the campaign.",
            "error": f"{type(e).__name__}:{str(e)[:300]}",
        }
        pipeline_health = _as_dict(result.get("pipeline_health"))
        if str(pipeline_health.get("status") or "").upper() not in {"FAILED_SAFE"}:
            pipeline_health["status"] = "DEGRADED"
        pipeline_health["empty_findings_means_no_bugs"] = False
        pipeline_health.setdefault("stage_failures", []).append(failure_code)
        pipeline_health["operator_note"] = (
            "闭环学习阶段失败；已执行缺陷收据仍保留，但学习产物不可用，需修复后重跑。"
        )
        result["pipeline_health"] = pipeline_health
        funnel = _as_dict(result.get("discovery_funnel"))
        if funnel:
            funnel["pipeline_health"] = dict(pipeline_health)
            result["discovery_funnel"] = funnel

    # ── Learning-effect observation: persist executed-set diffs vs the
    # previous round of the same campaign (diagnostic observability only —
    # never recall/precision; those need evaluator-private GT).
    try:
        from .learning_effect_observation import write_learning_effect_report

        effect_report = write_learning_effect_report(project, root)
        result["learning_effect"] = {
            "status": effect_report.get("status"),
            "campaign_count": effect_report.get("campaign_count"),
            "round_count": effect_report.get("round_count"),
            "report_path": str(
                root / "platform_outputs" / project / "learning_effect" / "learning_effect_report.json"
            ),
        }
    except Exception as exc:
        result["learning_effect"] = {
            "status": "FAILED",
            "failure": f"{type(exc).__name__}:{str(exc)[:200]}",
        }
    _phase_time(result, "closed_loop_learning_ms", _t_closed_loop)

    return result



def scan(project: str, root: Optional[Path] = None, *, prd_text: str = "", api_doc_path: str = "", api_doc_text: str = "", base_url: str = "", ci_gate: bool = False, multi_layer: bool = True, output_dir: Optional[Path] = None, save_report: bool = True, campaign_context: Optional[dict[str, Any]] = None, on_started: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """Public scan entry — runs core discovery then first-class post-hooks."""
    from .scan_post_hooks import apply_scan_post_hooks
    from .artifact_store import artifact_store_enabled

    # ── Scan-result lifecycle: cleanup transients at start (always); the
    # legacy scan_result_archive rotation is the store-disabled fallback only
    # (SPEC §32). With the artifact store active, Run history is described by
    # Run Manifests and the RunRetentionManager owns the lifecycle — new runs
    # must not depend on scan_result_archive_* (SPEC AC-10).
    try:
        from .scan_result_retention import cleanup_transient_artifacts

        cleanup_transient_artifacts(str(project or "").strip(), root or Path.cwd())
    except Exception:
        # Retention must never block the scan; failures are visible in the
        # receipt only if the run completes far enough to attach it.
        pass
    try:
        from .run_retention_manager import cleanup_stale_scratch

        applied_scratch_receipt = cleanup_stale_scratch(root or Path.cwd())
    except Exception:
        applied_scratch_receipt = None

    result = _scan_impl(
        project,
        root,
        prd_text=prd_text,
        api_doc_path=api_doc_path,
        api_doc_text=api_doc_text,
        base_url=base_url,
        ci_gate=ci_gate,
        multi_layer=multi_layer,
        output_dir=output_dir,
        save_report=save_report,
        campaign_context=campaign_context,
        on_started=on_started,
    )
    resolved_root = Path(root or Path.cwd())
    _t_hooks_total = time.perf_counter()
    applied = apply_scan_post_hooks(
        result, project=str(project or "").strip(), root=resolved_root
    )
    _hooks_total_ms = int((time.perf_counter() - _t_hooks_total) * 1000)
    if isinstance(applied, dict):
        applied.setdefault("scan_phase_timings", {})[
            "post_hooks_total_ms"
        ] = _hooks_total_ms
    # WARNING 级汇总：post-hooks 在持久化之后运行，其耗时必须走日志留痕
    _LOGGER.warning(
        "scan_tail_timing project=%s post_hooks_total_ms=%s",
        project,
        _hooks_total_ms,
    )
    if applied_scratch_receipt is not None:
        applied["scratch_ttl_receipt"] = applied_scratch_receipt
    # Legacy fallback (store disabled): archive the previous scan_result
    # (index + parts) now that the new one has been persisted; only RETAIN
    # archives are kept. Store-enabled runs skip this — history lives in
    # manifests + artifacts.
    if save_report and not artifact_store_enabled():
        try:
            from .scan_result_retention import rotate_scan_result_archive

            applied["scan_result_retention_receipt"] = (
                rotate_scan_result_archive(
                    str(project or "").strip(),
                    resolved_root,
                )
            )
        except Exception:
            # Non-blocking; the run result is already complete.
            pass
    return applied


def main() -> None:
    from .scan_cli_entry import run_cli

    run_cli()


def _build_cli_campaign_context(args: Any) -> dict[str, Any]:
    from .scan_cli_entry import build_cli_campaign_context

    return build_cli_campaign_context(args)


if __name__ == "__main__":
    main()
