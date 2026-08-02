# -*- coding: utf-8 -*-
"""Fresh-campaign diagnostic scan for Fact Experimentability + First-loss Phase 1.

Mints a new campaign_id / artifact namespace. Writes product funnel reports plus
fact experimentability / first-loss artifacts. Does not claim commercial Recall;
run tools/discovery_evaluation.py evaluate separately for GT join.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_test_asset_center.observed_product_scan_executor import (  # noqa: E402
    ObservedProductScanExecutor,
)
from ai_test_asset_center.scan_operational_metrics import (  # noqa: E402
    OperationalMetricsNotMeasured,
    collect_observed_scan_operational_metrics,
)

BUNDLE_DIR = REPO / "_private_eval" / "benchmark_mall_131_v1" / "runtime" / "held-in"
OUTPUT_ROOT = REPO / "_funnel_runs"
TARGET_ID = "benchmark-mall-held-in-131"
BASE_URL = "http://localhost:8080"
POLICY_ID = "policy-fact-first-loss-001"
POLICY_VERSION = "v1.0.0-fact-first-loss"


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
        "operational_metrics_reason": "local diagnostic replay without trusted observation gateway",
    }


def main() -> int:
    if not (BUNDLE_DIR / "input.json").is_file():
        raise SystemExit(f"missing runtime bundle: {BUNDLE_DIR / 'input.json'}")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    label = f"20260802_fact_first_loss_{stamp}"
    output_dir = OUTPUT_ROOT / label
    output_dir.mkdir(parents=True, exist_ok=True)

    input_bundle = json.loads((BUNDLE_DIR / "input.json").read_text(encoding="utf-8"))
    project_id = str(input_bundle.get("project_id") or "benchmark_mall").strip()
    campaign_id = f"eval-fact-first-loss-{stamp}"
    run_commit = _git_head()

    start_manifest = {
        "schema_version": "qualibug.fact-first-loss-run-manifest.v1",
        "label": label,
        "campaign_id": campaign_id,
        "target_id": TARGET_ID,
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "evaluation_mode": "replay",
        "base_url": BASE_URL,
        "bundle_dir": str(BUNDLE_DIR),
        "output_dir": str(output_dir),
        "code_commit": run_commit,
        "reuse_prior_campaign_artifacts": False,
        "notes": (
            "Phase-1 FactExperimentabilityReceipt + First-loss Ledger diagnostic. "
            "Internal findings are not Recall. Evaluate GT privately after completion."
        ),
    }
    (output_dir / "start_manifest.json").write_text(
        json.dumps(start_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

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

    # Ensure product fact-tracking reports exist even if the executor path
    # skipped the __main__ scan artifact writer.
    scan_dir = output_dir / "scan_output"
    scan_dir.mkdir(parents=True, exist_ok=True)
    try:
        from ai_test_asset_center.fact_first_loss_ledger import (
            write_fact_tracking_report_files,
        )
        from ai_test_asset_center.discovery_funnel import write_funnel_report_files

        write_funnel_report_files(scan_output, scan_dir)
        fact_paths = write_fact_tracking_report_files(scan_output, scan_dir)
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
    summary = {
        "elapsed_seconds": elapsed,
        "run_id": scan_output.get("run_id"),
        "target_id": scan_output.get("target_id"),
        "campaign_id": scan_output.get("campaign_id") or campaign_id,
        "policy_version": scan_output.get("policy_version") or POLICY_VERSION,
        "evaluation_mode": scan_output.get("evaluation_mode"),
        "code_commit": run_commit,
        "findings": len(scan_output.get("findings") or []),
        "candidates": len(scan_output.get("candidates") or []),
        "pipeline_status": pipeline.get("status"),
        "selected": ledger.get("selected_count"),
        "terminal": ledger.get("terminal_count"),
        "terminal_status_counts": ledger.get("terminal_status_counts"),
        "fact_experimentability_receipt_count": exp_report.get("receipt_count"),
        "fact_experimentability_ready_count": exp_report.get("ready_count"),
        "fact_first_loss_row_count": fact_ledger.get("row_count"),
        "fact_first_loss_conservation": (fact_ledger.get("conservation") or {}).get("status"),
        "fact_tracking_paths": fact_paths,
        "output_path": str(output_path),
        "next_step": (
            "python tools/discovery_evaluation.py evaluate "
            "--manifest _private_eval/benchmark_mall_131_v1/evaluation_manifest.json "
            f"--output-dir {output_dir / 'evaluation'}"
        ),
        "honesty": "internal_findings_are_not_recall",
    }
    (output_dir / "scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
