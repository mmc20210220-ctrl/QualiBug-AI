"""真实端到端漏斗对比：对运行中的 benchmark_mall 靶场跑一次 scan()。

用法:
  python _funnel_benchmark.py baseline   # UNIFY_ANALYZERS=0
  python _funnel_benchmark.py optimized  # UNIFY_ANALYZERS=1

走的是产品后端一模一样的入口 ai_test_asset_center.__main__.scan()，
即 run_v12_pipeline 主链。结果落盘到 _funnel_runs/<mode>.json。
"""
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

MODE = (sys.argv[1] if len(sys.argv) > 1 else "baseline").strip().lower()

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")

# 隔离每次运行的 mainline_unification 开关
if MODE == "baseline":
    os.environ["QUALIBUG_UNIFY_ANALYZERS"] = "0"
    os.environ["QUALIBUG_UNIFY_LLM_REASONER"] = "0"
elif MODE == "optimized":
    os.environ["QUALIBUG_UNIFY_ANALYZERS"] = "1"
    os.environ["QUALIBUG_UNIFY_LLM_REASONER"] = "0"
elif MODE == "llm":
    # 分析器 + LLM Reasoner 全开，思考模式打开
    os.environ["QUALIBUG_UNIFY_ANALYZERS"] = "1"
    os.environ["QUALIBUG_UNIFY_LLM_REASONER"] = "1"
    # LLM 配置：优先 .env/.env.local，没有则用 .env.local.example（内含真实 DeepSeek 配置）。
    # 已通过 _llm_probe.py 真实 health_check 验证 online。
    _cfg_file = next(
        (ROOT / n for n in (".env", ".env.local", ".env.local.example") if (ROOT / n).exists()),
        None,
    )
    if _cfg_file is None:
        raise SystemExit("no LLM config file found")
    print(f"LLM_CONFIG_FILE: {_cfg_file}")
    _vals = dotenv_values(_cfg_file)
    for _k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        if _vals.get(_k):
            os.environ[_k] = _vals[_k]
    # 用户要求：开启 LLM 思考模式
    os.environ["LLM_THINKING_MODE"] = "enabled"
    os.environ["LLM_RESPONSE_FORMAT"] = "json_object"
    # 守住配置地板（AGENTS.md）：Reader/Reasoner 需要长超时与大 token
    os.environ["LLM_TIMEOUT_SECONDS"] = str(max(300, int(_vals.get("LLM_TIMEOUT_SECONDS", "0") or 0)))
    os.environ["LLM_MAX_TOKENS"] = str(max(32768, int(_vals.get("LLM_MAX_TOKENS", "0") or 0)))
    # Reasoner 在每一轮 run_v12_pipeline 里都会重跑(11 引擎/4 workers),很贵。
    # 为了可控地拿到“LLM 是否贡献更多正式 Bug”的真实数字,把自动多轮限到 2 轮。
    os.environ.setdefault("QUALIBUG_SCAN_MAX_ROUNDS", "2")
elif MODE == "llm_throughput":
    # B: 打通执行吞吐瓶颈。LLM 全开 + 提高每轮切片预算,让 366 条源绑定假设
    # 尽量在单轮内被消化(而不是 15/轮 被饿死)。抬高的是“每轮切片执行天花板”,
    # 与 reasoner 的 MAX_HYPOTHESES=15 / max_workers=4 地板互不相干(AGENTS.md)。
    os.environ["QUALIBUG_UNIFY_ANALYZERS"] = "1"
    os.environ["QUALIBUG_UNIFY_LLM_REASONER"] = "1"
    _cfg_file = next(
        (ROOT / n for n in (".env", ".env.local", ".env.local.example") if (ROOT / n).exists()),
        None,
    )
    if _cfg_file is None:
        raise SystemExit("no LLM config file found")
    print(f"LLM_CONFIG_FILE: {_cfg_file}")
    _vals = dotenv_values(_cfg_file)
    for _k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        if _vals.get(_k):
            os.environ[_k] = _vals[_k]
    os.environ["LLM_THINKING_MODE"] = "enabled"
    os.environ["LLM_RESPONSE_FORMAT"] = "json_object"
    os.environ["LLM_TIMEOUT_SECONDS"] = str(max(300, int(_vals.get("LLM_TIMEOUT_SECONDS", "0") or 0)))
    os.environ["LLM_MAX_TOKENS"] = str(max(32768, int(_vals.get("LLM_MAX_TOKENS", "0") or 0)))
    # 不再手工设预算：每轮切片预算由 _auto_scale_slice_budget 跟随候选池自动伸缩。
    # 写探针也不再需要 QUALIBUG_ENABLE_SANDBOX_WRITE —— 非生产环境默认可写
    # （benchmark_mall 已在 real_project_config.json 声明 environment=test）。
    # 跟随 auto_scale round_limit 抽干优化后的候选池（与 full 模式一致）。
    os.environ.pop("QUALIBUG_SCAN_MAX_ROUNDS", None)
