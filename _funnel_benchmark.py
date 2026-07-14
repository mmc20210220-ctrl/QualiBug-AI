"""真实端到端漏斗对比：对运行中的 benchmark_mall 靶场跑一次 scan()。

用法:
  python _funnel_benchmark.py baseline   # UNIFY_ANALYZERS=0
  python _funnel_benchmark.py optimized  # UNIFY_ANALYZERS=1

走的是产品后端一模一样的入口 ai_test_asset_center.__main__.scan()，
即 run_v12_pipeline 主链。结果落盘到 _funnel_runs/<mode>.json。
"""
import atexit
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

# Exclusive lock: concurrent funnel_benchmark processes reset the same
# benchmark_mall DB/workspace and corrupt each other's audits.  Holders set
# QUALIBUG_FUNNEL_BENCHMARK_LOCK_HOLDER=1; everyone else exits before prep.
_LOCK_PATH = ROOT / "_funnel_runs" / ".exclusive_benchmark.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # Windows: signal 0 is not supported by os.kill; use OpenProcess.
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_exclusive_benchmark_lock() -> None:
    if str(os.environ.get("QUALIBUG_FUNNEL_BENCHMARK_LOCK_HOLDER") or "").strip() == "1":
        return
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        if _LOCK_PATH.exists():
            try:
                meta = json.loads(_LOCK_PATH.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            holder = int(meta.get("pid") or 0)
            if holder and holder != os.getpid() and _pid_alive(holder):
                raise SystemExit(
                    f"exclusive benchmark lock held by pid={holder} "
                    f"({meta.get('purpose') or 'unknown'}); refusing to start "
                    f"and reset shared benchmark_mall state"
                )
            # Stale lock — remove and retry.
            try:
                _LOCK_PATH.unlink()
            except OSError:
                time.sleep(0.2)
                continue
        payload = {
            "pid": os.getpid(),
            "purpose": f"funnel_benchmark:{MODE}",
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False))
            os.environ["QUALIBUG_FUNNEL_BENCHMARK_LOCK_HOLDER"] = "1"

            def _release() -> None:
                try:
                    if _LOCK_PATH.exists():
                        cur = json.loads(_LOCK_PATH.read_text(encoding="utf-8"))
                        if int(cur.get("pid") or 0) == os.getpid():
                            _LOCK_PATH.unlink()
                except Exception:
                    pass

            atexit.register(_release)
            return
        except FileExistsError:
            time.sleep(0.2)
            continue
    raise SystemExit(f"could not acquire exclusive benchmark lock at {_LOCK_PATH}")


_acquire_exclusive_benchmark_lock()

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

from ai_test_asset_center.customer_delivery_gate import (
    apply_governed_campaign_cleanup,
    is_customer_deliverable_defect,
)
from ai_test_asset_center.artifact_redactor import write_json_redacted
from ai_test_asset_center.discovery_quality_projection import (
    attach_quality_projection_to_scan_result,
    build_external_evaluation_projection,
)


def _projected_formal_count_projection(scan_result: dict) -> dict:
    counts = scan_result.get("formal_count_projection") if isinstance(scan_result, dict) else None
    if not isinstance(counts, dict) or counts.get("schema_version") != "qualibug.discovery-quality-projection.v2":
        raise RuntimeError("formal_count_projection_missing_after_attach")
    return counts


