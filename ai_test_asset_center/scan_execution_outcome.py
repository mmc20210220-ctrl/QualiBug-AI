"""Scan execution evidence, release-gate, and outcome projection helpers.

Extracted from ``__main__``. Symbols are re-exported for compatibility.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from .enterprise_test_data_plan import build_campaign_test_data_plan
from .product_scan_mainline import _as_dict, _first_text, _safe_project, _sha256
from .scan_customer_ready_artifacts import _persist_customer_ready_static_artifacts
from .scan_diagnostics import increment_scan_counter
from .scan_source_runtime import _scan_preflight_guide


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist JSON only after unified recursive redaction + secret scan."""
    from .artifact_redactor import ArtifactSecretLeakError, write_json_redacted

    try:
        write_json_redacted(path, payload)
    except ArtifactSecretLeakError as exc:
        import sys as _sys

        print(
            f"[scan] FAILED_SAFE artifact secret scan blocked write to {path}: {exc}",
            file=_sys.stderr,
        )
        raise

def _test_data_receipt_verifier(root: Path, project: str):
    def verify(kind: str, receipt_id: str, campaign_id: str, scope_id: str, environment_ref: str) -> bool:
        try:
            from .enterprise_test_data_receipts import verify_test_data_receipt
            verdict = verify_test_data_receipt(project, receipt_id, root=root, kind=kind, campaign_id=campaign_id, scope_id=scope_id, environment_ref=environment_ref)
            return bool(verdict.get("valid"))
        except Exception as exc:
            import sys

            print(
                "[scan] test-data receipt verification failed: "
                f"kind={kind} receipt_id={receipt_id} error={type(exc).__name__}:{exc}",
                file=sys.stderr,
            )
            return False
    return verify


def _persist_execution_evidence(project: str, root: Path, scan_id: str, campaign: dict[str, Any], runtime_contract: dict[str, Any], execution_status: str, v12: dict[str, Any]) -> dict[str, Any]:
    from .evidence_artifact_store import persist_evidence_bundle
    findings = v12.get("findings") if isinstance(v12.get("findings"), list) else []
    formal_projection = _as_dict(v12.get("formal_count_projection"))
    canonical_representatives = (
        formal_projection.get("canonical_representative_findings")
        if isinstance(formal_projection.get("canonical_representative_findings"), list)
        else []
    )
    external_findings = v12.get("external_findings") if isinstance(v12.get("external_findings"), list) else []
    runtime_candidates = (
        v12.get("candidate_findings")
        if isinstance(v12.get("candidate_findings"), list)
        else []
    )
    registry = _as_dict(v12.get("canonical_defect_registry"))
    registry_ids = [
        str(value or "").strip()
        for value in registry.get("canonical_defect_ids", [])
    ] if isinstance(registry.get("canonical_defect_ids"), list) else []
    representative_rows = [
        dict(item) for item in canonical_representatives if isinstance(item, dict)
    ]
    representative_ids = [
        str(item.get("canonical_defect_id") or "").strip()
        for item in representative_rows
    ]
    declared_rows = [dict(item) for item in findings if isinstance(item, dict)]
    if registry:
        if representative_ids != registry_ids:
            raise ValueError("canonical_representative_scope_mismatch")
        persisted_findings = representative_rows
    else:
        persisted_findings = declared_rows
    persisted_candidates = [
        dict(item)
        for item in [*runtime_candidates, *external_findings]
        if isinstance(item, dict)
    ]
    return persist_evidence_bundle(
        project,
        root=root,
        run_id=scan_id,
        campaign=campaign,
        runtime_contract=runtime_contract,
        execution_status=execution_status,
        auto_har=_as_dict(v12.get("auto_har")),
        evidence_graphs=v12.get("evidence_graphs") if isinstance(v12.get("evidence_graphs"), list) else [],
        findings=persisted_findings,
        candidate_findings=persisted_candidates,
        canonical_defect_registry=registry,
        delivery_occurrences=(
            v12.get("delivery_occurrences")
            if isinstance(v12.get("delivery_occurrences"), list)
            else []
        ),
        ui_execution=_as_dict(v12.get("ui_execution")),
    )


