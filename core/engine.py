"""Evidence-aware compatibility engine.

The engine intentionally remains an in-memory facade for development and
integration tests. It never presents simulated mutations as confirmed defects.
A confirmed verdict requires a real execution receipt with request, response,
assertion, target, actor, timestamp and reproducible step metadata.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from collections import deque
from typing import Any


class Auth:
    def verify(self, token: str | None) -> str:
        expected = os.environ.get("QUALIBUG_API_TOKEN", "").strip()
        if not expected:
            raise PermissionError("api token is not configured")
        if not token or not secrets.compare_digest(str(token), expected):
            raise PermissionError("invalid api token")
        return os.environ.get("QUALIBUG_TENANT_ID", "unscoped")


class RedisClient:
    """In-memory compatibility store; production wiring must inject a real store."""

    source = "memory"

    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.store.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self.store[key] = value


class PostgresClient:
    """In-memory compatibility trace store; it is not a production database."""

    source = "memory"

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def insert(self, table: str, data: dict[str, Any]) -> None:
        self.rows.append((table, data))


class KafkaClient:
    """In-memory compatibility event bus; it is not a production broker."""

    source = "memory"

    def __init__(self) -> None:
        self.stream: deque[dict[str, Any]] = deque()

    def publish(self, event: dict[str, Any]) -> None:
        self.stream.append(event)


class Engine:
    """Compatibility facade with an explicit evidence truthfulness gate."""

    REQUIRED_CONFIRMATION_FIELDS = (
        "request",
        "response",
        "assertion",
        "timestamp",
        "target",
        "actor",
        "reproduction_steps",
    )

    def __init__(self) -> None:
        self.version = "v11"
        self.auth = Auth()
        self.redis = RedisClient()
        self.pg = PostgresClient()
        self.kafka = KafkaClient()
        self.queue: deque[dict[str, Any]] = deque()
        self.metrics = {
            "requests": 0,
            "traces": 0,
            "bugs": 0,
            "confirmed_bugs": 0,
            "candidate_findings": 0,
            "simulated_findings": 0,
            "latency": 0.0,
        }
        self.graph: dict[str, dict[str, list[Any]]] = {}
        self.logs: list[dict[str, Any]] = []
        self.rate: dict[str, list[float]] = {}

    def rate_limit(self, tenant: str) -> bool:
        now = time.time()
        bucket = [item for item in self.rate.get(tenant, []) if now - item < 10]
        if len(bucket) > 200:
            return False
        bucket.append(now)
        self.rate[tenant] = bucket
        return True

    def mutate(self, request_name: str) -> list[dict[str, Any]]:
        """Create one generic synthetic probe without domain or role assumptions."""
        normalized = str(request_name or "").strip()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return [{
            "request": normalized,
            "mutation_id": f"simulation:{digest[:16]}",
            "execution_status": "not_executed",
            "evidence_level": "synthetic",
            "simulation": True,
        }]

    def worker(self, task: dict[str, Any]) -> dict[str, Any]:
        """Return a clearly-labelled simulation result, never an execution claim."""
        return {
            "status": "simulated_not_executed",
            "execution_status": "not_executed",
            "confirmation_status": "candidate",
            "evidence_level": "synthetic",
            "simulation": True,
            "reason": "in_memory_compatibility_engine_has_no_target_execution_receipt",
            "mutation_id": task.get("mutation_id", ""),
        }

    @classmethod
    def has_confirmation_evidence(cls, result: dict[str, Any]) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get("simulation") is True:
            return False
        if str(result.get("execution_status", "")).lower() != "executed":
            return False
        evidence = result.get("evidence")
        if not isinstance(evidence, dict):
            return False
        if not all(evidence.get(field) for field in cls.REQUIRED_CONFIRMATION_FIELDS):
            return False
        response = evidence.get("response")
        assertion = evidence.get("assertion")
        return isinstance(response, dict) and isinstance(assertion, dict)

    def judge(self, result: dict[str, Any]) -> str:
        """Classify truthfulness; only receipt-backed execution can be confirmed."""
        if self.has_confirmation_evidence(result):
            return "CONFIRMED"
        if result.get("simulation") is True:
            return "SIMULATED"
        if str(result.get("execution_status", "")).lower() == "executed":
            return "INCONCLUSIVE"
        return "CANDIDATE"

    def run(self, request_name: str, token: str | None = None) -> dict[str, Any]:
        tenant = self.auth.verify(token)
        if not self.rate_limit(tenant):
            return {"error": "rate_limited"}

        started = time.time()
        cache_key = f"{tenant}:{request_name}"
        cached = self.redis.get(cache_key)
        if cached:
            return cached

        for mutation in self.mutate(request_name):
            self.queue.append(mutation)

        traces: list[dict[str, Any]] = []
        run_counts = {"confirmed": 0, "candidate": 0, "simulated": 0, "inconclusive": 0}
        while self.queue:
            task = self.queue.popleft()
            result = self.worker(task)
            verdict = self.judge(result)
            trace = {
                "input": task,
                "result": result,
                "verdict": verdict,
                "execution_status": result.get("execution_status", "not_executed"),
                "confirmation_status": "confirmed" if verdict == "CONFIRMED" else (
                    "simulated" if verdict == "SIMULATED" else "candidate"
                ),
                "evidence_level": result.get("evidence_level", "none"),
                "timestamp": time.time(),
                "tenant": tenant,
            }
            self.pg.insert("traces", trace)
            self.kafka.publish({
                "type": "trace",
                "tenant": tenant,
                "execution_status": trace["execution_status"],
                "confirmation_status": trace["confirmation_status"],
            })
            self.metrics["traces"] += 1
            if verdict == "CONFIRMED":
                self.metrics["bugs"] += 1
                self.metrics["confirmed_bugs"] += 1
                run_counts["confirmed"] += 1
            elif verdict == "SIMULATED":
                self.metrics["simulated_findings"] += 1
                run_counts["simulated"] += 1
            elif verdict == "INCONCLUSIVE":
                run_counts["inconclusive"] += 1
            else:
                self.metrics["candidate_findings"] += 1
                run_counts["candidate"] += 1

            graph = self.graph.setdefault(tenant, {"nodes": [], "edges": []})
            graph["nodes"].append(task.get("mutation_id", ""))
            graph["edges"].append((task.get("mutation_id", ""), verdict))
            traces.append(trace)

        self.metrics["requests"] += 1
        self.metrics["latency"] = time.time() - started
        result = {
            "version": self.version,
            "tenant": tenant,
            "trace_count": len(traces),
            "metrics": dict(self.metrics),
            "summary": run_counts,
            "execution_source": "memory_simulation",
            "graph": self.graph.get(tenant),
            "traces": traces,
        }
        self.redis.set(cache_key, result)
        self.logs.append({
            "event": "run",
            "tenant": tenant,
            "execution_source": "memory_simulation",
            "confirmed_count": run_counts["confirmed"],
        })
        return result

    def replay(self, request_name: str, token: str | None = None) -> dict[str, Any]:
        return self.run(request_name, token)
