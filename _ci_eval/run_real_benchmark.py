#!/usr/bin/env python3
"""Run QualiBug against the real 131-bug benchmark target.

This runner deliberately has no ground-truth path or scoring imports.  The
workflow moves evaluator-private assets outside the target before invoking it;
scoring happens only after this process exits and persists the product report.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("QUALIBUG_REPO_ROOT") or Path(__file__).resolve().parents[1]).resolve()
PROJECT = os.environ.get("QUALIBUG_BENCHMARK_PROJECT", "benchmark_mall").strip()
BASE_URL = os.environ.get("QUALIBUG_TARGET_BASE_URL", "http://127.0.0.1:8080").strip()
VARIANT = os.environ.get("QUALIBUG_EVAL_VARIANT", "candidate").strip()
OUT_DIR = Path(os.environ.get("QUALIBUG_EVAL_OUT_DIR", str(ROOT / "_ci_results"))).resolve()
TARGET_ROOT = Path(os.environ["QUALIBUG_BENCHMARK_TARGET_ROOT"]).resolve()

sys.path.insert(0, str(ROOT))


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def stage(funnel: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        (row for row in as_list(funnel.get("stages")) if isinstance(row, dict) and row.get("name") == name),
        {},
    )


def strings(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif value is not None:
        yield str(value)


def blocker_counts(result: dict[str, Any]) -> dict[str, int]:
    needles = (
        "dependency_fixture_setup_not_generated",
        "dependency_db_read_unavailable",
        "runtime_read_binding_unresolved",
        "OBLIGATION_NOT_IN_PLAN",
        "BLOCKED_CONFLICTING_SOURCE",
        "CONTRACT_ORACLE_HARNESS_FAILED",
        "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
        "ASSERTION_INDETERMINATE",
    )
    counts: Counter[str] = Counter()
    for text in strings(result):
        for needle in needles:
            if needle in text:
                counts[needle] += 1
    return dict(counts)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from ai_test_asset_center.artifact_redactor import write_json_redacted
        write_json_redacted(path, payload)
    except Exception:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def prepare_product_inputs() -> Path:
    input_dir = ROOT / "projects" / PROJECT / "input"
    if not input_dir.is_dir():
        raise SystemExit(f"missing product benchmark input: {input_dir}")
    config = {
        "project_id": PROJECT,
        "project_name": "QualiBug enterprise benchmark v0.5",
        "base_url": BASE_URL,
        "environment": "test",
        "environment_type": "test",
        "execute_api": True,
        "database": {
            "type": "postgresql",
            "host": "127.0.0.1",
            "port": 55432,
            "database": "benchmark_mall",
            "username": "benchmark_user",
        },
    }
    (input_dir / "real_project_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for state_dir in (
        ROOT / "platform_workspace" / PROJECT,
        ROOT / "platform_outputs" / PROJECT,
    ):
        shutil.rmtree(state_dir, ignore_errors=True)
    lock = ROOT / "_funnel_runs" / ".exclusive_benchmark.lock"
    if lock.exists():
        lock.unlink()
    return input_dir


def configure_runtime() -> None:
    env = {
        "QUALIBUG_REPO_ROOT": str(ROOT),
        "QUALIBUG_UNIFY_ANALYZERS": "1",
        "QUALIBUG_UNIFY_LLM_REASONER": "0",
        "QUALIBUG_TARGET_BASE_URL": BASE_URL,
        "QUALIBUG_BENCHMARK_PROJECT": PROJECT,
        "QUALIBUG_BENCHMARK_TARGET_ROOT": str(TARGET_ROOT),
        "QUALIBUG_DB_DSN": "postgresql://benchmark_user:benchmark_pass@127.0.0.1:55432/benchmark_mall",
        "QUALIBUG_JWT_SECRET": "benchmark-secret-not-for-production",
        "QUALIBUG_LOGIN_PATH": "/api/auth/login",
        "QUALIBUG_TEST_ACCOUNTS_SOURCE": str(ROOT / "projects" / PROJECT / "input" / "TEST_ACCOUNTS.md"),
        "QUALIBUG_TEST_ACCOUNTS_PATH": str(ROOT / "platform_inputs" / PROJECT / "test_accounts.json"),
        "QUALIBUG_SKIP_TARGET_DB_RESET": "1",
        "QUALIBUG_SSRF_ALLOW_INTERNAL": "1",
        "QUALIBUG_FUNNEL_BENCHMARK_ALLOW_RESET": PROJECT,
        "QUALIBUG_AGENT_SEMANTIC_LINKING_ENABLED": "1",
        "QUALIBUG_RUNTIME_INTERFACE_DISCOVERY_ENABLED": "0",
        "ENABLE_V12_STATE_GRAPH_ENGINE": "true",
        "PYTHONUNBUFFERED": "1",
    }
    os.environ.update(env)


def main() -> int:
    if (TARGET_ROOT / "hidden_ground_truth").exists():
        raise SystemExit("evaluator-private hidden_ground_truth is visible to product runtime")
    if not (TARGET_ROOT / "docker-compose.yml").is_file():
        raise SystemExit(f"invalid target root: {TARGET_ROOT}")

    configure_runtime()
    input_dir = prepare_product_inputs()
    api_doc_text = (input_dir / "API_SPEC.md").read_text(encoding="utf-8")
    source_hash = hashlib.sha256(api_doc_text.encode("utf-8")).hexdigest()
    context = {
        "target_id": "benchmark_mall_local_scope",
        "scope_id": "benchmark_mall_local_scope",
        "environment_id": "benchmark_mall_test",
        "environment_ref": "benchmark_mall_test",
        "environment_kind": "test",
        "evaluation_mode": "operational",
        "agent_semantic_linking_enabled": True,
        "runtime_interface_discovery_enabled": False,
        "source_manifest": {
            "source_id": "benchmark_mall/API_SPEC.md",
            "source_hash": source_hash,
        },
    }

    from ai_test_asset_center.__main__ import scan
    from ai_test_asset_center.discovery_quality_projection import attach_quality_projection_to_scan_result

    started = time.time()
    print(f"REAL_BENCHMARK_START variant={VARIANT} base_url={BASE_URL}", flush=True)
    result = scan(
        project=PROJECT,
        root=ROOT,
        api_doc_text=api_doc_text,
        base_url=BASE_URL,
        ci_gate=False,
        multi_layer=True,
        save_report=True,
        campaign_context=context,
    )
    elapsed = time.time() - started
    result = attach_quality_projection_to_scan_result(result if isinstance(result, dict) else {})

    v12 = as_dict(result.get("v12"))
    funnel = as_dict(result.get("discovery_funnel"))
    formal = as_dict(result.get("formal_count_projection"))
    ledger = as_dict(result.get("obligation_attempt_ledger") or v12.get("obligation_attempt_ledger"))
    conservation = as_dict(funnel.get("conservation"))
    obligation_rows = as_list(as_dict(v12.get("test_obligations")).get("obligations"))

    findings = [row for row in as_list(result.get("findings")) if isinstance(row, dict)]
    candidate_findings = [row for row in as_list(result.get("candidate_findings")) if isinstance(row, dict)]

    summary = {
        "schema_version": "qualibug.real-benchmark-summary.v1",
        "variant": VARIANT,
        "elapsed_seconds": round(elapsed, 3),
        "success": result.get("success"),
        "execution_status": result.get("execution_status"),
        "generated": len(obligation_rows) or int(stage(funnel, "obligation_generation").get("success") or 0),
        "selected": int(ledger.get("selected_count") or funnel.get("candidate_count") or 0),
        "compiled": int(stage(funnel, "experiment_compile").get("success") or 0),
        "executed": int(stage(funnel, "governed_execution").get("success") or 0),
        "oracle_violations": int(conservation.get("oracle_violation_count") or 0),
        "formal_deliveries": int(formal.get("formal_customer_deliverable_count") or len(findings)),
        "reported_findings": len(findings),
        "candidate_findings": len(candidate_findings),
        "terminal_count": int(ledger.get("terminal_count") or 0),
        "blocker_counts_recursive": blocker_counts(result),
        "top_blocking_reasons": funnel.get("top_blocking_reasons") or [],
        "pipeline_health": funnel.get("pipeline_health") or result.get("pipeline_health") or {},
        "formal_count_projection": formal,
        "discovery_funnel": funnel,
        "campaign": result.get("campaign") or {},
    }

    # The evaluator accepts only this public product projection. Ground truth is
    # intentionally unavailable to this process and is scored later.
    report = {
        "schema_version": "qualibug.real-benchmark-report.v1",
        "variant": VARIANT,
        "findings": findings,
        "summary": summary,
    }
    write_json(OUT_DIR / f"{VARIANT}.report.json", report)
    write_json(OUT_DIR / f"{VARIANT}.summary.json", summary)
    write_json(OUT_DIR / f"{VARIANT}.full_result.json", {"summary": summary, "full_result": result})
    (OUT_DIR / f"{VARIANT}.summary.md").write_text(
        "\n".join([
            f"# Real benchmark: {VARIANT}",
            "",
            f"- Generated: {summary['generated']}",
            f"- Selected: {summary['selected']}",
            f"- Compiled: {summary['compiled']}",
            f"- Executed: {summary['executed']}",
            f"- Oracle violations: {summary['oracle_violations']}",
            f"- Formal deliveries: {summary['formal_deliveries']}",
            f"- Elapsed seconds: {summary['elapsed_seconds']}",
            f"- Blockers: `{json.dumps(summary['blocker_counts_recursive'], ensure_ascii=False)}`",
        ]) + "\n",
        encoding="utf-8",
    )
    print("REAL_BENCHMARK_SUMMARY=" + json.dumps(summary, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