def _evaluate_release_gate(*, project: str, root: Path, campaign: dict[str, Any], execution_status: str, runtime_contract: dict[str, Any], evidence_bundle: dict[str, Any], test_data_plan: dict[str, Any], findings: list[dict[str, Any]], coverage_gaps: list[dict[str, Any]], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    from .release_gate import evaluate_release_gate
    gate_policy = {"campaign_not_closed_verdict": "not_ready"}
    gate_policy.update(_as_dict(policy))
    verification: dict[str, Any] = {}
    if str(evidence_bundle.get("status") or "") == "persisted" and str(evidence_bundle.get("bundle_id") or ""):
        try:
            from .evidence_artifact_store import verify_evidence_bundle
            verification = verify_evidence_bundle(project, str(evidence_bundle["bundle_id"]), root=root)
        except Exception as exc:
            verification = {"valid": False, "code": f"EVIDENCE_BUNDLE_VERIFICATION_ERROR:{type(exc).__name__}"}
    return evaluate_release_gate(
        campaign=campaign,
        execution_status=execution_status,
        runtime_contract=runtime_contract,
        evidence_bundle=evidence_bundle,
        evidence_bundle_verification=verification,
        test_data_plan=test_data_plan,
        findings=findings,
        coverage_gaps=coverage_gaps,
        policy=gate_policy,
    )


def _blocked_result(project: str, root: Path, started: float, gaps: list[dict[str, str]], runtime_contract: dict[str, Any], context: dict[str, Any], save_report: bool, output_dir: Optional[Path]) -> dict[str, Any]:
    manifest = _as_dict(runtime_contract.get("source_manifest"))
    first_code = str(_as_dict(gaps[0]).get("code") or "SOURCE_CONTRACT_BLOCKED") if gaps else "SOURCE_CONTRACT_BLOCKED"
    campaign = {
        "campaign_id": "", "campaign_status": "blocked", "scope_id": str(context.get("scope_id") or ""),
        "environment_ref": str(context.get("environment_ref") or context.get("target_environment") or ""),
        "source_id": str(manifest.get("source_id") or ""), "source_hash": str(manifest.get("source_hash") or ""),
        "source_version_id": str(manifest.get("source_version_id") or ""), "source_origin": str(manifest.get("source_origin") or ""),
        "confirmed_slice_count": 0, "coverage_deferred_reason": first_code.lower(),
        "next_campaign_reason": "supply_registered_immutable_source" if first_code == "SOURCE_PROVENANCE_MISSING" else "correct_source_manifest_or_runtime_contract",
    }
    test_data_plan = build_campaign_test_data_plan(campaign, [], _as_dict(context.get("test_data_contract")), receipt_verifier=_test_data_receipt_verifier(root, project))
    coverage_gaps = gaps + list(test_data_plan.get("coverage_gaps") or [])
    evidence_bundle = {"status": "not_created", "reason": "scan_blocked"}
    release_gate = _evaluate_release_gate(project=project, root=root, campaign=campaign, execution_status="blocked", runtime_contract=runtime_contract, evidence_bundle=evidence_bundle, test_data_plan=test_data_plan, findings=[], coverage_gaps=coverage_gaps, policy=_as_dict(context.get("release_policy")))
    if first_code in {"SOURCE_PROVENANCE_MISSING", "SOURCE_HASH_INVALID", "SOURCE_HASH_MISMATCH"}:
        release_gate = {**release_gate, "verdict": "fail", "status": "blocked"}
    preflight_guide = _scan_preflight_guide(context=context, base_url="", manifest={**manifest, "actual_hash": manifest.get("source_hash", "")}, runtime_contract=runtime_contract, test_data_plan=test_data_plan)
    from .discovery_funnel import reconcile_product_pipeline_health

    discovery_funnel: dict[str, Any] = {}
    pipeline_health = reconcile_product_pipeline_health(
        {},
        execution_status="blocked",
        preflight_diagnostics={"ready": False, "all_checks_passed": False, "errors": 1},
    )
    discovery_funnel["pipeline_health"] = pipeline_health
    result: dict[str, Any] = {
        "success": True, "scan_id": f"scan_{_safe_project(project)}_{int(started * 1000)}", "project": project,
        "grade": "blocked", "score": 0.0, "coverage": 0.0, "total_findings": 0, "total_candidates": 0,
        "total_ms": int((time.time() - started) * 1000),
        "layers": {
            "source_grounded_discovery": {"tool": "blocked", "findings": 0, "candidates": 0, "ms": 0, "execution_status": "blocked"},
            "ui_execution": {"tool": "not_requested", "findings": 0, "candidates": 0, "ms": 0, "execution_status": "not_requested"},
            "legacy_domain_layers": {"tool": "disabled", "findings": 0, "candidates": 0, "ms": 0, "reason": "source_bound_scope_fixture_actor_cleanup_contract_required"},
        },
        "findings": [], "candidate_findings": [], "db_findings": [], "e2e_findings": [], "ui_findings": [], "deep_findings": [], "spectrum": {},
        "input_gaps": gaps, "coverage_gaps": coverage_gaps, "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "campaign": campaign,
        "behavior_slice_ledger": {"stop_reason": first_code.lower(), "selected_slice_ids": [], "confirmed_slice_ids": []},
        "incremental_discovery": {"status": "blocked", "stop_reason": first_code.lower()}, "execution_status": "blocked",
        "db_verification": {"status": "blocked", "reason": first_code.lower(), "findings": []},
        "ci_gate": {"status": "not_evaluated", "reason": first_code.lower()}, "auto_har": {"status": "no_traffic"},
        "evidence_bundle": evidence_bundle, "release_gate": release_gate, "scan_preflight_guide": preflight_guide, "ui_execution": {"status": "not_requested"}, "ui_test_data_bootstrap": {"status": "not_requested"}, "v12": {},
    }
    if save_report:
        output = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = output / "intelligence_report.json"
        _write_json(report_path, {"project": project, "real_findings": [], "risk_clues": [], "campaign": campaign, "coverage_gaps": coverage_gaps, "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "execution_status": "blocked", "evidence_bundle": evidence_bundle, "release_gate": release_gate})
        result["report_path"] = str(report_path)
    output_root = root / "platform_outputs" / _safe_project(project)
    from .scan_result_store import write_scan_result

    write_scan_result(output_root / "scan_result.json", result)
    increment_scan_counter(output_root / "scan_counter.json")
    _persist_customer_ready_static_artifacts(project, root, result)

    # ── Phase 108R: Auto-generate Issue Lifecycle Center after scan ──
    # Acceptance Criterion 12: lifecycle center aggregates discovery + regression
    # states and auto-migrates bug status based on evidence.
    try:
        from .issue_lifecycle_center import build_issue_lifecycle_center
        lifecycle = build_issue_lifecycle_center(project, root, options={"auto_generate_missing": False})
        result["lifecycle_center"] = {
            "ref": f"platform_outputs/{_safe_project(project)}/issue_lifecycle/issue_lifecycle.json",
            "summary": lifecycle.get("summary", {}),
            "active_issue_count": lifecycle.get("summary", {}).get("active_issue_count", 0),
        }
    except Exception as exc:
        import sys

        print(
            "[scan] issue lifecycle projection failed after blocked scan: "
            f"{type(exc).__name__}:{exc}",
            file=sys.stderr,
        )

    return result


def _compute_scan_score(confirmed: list[dict[str, Any]], candidates: list[dict[str, Any]], execution_status: str) -> tuple[float, float]:
    """Derive a score/coverage signal from real findings.

    Previously hardcoded to 0.0 regardless of outcome. Score rewards confirmed
    findings weighted by evidence strength; coverage reflects the share of
    executed work that reached a confirmed verdict.
    """
    if execution_status != "completed" and not confirmed:
        return 0.0, 0.0
    strength_weight = {"runtime_and_db": 1.0, "runtime_before_after": 0.8, "db": 0.75, "runtime": 0.6}
    total = 0.0
    for f in confirmed:
        eq = f.get("evidence_quality") if isinstance(f.get("evidence_quality"), dict) else {}
        strength = str(eq.get("evidence_strength") or f.get("evidence_strength") or "runtime")
        total += strength_weight.get(strength, 0.6)
    score = round(min(100.0, total * 10.0), 2)
    denom = len(confirmed) + len(candidates)
    coverage = round(len(confirmed) / denom, 4) if denom else (1.0 if confirmed else 0.0)
    return score, coverage


# High-risk slice kinds a customer cares most about: authorization / tenant
# isolation / money conservation / concurrency. Silently skipping any of these
# while reporting a clean completion is a delivery-credibility hazard.
_HIGH_VALUE_SLICE_KINDS = ("permission", "isolation", "money", "concurrency")


def _apply_coverage_honesty_guard(
    v12: dict[str, Any], grade: str, execution_status: str
) -> tuple[dict[str, Any], str]:
    """主链 4/5 覆盖诚实性守卫 (coverage honesty guard).

    The behavior model routinely plans more slices than a single campaign can
    execute — permission / isolation / money / concurrency slices often need a
    multi-actor runtime contract (multiple logged-in principals + state
    preconditions). When those high-value slices are never executed but the
    campaign still reports a clean 'inconclusive'/'evidence_ready' completion, a
    customer could read "completed / 0 findings" while EVERY authorization check
    was silently skipped.

    This guard makes that non-silent: it lists the unexecuted high-value slices
    and downgrades a *completed* clean grade to 'partial_coverage'. It only ever
    ADDS signal and only downgrades — it never upgrades a grade nor fabricates
    coverage. Single source of truth: the v12 ``behavior_slices`` contract +
    ``behavior_slice_ledger`` execution status (same producers as 主链 4).
    """
    slices = v12.get("behavior_slices") if isinstance(v12.get("behavior_slices"), list) else []
    ledger = v12.get("behavior_slice_ledger") if isinstance(v12.get("behavior_slice_ledger"), dict) else {}
    slice_status = ledger.get("slice_status") if isinstance(ledger.get("slice_status"), dict) else {}
    attempted_ids = {str(x) for x in (ledger.get("attempted_slice_ids") or []) if x}
    _attempted_states = {"running", "passed", "failed", "confirmed", "blocked"}

    high_value_total = 0
    unexecuted: list[dict[str, Any]] = []
    for s in slices:
        if not isinstance(s, dict):
            continue
        kind = str(s.get("kind") or "").strip().lower()
        if kind not in _HIGH_VALUE_SLICE_KINDS:
            continue
        high_value_total += 1
        sid = str(s.get("slice_id") or s.get("id") or "")
        state = str(slice_status.get(sid) or "").strip().lower()
        executed = (state in _attempted_states) or (bool(sid) and sid in attempted_ids)
        if not executed:
            unexecuted.append({
                "slice_id": sid,
                "kind": kind,
                "entity": s.get("entity"),
                "endpoints": list(s.get("endpoints") or [])[:6],
                "status": state or "not_executed",
            })

    honesty: dict[str, Any] = {
        "high_value_kinds": list(_HIGH_VALUE_SLICE_KINDS),
        "total_slices": len(slices),
        "high_value_total": high_value_total,
        "high_value_unexecuted": len(unexecuted),
        "unexecuted_high_value_slices": unexecuted[:50],
        "honest": len(unexecuted) == 0,
        "downgraded": False,
    }
    # ── Distinguish: 'resumable partial' (campaign is still active with a
    # concrete next_round — the customer can re-run the scan to continue) vs
    # 'terminal partial' (campaign claims completed yet high-value slices were
    # silently skipped — genuine credibility risk). ──
    _campaign = v12.get("campaign") if isinstance(v12.get("campaign"), dict) else {}
    _campaign_status = str(_campaign.get("campaign_status") or ledger.get("campaign_status") or "").strip().lower()
    _next_round = ledger.get("next_round")
    _has_next = bool(
        (_next_round is not None and str(_next_round).strip().isdigit() and int(_next_round) > 0)
    )
    honesty["campaign_status"] = _campaign_status or "unknown"
    honesty["next_round"] = _next_round
    honesty["resumable"] = _campaign_status == "active" and _has_next
    honesty["terminal_skip"] = _campaign_status == "completed" and not honesty["honest"]
    if honesty["terminal_skip"]:
        honesty["actionable"] = (
            "Campaign marked completed but high-value slices (permission/isolation/"
            "money/concurrency) were never executed — this is a genuine coverage gap "
            "that may hide authorization or data-integrity defects."
        )
    elif honesty["resumable"]:
        honesty["actionable"] = (
            "Campaign is still active — re-run the scan to continue from the "
            "next round and cover the remaining high-value slices."
        )
    # Only act on a *completed* campaign: a blocked / not-executed scan already
    # signals incompleteness through its own grade, and the phase-110 contract
    # tests rely on those staying 'blocked'/'inconclusive'.
    if execution_status == "completed" and unexecuted and grade in {"inconclusive", "evidence_ready"}:
        honesty["grade_before_guard"] = grade
        honesty["downgraded"] = True
        grade = "partial_coverage"
    return honesty, grade


def _discovery_verdict(confirmed: list[dict[str, Any]], db_verification: dict[str, Any]) -> dict[str, Any]:
    """Product-facing verdict: did QualiBug deliver reproducible defects?

    Kept separate from release_gate (which answers "is the target safe to
    ship?" and therefore *fails* precisely because P0 defects were found).
    """
    p0 = sum(1 for f in confirmed if str(f.get("severity") or "").upper() in {"P0", "CRITICAL"})
    db_backed = int(db_verification.get("findings_with_db_evidence") or 0) if isinstance(db_verification, dict) else 0
    if confirmed:
        verdict = "defects_delivered"
    else:
        verdict = "no_confirmed_defects"
    return {
        "verdict": verdict,
        "confirmed_defect_count": len(confirmed),
        "confirmed_p0_count": p0,
        "defects_with_db_evidence": db_backed,
    }


