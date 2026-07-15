"""Project Context Artifact cache with durable, non-blocking single-flight builds.

Reader calls are expensive and may be slow.  The cache makes project context a
versioned artifact: one builder compiles a source version while other loop
workers reuse the prior artifact or a structured degraded state.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ProjectContextArtifact:
    """Persisted Reader + local-parser output for one project source version."""

    artifact_id: str
    project_id: str
    artifact_status: str  # READY | REFRESHING | STALE | CONTEXT_PENDING | DEGRADED_CONTEXT | FAILED
    source_hashes: dict[str, str] = field(default_factory=dict)
    reader_contract_version: str = "v1"
    parser_version: str = "v1"
    reader_model_profile: str = ""
    generated_at: str = ""
    last_success_at: str = ""
    entities: list[dict] = field(default_factory=list)
    apis: list[dict] = field(default_factory=list)
    observers: list[dict] = field(default_factory=list)
    bindings: list[dict] = field(default_factory=list)
    candidate_lifecycles: list[dict] = field(default_factory=list)
    candidate_invariants: list[dict] = field(default_factory=list)
    evidence_snippets: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    gaps: list[dict] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    degradation_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectContextArtifact":
        defaults = cls(
            artifact_id="",
            project_id="",
            artifact_status="DEGRADED_CONTEXT",
        ).to_dict()
        defaults.update({key: value for key, value in (data or {}).items() if key in defaults})
        return cls(**defaults)


@dataclass
class _InFlightBuild:
    event: threading.Event
    owner_id: str
    started_at: float
    result: ProjectContextArtifact | None = None
    error: str = ""


class ArtifactCache:
    """Filesystem-backed artifact cache with builder/waiter single-flight semantics.

    A local in-process event avoids duplicate Reader API requests inside one
    worker.  A short-lived filesystem lease prevents duplicate builds between
    independently launched workers.  A stale artifact is always preferred to
    blocking a Discovery loop on an external Reader provider.
    """

    READER_CONTRACT_VERSION = "v2"
    PARSER_VERSION = "v2"
    DEFAULT_LEASE_TTL_SECONDS = 180.0

    def __init__(self, cache_dir: str | Path = "platform_outputs/artifact_cache") -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._in_flight: dict[str, _InFlightBuild] = {}
        self._process_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        # Defined later in this module; lookup happens when an instance is made.
        self.metrics = ReaderMetrics()

    def get_or_build(
        self,
        project_id: str,
        prd_text: str,
        api_spec_text: str,
        reader_fn: Callable[[str, str], dict[str, Any]],
        *,
        page_schema: str = "",
        project_config: dict[str, Any] | None = None,
        background_refresh: bool = False,
        wait_timeout_seconds: float = 120.0,
    ) -> ProjectContextArtifact:
        """Return a reusable artifact without duplicate Reader calls.

        ``background_refresh=True`` decouples Reader latency from the caller:
        stale context is returned immediately and the single builder refreshes
        it in the background.  With no prior artifact the caller receives
        ``CONTEXT_PENDING`` instead of blocking or crashing.
        """
        cache_key = self._compute_cache_key(
            project_id, prd_text, api_spec_text, page_schema, project_config
        )
        artifact_path = self._artifact_path(cache_key)
        cached = self._read_artifact(artifact_path)
        if cached is not None:
            self.metrics.cache_hits += 1
            return cached
        self.metrics.cache_misses += 1
        stale = self.get_stale_artifact(project_id, cache_key)

        with self._lock:
            flight = self._in_flight.get(cache_key)
            if flight is None:
                flight = _InFlightBuild(
                    event=threading.Event(),
                    owner_id=f"{self._process_id}:{uuid.uuid4().hex[:10]}",
                    started_at=time.time(),
                )
                self._in_flight[cache_key] = flight
                is_builder = True
            else:
                is_builder = False
                self.metrics.singleflight_waits += 1

        if is_builder:
            if background_refresh:
                thread = threading.Thread(
                    target=self._run_builder,
                    args=(cache_key, artifact_path, flight, project_id, prd_text, api_spec_text,
                          reader_fn, page_schema, project_config, stale),
                    name=f"qualibug-reader-{cache_key[:8]}",
                    daemon=True,
                )
                thread.start()
                if stale is not None:
                    return self._mark_stale(stale, "Reader refresh running in background")
                return self._pending_artifact(project_id, cache_key)
            return self._run_builder(
                cache_key, artifact_path, flight, project_id, prd_text, api_spec_text,
                reader_fn, page_schema, project_config, stale,
            )

        # Waiters never invoke reader_fn.  Background callers return immediately
        # so a slow Reader cannot become a Discovery critical-path dependency.
        if background_refresh:
            if stale is not None:
                return self._mark_stale(stale, "Reader refresh already running")
            return self._pending_artifact(project_id, cache_key)

        flight.event.wait(timeout=max(0.0, float(wait_timeout_seconds)))
        if flight.result is not None:
            return flight.result
        cached = self._read_artifact(artifact_path)
        if cached is not None:
            return cached
        if stale is not None:
            return self._mark_stale(stale, "Reader builder timed out; using prior artifact")
        return self._degraded_artifact(project_id, cache_key, "Reader builder timed out; no artifact available")

    def get_stale_artifact(self, project_id: str, cache_key: str | None = None) -> ProjectContextArtifact | None:
        """Return the newest artifact for the project when source versions differ."""
        for file_path in sorted(self._dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            artifact = self._read_artifact(file_path)
            if artifact is not None and artifact.project_id == project_id:
                return self._mark_stale(artifact, "Source version changed or Reader refresh unavailable")
        return None

    def invalidate(self, cache_key: str) -> None:
        self._artifact_path(cache_key).unlink(missing_ok=True)

    def _run_builder(
        self,
        cache_key: str,
        artifact_path: Path,
        flight: _InFlightBuild,
        project_id: str,
        prd_text: str,
        api_spec_text: str,
        reader_fn: Callable[[str, str], dict[str, Any]],
        page_schema: str,
        project_config: dict[str, Any] | None,
        stale: ProjectContextArtifact | None,
    ) -> ProjectContextArtifact:
        """Execute the sole builder, publish atomically, and wake all waiters."""
        started = time.time()
        lease_acquired = False
        try:
            lease_acquired = self._acquire_file_lease(cache_key, flight.owner_id)
            if not lease_acquired:
                # Another process owns the build.  Never duplicate it; preserve
                # availability with stale context or a pending result.
                result = (
                    self._mark_stale(stale, "Reader build owned by another worker")
                    if stale is not None
                    else self._pending_artifact(project_id, cache_key)
                )
            else:
                self.metrics.api_calls += 1
                built = self._build(
                    project_id, prd_text, api_spec_text, reader_fn,
                    page_schema=page_schema, project_config=project_config,
                )
                self.metrics.total_duration_seconds += max(0.0, time.time() - started)
                if built.artifact_status == "READY":
                    self._atomic_write_artifact(artifact_path, built)
                    self.metrics.refresh_successes += 1
                    result = built
                else:
                    self.metrics.refresh_failures += 1
                    result = (
                        self._mark_stale(stale, built.degradation_reason or "Reader refresh failed")
                        if stale is not None
                        else self._degraded_artifact(
                            project_id, cache_key,
                            built.degradation_reason or "Reader failed with no reusable artifact",
                        )
                    )
        except Exception as exc:
            self.metrics.refresh_failures += 1
            result = (
                self._mark_stale(stale, f"Reader builder exception: {str(exc)[:200]}")
                if stale is not None
                else self._degraded_artifact(project_id, cache_key, f"Reader builder exception: {str(exc)[:200]}")
            )
        finally:
            if lease_acquired:
                self._release_file_lease(cache_key, flight.owner_id)
            with self._lock:
                flight.result = result
                flight.event.set()
                self._in_flight.pop(cache_key, None)
        return result

    def _artifact_path(self, cache_key: str) -> Path:
        return self._dir / f"{cache_key}.json"

    def _lease_path(self, cache_key: str) -> Path:
        return self._dir / f"{cache_key}.reader.lease.json"

    def _read_artifact(self, path: Path) -> ProjectContextArtifact | None:
        if not path.exists():
            return None
        try:
            artifact = ProjectContextArtifact.from_dict(json.loads(path.read_text(encoding="utf-8")))
            return artifact if artifact.artifact_status == "READY" else None
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    def _atomic_write_artifact(self, path: Path, artifact: ProjectContextArtifact) -> None:
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _acquire_file_lease(self, cache_key: str, owner_id: str) -> bool:
        path = self._lease_path(cache_key)
        now = time.time()
        payload = {
            "owner_id": owner_id,
            "pid": os.getpid(),
            "acquired_at": now,
            "expires_at": now + self.DEFAULT_LEASE_TTL_SECONDS,
        }
        for _ in range(2):
            try:
                descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                return True
            except FileExistsError:
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                    if float(current.get("expires_at", 0.0)) <= now:
                        path.unlink(missing_ok=True)
                        continue
                except Exception:
                    # A corrupt lease is safe to reclaim because it has no
                    # trustworthy owner/expiry metadata.
                    try:
                        path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                return False
        return False

    def _release_file_lease(self, cache_key: str, owner_id: str) -> None:
        path = self._lease_path(cache_key)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("owner_id") == owner_id:
                path.unlink(missing_ok=True)
        except Exception:
            # Do not remove a lease we cannot prove is ours.
            return

    def _compute_cache_key(
        self,
        project_id: str,
        prd_text: str,
        api_spec_text: str,
        page_schema: str = "",
        project_config: dict[str, Any] | None = None,
    ) -> str:
        config = project_config or {}
        reader_profile = str(config.get("reader_model_profile") or config.get("reader_profile") or "default")
        parts = [
            project_id,
            hashlib.sha256(prd_text.encode("utf-8")).hexdigest()[:16],
            hashlib.sha256(api_spec_text.encode("utf-8")).hexdigest()[:16],
            hashlib.sha256(page_schema.encode("utf-8")).hexdigest()[:16] if page_schema else "no-page",
            hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16],
            self.READER_CONTRACT_VERSION,
            self.PARSER_VERSION,
            reader_profile,
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]

    def _pending_artifact(self, project_id: str, cache_key: str) -> ProjectContextArtifact:
        self.metrics.degraded_uses += 1
        return ProjectContextArtifact(
            artifact_id=f"ctx-{cache_key[:12]}",
            project_id=project_id,
            artifact_status="CONTEXT_PENDING",
            reader_contract_version=self.READER_CONTRACT_VERSION,
            parser_version=self.PARSER_VERSION,
            degradation_reason="Reader compilation started in background; no prior artifact available",
        )

    def _degraded_artifact(self, project_id: str, cache_key: str, reason: str) -> ProjectContextArtifact:
        self.metrics.degraded_uses += 1
        return ProjectContextArtifact(
            artifact_id=f"ctx-{cache_key[:12]}",
            project_id=project_id,
            artifact_status="DEGRADED_CONTEXT",
            reader_contract_version=self.READER_CONTRACT_VERSION,
            parser_version=self.PARSER_VERSION,
            degradation_reason=reason[:500],
        )

    def _mark_stale(self, artifact: ProjectContextArtifact, reason: str) -> ProjectContextArtifact:
        stale = ProjectContextArtifact.from_dict(artifact.to_dict())
        stale.artifact_status = "STALE"
        stale.degradation_reason = reason[:500]
        self.metrics.stale_uses += 1
        return stale

    def _build(
        self,
        project_id: str,
        prd_text: str,
        api_spec_text: str,
        reader_fn: Callable[[str, str], dict[str, Any]],
        *,
        page_schema: str = "",
        project_config: dict[str, Any] | None = None,
    ) -> ProjectContextArtifact:
        """Call Reader once and normalize all reusable context fields."""
        artifact_id = f"ctx-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        source_hashes = {
            "prd": hashlib.sha256(prd_text.encode("utf-8")).hexdigest()[:16],
            "openapi": hashlib.sha256(api_spec_text.encode("utf-8")).hexdigest()[:16],
            "page_schema": hashlib.sha256(page_schema.encode("utf-8")).hexdigest()[:16] if page_schema else "",
            "project_config": hashlib.sha256(json.dumps(project_config or {}, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16],
        }
        try:
            world = reader_fn(prd_text, api_spec_text) or {}
            if not isinstance(world, dict):
                raise ValueError(f"Reader returned {type(world).__name__}, expected object")
            if world.get("error"):
                return ProjectContextArtifact(
                    artifact_id=artifact_id, project_id=project_id, artifact_status="FAILED",
                    source_hashes=source_hashes,
                    reader_contract_version=self.READER_CONTRACT_VERSION,
                    parser_version=self.PARSER_VERSION,
                    degradation_reason=str(world.get("error"))[:500],
                )
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            documented_rules = list(world.get("documented_rules") or world.get("rules") or [])
            return ProjectContextArtifact(
                artifact_id=artifact_id,
                project_id=project_id,
                artifact_status="READY",
                source_hashes=source_hashes,
                reader_contract_version=self.READER_CONTRACT_VERSION,
                parser_version=self.PARSER_VERSION,
                reader_model_profile=str((project_config or {}).get("reader_model_profile") or ""),
                generated_at=timestamp,
                last_success_at=timestamp,
                entities=list(world.get("entities") or []),
                apis=list(world.get("apis") or world.get("operations") or []),
                observers=list(world.get("observers") or []),
                bindings=list(world.get("bindings") or []),
                candidate_lifecycles=list(world.get("candidate_lifecycles") or []),
                candidate_invariants=list(world.get("candidate_invariants") or []),
                evidence_snippets=list(world.get("evidence_snippets") or []),
                coverage={
                    **dict(world.get("coverage") or {}),
                    "entity_count": len(world.get("entities") or []),
                    "api_count": len(world.get("apis") or world.get("operations") or []),
                    "documented_rules": documented_rules,
                    "rule_count": len(documented_rules),
                },
                gaps=list(world.get("gaps") or []),
                source_refs=list(world.get("source_refs") or []),
            )
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                self.metrics.timeouts += 1
            elif isinstance(exc, ConnectionError):
                self.metrics.connection_errors += 1
            return ProjectContextArtifact(
                artifact_id=artifact_id,
                project_id=project_id,
                artifact_status="FAILED",
                source_hashes=source_hashes,
                reader_contract_version=self.READER_CONTRACT_VERSION,
                parser_version=self.PARSER_VERSION,
                degradation_reason=str(exc)[:500],
            )
# Evidence Pack Builder
# ═══════════════════════════════════════════════════════════════

class EvidencePackBuilder:
    """Builds engine-specific small context packs from artifact.

    Each Reasoner engine gets ≤ 1500 chars of relevant context,
    not the full 8000-char Reader output.
    """

    MAX_CHARS = 1500

    ENGINE_PRIORITIES = {
        "causality": ["entity", "lifecycle", "relation", "api"],
        "invariant": ["entity", "amount", "quantity", "rule"],
        "reconciliation": ["api", "observer", "entity", "field"],
        "counterexample": ["entity", "api", "gap", "rule"],
        "consistency": ["entity", "observer", "field", "api"],
        "population": ["entity", "api", "coverage"],
        "outcome": ["entity", "lifecycle", "api", "rule"],
        "temporal": ["entity", "lifecycle", "api", "version"],
        "saga": ["entity", "relation", "lifecycle", "api"],
        "event_chain": ["entity", "relation", "api", "lifecycle"],
        "metamorphic": ["api", "entity", "observer", "field"],
    }

    def build(self, artifact: ProjectContextArtifact, engine_name: str) -> str:
        """Build an evidence pack for a specific Reasoner engine."""
        priorities = self.ENGINE_PRIORITIES.get(engine_name, ["entity", "api"])

        sections = []

        # Entity section
        entities = artifact.entities[:10]
        if entities:
            entity_lines = []
            for e in entities:
                name = e.get("name", e.get("entity_alias", "?"))
                state_fields = e.get("state_fields", e.get("documented_rules", []))[:3]
                entity_lines.append(f"  - {name}" + (f" (states: {state_fields})" if state_fields else ""))
            sections.append(("entity", "Entities:\n" + "\n".join(entity_lines)))

        # API section
        apis = artifact.apis[:10]
        if apis:
            api_lines = [f"  {a.get('method','?')} {a.get('path','?')} → {a.get('capability','?')}" for a in apis if isinstance(a, dict)]
            if api_lines:
                sections.append(("api", "APIs:\n" + "\n".join(api_lines)))

        # Lifecycle section
        lifecycles = artifact.candidate_lifecycles[:3]
        if lifecycles:
            sections.append(("lifecycle", "Lifecycles: " + "; ".join(str(lc)[:80] for lc in lifecycles)))

        # Sort by priority and build
        priority_order = {p: i for i, p in enumerate(priorities)}
        sections.sort(key=lambda s: priority_order.get(s[0], 99))

        pack = ""
        for _, text in sections:
            if len(pack) + len(text) + 2 <= self.MAX_CHARS:
                pack += text + "\n"
            else:
                remaining = self.MAX_CHARS - len(pack) - 5
                if remaining > 50:
                    pack += text[:remaining] + "..."
                break

        if not pack:
            pack = f"[INSUFFICIENT_CONTEXT] No entities or APIs found for engine={engine_name}"

        return pack.strip()


# ═══════════════════════════════════════════════════════════════
# Global singleton
# ═══════════════════════════════════════════════════════════════

_cache: ArtifactCache | None = None
_builder: EvidencePackBuilder | None = None


def get_artifact_cache() -> ArtifactCache:
    global _cache
    if _cache is None:
        _cache = ArtifactCache()
    return _cache


def get_evidence_pack_builder() -> EvidencePackBuilder:
    global _builder
    if _builder is None:
        _builder = EvidencePackBuilder()
    return _builder


# ═══════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════

@dataclass
class ReaderMetrics:
    cache_hits: int = 0
    cache_misses: int = 0
    api_calls: int = 0
    singleflight_waits: int = 0
    timeouts: int = 0
    empty_responses: int = 0
    connection_errors: int = 0
    refresh_successes: int = 0
    refresh_failures: int = 0
    stale_uses: int = 0
    degraded_uses: int = 0
    total_duration_seconds: float = 0.0
    evidence_pack_avg_chars: float = 0.0
