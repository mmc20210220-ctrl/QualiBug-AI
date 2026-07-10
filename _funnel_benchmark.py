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
    # 仅为控制单次验证成本，把 scan 多轮驱动限到 2 轮。
    os.environ.setdefault("QUALIBUG_SCAN_MAX_ROUNDS", "2")
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
    "execution_mode": "safe_read_only",
    "source_manifest": {
        "source_id": "benchmark_mall/API_SPEC.md",
        "source_hash": source_hash,
    },
    "test_data_contract": {"strategy": "blocked_with_testability_gap"},
}

# 刷新测试账号 token（过期会导致权限/隔离探针全部降级）
if MODE in {"llm", "llm_throughput", "full"}:
    import subprocess
    _rt = ROOT / "_refresh_tokens.py"
    if _rt.exists():
        subprocess.run([sys.executable, str(_rt)], check=False)

from ai_test_asset_center.__main__ import scan  # noqa: E402

started = time.time()
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

v12 = result.get("v12", {}) if isinstance(result, dict) else {}

# 扫描后评分（仅测量召回，不参与发现过程）
benchmark_metrics: dict = {}
_gt_default = Path(
    r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
    r"\qualibug_enterprise_benchmark_v0_5_windows_native_stable\hidden_ground_truth\bugs.json"
)
_gt_path = Path(os.environ.get("QUALIBUG_BENCHMARK_GROUND_TRUTH", str(_gt_default)))
if _gt_path.exists():
    try:
        from ai_test_asset_center.benchmark_compute import compute_benchmark
        benchmark_metrics = compute_benchmark(
            PROJECT,
            result.get("findings") or [],
            candidates=result.get("candidate_findings") or [],
            root=ROOT,
            ground_truth_path=str(_gt_path),
        )
    except Exception as exc:
        benchmark_metrics = {"benchmark_active": False, "error": str(exc)[:200]}

summary = {
    "mode": MODE,
    "unify_analyzers": os.environ.get("QUALIBUG_UNIFY_ANALYZERS", ""),
    "unify_llm_reasoner": os.environ.get("QUALIBUG_UNIFY_LLM_REASONER", ""),
    "elapsed_sec": round(elapsed, 1),
    "success": result.get("success"),
    "grade": result.get("grade"),
    "execution_status": result.get("execution_status"),
    "total_findings": result.get("total_findings"),
    "total_candidates": result.get("total_candidates"),
    "error": result.get("error"),
    "mainline_unification": v12.get("mainline_unification"),
    "discovery_funnel": v12.get("discovery_funnel"),
    "auto_scale": v12.get("auto_scale"),
    "behavior_slice_summary": (v12.get("behavior_contract") or {}).get("summary")
    if isinstance(v12.get("behavior_contract"), dict)
    else None,
    "multi_round_summary": v12.get("multi_round_summary"),
    "input_gaps": [g.get("code") for g in (result.get("input_gaps") or []) if isinstance(g, dict)],
    "benchmark_recall": {
        "ground_truth_bug_count": benchmark_metrics.get("ground_truth_bug_count"),
        "true_positives": benchmark_metrics.get("true_positives"),
        "false_negatives": benchmark_metrics.get("false_negatives"),
        "recall": benchmark_metrics.get("recall"),
        "precision": benchmark_metrics.get("precision"),
        "f1_score": benchmark_metrics.get("f1_score"),
        "missed_bug_ids_sample": (benchmark_metrics.get("missed_bug_ids") or [])[:20],
    } if benchmark_metrics.get("benchmark_active") else benchmark_metrics,
}

out_dir = ROOT / "_funnel_runs"
out_dir.mkdir(exist_ok=True)
(out_dir / f"{MODE}.json").write_text(
    json.dumps({"summary": summary, "full_result": result}, ensure_ascii=False, indent=2, default=str),
    encoding="utf-8",
)

print("=" * 70)
print(f"MODE={MODE}  elapsed={elapsed:.1f}s")
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
print("=" * 70)
