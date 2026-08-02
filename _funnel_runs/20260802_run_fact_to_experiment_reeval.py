# -*- coding: utf-8 -*-
"""Phase-6 fresh 131-bug re-eval for Fact→Experiment SPEC (phases 1–5 installed).

Mints a new campaign_id / artifact namespace, runs the product scan against the
frozen held-in runtime view, writes fact-tracking + funnel artifacts, then
builds a v2 evaluation envelope and scores it with the evaluator-private
131-bug ground truth.

Internal finding counts are not Recall. Only the authenticated evaluator
receipt's TP/FP/FN are quality evidence. Local HMAC sealing without an
evaluator-owned observation gateway remains diagnostic, not commercial
promotion evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_test_asset_center.discovery_policy_evaluation_runner import (  # noqa: E402
    strategy_fingerprint,
)
from ai_test_asset_center.observed_product_scan_executor import (  # noqa: E402
    ObservedProductScanExecutor,
)
from ai_test_asset_center.policy_registry import get_policy_registry  # noqa: E402
from ai_test_asset_center.scan_operational_metrics import (  # noqa: E402
    OperationalMetricsNotMeasured,
    collect_observed_scan_operational_metrics,
)

# held-in/ is a stub (qualibug.runtime-input.v1). Use the product-scan-compatible
# frozen bundle that matches ObservedProductScanExecutor schemas.
BUNDLE_DIR = (
    REPO / "_private_eval" / "benchmark_mall_131_v1" / "runtime" / "held-in-20260801"
)
MANIFEST = REPO / "_private_eval" / "benchmark_mall_131_v1" / "evaluation_manifest.json"
OUTPUT_ROOT = REPO / "_funnel_runs"
TARGET_ID = "benchmark-mall-held-in-131"
PROJECT_ID = "evaluation-benchmark-mall-held-in-131"
BASE_URL = "http://localhost:8080"
POLICY_ID = "policy-fact-to-experiment-001"
POLICY_VERSION = "v1.0.0-fact-to-experiment"
HMAC_KEY_FILE = Path(
    r"C:\Users\Test\.qualibug-evaluator\observed-131-20260716\evaluator-hmac.key"
)
DEFAULT_BENCHMARK_TARGET_ROOT = Path(
    r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
    r"\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
)


def _git_head() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO),
            check=True,
            capture_output=True,
            text=True,
        )
        return (completed.stdout or "").strip()
    except Exception:
        return "NOT_MEASURED"


def _number(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def lenient_metrics(*, scan_result: dict, wall_clock_seconds: float, runtime_view: dict, **_):
    try:
        return collect_observed_scan_operational_metrics(
            scan_result=scan_result,
            wall_clock_seconds=wall_clock_seconds,
            runtime_view=runtime_view,
        )
    except OperationalMetricsNotMeasured:
        pass
    except Exception as exc:
        print(f"[metrics] fallback reason: {type(exc).__name__}: {exc}", flush=True)

    v12 = scan_result.get("v12") or {}
    phases = v12.get("phases") or {}
    execution = phases.get("execution") or {}
    observed_http = _number(execution.get("observed_http_request_count"))
    production = _number(execution.get("production_http_requests"))
    attempts = _number(execution.get("scenario_attempts"))
    executed = _number(execution.get("executed"))
    execution_rate = (executed / attempts) if attempts else None
    return {
        "wall_clock_seconds": round(wall_clock_seconds, 6),
        "estimated_cost_usd": None,
        "model_request_count": None,
        "model_cost_status": "NOT_REPORTED",
        "request_count": observed_http,
        "production_http_requests": production,
        "cleanup_failures": None,
        "safety_incidents": None if production is None else (0 if production == 0 else production),
        "dirty_test_environments": None,
        "execution_success_rate": execution_rate,
        "engine_success_rate": None,
        "duplicate_rate": None,
        "operational_metrics_status": "NOT_MEASURED",
        "operational_metrics_reason": (
            "local diagnostic replay without trusted observation gateway"
        ),
    }


def _load_full_product_scan_result(project_id: str) -> dict | None:
    """Load the durable product scan_result written by ``__main__.scan``."""

    path = REPO / "platform_outputs" / project_id / "scan_result.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(
            f"[scan] full scan_result unreadable: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    return payload


def build_raw_envelope(scan_output: dict) -> dict:
    mainline = scan_output["mainline_run"]
    authority = {
        field: scan_output[field]
        for field in (
            "formal_count_projection",
            "canonical_defect_registry",
            "defect_identity_consistency",
            "formal_delivery_authority",
        )
        if isinstance(scan_output.get(field), dict)
    }
    scan_result = {
        "findings": list(scan_output.get("findings") or []),
        "delivery_occurrences": list(scan_output.get("delivery_occurrences") or []),
        "candidate_findings": list(scan_output.get("candidates") or []),
        "obligation_attempt_ledger": scan_output["obligation_attempt_ledger"],
        "mainline_run": mainline,
        **authority,
    }
    if scan_output.get("trace_ledger") is not None:
        scan_result["trace_ledger"] = scan_output["trace_ledger"]
    raw = {
        "schema_version": "qualibug.discovery-evaluation-run-envelope.v2",
        "run_id": mainline["run_id"],
        "campaign_id": mainline["campaign_id"],
        "policy_id": POLICY_ID,
        "evaluation_mode": mainline.get("evaluation_mode") or "replay",
        "pipeline_health": scan_output.get("pipeline_health") or {},
        "operational_metrics": scan_output.get("operational_metrics") or {},
        "mainline_run": mainline,
        "scan_result": scan_result,
        **authority,
    }
    if scan_output.get("process_boundary") is not None:
        raw["process_boundary"] = scan_output["process_boundary"]
        scan_result["process_boundary"] = scan_output["process_boundary"]
    return raw


def run_scan(output_dir: Path, campaign_id: str, run_commit: str) -> dict:
    input_bundle = json.loads(
        (BUNDLE_DIR / "input.json").read_text(encoding="utf-8-sig")
    )
    project_id = str(input_bundle.get("project_id") or "benchmark_mall").strip()
    runtime_view = {
        "schema_version": "qualibug.discovery-runtime-view.v1",
        "target": {
            "target_id": TARGET_ID,
            "project_id": project_id,
            "industry": "ecommerce",
            "split": "held_in",
            "expectation": "seeded_defects",
            "runtime": {
                "environment_ref": BASE_URL,
                "environment_type": "test",
                "input_bundle_ref": str(BUNDLE_DIR / "input.json"),
                "fixture_snapshot_ref": str(BUNDLE_DIR / "fixture.json"),
                "context_artifact_ref": str(BUNDLE_DIR / "context.json"),
            },
        },
    }
    executor = ObservedProductScanExecutor(
        workspace_root=REPO,
        operational_metrics_collector=lenient_metrics,
        worker_timeout_seconds=7200,
    )
    started = time.monotonic()
    print(
        f"[scan] starting {TARGET_ID} campaign={campaign_id} "
        f"output={output_dir} ({time.strftime('%Y-%m-%d %H:%M:%S')})",
        flush=True,
    )
    scan_output = executor._execute_in_process(
        runtime_view=runtime_view,
        campaign_id=campaign_id,
        policy_id=POLICY_ID,
        policy_version=POLICY_VERSION,
        evaluation_mode="replay",
        agent_semantic_linking_enabled=True,
    )
    elapsed = round(time.monotonic() - started, 3)
    # ObservedProductScanExecutor returns a slim evaluation projection. Funnel
    # conservation and fact-tracking need the full product scan_result (with
    # test_obligations + fact_experimentability_ledger). Prefer that artifact.
    full_scan = _load_full_product_scan_result(project_id)
    report_source = full_scan if full_scan is not None else scan_output
    if full_scan is not None:
        v12 = full_scan.get("v12") if isinstance(full_scan.get("v12"), dict) else {}
        if isinstance(v12.get("runtime_feedback"), dict):
            scan_output["runtime_feedback"] = dict(v12["runtime_feedback"])
        if isinstance(v12.get("fact_experimentability_ledger"), dict):
            scan_output["fact_experimentability_ledger"] = dict(
                v12["fact_experimentability_ledger"]
            )
        # Keep evaluator submission health aligned with the sealed product result.
        if isinstance(full_scan.get("pipeline_health"), dict):
            scan_output["pipeline_health"] = dict(full_scan["pipeline_health"])

    scan_dir = output_dir / "scan_output"
    scan_dir.mkdir(parents=True, exist_ok=True)
    fact_paths: dict = {}
    try:
        from ai_test_asset_center.discovery_funnel import write_funnel_report_files
        from ai_test_asset_center.fact_first_loss_ledger import (
            write_fact_tracking_report_files,
        )

        # Prefer the product-sealed funnel already attached to the full scan.
        # Rebuilding from the full object can fail on post-hoc projection drift
        # (e.g. formal_projection_attempt_id_mismatch) even when the sealed
        # conservation status is PASS.
        if isinstance(report_source.get("discovery_funnel_report"), dict):
            (scan_dir / "discovery_funnel_report.json").write_text(
                json.dumps(
                    report_source["discovery_funnel_report"],
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
                + "\n",
                encoding="utf-8",
            )
        else:
            write_funnel_report_files(report_source, scan_dir)
        fact_paths = write_fact_tracking_report_files(report_source, scan_dir)
        if isinstance(report_source.get("fact_experimentability_report"), dict):
            scan_output["fact_experimentability_report"] = dict(
                report_source["fact_experimentability_report"]
            )
        if isinstance(report_source.get("fact_first_loss_ledger"), dict):
            scan_output["fact_first_loss_ledger"] = dict(
                report_source["fact_first_loss_ledger"]
            )
    except Exception as exc:
        fact_paths = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"[fact-tracking] write failed: {fact_paths['error']}", flush=True)

    output_path = output_dir / "scan_output.json"
    output_path.write_text(
        json.dumps(scan_output, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    pipeline = scan_output.get("pipeline_health") or {}
    ledger = scan_output.get("obligation_attempt_ledger") or {}
    fact_ledger = scan_output.get("fact_first_loss_ledger") or {}
    exp_report = scan_output.get("fact_experimentability_report") or {}
    if not exp_report and isinstance(
        scan_output.get("fact_experimentability_ledger"), dict
    ):
        from ai_test_asset_center.fact_first_loss_ledger import (
            build_fact_experimentability_report,
        )

        exp_report = build_fact_experimentability_report(
            scan_output.get("fact_experimentability_ledger")
        )
        scan_output["fact_experimentability_report"] = exp_report
    runtime_feedback = scan_output.get("runtime_feedback") or {}
    summary = {
        "elapsed_seconds": elapsed,
        "run_id": scan_output.get("run_id")
        or (scan_output.get("mainline_run") or {}).get("run_id"),
        "target_id": scan_output.get("target_id") or TARGET_ID,
        "campaign_id": (scan_output.get("mainline_run") or {}).get("campaign_id")
        or campaign_id,
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "evaluation_mode": scan_output.get("evaluation_mode")
        or (scan_output.get("mainline_run") or {}).get("evaluation_mode"),
        "code_commit": run_commit,
        "findings": len(scan_output.get("findings") or []),
        "candidates": len(scan_output.get("candidates") or []),
        "pipeline_status": pipeline.get("status"),
        "funnel_conservation_status": pipeline.get("funnel_conservation_status"),
        "selected": ledger.get("selected_count"),
        "terminal": ledger.get("terminal_count"),
        "terminal_status_counts": ledger.get("terminal_status_counts"),
        "fact_experimentability_receipt_count": exp_report.get("receipt_count"),
        "fact_experimentability_ready_count": exp_report.get("ready_count"),
        "fact_first_loss_row_count": fact_ledger.get("row_count"),
        "runtime_feedback_status": runtime_feedback.get("status"),
        "runtime_feedback_candidate_count": (
            (runtime_feedback.get("candidate_ledger") or {}).get("candidate_count")
        ),
        "full_scan_result_used_for_reports": full_scan is not None,
        "fact_tracking_paths": fact_paths,
        "output_path": str(output_path),
        "honesty": "internal_findings_are_not_recall",
    }
    (output_dir / "scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return scan_output


def run_evaluate(output_dir: Path, scan_output: dict) -> int:
    if "mainline_run" not in scan_output:
        raise SystemExit("scan_output missing mainline_run; cannot evaluate")
    if "obligation_attempt_ledger" not in scan_output:
        raise SystemExit("scan_output missing obligation_attempt_ledger; cannot evaluate")

    # Align evaluator policy_version with the immutable mainline contract.
    mainline_policy_version = str(
        (scan_output.get("mainline_run") or {}).get("policy_version") or POLICY_VERSION
    ).strip()
    envelope = build_raw_envelope(scan_output)
    envelope_path = output_dir / "envelope.v2.json"
    envelope_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"[evaluate] envelope written: {envelope_path}", flush=True)

    active = get_policy_registry().get_active()
    if active is None:
        raise SystemExit("no active policy registry entry for strategy fingerprint")
    fingerprint = strategy_fingerprint(active.strategy)
    if not HMAC_KEY_FILE.is_file():
        raise SystemExit(f"missing evaluator HMAC key: {HMAC_KEY_FILE}")
    key = HMAC_KEY_FILE.read_bytes()
    if len(key) < 32:
        raise SystemExit("evaluator HMAC key is too short")

    env = dict(os.environ)
    env["QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY"] = key.hex()
    receipt_root = output_dir / "evaluation"
    receipt_root.mkdir(parents=True, exist_ok=True)
    evaluated = subprocess.run(
        [
            sys.executable,
            str(REPO / "tools" / "discovery_evaluation.py"),
            "evaluate",
            "--manifest",
            str(MANIFEST),
            "--target-id",
            TARGET_ID,
            "--policy-id",
            POLICY_ID,
            "--policy-version",
            mainline_policy_version,
            "--strategy-fingerprint",
            fingerprint,
            "--run-envelope",
            str(envelope_path),
            "--output-root",
            str(receipt_root),
            "--hmac-key-file",
            str(HMAC_KEY_FILE),
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    (output_dir / "evaluate_stdout.txt").write_text(
        evaluated.stdout or "", encoding="utf-8"
    )
    (output_dir / "evaluate_stderr.txt").write_text(
        evaluated.stderr or "", encoding="utf-8"
    )
    print((evaluated.stdout or "").strip(), flush=True)
    if evaluated.returncode != 0:
        print((evaluated.stderr or "").strip(), file=sys.stderr, flush=True)
        return int(evaluated.returncode)

    # Surface measurement status + TP/FP/FN from the newest receipt.
    receipts = sorted(receipt_root.rglob("*.json"), key=lambda p: p.stat().st_mtime)
    for path in reversed(receipts):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        measurement = str(payload.get("measurement_status") or "").strip()
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
        score_source = metrics or quality
        if not measurement and not score_source:
            continue
        extract = {
            "receipt_path": str(path),
            "measurement_status": measurement or "NOT_MEASURED",
            "not_measured_reason": payload.get("not_measured_reason")
            or payload.get("reason")
            or "",
            "true_positives": score_source.get(
                "true_positives", score_source.get("tp")
            ),
            "false_positives": score_source.get(
                "false_positives", score_source.get("fp")
            ),
            "false_negatives": score_source.get(
                "false_negatives", score_source.get("fn")
            ),
            "precision": score_source.get("precision"),
            "recall": score_source.get("recall"),
            "pipeline_health_status": (
                (payload.get("pipeline_health") or {}).get("status")
                if isinstance(payload.get("pipeline_health"), dict)
                else None
            ),
            "fact_first_loss_diagnostics_present": isinstance(
                score_source.get("fact_first_loss_diagnostics"), dict
            ),
            "honesty": (
                "TP/FP/FN only when measurement_status=MEASURED; "
                "internal findings are not Recall"
            ),
        }
        (output_dir / "evaluation_score_extract.json").write_text(
            json.dumps(extract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(extract, ensure_ascii=False, indent=2), flush=True)
        break
    return 0


def _benchmark_env() -> dict[str, str]:
    env = dict(os.environ)
    if not str(env.get("QUALIBUG_BENCHMARK_TARGET_ROOT") or "").strip():
        if not DEFAULT_BENCHMARK_TARGET_ROOT.is_dir():
            raise SystemExit(
                "QUALIBUG_BENCHMARK_TARGET_ROOT is unset and default path is "
                f"missing: {DEFAULT_BENCHMARK_TARGET_ROOT}"
            )
        env["QUALIBUG_BENCHMARK_TARGET_ROOT"] = str(DEFAULT_BENCHMARK_TARGET_ROOT)
    env.setdefault("QUALIBUG_TARGET_BASE_URL", BASE_URL)
    env.setdefault("QUALIBUG_LOGIN_PATH", "/api/auth/login")
    # Account source lives under platform_inputs for the evaluation project id;
    # projects/benchmark_mall/input remains the legacy fallback.
    account_source = REPO / "platform_inputs" / PROJECT_ID / "TEST_ACCOUNTS.md"
    if not account_source.is_file():
        account_source = (
            REPO / "projects" / "benchmark_mall" / "input" / "TEST_ACCOUNTS.md"
        )
    env.setdefault("QUALIBUG_TEST_ACCOUNTS_SOURCE", str(account_source))
    env.setdefault(
        "QUALIBUG_TEST_ACCOUNTS_PATH",
        str(REPO / "platform_inputs" / PROJECT_ID / "test_accounts.json"),
    )
    env.setdefault("QUALIBUG_SSRF_ALLOW_INTERNAL", "1")
    return env


def reset_target(output_dir: Path) -> dict:
    from benchmark_evaluator.funnel_benchmark_prep import (
        prepare_funnel_benchmark_target,
    )

    env = _benchmark_env()
    print(
        "[reset] preparing frozen held-in benchmark target "
        f"root={env.get('QUALIBUG_BENCHMARK_TARGET_ROOT')}",
        flush=True,
    )
    prep = prepare_funnel_benchmark_target(
        root=REPO,
        env=env,
        project=PROJECT_ID,
        target_base_url=BASE_URL,
    )
    (output_dir / "target_reset_receipt.json").write_text(
        json.dumps(prep.get("reset_receipt") or prep, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    status = str((prep.get("reset_receipt") or {}).get("status") or "")
    print(f"[reset] status={status} path={prep.get('reset_receipt_path')}", flush=True)
    if status != "completed":
        raise SystemExit(
            "benchmark target reset did not complete; refusing dirty re-eval"
        )
    return prep


def main() -> int:
    if not (BUNDLE_DIR / "input.json").is_file():
        raise SystemExit(f"missing runtime bundle: {BUNDLE_DIR / 'input.json'}")
    if not MANIFEST.is_file():
        raise SystemExit(f"missing evaluation manifest: {MANIFEST}")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    label = f"20260802_fact_to_experiment_reeval_{stamp}"
    output_dir = OUTPUT_ROOT / label
    output_dir.mkdir(parents=True, exist_ok=True)
    campaign_id = f"eval-fact-to-experiment-{stamp}"
    run_commit = _git_head()

    start_manifest = {
        "schema_version": "qualibug.fact-to-experiment-reeval-manifest.v1",
        "label": label,
        "campaign_id": campaign_id,
        "target_id": TARGET_ID,
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "evaluation_mode": "replay",
        "base_url": BASE_URL,
        "bundle_dir": str(BUNDLE_DIR),
        "manifest": str(MANIFEST),
        "output_dir": str(output_dir),
        "code_commit": run_commit,
        "reuse_prior_campaign_artifacts": False,
        "phases_installed": [
            "fact_experimentability_first_loss",
            "abstract_experiment_materialization",
            "capability_frontload",
            "oracle_validity_effect_observation",
            "runtime_feedback_recompile",
        ],
        "notes": (
            "Phase-6 frozen 131-bug re-eval after Fact→Experiment phases 1–5. "
            "Internal findings are not Recall."
        ),
    }
    (output_dir / "start_manifest.json").write_text(
        json.dumps(start_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(start_manifest, ensure_ascii=False, indent=2), flush=True)

    reset_target(output_dir)
    scan_output = run_scan(output_dir, campaign_id, run_commit)
    return run_evaluate(output_dir, scan_output)


if __name__ == "__main__":
    raise SystemExit(main())
