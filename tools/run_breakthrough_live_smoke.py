"""Short live V12 smoke against declared non-production benchmark target.

Writes a new content-addressed receipt under _audit_packs/live_smoke_*; never
overwrites frozen llm_throughput baseline artifacts.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
PROJECT = "benchmark_mall"
BASE_URL = "http://localhost:8080"

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")
    os.environ["QUALIBUG_TARGET_BASE_URL"] = BASE_URL
    os.environ["QUALIBUG_SSRF_ALLOW_INTERNAL"] = "1"
    os.environ["QUALIBUG_UNIFY_ANALYZERS"] = "1"
    os.environ["QUALIBUG_UNIFY_LLM_REASONER"] = "0"
    os.environ["QUALIBUG_SCAN_MAX_ROUNDS"] = "1"
    os.environ["ENABLE_V12_STATE_GRAPH_ENGINE"] = "true"

    from ai_test_asset_center.artifact_redactor import write_json_redacted
    from ai_test_asset_center.benchmark_target_cleanliness import assert_benchmark_target_clean
    from _funnel_benchmark_prep import prepare_funnel_benchmark_target
    from ai_test_asset_center.__main__ import scan

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = ROOT / "_audit_packs" / f"live_smoke_{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    prep = prepare_funnel_benchmark_target(root=ROOT, project=PROJECT, target_base_url=BASE_URL)
    cleanliness = assert_benchmark_target_clean(
        root=ROOT,
        project=PROJECT,
        target_base_url=BASE_URL,
        reset_receipt_path=str(prep.get("reset_receipt_path") or ""),
    )
    api_doc = (ROOT / "projects" / PROJECT / "input" / "API_SPEC.md").read_text(encoding="utf-8")
    source_hash = hashlib.sha256(api_doc.encode("utf-8")).hexdigest()
    started = time.time()
    result = scan(
        project=PROJECT,
        root=ROOT,
        api_doc_text=api_doc,
        base_url=BASE_URL,
        ci_gate=False,
        multi_layer=True,
        save_report=True,
        campaign_context={
            "scope_id": "breakthrough_live_smoke",
            "environment_ref": "benchmark_mall_test",
            "environment_kind": "test",
            "environment_type": "test",
            "runtime": {"environment_type": "test", "environment_kind": "test"},
            "source_manifest": {"source_id": "benchmark_mall/API_SPEC.md", "source_hash": source_hash},
        },
    )
    elapsed = time.time() - started
    post = prepare_funnel_benchmark_target(root=ROOT, project=PROJECT, target_base_url=BASE_URL)
    post_clean = assert_benchmark_target_clean(
        root=ROOT,
        project=PROJECT,
        target_base_url=BASE_URL,
        reset_receipt_path=str(post.get("reset_receipt_path") or ""),
    )
    v12 = result.get("v12") if isinstance(result.get("v12"), dict) else {}
    receipt = {
        "schema_version": "qualibug.live-smoke-receipt.v1",
        "elapsed_seconds": round(elapsed, 3),
        "success": result.get("success"),
        "execution_status": result.get("execution_status"),
        "pipeline_health": result.get("pipeline_health"),
        "external_evaluation": result.get("external_evaluation"),
        "formal_count_projection": result.get("formal_count_projection"),
        "behavior_ir": v12.get("behavior_ir") or result.get("behavior_ir"),
        "test_obligations": (v12.get("test_obligations") or result.get("test_obligations") or {}),
        "experiment_compile": (v12.get("experiment_compile") or result.get("experiment_compile") or {}),
        "obligation_plan": (v12.get("obligation_plan") or result.get("obligation_plan") or {}),
        "phases_behavior_ir": (v12.get("phases") or {}).get("behavior_ir"),
        "prep": prep,
        "post_run_cleanup": post,
        "post_run_cleanliness": post_clean,
        "pre_cleanliness": cleanliness,
        "total_findings": result.get("total_findings"),
        "score_semantics": result.get("score_semantics"),
        "commercial_quality_score": result.get("commercial_quality_score"),
    }
    write_json_redacted(out / "live_smoke_receipt.json", receipt)
    write_json_redacted(out / "scan_result.redacted.json", result)
    print(json.dumps({
        "out": str(out),
        "elapsed": receipt["elapsed_seconds"],
        "behavior_ir_phase": receipt.get("phases_behavior_ir"),
        "measurement_status": (receipt.get("external_evaluation") or {}).get("measurement_status"),
        "commercial_quality_score": receipt.get("commercial_quality_score"),
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
