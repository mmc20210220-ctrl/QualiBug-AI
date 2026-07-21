"""
QualiBug Loop Watchdog (Loop 0) — 自主监测层

四层检测:
  1. HEARTBEAT — 心跳超时 (>5min 无更新) → 进程是否活着？LLM 是否 hang？
  2. TARGET — 目标服务是否可达？(MES /health)
  3. QUALIBUG — QualiBug 自身是否存活？
  4. METRICS — 发现率是否退化？(本轮 vs 上轮对比)

输出: .loop_events.jsonl (结构化事件流，Agent 一次性读完)
       stdout (简洁状态行，适合 cron log)

用法:
  python -m ai_test_asset_center.loop_watchdog
  python -m ai_test_asset_center.loop_watchdog --once  # 单次检查，不循环
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import warnings

# ── 配置 ──────────────────────────────────────────────────
PROJECT = os.environ.get("QUALIBUG_PROJECT", "real_project_demo")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "platform_outputs" / PROJECT
HEARTBEAT_FILE = OUTPUT_DIR / ".loop_heartbeat.json"
PROGRESS_FILE = OUTPUT_DIR / ".loop_progress.json"
SWEEP_REPORT = OUTPUT_DIR / "sweep_report.json"
EVENTS_FILE = OUTPUT_DIR / ".loop_events.jsonl"
CONFIG_FILE = PROJECT_ROOT / "platform_inputs" / PROJECT / "real_project_config.json"

HEARTBEAT_WARN_S = int(os.environ.get("QUALIBUG_HEARTBEAT_WARN_S", "300"))
HEARTBEAT_TIMEOUT_S = int(os.environ.get("QUALIBUG_HEARTBEAT_TIMEOUT_S", "900"))  # no automatic kill
DEGRADATION_BUG_DROP = 0.5      # Bug 数下降 >50% → 标记退化
DEGRADATION_INCONCLUSIVE_RISE = 0.20  # Inconclusive 率上升 >20pp → 标记退化
TARGET_HEALTH_TIMEOUT_S = 10
from .version import DEFAULT_PRIVATE_PILOT_PORT as QUALIBUG_PORT
POLL_INTERVAL_S = 120           # cron 每 2 分钟跑一次


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class WatchdogEvent:
    ts: float
    level: str          # OK | WARN | ERROR
    category: str       # heartbeat | target | qualibug | metrics
    detail: str
    suggestion: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict | None:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _write_event(event: WatchdogEvent):
    """追加事件到 .loop_events.jsonl"""
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "ts": event.ts,
        "ts_iso": datetime.fromtimestamp(event.ts, tz=timezone.utc).isoformat(),
        "level": event.level,
        "category": event.category,
        "detail": event.detail,
        "suggestion": event.suggestion,
    }, ensure_ascii=False)
    with open(EVENTS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _get_target_url() -> str | None:
    """从项目配置读取目标 URL"""
    config = _load_json(CONFIG_FILE)
    if config:
        return config.get("base_url", "")
    return None


# ── 检测 1: 心跳 ──────────────────────────────────────────

def check_heartbeat() -> WatchdogEvent | None:
    """Check durable heartbeat without killing a live long-running API stage.

    Reader/Reasoner calls can legitimately take many minutes.  A stale heartbeat
    from a *live* process is therefore a warning and an audit signal, not proof
    of a hung process.  The runtime supervisor owns retries/resume; this watchdog
    never kills a worker merely because an external provider is slow.
    """
    hb = _load_json(HEARTBEAT_FILE)
    if hb is None:
        return None

    status = str(hb.get("status", "RUNNING"))
    if status in {"COMPLETED", "CONVERGED", "STUCK", "FAILED_RETRYABLE", "FAILED_TERMINAL", "SKIPPED_ALREADY_RUNNING"}:
        return None

    age_s = time.time() - float(hb.get("ts", 0) or 0)
    pid = int(hb.get("pid", 0) or 0)
    if age_s < HEARTBEAT_WARN_S:
        return WatchdogEvent(
            ts=time.time(), level="OK", category="heartbeat",
            detail="心跳正常 (step={}, round={}, age={:.0f}s)".format(
                hb.get("step"), hb.get("round"), age_s),
        )

    process_alive = False
    if pid > 0:
        try:
            os.kill(pid, 0)
            process_alive = True
        except OSError:
            process_alive = False

    if process_alive:
        level = "WARN" if age_s < HEARTBEAT_TIMEOUT_S else "ERROR"
        return WatchdogEvent(
            ts=time.time(), level=level, category="heartbeat",
            detail=(
                "心跳陈旧 {:.0f}s (step={}, pid={})；进程仍存活。"
                "可能处于慢 Reader/Reasoner 调用，禁止 Watchdog 直接 kill。"
            ).format(age_s, hb.get("step"), pid),
            suggestion="检查 runtime 日志、网络活动和 provider 延迟；由 Runtime Supervisor 决定重试或恢复",
        )

    return WatchdogEvent(
        ts=time.time(), level="ERROR", category="heartbeat",
        detail="心跳陈旧 {:.0f}s (step={})，进程 {} 已退出 → loop 崩溃".format(
            age_s, hb.get("step"), pid),
        suggestion="读取 self_improving_report.json 中的 traceback；下一次调度将从持久化状态恢复",
    )


# ── 检测 2: 目标服务 ──────────────────────────────────────

def check_target() -> WatchdogEvent:
    """检测 MES 目标服务是否可达"""
    base_url = _get_target_url()
    if not base_url:
        return WatchdogEvent(
            ts=time.time(), level="WARN", category="target",
            detail="无法读取目标 URL 配置",
            suggestion="检查 platform_inputs/real_project_demo/real_project_config.json"
        )

    # 尝试多个健康检查端点
    health_endpoints = ["/openapi.json", "/docs", "/"]
    last_error = None

    for ep in health_endpoints:
        health_url = base_url.rstrip("/") + ep
        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=TARGET_HEALTH_TIMEOUT_S) as resp:
                if resp.status in (200, 302, 307):
                    return WatchdogEvent(
                        ts=time.time(), level="OK", category="target",
                        detail="目标存活 ({} → {})".format(health_url, resp.status),
                    )
        except urllib.error.HTTPError as e:
            last_error = e
            continue  # 404/405 尝试下一个端点
        except urllib.error.URLError as e:
            last_error = e
            continue
        except Exception as e:
            last_error = e
            continue

    error_detail = "目标不可达: 所有端点均失败"
    if last_error:
        error_detail = "目标不可达: {} → {}".format(
            base_url.rstrip("/") + health_endpoints[-1],
            getattr(last_error, 'reason', str(last_error)))
    return WatchdogEvent(
        ts=time.time(), level="ERROR", category="target",
        detail=error_detail,
        suggestion="检查 MES 服务: cd mes_target/mes-buglab-target/backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
    )


# ── 检测 3: QualiBug 自身 ─────────────────────────────────

def check_qualibug() -> WatchdogEvent:
    """检测 QualiBug 服务是否存活"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex(("127.0.0.1", QUALIBUG_PORT))
        sock.close()
        if result == 0:
            return WatchdogEvent(
                ts=time.time(), level="OK", category="qualibug",
                detail="QualiBug 存活 (端口 {})".format(QUALIBUG_PORT),
            )
        else:
            return WatchdogEvent(
                ts=time.time(), level="WARN", category="qualibug",
                detail="QualiBug 端口 {} 未监听".format(QUALIBUG_PORT),
                suggestion="启动 QualiBug: python -m ai_test_asset_center.private_pilot_entrypoint"
            )
    except Exception as e:
        return WatchdogEvent(
            ts=time.time(), level="ERROR", category="qualibug",
            detail="QualiBug 检测异常: {}".format(e),
        )


