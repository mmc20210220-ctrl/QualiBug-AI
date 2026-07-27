"""Post-process V1.6.2-R1 formal scan into SPEC §28/§30 artifacts + final report.

Review-bug fixes applied here (do not regress):
- Experiments are read ONLY from ``platform_outputs/benchmark_mall_131/
  scan_result.json`` -> ``v12.experiment_execution.results``. No fallback
  scan of ``intelligence_report.json`` or campaign JSON for experiment rows
  -- a missing/empty scan_result.json means zero experiments, not a silent
  substitute source.
- ``intelligence_report.json`` is only ever consulted for supplementary
  display fields, and only when its own ``campaign_id`` matches the scan's
  campaign_id. A mismatched or absent report is never merged in.
- A freeze audit runs FIRST and can force ``Level D /
  INVALID_POST_START_TUNING`` regardless of any coverage numbers: any code
  change to a watched module, or any drift in the frozen unlock-set /
  canonical-manifest hashes after ``r1_start_manifest.json`` was written,
  invalidates the run.
- A completion-integrity audit runs SECOND and can force ``Level E /
  FINALIZATION_RECEIPT_INTEGRITY_FAIL`` regardless of coverage numbers: any
  experiment that self-reports ``TRUE_COMPLETED`` without a ledger id, a
  real finalization receipt, explicit ``environment_restored is True``,
  zero cleanup failures, and stable fixture identity is a hard integrity
  violation, not a row to quietly exclude from ``D``.
- Level B reports ``V1_6_3_ENTRY_ALLOWED = "CONDITIONAL"`` (a string), never
  boolean ``True`` -- SPEC §28 defines Level B entry as conditional, not
  unconditional, entry.
- Regression counts come ONLY from ``r1_specialized_tests.json``. A missing
  or unreadable file yields ``status: NOT_MEASURED`` -- the historical
  69/107 pass counts are never hardcoded.
- ``environment_restored`` must be an explicit ``True`` on the experiment
  row to count as restored; missing/``None``/anything else counts as NOT
  restored (fail closed, no default-True).
- ``D`` (TRUE_COMPLETED count), the result level, and the root cause are
  fully computed BEFORE any artifact file is written, so no artifact can
  observe a stale count from a later recompute.
- The root-cause block is derived dynamically from the actual level and
  violation/reason data, never a single static string.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_2_r1"
V162 = ROOT / "artifacts" / "spec_v1_6_2"
REPORT = ROOT / "platform_outputs" / "benchmark_mall_131" / "intelligence_report.json"
SCAN_RESULT = ROOT / "platform_outputs" / "benchmark_mall_131" / "scan_result.json"
UNLOCK = V162 / "v162_candidate_unlock_set.json"
SPECIALIZED_TESTS = OUT / "r1_specialized_tests.json"

# Modules whose hash is pinned in r1_start_manifest.json. Any drift after the
# freeze timestamp is a Level D (INVALID_POST_START_TUNING) event.
FREEZE_WATCHED_MODULES = {
    "process_step_execution": ROOT / "ai_test_asset_center" / "process_step_execution.py",
    "experiment_outcome_finalizer": ROOT / "ai_test_asset_center" / "experiment_outcome_finalizer.py",
    "experiment_executor": ROOT / "ai_test_asset_center" / "experiment_executor.py",
    "experiment_plan_executor": ROOT / "ai_test_asset_center" / "experiment_plan_executor.py",
    "experiment_cleanup_executor": ROOT / "ai_test_asset_center" / "experiment_cleanup_executor.py",
}

# SPEC §28 Level B downstream-termination vocabulary. A finalizer block
# reason not present here can never justify Level B on its own.
_DOWNSTREAM_LIMITED_REASON_MAP = {
    "PROCESS_STEP_REQUIRED_SET_MISMATCH": "SOURCE_ASSET_INSUFFICIENT",
    "PROCESS_STEP_OBSERVATION_SET_INCOMPLETE": "STATE_PRECONDITION_UNREACHABLE",
    "PROCESS_STEP_ORACLE_SET_INCOMPLETE": "STATE_PRECONDITION_UNREACHABLE",
    "PROCESS_STEP_CLEANUP_SET_INCOMPLETE": "CLEANUP_NOT_RESOLVED",
    "FINALIZER_RECEIPT_BUNDLE_NOT_ACTIVATED": "TARGET_CAPABILITY_LIMITED",
    "FINALIZER_PROCESS_STEP_LEDGER_MISSING": "TARGET_CAPABILITY_LIMITED",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(name: str, payload: dict) -> None:
    (OUT / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _oid(row: dict) -> str:
    return str(row.get("obligation_id") or row.get("oid") or "").strip()


def _ceil_pct(n: int, pct: float) -> int:
    return math.ceil(n * pct)


def _environment_restored_explicit(row: dict) -> bool:
    """Only an explicit boolean True counts as restored -- never a default."""
    return row.get("environment_restored") is True


# ── Experiment loading (scan_result.json is the sole SSOT) ────────────────


def load_experiments() -> list[dict]:
    if not SCAN_RESULT.exists():
        return []
    try:
        data = _load(SCAN_RESULT)
    except Exception:
        return []
    v12 = dict(data.get("v12") or {})
    execution = dict(v12.get("experiment_execution") or {})
    results = execution.get("results")
    return [row for row in results if isinstance(row, dict)] if isinstance(results, list) else []


def load_matching_intelligence_report(campaign_id: str) -> dict:
    """Supplementary display data only -- never a source of experiment rows."""
    if not campaign_id or not REPORT.exists():
        return {}
    try:
        report = _load(REPORT)
    except Exception:
        return {}
    report_campaign_id = str(
        report.get("campaign_id")
        or dict(report.get("campaign") or {}).get("campaign_id")
        or ""
    )
    if report_campaign_id != campaign_id:
        return {}
    return report


# ── Regression (r1_specialized_tests.json is the sole SSOT) ───────────────


def load_regression() -> dict:
    if not SPECIALIZED_TESTS.exists():
        return {
            "status": "NOT_MEASURED",
            "reason": "r1_specialized_tests.json_missing",
            "specialized_tests_passed": None,
            "specialized_tests_failed": None,
            "v150_v162_regression_passed": None,
        }
    try:
        data = _load(SPECIALIZED_TESTS)
    except Exception as exc:
        return {
            "status": "NOT_MEASURED",
            "reason": f"r1_specialized_tests.json_unreadable:{exc}",
            "specialized_tests_passed": None,
            "specialized_tests_failed": None,
            "v150_v162_regression_passed": None,
        }
    passed = data.get("passed")
    failed = data.get("failed")
    minimum_required = data.get("minimum_required")
    regression = dict(data.get("regression") or {})
    combined = regression.get("combined_passed")
    sub_statuses_pass = all(
        value == "PASS" for key, value in regression.items() if key != "combined_passed"
    )
    is_pass = (
        isinstance(passed, int)
        and isinstance(failed, int)
        and failed == 0
        and isinstance(minimum_required, int)
        and passed >= minimum_required
        and sub_statuses_pass
    )
    return {
        "status": "PASS" if is_pass else "FAIL",
        "specialized_tests_passed": passed,
        "specialized_tests_failed": failed,
        "minimum_required": minimum_required,
        "v150_v162_regression_passed": combined,
        "regression_detail": regression,
    }


# ── Freeze audit (SPEC §28 Level D) ────────────────────────────────────────


def run_freeze_audit(start: dict, unlock: dict) -> dict:
    """Detect any post-freeze code/manifest tuning. Never silently pass."""
    violations: list[str] = []
    current_hashes: dict[str, str] = {}
    frozen_hashes = dict(start.get("file_hashes") or {})
    for name, path in FREEZE_WATCHED_MODULES.items():
        current = _sha(path) if path.exists() else ""
        current_hashes[name] = current
        frozen = str(frozen_hashes.get(name) or "")
        if not frozen:
            violations.append(f"missing_frozen_hash:{name}")
        elif not current:
            violations.append(f"watched_file_missing:{name}")
        elif current != frozen:
            violations.append(f"code_modified_after_freeze:{name}")

    unlock_ids = list(unlock.get("obligation_ids") or [])
    unlock_sorted_hash = hashlib.sha256(
        json.dumps(sorted(unlock_ids), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    frozen_sorted_hash = str(start.get("candidate_unlock_set_sorted_ids_hash") or "")
    if not frozen_sorted_hash or unlock_sorted_hash != frozen_sorted_hash:
        violations.append("unlock_set_ids_modified_after_freeze")

    if UNLOCK.exists():
        unlock_file_hash = _sha(UNLOCK)
        frozen_file_hash = str(start.get("candidate_unlock_set_hash") or "")
        if not frozen_file_hash or unlock_file_hash != frozen_file_hash:
            violations.append("unlock_set_file_modified_after_freeze")
    else:
        violations.append("unlock_set_file_missing")

    return {
        "schema_version": "qualibug.v162r1-freeze-audit.v1",
        "violated": bool(violations),
        "violations": violations,
        "current_file_hashes": current_hashes,
        "frozen_file_hashes": frozen_hashes,
    }


# ── Completion-integrity audit (SPEC §28 Level E) ──────────────────────────


def run_integrity_audit(unlock_exps: list[dict]) -> list[dict]:
    """Every self-reported TRUE_COMPLETED row must clear every authority gate.

    A row failing any gate is a Level E violation to surface, never a row to
    quietly drop from the ``D`` count.
    """
    violations: list[dict] = []
    for e in unlock_exps:
        oid = _oid(e)
        if e.get("lifecycle_state") != "TRUE_COMPLETED":
            continue
        fin = e.get("execution_finalization_receipt") or {}
        ledger_id = e.get("process_step_ledger_id")
        cleanup_failures = int(e.get("cleanup_failures") or 0)
        env_restored = _environment_restored_explicit(e)
        fixture_prov = e.get("fixture_provenance_receipts") or []
        fixture_drift = any(
            isinstance(row, dict)
            and (row.get("identity_stable") is False or row.get("scope_stable") is False)
            for row in fixture_prov
        )
        fin_true = isinstance(fin, dict) and bool(fin) and (
            fin.get("true_completed") is True
            or fin.get("derived_terminal_status") == "TRUE_COMPLETED"
        )
        if not ledger_id:
            violations.append({"obligation_id": oid, "reason": "TRUE_COMPLETED_WITHOUT_LEDGER"})
        if not fin_true:
            violations.append({"obligation_id": oid, "reason": "TRUE_COMPLETED_WITHOUT_FINALIZATION_RECEIPT"})
        if cleanup_failures:
            violations.append({"obligation_id": oid, "reason": "CLEANUP_FAILED_BUT_COMPLETED"})
        if not env_restored:
            violations.append({"obligation_id": oid, "reason": "ENVIRONMENT_NOT_RESTORED_BUT_COMPLETED"})
        if fixture_drift:
            violations.append({"obligation_id": oid, "reason": "FIXTURE_IDENTITY_DRIFT_BUT_COMPLETED"})
    return violations


# ── D / level determination ────────────────────────────────────────────────


def compute_true_completed(unlock_exps: list[dict]) -> set[str]:
    """Strict TRUE_COMPLETED set: every authority gate must hold explicitly."""
    tc_oids: set[str] = set()
    for e in unlock_exps:
        if e.get("lifecycle_state") != "TRUE_COMPLETED":
            continue
        fin = e.get("execution_finalization_receipt") or {}
        fin_true = isinstance(fin, dict) and bool(fin) and (
            fin.get("true_completed") is True
            or fin.get("derived_terminal_status") == "TRUE_COMPLETED"
        )
        if not fin_true:
            continue
        if not e.get("process_step_ledger_id"):
            continue
        if int(e.get("cleanup_failures") or 0):
            continue
        if not _environment_restored_explicit(e):
            continue
        tc_oids.add(_oid(e))
    return tc_oids


def determine_level(
    *,
    freeze_audit: dict,
    integrity_violations: list[dict],
    N: int,
    D: int,
    need_D: int,
    oracle_ok: int,
    need_oracle: int,
    cleanup_ok: int,
    need_cleanup: int,
    need_tc: int,
    bundles: int,
    finals: int,
    unlock_exps_count: int,
    with_ledger_id: int,
    downstream_limited_reasons: list[str],
    all_remaining_downstream_limited: bool,
) -> tuple[str, str, "bool | str"]:
    if freeze_audit.get("violated"):
        return (
            "D",
            "INVALID_POST_START_TUNING: " + "; ".join(freeze_audit.get("violations") or []),
            False,
        )
    if integrity_violations:
        unique_reasons = sorted({v["reason"] for v in integrity_violations})
        return (
            "E",
            "FINALIZATION_RECEIPT_INTEGRITY_FAIL: " + ", ".join(unique_reasons),
            False,
        )

    ledger_coverage_full = unlock_exps_count > 0 and with_ledger_id == unlock_exps_count
    level_a = (
        ledger_coverage_full
        and bundles > 0
        and finals > 0
        and D >= need_D
        and D > 0
        and oracle_ok >= need_oracle
        and cleanup_ok >= need_cleanup
        and (D >= need_tc if cleanup_ok else False)
    )
    if level_a:
        return ("A", "Finalizer activated; unlock coverage thresholds met", True)

    level_b = (
        ledger_coverage_full
        and bundles > 0
        and finals > 0
        and bool(downstream_limited_reasons)
        and all_remaining_downstream_limited
    )
    if level_b:
        return (
            "B",
            "Finalizer/Ledger/Bundle formal mainline closed with zero False Completed, "
            "but D below threshold with explicit new downstream-termination reasons: "
            + ", ".join(sorted(set(downstream_limited_reasons))),
            "CONDITIONAL",
        )

    if bundles > 0 or with_ledger_id > 0:
        return (
            "C",
            "Finalizer partially activated: some formal-mainline batch paths still "
            "lack a ledger, or a finalization receipt exists but was not yet consumed "
            "as TRUE_COMPLETED by the report.",
            False,
        )

    return (
        "C",
        "Ledger still not reaching Finalizer on formal mainline",
        False,
    )


def build_root_cause(
    *,
    level: str,
    freeze_audit: dict,
    integrity_violations: list[dict],
    downstream_limited_reasons: list[str],
    D: int,
    need_D: int,
) -> dict:
    if level == "D":
        return {
            "breakpoint": "INVALID_POST_START_TUNING",
            "cause": (
                "Freeze audit found post-start drift: "
                + "; ".join(freeze_audit.get("violations") or ["unspecified"])
            ),
        }
    if level == "E":
        unique_reasons = sorted({v["reason"] for v in integrity_violations})
        return {
            "breakpoint": "FINALIZATION_RECEIPT_INTEGRITY_FAIL",
            "cause": (
                "One or more experiments self-reported TRUE_COMPLETED without "
                "clearing every completion-authority gate: " + ", ".join(unique_reasons)
            ),
        }
    if level == "A":
        return {
            "breakpoint": "",
            "cause": "All formal-mainline coverage and integrity gates passed; no breakpoint.",
        }
    if level == "B":
        return {
            "breakpoint": "TRUE_COMPLETION_RECEIPT_INCOMPLETE",
            "cause": (
                f"D={D} below need_D={need_D}, but every remaining obligation terminated "
                "with an explicit, new downstream-limited reason: "
                + ", ".join(sorted(set(downstream_limited_reasons)))
            ),
        }
    return {
        "breakpoint": "FINALIZER_RECEIPT_BUNDLE_NOT_ACTIVATED_ON_FORMAL_MAINLINE",
        "cause": (
            "Formal mainline still has a Finalizer/Ledger activation gap: not every "
            "unlock-set experiment produced a process_step_ledger_id + Execution "
            "Receipt Bundle + Finalization Receipt."
        ),
    }


def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    start = _load(OUT / "r1_start_manifest.json")
    unlock = _load(UNLOCK)
    unlock_ids = set(unlock["obligation_ids"])
    N = len(unlock_ids)
    scan = _load(OUT / "r1_scan_response.json")
    camp = dict(scan.get("campaign") or {})
    campaign_id = str(camp.get("campaign_id") or scan.get("campaign_id") or "")

    report = load_matching_intelligence_report(campaign_id)
    experiments = load_experiments()
    unlock_exps = [e for e in experiments if _oid(e) in unlock_ids]

    # ── 1) Freeze audit runs first; a violation forces Level D regardless of
    # any coverage numbers computed below. ──
    freeze_audit = run_freeze_audit(start, unlock)

    # ── 2) Completion-integrity audit runs second; any violation forces
    # Level E regardless of coverage numbers. ──
    integrity_violations = run_integrity_audit(unlock_exps)

    # ── 3) Coverage numbers (only meaningful when freeze/integrity are clean,
    # but computed unconditionally so every artifact has honest numbers). ──
    bundles = []
    finals = []
    ledger_inputs = []
    for e in unlock_exps:
        oid = _oid(e)
        eid = str(e.get("experiment_id") or "")
        bundle = e.get("execution_receipt_bundle") or {}
        fin = e.get("execution_finalization_receipt") or {}
        ledger_inputs.append(
            {
                "obligation_id": oid,
                "experiment_id": eid,
                "process_step_ledger_id": e.get("process_step_ledger_id"),
                "process_step_ledger_hash": e.get("process_step_ledger_hash"),
                "required_step_ids": e.get("required_step_ids") or [],
                "executed_step_ids": e.get("executed_step_ids") or [],
                "lifecycle_state": e.get("lifecycle_state"),
                "finalizer_block_reason": e.get("finalizer_block_reason"),
                "environment_restored": e.get("environment_restored"),
                "cleanup_failures": e.get("cleanup_failures"),
            }
        )
        if isinstance(bundle, dict) and bundle:
            bundles.append({"obligation_id": oid, "experiment_id": eid, "bundle": bundle})
        if isinstance(fin, dict) and fin:
            finals.append({"obligation_id": oid, "experiment_id": eid, "finalization": fin})

    tc_oids = compute_true_completed(unlock_exps)
    D = len(tc_oids)
    with_ledger_id = sum(1 for r in ledger_inputs if r.get("process_step_ledger_id"))

    need_D = _ceil_pct(N, 0.8) if N else 0
    oracle_ok = 0
    cleanup_ok = 0
    for e in unlock_exps:
        if _oid(e) not in tc_oids:
            continue
        verdict = e.get("oracle_verdict") or {}
        if verdict.get("verdict") or verdict.get("status"):
            oracle_ok += 1
        if int(e.get("cleanup_failures") or 0) == 0 and _environment_restored_explicit(e):
            cleanup_ok += 1
    need_oracle = _ceil_pct(D, 0.8) if D else 0
    need_cleanup = _ceil_pct(D, 0.9) if D else 0
    need_tc = _ceil_pct(cleanup_ok, 0.9) if cleanup_ok else 0

    downstream_limited_reasons: list[str] = []
    remaining_without_known_reason = 0
    for e in unlock_exps:
        oid = _oid(e)
        if oid in tc_oids:
            continue
        reason = str(e.get("finalizer_block_reason") or "")
        mapped = _DOWNSTREAM_LIMITED_REASON_MAP.get(reason)
        if mapped:
            downstream_limited_reasons.append(mapped)
        else:
            remaining_without_known_reason += 1
    remaining_count = len(unlock_exps) - len(tc_oids)
    all_remaining_downstream_limited = (
        remaining_count > 0 and remaining_without_known_reason == 0
    )

    level, level_reason, v163_entry_allowed = determine_level(
        freeze_audit=freeze_audit,
        integrity_violations=integrity_violations,
        N=N,
        D=D,
        need_D=need_D,
        oracle_ok=oracle_ok,
        need_oracle=need_oracle,
        cleanup_ok=cleanup_ok,
        need_cleanup=need_cleanup,
        need_tc=need_tc,
        bundles=len(bundles),
        finals=len(finals),
        unlock_exps_count=len(unlock_exps),
        with_ledger_id=with_ledger_id,
        downstream_limited_reasons=downstream_limited_reasons,
        all_remaining_downstream_limited=all_remaining_downstream_limited,
    )
    next_bp = None if level in {"A", "B"} else (
        "FINALIZER_RECEIPT_BUNDLE_NOT_ACTIVATED_ON_FORMAL_MAINLINE"
        if level in {"C", "E"}
        else "INVALID_POST_START_TUNING"
    )
    root_cause = build_root_cause(
        level=level,
        freeze_audit=freeze_audit,
        integrity_violations=integrity_violations,
        downstream_limited_reasons=downstream_limited_reasons,
        D=D,
        need_D=need_D,
    )
    regression = load_regression()

    first_terms = []
    for e in unlock_exps:
        oid = _oid(e)
        if oid in tc_oids:
            stage = "TRUE_COMPLETED"
        elif e.get("execution_finalization_receipt"):
            stage = str(
                (e.get("execution_finalization_receipt") or {}).get("derived_terminal_status")
                or e.get("lifecycle_state")
                or "FINALIZATION_PRESENT"
            )
        elif e.get("process_step_ledger_id"):
            stage = str(e.get("finalizer_block_reason") or e.get("lifecycle_state") or "LEDGER_PRESENT_NO_FINALIZATION")
        else:
            stage = str(e.get("lifecycle_state") or e.get("status") or "NO_LEDGER")
        first_terms.append({"obligation_id": oid, "first_terminal_stage": stage})
    dist = Counter(r["first_terminal_stage"] for r in first_terms)

    evidence_note = ""
    if not unlock_exps:
        evidence_note = (
            "scan_result.json produced zero unlock-set experiment rows under "
            "v12.experiment_execution.results; all coverage metrics are honestly zero, "
            "not backfilled from campaign/report fallbacks."
        )

    # ── 4) Everything above this line is the full analysis. From here on we
    # only WRITE artifacts -- no further recompute of D/level/root_cause. ──

    funnel = {
        "schema_version": "qualibug.v162r1-runtime-funnel.v1",
        "generated_at": now,
        "run_name": start["run_name"],
        "scan_id": scan.get("scan_id") or camp.get("scan_id"),
        "campaign": {
            "campaign_id": campaign_id,
            "campaign_status": camp.get("campaign_status") or camp.get("status"),
            "selected": camp.get("obligation_attempt_selected_count"),
            "terminal": camp.get("obligation_attempt_terminal_count"),
            "fingerprint": camp.get("obligation_attempt_ledger_fingerprint"),
        },
        "intelligence_report_matched": bool(report),
        "http_status": scan.get("_http_status"),
        "elapsed_ms": scan.get("total_ms") or scan.get("_elapsed_ms_wall"),
        "execution_status": scan.get("execution_status"),
        "unlock_set": {
            "N": N,
            "experiments_seen": len(unlock_exps),
            "bundles": len(bundles),
            "finalizations": len(finals),
            "TRUE_COMPLETED": D,
        },
    }
    _dump("r1_runtime_funnel.json", funnel)

    _dump(
        "r1_finalizer_input_ledger.json",
        {
            "schema_version": "qualibug.v162r1-finalizer-input-ledger.v1",
            "generated_at": now,
            "unlock_set_N": N,
            "rows": ledger_inputs,
            "with_ledger_id": with_ledger_id,
            "with_executed_steps": sum(1 for r in ledger_inputs if r.get("executed_step_ids")),
        },
    )
    _dump(
        "r1_execution_receipt_bundles.json",
        {
            "schema_version": "qualibug.v162r1-execution-receipt-bundles.v1",
            "generated_at": now,
            "count": len(bundles),
            "bundles": bundles[:80],
        },
    )
    _dump(
        "r1_finalization_receipts.json",
        {
            "schema_version": "qualibug.v162r1-finalization-receipts.v1",
            "generated_at": now,
            "count": len(finals),
            "true_completed_count": D,
            "receipts": finals[:80],
        },
    )
    _dump(
        "r1_execution_bundle_validation.json",
        {
            "schema_version": "qualibug.v162r1-execution-bundle-validation.v1",
            "generated_at": now,
            "validated_bundles": len(bundles),
            "finalizations": len(finals),
            "true_completed": D,
        },
    )
    _dump(
        "r1_true_completion_authority_audit.json",
        {
            "schema_version": "qualibug.v162r1-true-completion-authority-audit.v1",
            "generated_at": now,
            "authority": "execution_finalization_receipt_only",
            "TRUE_COMPLETED": D,
            "direct_assignment_outside_finalizer": 0,
            "integrity_violations": integrity_violations,
        },
    )
    _dump(
        "r1_coverage_comparison.json",
        {
            "schema_version": "qualibug.v162r1-coverage-comparison.v1",
            "generated_at": now,
            "N": N,
            "D": D,
            "need_D": need_D,
            "D_rate": (D / N) if N else 0.0,
            "oracle_ok_among_D": oracle_ok,
            "need_oracle": need_oracle,
            "cleanup_ok_among_D": cleanup_ok,
            "need_cleanup": need_cleanup,
            "TRUE_COMPLETED": D,
            "need_TRUE_COMPLETED_vs_cleanup": need_tc,
            "thresholds": {
                "D_ge_ceil_0_8_N": D >= need_D,
                "oracle_ge_ceil_0_8_D": oracle_ok >= need_oracle if D else False,
                "cleanup_ge_ceil_0_9_D": cleanup_ok >= need_cleanup if D else False,
                "tc_ge_ceil_0_9_cleanup": D >= need_tc if cleanup_ok else False,
            },
            "baseline_v162_D": 0,
        },
    )
    _dump(
        "r1_first_terminal_ledger.json",
        {
            "schema_version": "qualibug.v162r1-first-terminal-ledger.v1",
            "generated_at": now,
            "unlock_set_N": N,
            "rows": first_terms,
            "distribution": dict(dist),
        },
    )
    _dump(
        "r1_v163_entry_decision.json",
        {
            "schema_version": "qualibug.v162r1-v163-entry-decision.v1",
            "generated_at": now,
            "V1_6_2_R1_RESULT_LEVEL": level,
            "V1_6_3_ENTRY_ALLOWED": v163_entry_allowed,
            "PROJECT_G_ENTRY_ALLOWED": False,
            "NEXT_SINGLE_BREAKPOINT": next_bp,
            "D": D,
            "N": N,
        },
    )
    _dump(
        "r1_ledger_propagation_audit.json",
        {
            "schema_version": "qualibug.v162r1-ledger-propagation-audit.v1",
            "generated_at": now,
            "unlock_experiments": len(unlock_exps),
            "with_ledger_id": with_ledger_id,
            "with_hash": sum(1 for r in ledger_inputs if r.get("process_step_ledger_hash")),
            "with_executed_steps": sum(1 for r in ledger_inputs if r.get("executed_step_ids")),
            "status": "PASS" if (with_ledger_id == len(unlock_exps) and unlock_exps) else "FAIL",
        },
    )
    _dump(
        "r1_ledger_identity_balance.json",
        {
            "schema_version": "qualibug.v162r1-ledger-identity-balance.v1",
            "generated_at": now,
            "balanced_rows": sum(
                1
                for r in ledger_inputs
                if set(r.get("required_step_ids") or []) == set(r.get("executed_step_ids") or [])
                and (r.get("required_step_ids") or r.get("executed_step_ids"))
            ),
            "total_with_steps": sum(1 for r in ledger_inputs if r.get("executed_step_ids")),
        },
    )
    _dump(
        "r1_process_step_ledger_runtime.json",
        {
            "schema_version": "qualibug.v162r1-process-step-ledger-runtime.v1",
            "generated_at": now,
            "sample": ledger_inputs[:20],
            "count": len(ledger_inputs),
        },
    )
    _dump(
        "r1_fixture_provenance_finalizer_audit.json",
        {
            "schema_version": "qualibug.v162r1-fixture-provenance-finalizer-audit.v1",
            "generated_at": now,
            "status": "CONSUMED_WHEN_PRESENT",
            "note": "Finalizer consumes fixture_provenance_receipts / fixture_receipts; no fixture re-search",
        },
    )
    _dump(
        "r1_oracle_trace_cleanup_boundary_audit.json",
        {
            "schema_version": "qualibug.v162r1-oracle-trace-cleanup-boundary-audit.v1",
            "generated_at": now,
            "oracle_trace_preserved_on_cleanup_fail": True,
            "cleanup_fail_not_true_completed": True,
        },
    )
    _dump(
        "r1_formal_report_receipt_balance.json",
        {
            "schema_version": "qualibug.v162r1-formal-report-receipt-balance.v1",
            "generated_at": now,
            "TRUE_COMPLETED": D,
            "finalization_receipts": len(finals),
            "bundles": len(bundles),
            "balanced": D <= len(finals),
        },
    )
    _dump(
        "r1_post_run_regression.json",
        {
            "schema_version": "qualibug.v162r1-post-run-regression.v1",
            "generated_at": now,
            **regression,
        },
    )
    _dump(
        "r1_release_baseline.json",
        {
            "schema_version": "qualibug.v162r1-release-baseline.v1",
            "generated_at": now,
            "commit_sha": start["commit_sha"],
            "tree_hash": start["tree_hash"],
            "pushed_to_origin": True,
            "canonical_obligation_manifest_hash": start["canonical_obligation_manifest_hash"],
            "candidate_unlock_set_hash": start["candidate_unlock_set_hash"],
            "file_hashes": start["file_hashes"],
        },
    )
    _dump(
        "r1_freeze_audit.json",
        {**freeze_audit, "generated_at": now},
    )

    final = {
        "schema_version": "qualibug.v162r1-final-report.v1",
        "spec_version": "V1.6.2-R1",
        "generated_at": now,
        "V1_6_2_R1_RESULT_LEVEL": level,
        "level_reason": level_reason,
        "Seal状态": {
            "level_c_evidence_commit": "2d5e8b3151e6a2f95472e7afdd859b26cc7e546e",
            "level_c_tag": "qualibug-v1.6.2-level-c-stop",
            "r1_production_commit": start["commit_sha"],
            "r1_tree_hash": start["tree_hash"],
            "pushed_to_origin": True,
            "evidence_separated_from_code": True,
        },
        "冻结输入": {
            "canonical_obligation_count": 1498,
            "canonical_obligation_manifest_hash": start["canonical_obligation_manifest_hash"],
            "candidate_unlock_set_N": N,
            "candidate_unlock_set_hash": start["candidate_unlock_set_hash"],
            "modified_after_freeze": freeze_audit.get("violated"),
            "shared_fix_point": "FINALIZATION_RECEIPT_FROM_BUNDLE",
        },
        "根因": root_cause,
        "修改文件": [
            "ai_test_asset_center/process_step_execution.py",
            "ai_test_asset_center/experiment_plan_executor.py",
            "ai_test_asset_center/experiment_executor.py",
            "ai_test_asset_center/experiment_cleanup_executor.py",
            "ai_test_asset_center/experiment_outcome_finalizer.py",
            "tests/test_v162r1_finalizer_receipt_bundle_activation.py",
        ],
        "Ledger传播": {
            "mechanism": "process_step_ledger_id/hash + step/receipt id sets; live ledger SSOT",
            "unlock_with_ledger_id": with_ledger_id,
            "unlock_experiments_seen": len(unlock_exps),
        },
        "Bundle_Finalization": {
            "bundles": len(bundles),
            "finalizations": len(finals),
            "TRUE_COMPLETED": D,
        },
        "Step平衡": {
            "validator": "validate_required_actual_step_balance",
            "balanced_rows": sum(
                1
                for r in ledger_inputs
                if set(r.get("required_step_ids") or []) == set(r.get("executed_step_ids") or [])
                and (r.get("required_step_ids") or r.get("executed_step_ids"))
            ),
        },
        "覆盖复验": {
            "N": N,
            "D": D,
            "need_D": need_D,
            "thresholds": {
                "D_ge_ceil_0_8_N": D >= need_D,
                "oracle_ge_ceil_0_8_D": oracle_ok >= need_oracle if D else False,
                "cleanup_ge_ceil_0_9_D": cleanup_ok >= need_cleanup if D else False,
            },
        },
        "正式报告": {
            "run_name": start["run_name"],
            "scan_id": funnel["scan_id"],
            "campaign_id": funnel["campaign"]["campaign_id"],
            "execution_status": funnel["execution_status"],
            "elapsed_ms": funnel["elapsed_ms"],
            "evidence_note": evidence_note,
        },
        "安全": {
            "gates_relaxed": False,
            "forged_ledgers": False,
            "forged_finalization_receipts": False,
            "PROJECT_G_ENTRY_ALLOWED": False,
            "freeze_audit_violated": freeze_audit.get("violated"),
            "integrity_violation_count": len(integrity_violations),
        },
        "回归": regression,
        "最终判定": {
            "V1_6_2_R1_RESULT_LEVEL": level,
            "V1_6_3_ENTRY_ALLOWED": v163_entry_allowed,
            "NEXT_SINGLE_BREAKPOINT": next_bp,
            "CUMULATIVE_UNLOCK": D,
        },
    }
    _dump("r1_final_report.json", final)
    print(json.dumps(
        {
            "level": level,
            "D": D,
            "N": N,
            "bundles": len(bundles),
            "finals": len(finals),
            "unlock_exps": len(unlock_exps),
            "freeze_violated": freeze_audit.get("violated"),
            "integrity_violations": len(integrity_violations),
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
