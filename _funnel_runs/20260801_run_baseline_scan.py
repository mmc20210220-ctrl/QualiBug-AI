# -*- coding: utf-8 -*-
"""Run the 2026-08-01 diagnostic baseline scan with manifest target identity.

Uses the same product surface as the observed evaluator flow
(ObservedProductScanExecutor -> scan -> v12 discovery mainline) but without
the evaluator-owned loopback observation gateway. The output is therefore a
product-side diagnostic replay: it can be scored against the frozen hidden GT
locally, but it is NOT commercial promotion evidence.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_test_asset_center.observed_product_scan_executor import (  # noqa: E402
    ObservedProductScanExecutor,
)
from ai_test_asset_center.scan_operational_metrics import (  # noqa: E402
    OperationalMetricsNotMeasured,
    collect_observed_scan_operational_metrics,
)

BUNDLE_DIR = REPO / "_private_eval" / "benchmark_mall_131_v1" / "runtime" / "held-in-20260801"
OUTPUT_DIR = REPO / "_funnel_runs" / "20260801_baseline"
TARGET_ID = "benchmark-mall-held-in-131"
BASE_URL = "http://localhost:8080"


def _number(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _cleanup_failures(scan_result: dict) -> int:
    v12 = scan_result.get("v12") or {}
    count = 0

    def walk(item: object, path: str) -> None:
        nonlocal count
        if isinstance(item, dict):
            cleanup = item.get("cleanup")
            if isinstance(cleanup, dict):
                status = str(cleanup.get("status") or "").lower()
                if status in {"failed", "cleanup_incomplete", "incomplete", "not_reversible"}:
                    count += 1
            for key, child in item.items():
                walk(child, f"{path}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")

    walk(v12, "v12")
    return count


def lenient_metrics(*, scan_result: dict, wall_clock_seconds: float, runtime_view: dict, **_):
    """Return strict metrics when observable; otherwise honest NOT_MEASURED nulls."""
    try:
        return collect_observed_scan_operational_metrics(
            scan_result=scan_result,
            wall_clock_seconds=wall_clock_seconds,
            runtime_view=runtime_view,
        )
    except OperationalMetricsNotMeasured as exc:
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
    cleanup_failures = _cleanup_failures(scan_result)
    execution_rate = (executed / attempts) if attempts else None
    return {
        "wall_clock_seconds": round(wall_clock_seconds, 6),
        "estimated_cost_usd": None,
        "model_request_count": None,
        "model_cost_status": "NOT_REPORTED",
        "request_count": observed_http,
        "production_http_requests": production,
        "cleanup_failures": cleanup_failures,
        "safety_incidents": None if production is None else (0 if production == 0 else production),
        "dirty_test_environments": 1 if cleanup_failures else 0,
        "execution_success_rate": execution_rate,
        "engine_success_rate": None,
        "duplicate_rate": None,
        "operational_metrics_status": "NOT_MEASURED",
        "operational_metrics_reason": "local diagnostic replay without trusted observation gateway",
    }


def main() -> int:
    input_bundle = json.loads((BUNDLE_DIR / "input.json").read_text(encoding="utf-8"))
    context_bundle = json.loads((BUNDLE_DIR / "context.json").read_text(encoding="utf-8"))
    fixture_path = BUNDLE_DIR / "fixture.json"
    project_id = str(input_bundle.get("project_id") or "benchmark_mall").strip()
    campaign_id = "eval-20260801-" + str(int(time.time()))
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
                "fixture_snapshot_ref": str(fixture_path),
                "context_artifact_ref": str(BUNDLE_DIR / "context.json"),
            },
            "runtime_fingerprint": (
                "7df24d66250de541954b35cdd2a269d6303aa3358c9278a276440cdb3551d02a"
            ),
        },
    }
    executor = ObservedProductScanExecutor(
        workspace_root=REPO,
        operational_metrics_collector=lenient_metrics,
        worker_timeout_seconds=7200,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    print(f"[scan] starting {TARGET_ID} at {BASE_URL} ({time.strftime('%Y-%m-%d %H:%M:%S')})", flush=True)
    scan_output = executor._execute_in_process(
        runtime_view=runtime_view,
        campaign_id=campaign_id,
        policy_id="policy-baseline-001",
        policy_version="v1.0.0-baseline",
        evaluation_mode="replay",
        # Canonical observed evaluation enables the sanctioned agent semantic
        # linker (accepted rule/interface identities with exact refs); the
        # executor default of False silently skips rule-to-interface linking,
        # which starves permits relations and blocks state/authorization
        # obligations with BLOCKED_MISSING_ACTOR / MISSING_BINDING.
        agent_semantic_linking_enabled=True,
    )
    elapsed = round(time.monotonic() - started, 3)
    output_path = OUTPUT_DIR / "scan_output.json"
    output_path.write_text(
        json.dumps(scan_output, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    pipeline = scan_output.get("pipeline_health") or {}
    ledger = scan_output.get("obligation_attempt_ledger") or {}
    summary = {
        "elapsed_seconds": elapsed,
        "run_id": scan_output.get("run_id"),
        "target_id": scan_output.get("target_id"),
        "campaign_id": scan_output.get("campaign_id"),
        "policy_version": scan_output.get("policy_version"),
        "evaluation_mode": scan_output.get("evaluation_mode"),
        "findings": len(scan_output.get("findings") or []),
        "candidates": len(scan_output.get("candidates") or []),
        "pipeline_status": pipeline.get("status"),
        "selected": ledger.get("selected_count"),
        "terminal": ledger.get("terminal_count"),
        "terminal_status_counts": ledger.get("terminal_status_counts"),
        "cleanup_failures": pipeline.get("cleanup_failure_count"),
        "output_path": str(output_path),
    }
    (OUTPUT_DIR / "scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