# ── 检测 4: 发现率退化 ───────────────────────────────────

def check_metrics() -> WatchdogEvent | None:
    """
    对比 sweep_report.json 中最近两轮的数据:
      - Bug 数下降 >50% → WARN
      - Inconclusive 率上升 >20pp → WARN
    """
    report = _load_json(SWEEP_REPORT)
    if report is None:
        return None  # 还没跑过 sweep

    rounds = report.get("rounds", [])
    if len(rounds) < 2:
        return None  # 只有一轮，没有对比基准

    prev = rounds[-2]
    curr = rounds[-1]

    prev_bugs = prev.get("confirmed", 0) + prev.get("scenario_bugs", 0) + prev.get("db_bugs", 0)
    curr_bugs = curr.get("confirmed", 0) + curr.get("scenario_bugs", 0) + curr.get("db_bugs", 0)

    prev_total = max(prev.get("executed", 1), 1)
    curr_total = max(curr.get("executed", 1), 1)
    prev_inconclusive_rate = prev.get("inconclusive", 0) / prev_total
    curr_inconclusive_rate = curr.get("inconclusive", 0) / curr_total

    warnings = []

    if prev_bugs > 0 and curr_bugs < prev_bugs * (1 - DEGRADATION_BUG_DROP):
        drop_pct = 100 * (1 - curr_bugs / max(prev_bugs, 1))
        warnings.append("Bug 发现数退化: {} → {} (下降 {:.0f}%)".format(prev_bugs, curr_bugs, drop_pct))

    if curr_inconclusive_rate - prev_inconclusive_rate > DEGRADATION_INCONCLUSIVE_RISE:
        warnings.append("Inconclusive 率上升: {:.0%} → {:.0%}".format(
            prev_inconclusive_rate, curr_inconclusive_rate))

    if warnings:
        return WatchdogEvent(
            ts=time.time(), level="WARN", category="metrics",
            detail="; ".join(warnings),
            suggestion="检查最近是否改了 prompt / verifier / route_map，考虑 git diff 回看变更"
        )

    return WatchdogEvent(
        ts=time.time(), level="OK", category="metrics",
        detail="指标正常 (bugs: {}→{}, inconclusive: {:.0%}→{:.0%})".format(
            prev_bugs, curr_bugs, prev_inconclusive_rate, curr_inconclusive_rate),
    )


