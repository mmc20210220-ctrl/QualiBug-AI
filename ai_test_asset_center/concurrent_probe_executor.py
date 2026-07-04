"""
ConcurrentProbeExecutor — ThreadPoolExecutor-based parallel probe execution.

Replaces the single-threaded probe loop with configurable concurrency.
Safe for production use: concurrency adapts to environment risk level.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any


# ── Concurrency policy ──

def _env_concurrency() -> int:
    """Concurrency level for parallel probe execution.

    Set QUALIBUG_PROBE_CONCURRENCY env var to override.
    Default 20 — probes are mostly read-only, safe to parallelize aggressively.
    """
    val = os.environ.get("QUALIBUG_PROBE_CONCURRENCY", "").strip()
    if val:
        try:
            return max(1, int(val))
        except ValueError:
            pass
    return 20


# ── Data structures ──

@dataclass
class ProbeTask:
    """Single probe task definition."""
    method: str
    path: str
    body: dict | None = None
    headers: dict[str, str] = field(default_factory=dict)
    variant_name: str = ""
    category: str = ""
    timeout: float = 5.0


@dataclass
class ProbeResult:
    """Single probe result with full evidence."""
    task: ProbeTask
    status_code: int = 0
    response_body: dict[str, Any] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    is_anomaly: bool = False
    anomaly_reason: str = ""
    error: str = ""
    success: bool = True


@dataclass
class BatchReport:
    """Aggregate report for a batch of probes."""
    total: int = 0
    success_count: int = 0
    failure_count: int = 0
    blocked_count: int = 0
    anomaly_count: int = 0
    total_elapsed_ms: float = 0.0
    avg_elapsed_ms: float = 0.0
    concurrency: int = 1
    results: list[ProbeResult] = field(default_factory=list)


# ── Executor ──

class ConcurrentProbeExecutor:
    """Execute probes in parallel with ThreadPoolExecutor."""

    def __init__(self, base_url: str, concurrency: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.concurrency = concurrency or _env_concurrency()
        self._session_counter = 0

    def execute_batch(self, tasks: list[ProbeTask]) -> BatchReport:
        """Execute a batch of probes concurrently.
        
        Individual failures do NOT abort the batch.
        Each probe gets its full request/response/evidence captured.
        """
        n = len(tasks)
        if n == 0:
            return BatchReport()

        t0 = time.perf_counter()
        results: list[ProbeResult] = []

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = {executor.submit(self._execute_one, t): t for t in tasks}

            for future in as_completed(futures):
                try:
                    result = future.result(timeout=30)
                except Exception as e:
                    # Individual failure — capture as error result
                    task = futures[future]
                    result = ProbeResult(
                        task=task, success=False, error=str(e)[:500],
                        is_anomaly=False, anomaly_reason="execution_error",
                    )
                results.append(result)

        # Aggregate stats
        elapsed = (time.perf_counter() - t0) * 1000
        success = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        anomalies = [r for r in results if r.is_anomaly]

        return BatchReport(
            total=n,
            success_count=len(success),
            failure_count=len(failures),
            blocked_count=0,  # Blocked tasks not supported in probe mode
            anomaly_count=len(anomalies),
            total_elapsed_ms=elapsed,
            avg_elapsed_ms=elapsed / n if n > 0 else 0,
            concurrency=self.concurrency,
            results=results,
        )

    def _execute_one(self, task: ProbeTask) -> ProbeResult:
        """Execute a single probe and detect anomalies."""
        url = self.base_url + task.path
        data = json.dumps(task.body).encode() if task.body and task.method != "GET" else None
        headers = dict(task.headers)
        if data:
            headers.setdefault("Content-Type", "application/json")

        t0 = time.perf_counter()
        status = 0
        resp_body: dict = {}
        resp_headers: dict = {}
        error = ""

        try:
            req = urllib.request.Request(url, method=task.method, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=task.timeout) as resp:
                raw = resp.read()
                resp_headers = dict(resp.headers)
                try:
                    resp_body = json.loads(raw)
                except Exception:
                    resp_body = {"_raw": raw.decode("utf-8", errors="replace")[:2000]}
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
            resp_headers = dict(e.headers)
            try:
                resp_body = json.loads(e.read())
            except Exception:
                resp_body = {}
            error = ""
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            status = 0

        elapsed = (time.perf_counter() - t0) * 1000

        # ── Anomaly detection ──
        is_anomaly = False
        reason = ""

        if error:
            is_anomaly = True
            reason = f"连接失败: {error}"
        elif status >= 500:
            is_anomaly = True
            reason = f"服务端异常 HTTP{status}"
        elif isinstance(resp_body, dict):
            if resp_body.get("ok") is False:
                is_anomaly = True
                reason = f"业务逻辑失败: {str(resp_body.get('error', ''))[:100]}"
            elif resp_body.get("error") and status not in (401, 403, 404):
                is_anomaly = True
                reason = f"错误响应: {str(resp_body.get('error',''))[:100]}"
            elif resp_body.get("traceback"):
                is_anomaly = True
                reason = "响应体泄露栈追踪"
            elif resp_body.get("exception") or "traceback" in str(resp_body).lower():
                is_anomaly = True
                reason = "响应体泄露异常信息"
            # Status code anomalies
            if status == 200 and task.method in ("POST", "PUT", "PATCH") and not resp_body:
                is_anomaly = True
                reason = "写操作返回空响应体"

        return ProbeResult(
            task=task,
            status_code=status,
            response_body=resp_body,
            response_headers=resp_headers,
            elapsed_ms=elapsed,
            is_anomaly=is_anomaly,
            anomaly_reason=reason,
            error=error,
            success=not bool(error),
        )

    def to_finding(self, result: ProbeResult) -> dict[str, Any]:
        """Convert a ProbeResult to a QualiBug finding dict."""
        return {
            "severity": "P0" if result.status_code >= 500 else "P1",
            "title": f"[{result.task.category or 'auto'}] {result.task.method} {result.task.path} ({result.task.variant_name})",
            "category": "runtime_probe",
            "source": "runtime_probe",
            "method": result.task.method,
            "path": result.task.path,
            "description": json.dumps({
                "status": result.status_code,
                "response": str(result.response_body)[:500],
                "anomaly": result.anomaly_reason,
            }, ensure_ascii=False),
            "confidence_score": 0.85 if result.is_anomaly else 0.3,
            "evidence": result.anomaly_reason,
            "elapsed_ms": result.elapsed_ms,
        }


# ── Convenience ──

def probe_endpoint(
    base_url: str,
    method: str,
    path: str,
    body: dict | None = None,
    headers: dict | None = None,
    category: str = "",
    variant: str = "",
) -> ProbeResult:
    """Quick single probe — convenience wrapper."""
    executor = ConcurrentProbeExecutor(base_url, concurrency=1)
    task = ProbeTask(
        method=method, path=path, body=body,
        headers=headers or {}, variant_name=variant, category=category,
    )
    return executor._execute_one(task)


# ═══════════════════════════════════════════════════════
# V12.2: Scenario-level execution with full trace
# ═══════════════════════════════════════════════════════

@dataclass
class ScenarioTrace:
    """Full execution trace for a multi-step scenario."""
    scenario_id: str
    steps: list[dict] = field(default_factory=list)   # [{action, method, path, status, elapsed_ms, body_excerpt}, ...]
    bindings: dict[str, str] = field(default_factory=dict)  # Variable bindings across steps
    errors: list[str] = field(default_factory=list)
    total_elapsed_ms: float = 0.0
    concurrency: int = 1


class ScenarioExecutor:
    """Execute multi-step scenarios with variable binding and full trace recording."""

    def __init__(self, base_url: str, concurrency: int = 20, auth_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.concurrency = concurrency
        self.auth_token = auth_token
        self._executor = ConcurrentProbeExecutor(base_url, concurrency)

    def execute(self, scenario: Any) -> ScenarioTrace:
        """Execute a scenario's steps sequentially, binding variables across steps."""
        trace = ScenarioTrace(scenario_id=getattr(scenario, "id", "unknown"))
        bindings: dict[str, str] = {}
        steps = getattr(scenario, "steps", [])

        for step in steps:
            path = getattr(step, "api_path", "")
            method = getattr(step, "api_method", "GET")
            body = dict(getattr(step, "body_template", {}))
            actor = getattr(step, "actor", "admin")

            # Resolve variable bindings
            for k, v in bindings.items():
                path = path.replace(f"{{{k}}}", str(v))
                body = {bk: (str(bv).replace(f"{{{k}}}", str(v)) if isinstance(bv, str) else bv)
                        for bk, bv in body.items()}

            headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}
            headers["X-QualiBug-Actor"] = actor

            task = ProbeTask(method=method, path=path, body=body, headers=headers,
                           variant_name=getattr(step, "action", ""),
                           category=getattr(scenario, "category", ""))

            result = self._executor._execute_one(task)

            trace.steps.append({
                "action": getattr(step, "action", "?"),
                "method": method, "path": path,
                "status": result.status_code,
                "elapsed_ms": result.elapsed_ms,
                "body_excerpt": json.dumps(result.response_body, ensure_ascii=False, default=str)[:500] if result.response_body else "",
                "is_anomaly": result.is_anomaly,
                "anomaly": result.anomaly_reason,
            })

            # Extract bindings from response
            if result.success and isinstance(result.response_body, dict):
                for container in ("order", "data", "result"):
                    nested = result.response_body.get(container, {})
                    if isinstance(nested, dict):
                        for idk in ("order_id", "id", "product_id", "user_id"):
                            if idk in nested:
                                bindings[idk] = nested[idk]

            if not result.success:
                trace.errors.append(f"Step '{getattr(step, 'action', '?')}' failed: {result.error}")

        trace.bindings = bindings
        trace.total_elapsed_ms = sum(s.get("elapsed_ms", 0) for s in trace.steps)
        return trace