elif MODE == "full":
    # 产品全量发现模式：分析器+LLM+自适应预算+非生产默认可写，不限制多轮。
    os.environ["QUALIBUG_UNIFY_ANALYZERS"] = "1"
    os.environ["QUALIBUG_UNIFY_LLM_REASONER"] = "1"
    _cfg_file = next(
        (ROOT / n for n in (".env", ".env.local", ".env.local.example") if (ROOT / n).exists()),
        None,
    )
    if _cfg_file is None:
        raise SystemExit("no LLM config file found")
    print(f"LLM_CONFIG_FILE: {_cfg_file}")
    _vals = dotenv_values(_cfg_file)
    for _k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        if _vals.get(_k):
            os.environ[_k] = _vals[_k]
    os.environ["LLM_THINKING_MODE"] = "enabled"
    os.environ["LLM_RESPONSE_FORMAT"] = "json_object"
    os.environ["LLM_TIMEOUT_SECONDS"] = str(max(300, int(_vals.get("LLM_TIMEOUT_SECONDS", "0") or 0)))
    os.environ["LLM_MAX_TOKENS"] = str(max(32768, int(_vals.get("LLM_MAX_TOKENS", "0") or 0)))
    # 不设置 QUALIBUG_SCAN_MAX_ROUNDS —— 跟随 auto_scale round_limit 抽干候选池
    os.environ.pop("QUALIBUG_SCAN_MAX_ROUNDS", None)
else:
    raise SystemExit(f"unknown mode: {MODE!r} (baseline|optimized|llm|llm_throughput|full)")

# 靶场运行时（与 _e2e_benchmark.py 一致）
os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")
os.environ["QUALIBUG_TARGET_BASE_URL"] = "http://localhost:8080"
# The benchmark target is an explicitly declared local non-production system.
# Permit diagnostic/preflight requests to that exact internal target; the
# production/unknown write boundary remains enforced by the runtime contract.
os.environ["QUALIBUG_SSRF_ALLOW_INTERNAL"] = "1"
os.environ["QUALIBUG_DB_DSN"] = (
    "postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall"
)
os.environ["ENABLE_V12_STATE_GRAPH_ENGINE"] = "true"

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
PROJECT = "benchmark_mall"
INPUT = ROOT / "projects" / PROJECT / "input"
BASE_URL = "http://localhost:8080"

# 每次运行前彻底清空该项目的持久化状态（campaign 账本、slice ledger、lease、
# 历史发现），保证 baseline / optimized 都是从零开始的干净扫描，避免上一轮
# 已完成的 campaign 被 resume 导致第二次跑“0 发现直接返回”。仅限本靶场项目。
assert PROJECT == "benchmark_mall", "reset guard only for benchmark_mall"
from ai_test_asset_center.benchmark_target_cleanliness import assert_benchmark_target_clean
from _funnel_benchmark_prep import prepare_funnel_benchmark_target  # noqa: E402

# Reset target DB first so write-probe residue cannot poison recall, then prove
# cleanliness with the reset receipt before wiping local campaign state.
_prep = prepare_funnel_benchmark_target(root=ROOT, project=PROJECT, target_base_url=BASE_URL)
print(f"PREP: {_prep}")
_cleanliness = assert_benchmark_target_clean(
    root=ROOT,
    project=PROJECT,
    target_base_url=BASE_URL,
    reset_receipt_path=str(_prep.get("reset_receipt_path") or os.environ.get("QUALIBUG_BENCHMARK_RESET_RECEIPT_PATH", "")),
)
print(f"TARGET_CLEANLINESS: {json.dumps(_cleanliness, ensure_ascii=False)}")
for _state_dir in (
    ROOT / "platform_workspace" / PROJECT,
    ROOT / "platform_outputs" / PROJECT,
):
    if _state_dir.exists():
        shutil.rmtree(_state_dir, ignore_errors=True)
        print(f"RESET: removed {_state_dir}")

api_doc_text = (INPUT / "API_SPEC.md").read_text(encoding="utf-8")
source_hash = hashlib.sha256(api_doc_text.encode("utf-8")).hexdigest()

context = {
    "scope_id": "benchmark_mall_local_scope",
    "environment_ref": "benchmark_mall_test",
    "environment_kind": "test",
    "source_manifest": {
        "source_id": "benchmark_mall/API_SPEC.md",
        "source_hash": source_hash,
    },
}
# Exercise the same product defaults used by customer scans.  The explicitly
# declared non-production target must enter governed sandbox-write mode and
# receive a disposable test-data contract; production/unknown targets remain
# fail-closed in the shared runtime and sandbox gates.  Hard-coding read-only
# here previously hid most stateful defects and made benchmark recall
# unrepresentative of the commercial product.

