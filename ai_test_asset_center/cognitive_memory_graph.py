"""Phase91 Cognitive Memory Graph & Risk Frontier.

This module is the typed, private, auditable memory layer used by QualiBug
Discovery.  It deliberately does *not* depend on Obsidian or Markdown: SQLite
is the system of record.  Markdown is a later, read-only export projection.

Design invariants
-----------------
* Every record is partitioned by project_id and environment_id.
* Facts carry source refs, confidence and approval status.
* Inferred/disputed facts are excluded from high-risk execution context.
* Graph context is local (bounded BFS) and traceable to graph ids.
* Frontier priority is explainable and safety/cleanup aware.
* Human input is proposal-only until explicitly approved.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


NODE_TYPES = frozenset({
    "Project", "Environment", "SourceDocument", "BusinessFact", "Entity", "Field", "API",
    "Role", "Permission", "TenantBoundary", "State", "StateTransition", "Invariant", "Flow",
    "Observer", "Event", "Evidence", "Hypothesis", "Finding", "RegressionGuard", "CoverageGap",
    "Policy", "PolicyEvaluation", "Decision", "HumanFactProposal", "CleanupRecord", "ChangeSet",
})

EDGE_TYPES = frozenset({
    "reads", "mutates", "requires", "can_execute", "has_field", "has_state", "transitions_to",
    "constrains", "validated_by", "uses", "produces", "observes", "violates", "targets",
    "has_evidence", "has_disproof", "guarded_by", "belongs_to", "prioritizes", "impacts",
    "proposes_change_to", "cleans", "related_to", "derived_from", "refutes", "covers",
})

CONFIDENCE = frozenset({"confirmed", "evidenced", "inferred", "disputed", "rejected"})
APPROVAL = frozenset({"system_generated", "pending_review", "approved", "rejected"})
FRONTIER_STATES = frozenset({
    "UNSEEN", "FACT_INCOMPLETE", "EVIDENCE_INCOMPLETE", "READY_FOR_DISCOVERY", "IN_PROGRESS",
    "VALIDATED", "REJECTED_WITH_EVIDENCE", "BLOCKED_BY_BINDING", "BLOCKED_BY_FIXTURE",
    "BLOCKED_BY_SAFETY", "BLOCKED_BY_CLEANUP", "DEPRIORITIZED_AS_DUPLICATE",
})
SENSITIVE_RE = re.compile(r"(token|password|secret|authorization|cookie|api[_-]?key|credential|private[_-]?key)", re.I)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _graph_context_mode() -> str:
    """Resolve the graph context mode from either the canonical env name or the
    historical operator-facing name.

    ``active`` is operator-declared (an explicit, receipted deployment choice),
    never an automatic promotion from the A/B evaluator. The default remains
    ``shadow``: measured but non-authoritative.
    """
    return (
        os.environ.get("QUALIBUG_GRAPH_CONTEXT_MODE")
        or os.environ.get("GRAPH_CONTEXT_MODE")
        or "shadow"
    ).strip().lower() or "shadow"


def _norm(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _safe_id(value: Any, fallback: str = "default") -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "").strip())[:120]
    return clean or fallback


def _digest(*parts: Any, width: int = 24) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:width]


def _redact(value: Any) -> Any:
    """Return a safe metadata representation; never persist credentials/raw payloads."""
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if SENSITIVE_RE.search(str(k)) else _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, tuple):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        # keep metadata sized; content is source-referenced elsewhere
        return value[:4000]
    return value


def _json(value: Any) -> str:
    return json.dumps(_redact(value or {}), ensure_ascii=False, sort_keys=True, default=str)


def _load_json(value: str | None) -> dict[str, Any]:
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


@dataclass(frozen=True)
class GraphRef:
    node_id: str
    node_type: str
    project_id: str
    environment_id: str


class CognitiveMemoryGraph:
    """SQLite typed graph. It is private-deployment local and source-of-truth."""

    SCHEMA_VERSION = "phase91-v1"

    def __init__(
        self,
        project_id: str = "real_project_demo",
        environment_id: str = "test",
        root: str | Path | None = None,
    ) -> None:
        self.project_id = _safe_id(project_id, "project")
        self.environment_id = _safe_id(environment_id, "test")
        self.root = Path(root or ".")
        self.path = self.root / "platform_workspace" / self.project_id / "cognitive_memory_graph.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    node_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    node_key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    policy_version TEXT NOT NULL DEFAULT '',
                    UNIQUE(project_id, environment_id, node_type, node_key)
                );
                CREATE INDEX IF NOT EXISTS idx_cmg_nodes_scope ON graph_nodes(project_id, environment_id, node_type);
                CREATE TABLE IF NOT EXISTS graph_edges (
                    edge_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    from_node_id TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    policy_version TEXT NOT NULL DEFAULT '',
                    UNIQUE(project_id, environment_id, from_node_id, to_node_id, edge_type)
                );
                CREATE INDEX IF NOT EXISTS idx_cmg_edges_scope ON graph_edges(project_id, environment_id, from_node_id, to_node_id);
                CREATE TABLE IF NOT EXISTS human_fact_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    author TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    proposed_change_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    reviewer TEXT NOT NULL DEFAULT '',
                    decision_time TEXT NOT NULL DEFAULT '',
                    impact_analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS graph_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            con.commit()
        self.upsert_node("Project", self.project_id, self.project_id, source="system", source_ref="project")
        self.upsert_node("Environment", self.environment_id, self.environment_id, source="system", source_ref="environment")

    # ---- basic storage -------------------------------------------------
    def upsert_node(
        self,
        node_type: str,
        node_key: str,
        label: str,
        *,
        source: str,
        source_ref: str,
        confidence: str = "evidenced",
        approval_status: str = "system_generated",
        payload: dict[str, Any] | None = None,
        evidence_refs: Iterable[str] | None = None,
        run_id: str = "",
        policy_version: str = "",
        preserve_confirmed: bool = True,
    ) -> GraphRef:
        if node_type not in NODE_TYPES:
            raise ValueError(f"unsupported graph node type: {node_type}")
        if confidence not in CONFIDENCE:
            raise ValueError(f"unsupported confidence: {confidence}")
        if approval_status not in APPROVAL:
            raise ValueError(f"unsupported approval status: {approval_status}")
        node_key = _safe_id(node_key, _digest(node_type, label))
        now = _now()
        node_id = f"CMG_{node_type[:4].upper()}_{_digest(self.project_id, self.environment_id, node_type, node_key)}"
        safe_refs = sorted({str(v)[:500] for v in (evidence_refs or []) if str(v).strip()})
        with closing(self._connect()) as con:
            existing = con.execute("SELECT confidence, approval_status, payload_json, evidence_refs_json, created_at FROM graph_nodes WHERE node_id = ?", (node_id,)).fetchone()
            if existing and preserve_confirmed and str(existing["confidence"]) == "confirmed" and confidence in {"inferred", "disputed"}:
                # A weak LLM inference cannot overwrite an approved fact. Store conflict separately.
                conflict_key = f"conflict:{node_key}:{_digest(payload or {}, source_ref)}"
                con.execute("""INSERT OR REPLACE INTO graph_nodes
                    (node_id,project_id,environment_id,node_type,node_key,label,source,source_ref,confidence,approval_status,payload_json,evidence_refs_json,created_at,updated_at,valid_from,valid_to,run_id,policy_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"CMG_FACT_{_digest(self.project_id,self.environment_id,conflict_key)}", self.project_id, self.environment_id,
                     "BusinessFact", conflict_key, f"Disputed: {label}"[:500], source, source_ref, "disputed", "pending_review",
                     _json({"conflicts_with": node_id, "proposed_payload": payload or {}}), _json(safe_refs), now, now, now, "", run_id, policy_version))
                con.commit()
                return GraphRef(node_id, node_type, self.project_id, self.environment_id)
            created_at = str(existing["created_at"]) if existing else now
            merged_payload = _load_json(existing["payload_json"]) if existing else {}
            merged_payload.update(_redact(payload or {}))
            merged_refs = set(_load_json(existing["evidence_refs_json"]).get("refs", [])) if existing else set()
            merged_refs.update(safe_refs)
            # Proven facts keep their stronger status on like-for-like updates.
            if existing and str(existing["confidence"]) in {"confirmed", "evidenced"} and confidence == "inferred":
                confidence = str(existing["confidence"])
            con.execute("""INSERT INTO graph_nodes
                (node_id,project_id,environment_id,node_type,node_key,label,source,source_ref,confidence,approval_status,payload_json,evidence_refs_json,created_at,updated_at,valid_from,valid_to,run_id,policy_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                  label=excluded.label, source=excluded.source, source_ref=excluded.source_ref, confidence=excluded.confidence,
                  approval_status=excluded.approval_status, payload_json=excluded.payload_json, evidence_refs_json=excluded.evidence_refs_json,
                  updated_at=excluded.updated_at, run_id=excluded.run_id, policy_version=excluded.policy_version
            """, (node_id, self.project_id, self.environment_id, node_type, node_key, _norm(label, 500), _norm(source, 160), _norm(source_ref, 500),
                  confidence, approval_status, _json(merged_payload), _json({"refs": sorted(merged_refs)}), created_at, now, created_at, "", run_id, policy_version))
            con.commit()
        return GraphRef(node_id, node_type, self.project_id, self.environment_id)

    def add_edge(
        self,
        from_node_id: str,
        to_node_id: str,
        edge_type: str,
        *,
        source: str,
        source_ref: str,
        confidence: str = "evidenced",
        approval_status: str = "system_generated",
        payload: dict[str, Any] | None = None,
        evidence_refs: Iterable[str] | None = None,
        run_id: str = "",
        policy_version: str = "",
    ) -> str:
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"unsupported graph edge type: {edge_type}")
        if confidence not in CONFIDENCE or approval_status not in APPROVAL:
            raise ValueError("invalid confidence or approval status")
        edge_id = f"CME_{_digest(self.project_id, self.environment_id, from_node_id, to_node_id, edge_type)}"
        now = _now()
        refs = sorted({str(v)[:500] for v in (evidence_refs or []) if str(v).strip()})
        with closing(self._connect()) as con:
            # Explicit scope join prevents cross-project/environment edges.
            scope = con.execute("SELECT project_id, environment_id FROM graph_nodes WHERE node_id IN (?,?)", (from_node_id, to_node_id)).fetchall()
            if len(scope) != 2 or any(row["project_id"] != self.project_id or row["environment_id"] != self.environment_id for row in scope):
                raise ValueError("cross-project or cross-environment graph edge rejected")
            con.execute("""INSERT INTO graph_edges
                (edge_id,project_id,environment_id,from_node_id,to_node_id,edge_type,source,source_ref,confidence,approval_status,payload_json,evidence_refs_json,created_at,updated_at,valid_from,valid_to,run_id,policy_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(edge_id) DO UPDATE SET payload_json=excluded.payload_json, evidence_refs_json=excluded.evidence_refs_json, updated_at=excluded.updated_at, confidence=excluded.confidence, approval_status=excluded.approval_status
            """, (edge_id, self.project_id, self.environment_id, from_node_id, to_node_id, edge_type, _norm(source,160), _norm(source_ref,500), confidence, approval_status,
                  _json(payload or {}), _json({"refs": refs}), now, now, now, "", run_id, policy_version))
            con.commit()
        return edge_id

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM graph_nodes WHERE node_id=? AND project_id=? AND environment_id=?", (node_id, self.project_id, self.environment_id)).fetchone()
        return self._row_node(row) if row else None

    def nodes(self, *, node_types: Iterable[str] | None = None, include_unapproved: bool = True) -> list[dict[str, Any]]:
        clauses = ["project_id=?", "environment_id=?"]
        args: list[Any] = [self.project_id, self.environment_id]
        types = [str(v) for v in (node_types or []) if str(v)]
        if types:
            clauses.append("node_type IN (%s)" % ",".join("?" for _ in types))
            args.extend(types)
        if not include_unapproved:
            clauses.append("approval_status IN ('system_generated','approved')")
        with closing(self._connect()) as con:
            rows = con.execute(f"SELECT * FROM graph_nodes WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC, node_id", args).fetchall()
        return [self._row_node(row) for row in rows]

    def edges(self, node_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        clauses = ["project_id=?", "environment_id=?"]
        args: list[Any] = [self.project_id, self.environment_id]
        ids = sorted({str(v) for v in (node_ids or []) if str(v)})
        if ids:
            q = ",".join("?" for _ in ids)
            clauses.append(f"(from_node_id IN ({q}) OR to_node_id IN ({q}))")
            args.extend(ids + ids)
        with closing(self._connect()) as con:
            rows = con.execute(f"SELECT * FROM graph_edges WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC, edge_id", args).fetchall()
        return [self._row_edge(row) for row in rows]

    @staticmethod
    def _row_node(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = _load_json(value.pop("payload_json", "{}"))
        value["evidence_refs"] = _load_json(value.pop("evidence_refs_json", "{}")).get("refs", [])
        return value

    @staticmethod
    def _row_edge(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = _load_json(value.pop("payload_json", "{}"))
        value["evidence_refs"] = _load_json(value.pop("evidence_refs_json", "{}")).get("refs", [])
        return value

    def local_neighborhood(self, seed_ids: Iterable[str], *, hops: int = 2, allowed_confidence: set[str] | None = None, limit: int = 80) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """BFS neighborhood constrained to this project/environment only."""
        allowed_confidence = allowed_confidence or {"confirmed", "evidenced", "inferred", "disputed"}
        seeds = {str(v) for v in seed_ids if str(v)}
        if not seeds:
            return [], []
        seen = set(seeds)
        frontier = deque((node_id, 0) for node_id in seeds)
        while frontier and len(seen) < limit:
            node_id, depth = frontier.popleft()
            if depth >= max(0, int(hops)):
                continue
            for edge in self.edges([node_id]):
                if edge["confidence"] not in allowed_confidence:
                    continue
                other = edge["to_node_id"] if edge["from_node_id"] == node_id else edge["from_node_id"]
                if other not in seen:
                    seen.add(other)
                    frontier.append((other, depth + 1))
                    if len(seen) >= limit:
                        break
        selected = [node for node in self.nodes() if node["node_id"] in seen and node["confidence"] in allowed_confidence]
        selected_ids = {node["node_id"] for node in selected}
        selected_edges = [edge for edge in self.edges(selected_ids) if edge["from_node_id"] in selected_ids and edge["to_node_id"] in selected_ids]
        return selected, selected_edges

    # ---- context synchronization -------------------------------------
    def sync_context(
        self,
        project_context: dict[str, Any] | None,
        artifact: Any | None = None,
        *,
        prd_source_ref: str = "prd",
        api_source_ref: str = "openapi",
        run_id: str = "",
        policy_version: str = "",
    ) -> dict[str, Any]:
        """Turn local compiler + Reader artifact output into source-traceable facts."""
        project_context = project_context or {}
        entities = list(project_context.get("entities") or [])
        apis = list(project_context.get("apis") or [])
        invariants = list(project_context.get("candidate_invariants") or project_context.get("invariants") or [])
        transitions = list(project_context.get("candidate_lifecycle_transitions") or project_context.get("transitions") or [])
        observers = list(project_context.get("observers") or [])
        if artifact is not None:
            entities = list(getattr(artifact, "entities", None) or entities)
            apis = list(getattr(artifact, "apis", None) or apis)
            invariants = list(getattr(artifact, "candidate_invariants", None) or invariants)
            transitions = list(getattr(artifact, "candidate_lifecycles", None) or transitions)
            observers = list(getattr(artifact, "observers", None) or observers)
            artifact_ref = getattr(artifact, "artifact_id", "")
        else:
            artifact_ref = ""
        prd_doc = self.upsert_node("SourceDocument", f"prd:{prd_source_ref}", "Project requirements", source="project_context", source_ref=prd_source_ref, confidence="evidenced", payload={"kind": "PRD"}, run_id=run_id, policy_version=policy_version)
        api_doc = self.upsert_node("SourceDocument", f"api:{api_source_ref}", "API contract", source="project_context", source_ref=api_source_ref, confidence="evidenced", payload={"kind": "OpenAPI"}, run_id=run_id, policy_version=policy_version)
        if artifact_ref:
            self.upsert_node("SourceDocument", f"artifact:{artifact_ref}", "Reader context artifact", source="reader_artifact", source_ref=artifact_ref, confidence="evidenced", payload={"artifact_id": artifact_ref}, run_id=run_id, policy_version=policy_version)
        entity_refs: dict[str, GraphRef] = {}
        api_refs: dict[str, GraphRef] = {}
        for raw in entities:
            if not isinstance(raw, dict):
                continue
            name = _norm(raw.get("entity_alias") or raw.get("name") or raw.get("entity") or raw.get("title"), 160)
            if not name:
                continue
            conf = "evidenced" if float(raw.get("confidence") or 0.0) >= 0.7 else "inferred"
            ref = self.upsert_node("Entity", name.lower(), name, source="reader_artifact" if artifact_ref else "project_context", source_ref=artifact_ref or prd_source_ref, confidence=conf, payload=raw, evidence_refs=[artifact_ref or prd_source_ref], run_id=run_id, policy_version=policy_version)
            entity_refs[name.lower()] = ref
            self.add_edge(prd_doc.node_id, ref.node_id, "derived_from", source="context_sync", source_ref=prd_source_ref, confidence=conf, run_id=run_id, policy_version=policy_version)
            for field_name in list(raw.get("state_fields") or []) + list(raw.get("amount_fields") or []) + list(raw.get("quantity_fields") or []):
                field = self.upsert_node("Field", f"{name}:{field_name}", f"{name}.{field_name}", source="project_context", source_ref=prd_source_ref, confidence=conf, payload={"entity": name, "field": field_name}, run_id=run_id, policy_version=policy_version)
                self.add_edge(ref.node_id, field.node_id, "has_field", source="context_sync", source_ref=prd_source_ref, confidence=conf, run_id=run_id, policy_version=policy_version)
                if str(field_name).lower() in {"status", "state", "lifecycle", "phase", "stage"}:
                    state = self.upsert_node("State", f"{name}:{field_name}", f"{name} state via {field_name}", source="project_context", source_ref=prd_source_ref, confidence=conf, payload={"entity": name, "field": field_name}, run_id=run_id, policy_version=policy_version)
                    self.add_edge(ref.node_id, state.node_id, "has_state", source="context_sync", source_ref=prd_source_ref, confidence=conf, run_id=run_id, policy_version=policy_version)
        for raw in apis:
            if not isinstance(raw, dict):
                continue
            method = str(raw.get("method") or raw.get("http_method") or "GET").upper()
            path = _norm(raw.get("path") or raw.get("endpoint") or raw.get("route"), 300)
            if not path:
                continue
            key = f"{method}:{path}"
            capability = str(raw.get("capability") or raw.get("operation_type") or "unknown")
            ref = self.upsert_node("API", key, key, source="reader_artifact" if artifact_ref else "project_context", source_ref=artifact_ref or api_source_ref, confidence="evidenced" if raw.get("confidence", 1) else "inferred", payload={**raw, "method": method, "path": path, "capability": capability}, evidence_refs=[artifact_ref or api_source_ref], run_id=run_id, policy_version=policy_version)
            api_refs[key] = ref
            self.add_edge(api_doc.node_id, ref.node_id, "derived_from", source="context_sync", source_ref=api_source_ref, run_id=run_id, policy_version=policy_version)
            target_entity = _norm(raw.get("entity_alias") or raw.get("entity") or raw.get("resource"), 160).lower()
            if target_entity and target_entity in entity_refs:
                relation = "reads" if method in {"GET", "HEAD", "OPTIONS"} else "mutates"
                self.add_edge(ref.node_id, entity_refs[target_entity].node_id, relation, source="context_sync", source_ref=api_source_ref, run_id=run_id, policy_version=policy_version)
        for raw in invariants:
            if isinstance(raw, str):
                raw = {"definition": raw}
            if not isinstance(raw, dict):
                continue
            definition = _norm(raw.get("definition") or raw.get("invariant") or raw.get("description") or raw.get("title"), 500)
            if not definition:
                continue
            entity_name = _norm(raw.get("entity") or raw.get("entity_alias") or raw.get("resource"), 160).lower()
            conf = "evidenced" if raw.get("evidence") or raw.get("source_ref") else "inferred"
            inv = self.upsert_node("Invariant", _digest(definition), definition, source="reader_artifact" if artifact_ref else "project_context", source_ref=str(raw.get("source_ref") or artifact_ref or prd_source_ref), confidence=conf, payload=raw, evidence_refs=[str(raw.get("source_ref") or artifact_ref or prd_source_ref)], run_id=run_id, policy_version=policy_version)
            if entity_name and entity_name in entity_refs:
                self.add_edge(inv.node_id, entity_refs[entity_name].node_id, "constrains", source="context_sync", source_ref=prd_source_ref, confidence=conf, run_id=run_id, policy_version=policy_version)
        for raw in transitions:
            if isinstance(raw, str):
                raw = {"definition": raw}
            if not isinstance(raw, dict):
                continue
            label = _norm(raw.get("definition") or raw.get("transition") or raw.get("description"), 500)
            if not label:
                continue
            ref = self.upsert_node("StateTransition", _digest(label), label, source="reader_artifact" if artifact_ref else "project_context", source_ref=str(raw.get("source_ref") or artifact_ref or prd_source_ref), confidence="inferred" if not raw.get("evidence") else "evidenced", payload=raw, run_id=run_id, policy_version=policy_version)
        for raw in observers:
            if not isinstance(raw, dict):
                continue
            entity = _norm(raw.get("entity_alias") or raw.get("entity") or "", 160).lower()
            label = _norm(raw.get("observer_id") or raw.get("path") or raw.get("method"), 300)
            if not label:
                continue
            obs = self.upsert_node("Observer", label, label, source="reader_artifact", source_ref=artifact_ref or api_source_ref, confidence="evidenced" if raw.get("read_only_confidence", 0) >= 0.7 else "inferred", payload=raw, run_id=run_id, policy_version=policy_version)
            if entity and entity in entity_refs:
                self.add_edge(obs.node_id, entity_refs[entity].node_id, "observes", source="context_sync", source_ref=artifact_ref or api_source_ref, run_id=run_id, policy_version=policy_version)
        self._seed_coverage_gaps(entity_refs, api_refs, run_id=run_id, policy_version=policy_version)
        return self.stats()

    def _seed_coverage_gaps(self, entity_refs: dict[str, GraphRef], api_refs: dict[str, GraphRef], *, run_id: str, policy_version: str) -> None:
        risk_types = ("idempotency", "authorization", "lifecycle", "consistency", "conservation", "async_consistency")
        for api_key, api_ref in api_refs.items():
            api = self.get_node(api_ref.node_id) or {}
            method = str((api.get("payload") or {}).get("method") or "GET").upper()
            if method in {"GET", "HEAD", "OPTIONS"}:
                candidates = ("authorization", "consistency")
            else:
                candidates = risk_types
            for risk_type in candidates:
                gap_key = f"{api_key}:{risk_type}"
                self.upsert_node("CoverageGap", gap_key, f"Unverified {risk_type}: {api_key}", source="graph_seed", source_ref=api_key, confidence="inferred", payload={"api": api_key, "risk_type": risk_type, "state": "UNSEEN", "business_impact": 0.7 if method not in {"GET", "HEAD", "OPTIONS"} else 0.45, "coverage_gap": 1.0, "execution_cost": 0.3, "duplicate_risk": 0.1}, run_id=run_id, policy_version=policy_version)

    def record_findings(self, findings: Iterable[Any], *, run_id: str = "", policy_version: str = "") -> dict[str, int]:
        result = {"findings": 0, "evidence": 0, "updated_gaps": 0}
        for raw in findings:
            if hasattr(raw, "__dict__") and not isinstance(raw, dict):
                raw = dict(raw.__dict__)
            if not isinstance(raw, dict):
                continue
            evidence = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}
            contract = evidence.get("business_finding") if isinstance(evidence.get("business_finding"), dict) else {}
            verdict = str(raw.get("verdict") or contract.get("verdict") or "NEEDS_MORE_EVIDENCE").upper()
            title = _norm(raw.get("title") or contract.get("title") or "Discovery finding", 500)
            hyp_id = _norm(raw.get("hypothesis_id") or contract.get("hypothesis_id") or _digest(title), 160)
            confidence = "evidenced" if verdict in {"VALIDATED_CANDIDATE", "CONFIRMED_BY_HUMAN"} else "disputed" if verdict in {"REJECTED", "SCHEMA_INVALID"} else "inferred"
            finding = self.upsert_node("Finding", hyp_id, title, source="discovery", source_ref=hyp_id, confidence=confidence, approval_status="approved" if verdict == "CONFIRMED_BY_HUMAN" else "system_generated", payload={"verdict": verdict, "severity": raw.get("severity"), "contract": contract}, evidence_refs=list(contract.get("evidence_refs") or []), run_id=run_id, policy_version=policy_version)
            result["findings"] += 1
            inv = contract.get("violated_invariant") if isinstance(contract.get("violated_invariant"), dict) else {}
            inv_def = _norm(inv.get("definition") or "", 500)
            if inv_def:
                inv_ref = self.upsert_node("Invariant", _digest(inv_def), inv_def, source="finding", source_ref=hyp_id, confidence="evidenced" if inv.get("evidence_ref") else "inferred", payload=inv, evidence_refs=[str(inv.get("evidence_ref") or "")], run_id=run_id, policy_version=policy_version)
                self.add_edge(finding.node_id, inv_ref.node_id, "violates", source="finding", source_ref=hyp_id, confidence=confidence, run_id=run_id, policy_version=policy_version)
            for ref in list(contract.get("evidence_refs") or [])[:20]:
                evidence_ref = self.upsert_node("Evidence", _digest(hyp_id, ref), f"Evidence for {title}", source="finding", source_ref=str(ref), confidence="evidenced", payload={"finding_id": hyp_id, "ref": str(ref)}, evidence_refs=[str(ref)], run_id=run_id, policy_version=policy_version)
                self.add_edge(finding.node_id, evidence_ref.node_id, "has_evidence", source="finding", source_ref=hyp_id, confidence="evidenced", run_id=run_id, policy_version=policy_version)
                result["evidence"] += 1
            # Update matching coverage gap without manufacturing a completed verdict.
            risk_type = _norm((inv.get("kind") or contract.get("risk_type") or evidence.get("kind") or "unknown"), 80).lower() or "unknown"
            entry = contract.get("entrypoint") if isinstance(contract.get("entrypoint"), dict) else {}
            flow = _norm(entry.get("flow_id") or evidence.get("route") or "", 300)
            for gap in self.nodes(node_types=["CoverageGap"]):
                payload = gap.get("payload") or {}
                if risk_type in str(payload.get("risk_type") or "").lower() and (not flow or flow in str(payload.get("api") or "")):
                    state = "VALIDATED" if verdict in {"VALIDATED_CANDIDATE", "CONFIRMED_BY_HUMAN"} else "REJECTED_WITH_EVIDENCE" if verdict == "REJECTED" else "EVIDENCE_INCOMPLETE"
                    payload.update({"state": state, "last_verdict": verdict, "last_finding_id": hyp_id, "coverage_gap": 0.0 if state in {"VALIDATED", "REJECTED_WITH_EVIDENCE"} else 0.6})
                    self.upsert_node("CoverageGap", gap["node_key"], gap["label"], source="finding", source_ref=hyp_id, confidence="evidenced" if state != "EVIDENCE_INCOMPLETE" else "inferred", payload=payload, evidence_refs=[hyp_id], run_id=run_id, policy_version=policy_version)
                    result["updated_gaps"] += 1
        return result

    def record_cleanup(
        self,
        *,
        run_id: str,
        status: str,
        evidence_ref: str = "",
        target: dict[str, Any] | None = None,
        policy_version: str = "",
    ) -> dict[str, Any]:
        """Persist cleanup evidence and block matching high-risk frontier work.

        A cleanup failure never disappears into a textual report.  The matching
        risk surface is explicitly marked BLOCKED_BY_CLEANUP so future planning
        can remain read-only until a recovery receipt exists.
        """
        target = dict(target or {})
        normalized = str(status or "NOT_APPLICABLE").upper()
        cleanup = self.upsert_node(
            "CleanupRecord", f"{run_id}:{normalized}", f"Cleanup {normalized}: {run_id or 'run'}",
            source="controlled_executor", source_ref=evidence_ref or run_id or "cleanup",
            confidence="evidenced" if normalized in {"CLEAN", "NOT_APPLICABLE", "RESTORED"} else "disputed",
            approval_status="system_generated",
            payload={"run_id": run_id, "status": normalized, "target": _redact(target)},
            evidence_refs=[evidence_ref] if evidence_ref else [], run_id=run_id, policy_version=policy_version,
        )
        dirty = normalized in {"CLEANUP_FAILED", "FAILED", "DIRTY_TEST_ENVIRONMENT", "RESTORE_FAILED"}
        updated = 0
        if dirty:
            api = str(target.get("api") or target.get("path") or "")
            risk_type = str(target.get("risk_type") or "")
            for gap in self.nodes(node_types=["CoverageGap"]):
                payload = dict(gap.get("payload") or {})
                if (not api or api in str(payload.get("api") or "")) and (not risk_type or risk_type in str(payload.get("risk_type") or "")):
                    payload.update({"cleanup_status": "DIRTY_TEST_ENVIRONMENT", "state": "BLOCKED_BY_CLEANUP", "cleanup_record_id": cleanup.node_id})
                    self.upsert_node("CoverageGap", gap["node_key"], gap["label"], source="cleanup", source_ref=cleanup.node_id,
                                     confidence="evidenced", payload=payload, evidence_refs=[cleanup.node_id], run_id=run_id, policy_version=policy_version)
                    self.add_edge(cleanup.node_id, gap["node_id"], "cleans", source="cleanup", source_ref=cleanup.node_id,
                                  confidence="evidenced", run_id=run_id, policy_version=policy_version)
                    updated += 1
        return {"cleanup_record_id": cleanup.node_id, "dirty": dirty, "updated_frontier_count": updated}

    def record_replay_packets(self, packets: Iterable[dict[str, Any]], *, run_id: str = "", policy_version: str = "") -> dict[str, int]:
        """Attach sanitized replay evidence to the same fact graph.

        Replay packets remain evidence, not automatic formal findings.  They
        become source-traceable Evidence nodes and enrich adjacent risk surfaces.
        """
        total = 0
        for packet in packets:
            if not isinstance(packet, dict):
                continue
            packet_id = str(packet.get("packet_id") or _digest(packet))
            finding_id = str(packet.get("issue_id") or packet.get("finding_id") or packet.get("title") or packet_id)
            finding = self.upsert_node("Finding", f"replay:{finding_id}", _norm(packet.get("title") or "Replay candidate", 500),
                                       source="replay_evidence", source_ref=packet_id, confidence="inferred",
                                       payload={"verdict": "EVIDENCE_CAPTURED", "risk_type": packet.get("risk_type"), "flow_id": packet.get("flow_id")},
                                       evidence_refs=[packet_id], run_id=run_id, policy_version=policy_version)
            evidence = self.upsert_node("Evidence", f"replay:{packet_id}", f"Replay evidence: {packet_id}", source="replay_evidence",
                                        source_ref=packet_id, confidence="evidenced", payload=_redact(packet),
                                        evidence_refs=[packet_id], run_id=run_id, policy_version=policy_version)
            self.add_edge(finding.node_id, evidence.node_id, "has_evidence", source="replay_evidence", source_ref=packet_id,
                          confidence="evidenced", run_id=run_id, policy_version=policy_version)
            total += 1
        return {"replay_packets": total}

    # ---- human proposals ------------------------------------------------
    def create_proposal(
        self,
        *, author: str, target_node_id: str, proposed_change: dict[str, Any], reason: str,
        evidence_refs: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        target = self.get_node(target_node_id)
        if target is None:
            raise KeyError("proposal target must belong to current project/environment")
        if not isinstance(proposed_change, dict) or not proposed_change:
            raise ValueError("proposed_change must be a non-empty mapping")
        if not _norm(reason, 1000):
            raise ValueError("proposal reason is required")
        proposal_id = f"HFP_{_digest(self.project_id,self.environment_id,target_node_id,author,proposed_change,_now())}"
        now = _now()
        with closing(self._connect()) as con:
            con.execute("""INSERT INTO human_fact_proposals
                (proposal_id,project_id,environment_id,author,target_node_id,proposed_change_json,reason,evidence_refs_json,approval_status,reviewer,decision_time,impact_analysis_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (proposal_id, self.project_id, self.environment_id, _norm(author,160), target_node_id, _json(proposed_change), _norm(reason,1000),
                 _json({"refs": [str(v)[:500] for v in (evidence_refs or []) if str(v).strip()]}), "pending_review", "", "", _json({}), now, now))
            con.commit()
        node = self.upsert_node("HumanFactProposal", proposal_id, f"Proposal for {target['label']}", source="human", source_ref=proposal_id, confidence="inferred", approval_status="pending_review", payload={"target_node_id": target_node_id, "author": author, "reason": reason}, evidence_refs=list(evidence_refs or []))
        self.add_edge(node.node_id, target_node_id, "proposes_change_to", source="human", source_ref=proposal_id, confidence="inferred", approval_status="pending_review")
        return self.get_proposal(proposal_id) or {}

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM human_fact_proposals WHERE proposal_id=? AND project_id=? AND environment_id=?", (proposal_id, self.project_id, self.environment_id)).fetchone()
        if not row:
            return None
        value = dict(row)
        for key in ("proposed_change_json", "evidence_refs_json", "impact_analysis_json"):
            value[key[:-5]] = _load_json(value.pop(key, "{}"))
        return value

    def review_proposal(self, proposal_id: str, *, reviewer: str, decision: str, impact_analysis: dict[str, Any] | None = None) -> dict[str, Any]:
        decision = str(decision or "").lower()
        if decision not in {"approved", "rejected", "revoked"}:
            raise ValueError("decision must be approved, rejected, or revoked")
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError("unknown proposal")
        target = self.get_node(str(proposal["target_node_id"]))
        if target is None:
            raise ValueError("proposal target no longer belongs to scope")
        now = _now()
        impact = _redact(impact_analysis or self._impact_analysis(target))
        with closing(self._connect()) as con:
            con.execute("UPDATE human_fact_proposals SET approval_status=?, reviewer=?, decision_time=?, impact_analysis_json=?, updated_at=? WHERE proposal_id=?", (decision, _norm(reviewer,160), now, _json(impact), now, proposal_id))
            con.commit()
        if decision == "approved":
            payload = dict(target.get("payload") or {})
            payload.update(_load_json(json.dumps(proposal.get("proposed_change") or {})))
            self.upsert_node(target["node_type"], target["node_key"], target["label"], source="human_approved", source_ref=proposal_id, confidence="confirmed" if target["confidence"] == "confirmed" else "evidenced", approval_status="approved", payload=payload, evidence_refs=[proposal_id, *target.get("evidence_refs", [])], preserve_confirmed=False)
        elif decision == "revoked":
            # Revocation never deletes evidence; it marks the fact disputed and forces re-evaluation.
            payload = dict(target.get("payload") or {})
            payload["revoked_by_proposal"] = proposal_id
            self.upsert_node(target["node_type"], target["node_key"], target["label"], source="human_revoke", source_ref=proposal_id, confidence="disputed", approval_status="pending_review", payload=payload, evidence_refs=[proposal_id, *target.get("evidence_refs", [])], preserve_confirmed=False)
        return self.get_proposal(proposal_id) or {}

    def _impact_analysis(self, target: dict[str, Any]) -> dict[str, Any]:
        neighbors, edges = self.local_neighborhood([target["node_id"]], hops=2)
        return {"affected_node_ids": [n["node_id"] for n in neighbors], "affected_edges": [e["edge_id"] for e in edges], "requires_frontier_recompute": True}

    # ---- export/stats --------------------------------------------------
    def stats(self) -> dict[str, Any]:
        nodes = self.nodes()
        edges = self.edges()
        by_type: dict[str, int] = defaultdict(int)
        by_conf: dict[str, int] = defaultdict(int)
        for node in nodes:
            by_type[node["node_type"]] += 1
            by_conf[node["confidence"]] += 1
        return {"schema_version": self.SCHEMA_VERSION, "project_id": self.project_id, "environment_id": self.environment_id, "node_count": len(nodes), "edge_count": len(edges), "node_types": dict(sorted(by_type.items())), "confidence": dict(sorted(by_conf.items())), "path": str(self.path)}

    def export_markdown_vault(self, output_dir: str | Path) -> dict[str, Any]:
        """Read-only, redacted projection. Never read back into the graph."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        count = 0
        for node in self.nodes():
            safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", node["label"])[:80] or node["node_id"]
            category = re.sub(r"[^A-Za-z0-9._-]+", "_", node["node_type"])
            folder = output / category
            folder.mkdir(parents=True, exist_ok=True)
            related = self.edges([node["node_id"]])[:20]
            link_lines = []
            for edge in related:
                other_id = edge["to_node_id"] if edge["from_node_id"] == node["node_id"] else edge["from_node_id"]
                other = self.get_node(other_id)
                if other:
                    link_lines.append(f"- {edge['edge_type']}: [[{other['node_type']}/{re.sub(r'[^A-Za-z0-9._-]+', '_', other['label'])[:80] or other['node_id']}]]")
            content = "\n".join([
                "---",
                f"node_id: {node['node_id']}",
                f"node_type: {node['node_type']}",
                f"project_id: {node['project_id']}",
                f"environment_id: {node['environment_id']}",
                f"confidence: {node['confidence']}",
                f"approval_status: {node['approval_status']}",
                f"source_ref: {node['source_ref']}",
                "---",
                "",
                f"# {node['label']}",
                "",
                "## Related",
                *(link_lines or ["- None"]),
                "",
                "## Safe metadata",
                "```json",
                json.dumps(_redact(node.get("payload") or {}), ensure_ascii=False, indent=2, sort_keys=True),
                "```",
            ])
            (folder / f"{safe_label}.md").write_text(content, encoding="utf-8")
            count += 1
        (output / "_index.md").write_text("# QualiBug Cognitive Memory Graph Export\n\nThis is a read-only projection. The QualiBug SQLite graph is the system of record.\n", encoding="utf-8")
        return {"output_dir": str(output), "exported_notes": count, "read_only": True}


class GraphContextComposer:
    """Builds bounded, source-traceable evidence packs from local graph facts."""
    def __init__(self, graph: CognitiveMemoryGraph, *, max_nodes: int = 48, max_chars: int = 4500) -> None:
        self.graph = graph
        self.max_nodes = max(8, int(max_nodes))
        self.max_chars = max(800, int(max_chars))

    def compose(self, target: dict[str, Any] | None = None, *, high_risk_write: bool = False) -> dict[str, Any]:
        target = dict(target or {})
        seed_ids = self._resolve_target_nodes(target)
        allowed = {"confirmed", "evidenced"}
        if not high_risk_write:
            allowed.add("inferred")
        nodes, edges = self.graph.local_neighborhood(seed_ids, hops=3, allowed_confidence=allowed, limit=self.max_nodes)
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        refs: list[dict[str, Any]] = []
        for node in nodes:
            row = {"node_id": node["node_id"], "label": node["label"], "type": node["node_type"], "confidence": node["confidence"], "source_ref": node["source_ref"], "payload": node["payload"]}
            refs.append({"node_id": node["node_id"], "source_ref": node["source_ref"], "confidence": node["confidence"]})
            mapping = {
                "BusinessFact": "facts", "Entity": "facts", "Field": "facts", "API": "facts", "Invariant": "invariants",
                "Permission": "permissions", "Role": "permissions", "State": "state_transitions", "StateTransition": "state_transitions",
                "Finding": "historical_findings", "Evidence": "historical_findings", "CoverageGap": "coverage_gaps",
                "Observer": "observers", "CleanupRecord": "cleanup_constraints", "Environment": "safety_constraints",
            }
            buckets[mapping.get(node["node_type"], "facts")].append(row)
        env_is_production = self.graph.environment_id.lower() in {"production", "prod"}
        safety = list(buckets.get("safety_constraints", []))
        safety.append({"rule": "production_http_requests=0", "enforced": True, "environment": self.graph.environment_id})
        pack = {
            "target": target,
            "facts": buckets.get("facts", []),
            "invariants": buckets.get("invariants", []),
            "permissions": buckets.get("permissions", []),
            "state_transitions": buckets.get("state_transitions", []),
            "historical_findings": buckets.get("historical_findings", []),
            "disproofs": [row for row in buckets.get("historical_findings", []) if str((row.get("payload") or {}).get("verdict", "")).upper() == "REJECTED"],
            "coverage_gaps": buckets.get("coverage_gaps", []),
            "observers": buckets.get("observers", []),
            "safety_constraints": safety,
            "cleanup_constraints": buckets.get("cleanup_constraints", []),
            "context_refs": refs,
            "graph_ready": bool(nodes),
            "high_risk_write_allowed": bool(nodes) and not env_is_production and (not high_risk_write or all(n["confidence"] in {"confirmed", "evidenced"} for n in nodes)),
            "graph_mode": _graph_context_mode(),
        }
        pack["rendered_context"] = self.render(pack)
        return pack

    def _resolve_target_nodes(self, target: dict[str, Any]) -> list[str]:
        terms = [str(target.get(k) or "").lower() for k in ("api", "path", "entity", "entity_alias", "risk_type", "invariant")]
        terms = [term for term in terms if term]
        matches = []
        for node in self.graph.nodes():
            hay = " ".join([node["label"].lower(), json.dumps(node.get("payload") or {}, ensure_ascii=False).lower(), node.get("node_key", "").lower()])
            if not terms or any(term in hay for term in terms):
                matches.append(node["node_id"])
        return matches[:8]

    def render(self, pack: dict[str, Any]) -> str:
        """Render a compact prompt snippet, bounded and free of raw Markdown imports."""
        lines = ["QUALIBUG_GRAPH_CONTEXT_V1", f"Target: {json.dumps(_redact(pack.get('target') or {}), ensure_ascii=False, sort_keys=True)}"]
        for section in ("facts", "invariants", "permissions", "state_transitions", "historical_findings", "coverage_gaps", "observers", "safety_constraints", "cleanup_constraints"):
            rows = list(pack.get(section) or [])[:8]
            if not rows:
                continue
            lines.append(f"[{section}]")
            for row in rows:
                if isinstance(row, dict):
                    label = _norm(row.get("label") or row.get("rule") or json.dumps(_redact(row), ensure_ascii=False), 300)
                    source = _norm(row.get("source_ref") or "", 160)
                    confidence = _norm(row.get("confidence") or "", 30)
                    lines.append(f"- {label} | confidence={confidence} | source={source}")
                else:
                    lines.append(f"- {_norm(row, 300)}")
        rendered = "\n".join(lines)
        return rendered[: self.max_chars]


class RiskFrontierPlanner:
    """Ranks graph-derived risk surfaces by impact, uncertainty and safe feasibility."""
    def __init__(self, graph: CognitiveMemoryGraph) -> None:
        self.graph = graph

    def rank(self, *, active_policy: dict[str, Any] | None = None, limit: int = 20) -> list[dict[str, Any]]:
        policy = active_policy or {}
        surfaces = self.graph.nodes(node_types=["CoverageGap"])
        ranked: list[dict[str, Any]] = []
        for node in surfaces:
            payload = dict(node.get("payload") or {})
            state = str(payload.get("state") or "UNSEEN").upper()
            if state not in FRONTIER_STATES:
                state = "UNSEEN"
            safety_blocked = self.graph.environment_id.lower() in {"prod", "production"}
            cleanup_status = str(payload.get("cleanup_status") or "READY").upper()
            if safety_blocked:
                state = "BLOCKED_BY_SAFETY"
            elif cleanup_status in {"FAILED", "DIRTY_TEST_ENVIRONMENT"}:
                state = "BLOCKED_BY_CLEANUP"
            impact = float(payload.get("business_impact") or 0.5)
            uncertainty = float(payload.get("uncertainty") or (1.0 if node["confidence"] in {"inferred", "disputed"} else 0.45))
            coverage_gap = float(payload.get("coverage_gap") if payload.get("coverage_gap") is not None else 1.0)
            recent_change = float(payload.get("recent_change") or 0.0)
            adjacency = float(payload.get("finding_adjacency") or 0.0)
            observer = float(payload.get("available_observer_quality") or 0.5)
            cost = max(0.1, float(payload.get("execution_cost") or 0.3))
            duplicate = max(0.1, float(payload.get("duplicate_risk") or 0.1))
            risk = max(0.1, float(payload.get("safety_risk") or 0.1))
            raw_score = (impact * uncertainty * coverage_gap * (1 + recent_change) * (1 + adjacency) * max(0.2, observer)) / (cost * duplicate * risk)
            reasons = []
            if coverage_gap >= 0.8: reasons.append("high-value uncovered risk surface")
            if recent_change > 0: reasons.append("recently changed business relation")
            if adjacency > 0: reasons.append("adjacent to historical finding")
            if node["confidence"] in {"inferred", "disputed"}: reasons.append("business facts require evidence")
            if state.startswith("BLOCKED"): reasons.append(state.lower())
            if state in {"VALIDATED", "REJECTED_WITH_EVIDENCE"}:
                raw_score *= 0.08
                reasons.append("already concluded; de-prioritized as duplicate")
                state = "DEPRIORITIZED_AS_DUPLICATE"
            allowed = state not in {"BLOCKED_BY_SAFETY", "BLOCKED_BY_CLEANUP"}
            # A hard safety/cleanup block is never executable and must not win
            # the next-action queue merely because its theoretical business
            # impact is high.  Keep the score explainable for every other item.
            if not allowed:
                raw_score = 0.0
            rank = {
                "risk_surface_id": node["node_id"], "target": {"api": payload.get("api", ""), "risk_type": payload.get("risk_type", "unknown")},
                "state": state, "priority_score": round(raw_score, 6), "priority_reasons": reasons,
                "dimensions": {"business_impact": impact, "uncertainty": uncertainty, "coverage_gap": coverage_gap, "recent_change": recent_change, "finding_adjacency": adjacency, "available_observer_quality": observer, "execution_cost": cost, "duplicate_risk": duplicate, "safety_risk": risk},
                "execution_allowed": allowed, "policy_version": str(policy.get("policy_version") or ""),
            }
            ranked.append(rank)
        ranked.sort(key=lambda row: (-float(row["priority_score"]), row["risk_surface_id"]))
        return ranked[: max(1, int(limit))]

    def record_selection(self, selected: dict[str, Any], *, run_id: str = "", policy_version: str = "") -> dict[str, Any]:
        node = self.graph.get_node(str(selected.get("risk_surface_id") or ""))
        if node is None:
            raise KeyError("unknown risk surface")
        payload = dict(node.get("payload") or {})
        previous = str(payload.get("state") or "UNSEEN")
        payload.update({"state": "IN_PROGRESS", "selected_at": _now(), "last_priority": selected.get("priority_score"), "priority_reasons": selected.get("priority_reasons") or []})
        self.graph.upsert_node("CoverageGap", node["node_key"], node["label"], source="risk_frontier", source_ref=str(selected.get("risk_surface_id")), confidence=node["confidence"], approval_status=node["approval_status"], payload=payload, evidence_refs=node.get("evidence_refs") or [], run_id=run_id, policy_version=policy_version)
        return {"previous_state": previous, "current_state": "IN_PROGRESS", "coverage_delta": {"selected": 1, "uncovered_before": previous in {"UNSEEN", "FACT_INCOMPLETE", "EVIDENCE_INCOMPLETE"}}}


class Phase91ABEvaluator:
    """Data-driven A/B report for baseline vs graph context.

    The evaluator never fabricates LLM latency or finding quality. It measures
    local context size/traceability and accepts observed runtime metrics when
    callers supply replay results.
    """
    def evaluate(self, *, baseline_prompt: str, graph_pack: dict[str, Any], baseline_metrics: dict[str, Any] | None = None, challenger_metrics: dict[str, Any] | None = None) -> dict[str, Any]:
        baseline_metrics = baseline_metrics or {}
        challenger_metrics = challenger_metrics or {}
        graph_prompt = str(graph_pack.get("rendered_context") or "")
        base_chars = len(baseline_prompt)
        graph_chars = len(graph_prompt)
        required = ("production_http_requests", "cleanup_failures", "safety_violations", "false_positive_rate", "duplicate_rate", "coverage_gain", "evidence_completeness")
        metrics = {"baseline": baseline_metrics, "challenger": challenger_metrics}
        safety_passed = all(float(challenger_metrics.get(k, 0) or 0) == 0 for k in ("production_http_requests", "cleanup_failures", "safety_violations"))
        quality_known = all(k in challenger_metrics for k in required)
        quality_passed = bool(quality_known and challenger_metrics.get("evidence_completeness", 0) >= baseline_metrics.get("evidence_completeness", 0) and challenger_metrics.get("false_positive_rate", 1) <= baseline_metrics.get("false_positive_rate", 1) and challenger_metrics.get("duplicate_rate", 1) <= baseline_metrics.get("duplicate_rate", 1) and challenger_metrics.get("coverage_gain", 0) >= baseline_metrics.get("coverage_gain", 0))
        return {
            "baseline": {"prompt_chars": base_chars, **baseline_metrics},
            "challenger": {"prompt_chars": graph_chars, "context_refs": len(graph_pack.get("context_refs") or []), "graph_ready": bool(graph_pack.get("graph_ready")), **challenger_metrics},
            "deltas": {"prompt_chars": graph_chars - base_chars, "prompt_reduction_ratio": round(1 - (graph_chars / base_chars), 6) if base_chars else 0.0},
            "safety_passed": safety_passed,
            "quality_known": quality_known,
            "quality_passed": quality_passed,
            "promotion": "active" if safety_passed and quality_passed else "shadow",
            "reason": "Graph context remains shadow until measured replay/shadow quality gates pass" if not (safety_passed and quality_passed) else "Measured safe improvement qualifies for active mode",
            "metrics": metrics,
        }


def export_knowledge_vault(project_id: str, output_dir: str | Path, *, environment_id: str = "test", root: str | Path | None = None) -> dict[str, Any]:
    return CognitiveMemoryGraph(project_id, environment_id, root).export_markdown_vault(output_dir)


__all__ = [
    "CognitiveMemoryGraph", "GraphContextComposer", "RiskFrontierPlanner", "Phase91ABEvaluator", "GraphRef",
    "NODE_TYPES", "EDGE_TYPES", "FRONTIER_STATES", "export_knowledge_vault",
]