# Runtime discovery must never load evaluator-private ground truth.  Persist a
# completed-run envelope for the separate evaluator and remain NOT_MEASURED
# until that evaluator emits an integrity-checked receipt.
if isinstance(result, dict):
    _cleanup_reset_id = str(
        (_post_run_cleanliness or {}).get("reset_receipt_id")
        or ((_post_run_cleanup or {}).get("reset_receipt") or {}).get("receipt_id")
        or ""
    )
    _cleanup_observation_ref = str(
        (_post_run_cleanliness or {}).get("archived_receipt")
        or (_post_run_cleanup or {}).get("reset_receipt_path")
        or ""
    )
    if (
        (_post_run_cleanliness or {}).get("status") == "clean_reset_receipt_verified"
        and _cleanup_reset_id
        and _cleanup_observation_ref
    ):
        _all_delivery_items = [
            item
            for item in list(result.get("findings") or []) + list(result.get("candidate_findings") or [])
            if isinstance(item, dict)
        ]
        _before_formal_count = sum(1 for item in _all_delivery_items if is_customer_deliverable_defect(item))
        _defects_after_cleanup, _clues_after_cleanup = apply_governed_campaign_cleanup(
            _all_delivery_items,
            {
                "status": "SUCCEEDED",
                "dirty_environment": False,
                "audit_receipt_id": _cleanup_reset_id,
                "after_cleanup_observation_ref": _cleanup_observation_ref,
            },
        )
        _after_formal_count = sum(1 for item in _defects_after_cleanup if is_customer_deliverable_defect(item))
        result["findings"] = _defects_after_cleanup
        result["candidate_findings"] = _clues_after_cleanup
        result["post_run_cleanup_readjudication"] = {
            "status": "applied",
            "scope": "cleanup_only_customer_delivery_gate_reasons",
            "formal_before": _before_formal_count,
            "formal_after": _after_formal_count,
            "promoted_by_campaign_cleanup": max(0, _after_formal_count - _before_formal_count),
            "cleanup_failures_preserved_in_operational_metrics": True,
            "audit_receipt_id": _cleanup_reset_id,
            "after_cleanup_observation_ref": _cleanup_observation_ref,
        }
result = attach_quality_projection_to_scan_result(result if isinstance(result, dict) else {})
_counts = _projected_formal_count_projection(result)
_campaign = result.get("campaign") if isinstance(result.get("campaign"), dict) else {}
_pipeline_health = (
    result.get("pipeline_health") if isinstance(result.get("pipeline_health"), dict) else {}
)
_ops_metrics: dict = {
    "elapsed_seconds": round(elapsed, 3),
    "wall_clock_seconds": round(elapsed, 3),
    "total_findings": int(result.get("total_findings") or 0),
    "total_candidates": int(result.get("total_candidates") or 0),
    "formal_customer_deliverable_count": _counts["formal_customer_deliverable_count"],
    "discovery_funnel": result.get("discovery_funnel") or {},
    "formal_count_projection": _counts,
}
# Prefer strict observed metrics; never invent cost=0 when usage is unknown.
try:
    from ai_test_asset_center.scan_operational_metrics import (
        OperationalMetricsNotMeasured,
        collect_observed_scan_operational_metrics,
    )

    _strict_ops = collect_observed_scan_operational_metrics(
        scan_result=result,
        wall_clock_seconds=elapsed,
        runtime_view={
            "target": {
                "runtime": {
                    "environment_type": "test",
                    "base_url": BASE_URL,
                }
            }
        },
    )
    _ops_metrics.update(_strict_ops)
    _ops_metrics["operational_metrics_status"] = "MEASURED"
except Exception as _ops_exc:  # OperationalMetricsNotMeasured or missing fields
    _ops_metrics["operational_metrics_status"] = "NOT_MEASURED"
    _ops_metrics["operational_metrics_reason"] = f"{type(_ops_exc).__name__}: {str(_ops_exc)[:200]}"
    # Explicit nulls — never display unknown cost/usage as 0.
    _ops_metrics["estimated_cost_usd"] = None
    _ops_metrics["request_count"] = None
    _ops_metrics["engine_success_rate"] = None
    # Still surface cleanup/wall-clock when the run observed them, so envelopes
    # remain scorable for safety gates even when unit_cost is NOT_MEASURED.
    try:
        from ai_test_asset_center.scan_operational_metrics import _cleanup_failure_count

        _ops_metrics["cleanup_failures"] = int(_cleanup_failure_count(v12 if isinstance(v12, dict) else result))
    except Exception:
        _ops_metrics.setdefault("cleanup_failures", None)
    _ops_metrics.setdefault("wall_clock_seconds", round(elapsed, 3))
    _ph = _pipeline_health if isinstance(_pipeline_health, dict) else {}
    if _ops_metrics.get("cleanup_failures") is None and _ph.get("cleanup_failure_count") is not None:
        _ops_metrics["cleanup_failures"] = int(_ph.get("cleanup_failure_count") or 0)
    _ops_metrics["dirty_test_environments"] = (
        1 if int(_ops_metrics.get("cleanup_failures") or 0) > 0 else 0
    )