from ai_test_asset_center.__main__ import scan  # noqa: E402

started = time.time()
_post_run_cleanup: dict = {}
_post_run_cleanliness: dict = {}
try:
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
finally:
    # A benchmark run is allowed to exercise stateful probes, but it must leave
    # the target clean even when scan() raises.  The real reset script and its
    # receipt are the only accepted proof; deleting the audit log is forbidden.
    _post_run_cleanup = prepare_funnel_benchmark_target(
        root=ROOT,
        project=PROJECT,
        target_base_url=BASE_URL,
    )
    _post_run_cleanliness = assert_benchmark_target_clean(
        root=ROOT,
        project=PROJECT,
        target_base_url=BASE_URL,
        reset_receipt_path=str(_post_run_cleanup.get("reset_receipt_path") or ""),
    )
elapsed = time.time() - started

v12 = result.get("v12", {}) if isinstance(result, dict) else {}

from ai_test_asset_center.customer_delivery_gate import is_customer_deliverable_defect

# Runtime discovery must never load evaluator-private ground truth.  Persist a
# completed-run envelope for the separate evaluator and remain NOT_MEASURED
# until that evaluator emits an integrity-checked receipt.
_formal_findings = [
    finding
    for finding in (result.get("findings") or [])
    if isinstance(finding, dict) and is_customer_deliverable_defect(finding)
]
_campaign = result.get("campaign") if isinstance(result.get("campaign"), dict) else {}
_pipeline_health = (
    result.get("pipeline_health") if isinstance(result.get("pipeline_health"), dict) else {}
)
evaluation_submission = {
    "schema_version": "discovery_evaluation_submission.v1",
    "run_id": str(result.get("scan_id") or _campaign.get("campaign_id") or ""),
    "policy_id": str(_campaign.get("policy_version") or "unversioned"),
    "evaluation_mode": "replay",
    "scan_result": {
        "findings": list(result.get("findings") or []),
        "candidate_findings": list(result.get("candidate_findings") or []),
    },
    "pipeline_health": dict(_pipeline_health),
    "operational_metrics": {
        "elapsed_seconds": round(elapsed, 3),
        "total_findings": int(result.get("total_findings") or 0),
        "total_candidates": int(result.get("total_candidates") or 0),
        "formal_customer_deliverable_count": len(_formal_findings),
        "discovery_funnel": result.get("discovery_funnel") or {},
    },
    "fixture_governance": {
        "post_run_cleanup": _post_run_cleanup,
        "post_run_cleanliness": _post_run_cleanliness,
    },
}

summary = {
    "mode": MODE,
    "unify_analyzers": os.environ.get("QUALIBUG_UNIFY_ANALYZERS", ""),
    "unify_llm_reasoner": os.environ.get("QUALIBUG_UNIFY_LLM_REASONER", ""),
    "elapsed_sec": round(elapsed, 1),
    "prep": _prep,
    "post_run_cleanup": _post_run_cleanup,
    "post_run_cleanliness": _post_run_cleanliness,
    "success": result.get("success"),
    "grade": result.get("grade"),
    "execution_status": result.get("execution_status"),
    "total_findings": result.get("total_findings"),
    "total_candidates": result.get("total_candidates"),
    "error": result.get("error"),
    "mainline_unification": v12.get("mainline_unification"),
    "discovery_funnel": result.get("discovery_funnel"),
    "auto_scale": v12.get("auto_scale"),
    "behavior_slice_summary": (v12.get("behavior_contract") or {}).get("summary")
    if isinstance(v12.get("behavior_contract"), dict)
    else None,
    "multi_round_summary": v12.get("multi_round_summary"),
    "input_gaps": [g.get("code") for g in (result.get("input_gaps") or []) if isinstance(g, dict)],
    "external_evaluation": {
        "measurement_status": "NOT_MEASURED",
        "reason": "external_evaluator_receipt_required",
        "commercial_promotion_evidence": False,
        "formal_customer_deliverable_count": len(_formal_findings),
        "submission_file": f"{MODE}.evaluation_submission.json",
    },
}

out_dir = ROOT / "_funnel_runs"
out_dir.mkdir(exist_ok=True)
(out_dir / f"{MODE}.evaluation_submission.json").write_text(
    json.dumps(evaluation_submission, ensure_ascii=False, indent=2, default=str),
    encoding="utf-8",
)
(out_dir / f"{MODE}.json").write_text(
    json.dumps({"summary": summary, "full_result": result}, ensure_ascii=False, indent=2, default=str),
    encoding="utf-8",
)

print("=" * 70)
print(f"MODE={MODE}  elapsed={elapsed:.1f}s")
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
print("=" * 70)