# ── 自愈动作 ──────────────────────────────────────────────

def auto_recover(events: list[WatchdogEvent]):
    """Record recovery intent only; never SIGTERM a live discovery worker.

    Actual restart/resume is coordinated by LoopRuntimeSession and the scheduler
    lease.  Killing a live worker based only on heartbeat age caused normal slow
    DeepSeek calls to be misclassified as hangs.
    """
    for evt in events:
        if evt.category == "heartbeat" and evt.level == "ERROR":
            _write_event(WatchdogEvent(
                ts=time.time(), level="WARN", category="heartbeat",
                detail="自愈请求已记录；Watchdog 不会直接终止 worker",
                suggestion="等待调度器按 lease / terminal state 恢复",
            ))


# ── 主入口 ────────────────────────────────────────────────

def run_watchdog(once: bool = False) -> list[WatchdogEvent]:
    """运行一次完整检测，返回所有事件"""
    events: list[WatchdogEvent] = []

    # 1. 心跳
    hb_event = check_heartbeat()
    if hb_event:
        events.append(hb_event)

    # 2. 目标
    events.append(check_target())

    # 3. QualiBug
    events.append(check_qualibug())

    # 4. 指标
    metrics_event = check_metrics()
    if metrics_event:
        events.append(metrics_event)

    # 5. Evolution (Phase81)
    try:
        from .evolution_watchdog import tick_evolution_watchdog
        evo_status = tick_evolution_watchdog()
        if evo_status.get("alerts"):
            for alert in evo_status["alerts"]:
                events.append(WatchdogEvent(
                    level="WARN",
                    category="evolution",
                    detail=f"Job {alert['job_id']}: {alert['type']} (state={alert.get('state','?')})",
                    suggestion="Evolution job recovered" if evo_status.get("recovered", 0) > 0 else "Check evolution_jobs.json",
                ))
        if evo_status.get("recovered", 0) > 0:
            events.append(WatchdogEvent(
                level="OK",
                category="evolution",
                detail=f"Auto-recovered {evo_status['recovered']} evolution job(s)",
            ))
    except Exception:
        pass  # Evolution watchdog optional

    # 写入事件流
    for evt in events:
        _write_event(evt)

    # 输出摘要
    errors = [e for e in events if e.level == "ERROR"]
    warns = [e for e in events if e.level == "WARN"]
    oks = [e for e in events if e.level == "OK"]

    ts = _now_iso()[:19]
    status_icon = "RED" if errors else ("YELLOW" if warns else "GREEN")
    print("{} [{}] Watchdog: {} OK, {} WARN, {} ERROR".format(
        status_icon, ts, len(oks), len(warns), len(errors)), flush=True)

    for e in errors + warns:
        print("  [{}] {}: {}".format(e.level, e.category, e.detail), flush=True)
        if e.suggestion:
            print("         -> {}".format(e.suggestion), flush=True)

    # 自愈
    if errors:
        auto_recover(events)

    return events


def run_continuously():
    """持续运行模式 (给 cron 的替代，调试用)"""
    print("QualiBug Loop Watchdog — 每 {}s 检测一次".format(POLL_INTERVAL_S), flush=True)
    print("事件流: {}".format(EVENTS_FILE), flush=True)
    print("心跳文件: {}".format(HEARTBEAT_FILE), flush=True)
    print()
    while True:
        try:
            run_watchdog(once=True)
        except Exception as e:
            print("  [watchdog error] {}".format(e), flush=True)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_watchdog(once=True)
    else:
        run_continuously()