from ai_test_asset_center.campaign_api_contract import (
    build_evaluation_submission,
)
from tools.normalize_evaluation_run_envelope import normalize_envelope

_mainline_run = result.get("mainline_run") or v12.get("mainline_run")
if not isinstance(_mainline_run, dict):
    _err = ""
    if isinstance(result, dict):
        _err = str(result.get("error") or "")
        print(
            "MAINLINE_MISSING_DIAG:",
            {
                "result_keys": sorted(result.keys())[:40],
                "success": result.get("success"),
                "error": _err[:800],
                "v12_type": type(v12).__name__,
                "v12_keys": sorted(v12.keys())[:20] if isinstance(v12, dict) else [],
            },
            flush=True,
        )
    raise RuntimeError(f"benchmark_mainline_run_missing:{_err[:500]}")

out_dir = ROOT / "_funnel_runs"
out_dir.mkdir(exist_ok=True)

_external = build_external_evaluation_projection(
    measurement_status="NOT_MEASURED",
    reason="external_evaluator_receipt_required",
    formal_customer_deliverable_count=_counts["formal_customer_deliverable_count"],
)
_external["commercial_promotion_evidence"] = False
_external["submission_file"] = f"{MODE}.evaluation_submission.json"

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
    "formal_customer_deliverable_count": _counts["formal_customer_deliverable_count"],
    "formal_count_projection": _counts,
    "error": result.get("error"),
    "mainline_unification": v12.get("mainline_unification"),
    "discovery_funnel": result.get("discovery_funnel"),
    "auto_scale": v12.get("auto_scale"),
    "behavior_slice_summary": (v12.get("behavior_contract") or {}).get("summary")
    if isinstance(v12.get("behavior_contract"), dict)
    else None,
    "multi_round_summary": v12.get("multi_round_summary"),
    "input_gaps": [g.get("code") for g in (result.get("input_gaps") or []) if isinstance(g, dict)],
    "external_evaluation": _external,
    "score_semantics": result.get("score_semantics"),
    "commercial_quality_score": result.get("commercial_quality_score"),
}

# Persist the scan envelope before the evaluation submission rebuild. A late
# MemoryError must not erase a completed discovery run that already projected
# formal counts — evaluator scoring can use this dump independently.
write_json_redacted(out_dir / f"{MODE}.json", {"summary": summary, "full_result": result})

import gc

gc.collect()
try:
    _product_submission = build_evaluation_submission(
        ROOT,
        PROJECT,
        {"evaluation_mode": str(_mainline_run.get("evaluation_mode") or "")},
    )
    evaluation_submission = normalize_envelope({
        **_product_submission,
        "pipeline_health": dict(_pipeline_health),
        "operational_metrics": _ops_metrics,
        "fixture_governance": {
            "post_run_cleanup": _post_run_cleanup,
            "post_run_cleanliness": _post_run_cleanliness,
        },
    })
    # Never persist recoverable secrets in evaluator submissions or run dumps.
    write_json_redacted(out_dir / f"{MODE}.evaluation_submission.json", evaluation_submission)
except MemoryError:
    summary["evaluation_submission_error"] = "MemoryError"
    _external["submission_file"] = ""
    _external["reason"] = "evaluation_submission_memory_error"
    write_json_redacted(
        out_dir / f"{MODE}.json",
        {"summary": summary, "full_result": result},
    )
    print("EVALUATION_SUBMISSION: MemoryError (scan envelope already persisted)", flush=True)
    print("=" * 70)
    print(f"MODE={MODE}  elapsed={elapsed:.1f}s")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print("=" * 70)
    raise

print("=" * 70)
print(f"MODE={MODE}  elapsed={elapsed:.1f}s")
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
print("=" * 70)
