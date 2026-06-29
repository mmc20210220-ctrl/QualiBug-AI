from __future__ import annotations

"""Persistent, evidence-first Agent Loop control plane.

This module is intentionally a *single canonical state store* for autonomous
business Bug discovery.  It does not know how many defects exist in a target
and it never treats a static hint or LLM idea as a confirmed defect.

The canonical state is a project-local SQLite ledger.  A CSV projection is
exported for business owners and QA reviewers, but the CSV is not writable
state.  Every loop iteration reads the ledger, synchronises grounded
hypotheses from existing engines, plans the next safest high-information-gain
experiment, and records evidence/review outcomes back into the same ledger.

Boundaries:
* The loop does not read benchmark truth files or hidden answer sets.
* It never executes writes itself.  Existing safe-read and sandbox executors
  remain the only execution paths and retain their own safety gates.
* Human review is required before a runtime observation becomes a confirmed
  root cause and before a regression guard is created.
* Static/LLM evidence remains candidate-only until deterministic replay.
"""

import csv
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from .business_world_model import build_business_world_model_profile
from .concurrency_async_sandbox import build_concurrency_async_sandbox_plan
from .deployment_config_resolver import (
    build_deployment_config_snapshot,
    detect_deployment_config_drift,
    evaluate_deployment_drift_unlock,
    load_deployment_config_snapshot,
    persist_deployment_config_snapshot,
    resolve_deployment_config,
)
from .document_contract_fuzzing import compile_document_contracts
from .real_project_onboarding import ROOT, _safe_project_id, config_paths

PHASE = "phase74_agent_discovery_loop"
TERMINAL_STATES = {"CONFIRMED", "REJECTED", "REGRESSION_GUARD"}
STATE_ORDER = {
    "HYPOTHESIS_NEEDS_REVIEW": 10,
    "READY_FOR_READONLY": 20,
    "BLOCKED_BY_APPROVAL": 20,
    "EVIDENCE_CAPTURED": 30,
    "CONFIRMED": 40,
    "REJECTED": 40,
    "REGRESSION_GUARD": 50,
}
SEVERITY_WEIGHT = {"P0": 1.0, "P1": 0.82, "P2": 0.58, "P3": 0.32}
EVIDENCE_WEIGHT = {
    "llm_inferred": 0.10,
    "static_inferred": 0.30,
    "contract_inferred": 0.45,
    "schema_grounded": 0.55,
    "runtime_observed": 0.78,
    "runtime_strong": 0.96,
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, length: int = 24) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:length]


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("/", " ").replace("_", " ").split())


def _redact(value: Any, limit: int = 6000) -> Any:
    """Retain useful structured evidence without persisting secrets."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(token in key_text for token in ("password", "token", "authorization", "api_key", "secret", "cookie")):
                clean[str(key)] = "[REDACTED]"
            else:
                clean[str(key)] = _redact(item, limit)
        return clean
    if isinstance(value, list):
        return [_redact(item, limit) for item in value[:100]]
    text = str(value or "")
    for token in ("Bearer ", "bearer "):
        if token in text:
            head, _, tail = text.partition(token)
            text = head + token + "[REDACTED]" + ("" if not tail else "")
    return text[:limit]


def _paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "agent_discovery_loop"
    output = root / "platform_outputs" / project / "agent_discovery_loop"
    return {
        "workspace": workspace,
        "output": output,
        "ledger": workspace / "canonical_discovery_ledger.sqlite3",
        "report": output / "agent_discovery_loop_report.json",
        "spreadsheet": output / "canonical_discovery_ledger.csv",
        "dispatch": output / "next_best_experiment_manifest.json",
    }


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS loop_items (
            item_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            oracle_family TEXT,
            risk_type TEXT,
            severity TEXT NOT NULL,
            state TEXT NOT NULL,
            execution_policy TEXT NOT NULL,
            safety_status TEXT NOT NULL,
            human_review_state TEXT NOT NULL,
            information_gain REAL NOT NULL,
            priority_score REAL NOT NULL,
            evidence_strength TEXT NOT NULL,
            root_cause_cluster TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            UNIQUE(project_id, source, source_ref)
        );
        CREATE TABLE IF NOT EXISTS loop_events (
            event_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            item_id TEXT,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            at_utc TEXT NOT NULL,
            previous_event_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS loop_experiments (
            experiment_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            scenario_fingerprint TEXT NOT NULL,
            experiment_type TEXT NOT NULL,
            state TEXT NOT NULL,
            executor TEXT NOT NULL,
            scenario_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            UNIQUE(project_id, item_id, scenario_fingerprint)
        );
        CREATE INDEX IF NOT EXISTS idx_loop_items_project_state ON loop_items(project_id, state);
        CREATE INDEX IF NOT EXISTS idx_loop_items_project_priority ON loop_items(project_id, priority_score DESC);
        CREATE INDEX IF NOT EXISTS idx_loop_events_project_time ON loop_events(project_id, at_utc);
        CREATE INDEX IF NOT EXISTS idx_loop_experiments_project_state ON loop_experiments(project_id, state);
        CREATE INDEX IF NOT EXISTS idx_loop_experiments_item ON loop_experiments(project_id, item_id);
        """
    )
    return connection


def _event(connection: sqlite3.Connection, project: str, item_id: str | None, event_type: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    previous_row = connection.execute(
        "SELECT event_hash FROM loop_events WHERE project_id = ? ORDER BY rowid DESC LIMIT 1", (project,)
    ).fetchone()
    previous = str(previous_row["event_hash"]) if previous_row else ""
    entry = {
        "event_id": f"LOOP_EVT_{_hash([project, item_id, event_type, _now(), time.monotonic()], 32)}",
        "project_id": project,
        "item_id": item_id,
        "event_type": event_type,
        "actor": str(actor or "agent_loop")[:120],
        "payload": _redact(payload),
        "at_utc": _now(),
        "previous_event_hash": previous,
    }
    entry["event_hash"] = _hash(entry, 64)
    connection.execute(
        """INSERT INTO loop_events(event_id, project_id, item_id, event_type, actor, payload_json, at_utc, previous_event_hash, event_hash)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            entry["event_id"], project, item_id, event_type, entry["actor"],
            json.dumps(entry["payload"], ensure_ascii=False, sort_keys=True), entry["at_utc"], previous, entry["event_hash"],
        ),
    )
    return entry


def verify_agent_discovery_ledger(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = _paths(project, root)["ledger"]
    if not path.exists():
        return {"passed": True, "event_count": 0, "reason": "ledger_not_initialized"}
    with closing(_connect(path)) as connection:
        rows = connection.execute(
            "SELECT event_id, item_id, event_type, actor, payload_json, at_utc, previous_event_hash, event_hash FROM loop_events WHERE project_id = ? ORDER BY rowid ASC",
            (project,),
        ).fetchall()
    previous = ""
    errors: list[str] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        base = {
            "event_id": row["event_id"], "project_id": project, "item_id": row["item_id"],
            "event_type": row["event_type"], "actor": row["actor"], "payload": payload,
            "at_utc": row["at_utc"], "previous_event_hash": row["previous_event_hash"],
        }
        expected = _hash(base, 64)
        if row["previous_event_hash"] != previous or row["event_hash"] != expected:
            errors.append(str(row["event_id"]))
        previous = str(row["event_hash"])
    return {"passed": not errors, "event_count": len(rows), "invalid_event_ids": errors[:20]}


def _risk_score(severity: Any) -> float:
    return SEVERITY_WEIGHT.get(str(severity or "P2").upper(), SEVERITY_WEIGHT["P2"])


def _evidence_score(strength: Any) -> float:
    return EVIDENCE_WEIGHT.get(str(strength or "contract_inferred"), EVIDENCE_WEIGHT["contract_inferred"])


def _state_for_payload(payload: dict[str, Any]) -> tuple[str, str, str]:
    policy = str(payload.get("execution_policy") or "candidate_only")
    if policy in {"safe_read_only", "execute_safe_read_only"}:
        return "READY_FOR_READONLY", "safe_read_only", "pending"
    if policy in {"sandbox_required", "approved_disposable_sandbox"}:
        return "BLOCKED_BY_APPROVAL", "sandbox_requires_approval", "pending"
    return "HYPOTHESIS_NEEDS_REVIEW", "candidate_only", "pending"


def _cluster(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("root_cause_cluster") or payload.get("risk_type") or payload.get("oracle_family") or "business_hypothesis")
    path = str(payload.get("path") or payload.get("source_path") or "")
    return f"RC_{_hash([_norm(explicit), _norm(path)], 20)}"


def _scores(payload: dict[str, Any], state: str) -> tuple[float, float]:
    risk = _risk_score(payload.get("severity"))
    evidence = _evidence_score(payload.get("evidence_strength"))
    uncertainty = {
        "HYPOTHESIS_NEEDS_REVIEW": 1.0,
        "READY_FOR_READONLY": 0.80,
        "BLOCKED_BY_APPROVAL": 0.72,
        "EVIDENCE_CAPTURED": 0.45,
        "CONFIRMED": 0.10,
        "REJECTED": 0.05,
        "REGRESSION_GUARD": 0.12,
    }.get(state, 0.65)
    actionability = 1.0 if state in {"READY_FOR_READONLY", "EVIDENCE_CAPTURED", "HYPOTHESIS_NEEDS_REVIEW"} else 0.62
    information_gain = min(1.0, round(0.60 * uncertainty + 0.28 * risk + 0.12 * (1.0 - evidence), 4))
    priority = round((0.56 * risk + 0.34 * information_gain + 0.10 * evidence) * actionability, 4)
    return information_gain, priority


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("payload_json", "evidence_json"):
        try:
            result[key[:-5]] = json.loads(result.pop(key) or "{}")
        except Exception:
            result[key[:-5]] = {}
    return result


def _item_payload(
    *, item_id: str, item_type: str, title: str, source: str, source_ref: str,
    severity: str, payload: dict[str, Any], evidence_strength: str,
) -> dict[str, Any]:
    state, safety, review = _state_for_payload(payload)
    info_gain, priority = _scores({**payload, "severity": severity, "evidence_strength": evidence_strength}, state)
    return {
        "item_id": item_id,
        "item_type": item_type,
        "title": title,
        "source": source,
        "source_ref": source_ref,
        "oracle_family": str(payload.get("oracle_family") or ""),
        "risk_type": str(payload.get("risk_type") or payload.get("candidate_type") or "business_hypothesis"),
        "severity": str(severity or "P2").upper(),
        "state": state,
        "execution_policy": str(payload.get("execution_policy") or "candidate_only"),
        "safety_status": safety,
        "human_review_state": review,
        "information_gain": info_gain,
        "priority_score": priority,
        "evidence_strength": evidence_strength,
        "root_cause_cluster": _cluster(payload),
        "payload": _redact(payload),
        "evidence": _redact(payload.get("evidence") or {}),
    }


def _upsert_item(connection: sqlite3.Connection, project: str, item: dict[str, Any], actor: str) -> dict[str, Any]:
    existing = connection.execute(
        "SELECT * FROM loop_items WHERE project_id = ? AND source = ? AND source_ref = ?", (project, item["source"], item["source_ref"])
    ).fetchone()
    now = _now()
    if existing:
        old = _row_dict(existing)
        old_state = str(old.get("state") or "")
        # A sync must never silently downgrade human-confirmed/terminal work.
        state = old_state if STATE_ORDER.get(old_state, 0) >= STATE_ORDER.get(item["state"], 0) else item["state"]
        review = str(old.get("human_review_state") or item["human_review_state"])
        if old_state in TERMINAL_STATES:
            state = old_state
        info_gain, priority = _scores({**item["payload"], "severity": item["severity"], "evidence_strength": item["evidence_strength"]}, state)
        connection.execute(
            """UPDATE loop_items SET title=?, oracle_family=?, risk_type=?, severity=?, state=?, execution_policy=?, safety_status=?,
            human_review_state=?, information_gain=?, priority_score=?, evidence_strength=?, root_cause_cluster=?, payload_json=?, evidence_json=?, updated_at_utc=?
            WHERE item_id=?""",
            (item["title"], item["oracle_family"], item["risk_type"], item["severity"], state, item["execution_policy"], item["safety_status"], review,
             info_gain, priority, item["evidence_strength"], item["root_cause_cluster"], json.dumps(item["payload"], ensure_ascii=False),
             json.dumps(item["evidence"], ensure_ascii=False), now, old["item_id"]),
        )
        _event(connection, project, old["item_id"], "source_synchronized", actor, {"source": item["source"], "state": state})
        return _row_dict(connection.execute("SELECT * FROM loop_items WHERE item_id = ?", (old["item_id"],)).fetchone())
    connection.execute(
        """INSERT INTO loop_items(item_id, project_id, item_type, title, source, source_ref, oracle_family, risk_type, severity, state,
        execution_policy, safety_status, human_review_state, information_gain, priority_score, evidence_strength, root_cause_cluster,
        payload_json, evidence_json, created_at_utc, updated_at_utc)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item["item_id"], project, item["item_type"], item["title"], item["source"], item["source_ref"], item["oracle_family"], item["risk_type"],
         item["severity"], item["state"], item["execution_policy"], item["safety_status"], item["human_review_state"], item["information_gain"],
         item["priority_score"], item["evidence_strength"], item["root_cause_cluster"], json.dumps(item["payload"], ensure_ascii=False),
         json.dumps(item["evidence"], ensure_ascii=False), now, now),
    )
    _event(connection, project, item["item_id"], "item_discovered", actor, {"source": item["source"], "state": item["state"], "risk_type": item["risk_type"]})
    return _row_dict(connection.execute("SELECT * FROM loop_items WHERE item_id = ?", (item["item_id"],)).fetchone())


def _world_model_items(project: str, root: Path) -> list[dict[str, Any]]:
    profile = build_business_world_model_profile(project, root)
    items: list[dict[str, Any]] = []
    for candidate in profile.get("candidate_contracts") or []:
        if not isinstance(candidate, dict):
            continue
        identifier = str(candidate.get("candidate_id") or _hash(candidate))
        items.append(_item_payload(
            item_id=f"LOOP_WM_{_hash(identifier, 24)}", item_type="business_hypothesis",
            title=f"确认业务关系/状态假设：{candidate.get('candidate_type') or 'world_model'}",
            source="business_world_model", source_ref=identifier,
            severity="P1" if candidate.get("candidate_type") == "referential_relation" else "P2",
            payload={**candidate, "execution_policy": "candidate_only", "risk_type": "business_world_model_hypothesis"},
            evidence_strength="schema_grounded",
        ))
    for contract in profile.get("confirmed_contracts") or []:
        if not isinstance(contract, dict):
            continue
        identifier = str(contract.get("contract_id") or contract.get("candidate_id") or _hash(contract))
        items.append(_item_payload(
            item_id=f"LOOP_WM_CONF_{_hash(identifier, 24)}", item_type="approved_oracle",
            title=f"执行已确认业务 Oracle：{contract.get('oracle_family') or 'world_model'}",
            source="business_world_model", source_ref=f"confirmed:{identifier}",
            severity="P1" if contract.get("candidate_type") == "referential_relation" else "P2",
            payload={**contract, "execution_policy": "safe_read_only", "risk_type": "business_world_model_contract"},
            evidence_strength="schema_grounded",
        ))
    return items


def _sandbox_items(project: str, root: Path) -> list[dict[str, Any]]:
    plan = build_concurrency_async_sandbox_plan(project, root)
    items: list[dict[str, Any]] = []
    for contract in plan.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        identifier = str(contract.get("contract_id") or _hash(contract))
        items.append(_item_payload(
            item_id=f"LOOP_SANDBOX_{_hash(identifier, 24)}", item_type="sandbox_experiment",
            title=f"沙箱验证并发/重试副作用：{contract.get('title') or identifier}",
            source="concurrency_async_sandbox", source_ref=identifier,
            severity=str(contract.get("severity") or "P1"),
            payload={**contract, "risk_type": "concurrent_idempotency", "execution_policy": "sandbox_required"},
            evidence_strength="contract_inferred",
        ))
    return items


def _document_items(project: str, root: Path) -> list[dict[str, Any]]:
    """Compile uploaded Markdown API/PRD constraints into ledger hypotheses.

    The document compiler is intentionally generic.  It contributes only
    document-backed experiment candidates; write candidates remain blocked by
    the existing disposable-sandbox executor and are never auto-dispatched.
    """
    input_dir = config_paths(project, root)["input_dir"]
    prd_path = input_dir / "prd.md"
    prd = prd_path.read_text(encoding="utf-8", errors="replace") if prd_path.exists() else ""
    candidates = [path for path in input_dir.glob("*.md") if path.name.lower() not in {"prd.md", "readme.md"}]
    if not candidates:
        return []
    # Pick the richest endpoint-like document, not arbitrary prose.  This
    # lets enterprises upload API.md without first converting it to OpenAPI.
    def api_score(path: Path) -> tuple[int, int]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return (sum(text.upper().count(method) for method in ("GET", "POST", "PUT", "PATCH", "DELETE")), len(text))
    api_path = max(candidates, key=api_score)
    if api_score(api_path)[0] <= 0:
        return []
    api_text = api_path.read_text(encoding="utf-8", errors="replace")
    compiled = compile_document_contracts(prd, api_text)
    items: list[dict[str, Any]] = []
    for contract in compiled.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        identifier = str(contract.get("contract_id") or _hash(contract))
        policy = str(contract.get("execution_policy") or "candidate_only")
        normalized_policy = "safe_read_only" if policy == "safe_read_only" else "sandbox_required"
        items.append(_item_payload(
            item_id=f"LOOP_DOC_{_hash(identifier, 24)}", item_type="document_backed_experiment",
            title=str(contract.get("title") or "文档约束验证")[:500],
            source="document_contract_fuzzing", source_ref=identifier,
            severity=str(contract.get("severity") or "P2"),
            payload={
                **contract,
                "execution_policy": normalized_policy,
                "risk_type": str(contract.get("kind") or "document_business_constraint"),
                "document_source": api_path.name,
            },
            evidence_strength="contract_inferred",
        ))
    return items


def _finding_items(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        identifier = str(finding.get("issue_id") or finding.get("finding_id") or finding.get("probe_id") or _hash(finding))
        strength = str(finding.get("evidence_strength") or "contract_inferred")
        runtime = strength in {"runtime_observed", "runtime_strong"}
        execution_policy = "candidate_only" if not runtime else "evidence_captured"
        payload = {**finding, "execution_policy": execution_policy}
        item = _item_payload(
            item_id=f"LOOP_FINDING_{_hash(identifier, 24)}", item_type="observed_candidate",
            title=str(finding.get("title") or finding.get("risk_type") or "发现候选")[:500],
            source=str(finding.get("source") or "discovery_pipeline"), source_ref=identifier,
            severity=str(finding.get("severity") or "P2"), payload=payload, evidence_strength=strength,
        )
        if runtime:
            item["state"] = "EVIDENCE_CAPTURED"
            item["safety_status"] = "evidence_requires_human_review"
            item["human_review_state"] = "pending"
            item["information_gain"], item["priority_score"] = _scores({**payload, "severity": item["severity"], "evidence_strength": strength}, item["state"])
        items.append(item)
    return items


def _refresh_scores(connection: sqlite3.Connection, project: str) -> None:
    rows = connection.execute("SELECT * FROM loop_items WHERE project_id = ?", (project,)).fetchall()
    for row in rows:
        item = _row_dict(row)
        info, priority = _scores({**item.get("payload", {}), "severity": item.get("severity"), "evidence_strength": item.get("evidence_strength")}, str(item.get("state")))
        connection.execute("UPDATE loop_items SET information_gain = ?, priority_score = ?, updated_at_utc = ? WHERE item_id = ?", (info, priority, _now(), item["item_id"]))


def _action_for(item: dict[str, Any]) -> tuple[str, str]:
    state = str(item.get("state") or "")
    if state == "HYPOTHESIS_NEEDS_REVIEW":
        return "request_business_confirmation", "业务关系/状态机需要人类确认后才允许生成 Oracle"
    if state == "READY_FOR_READONLY":
        return "dispatch_safe_read_only", "已确认业务契约，适合由现有只读执行器回放"
    if state == "BLOCKED_BY_APPROVAL":
        return "request_sandbox_approval", "高风险实验需要可销毁沙箱、双重批准和现有安全门"
    if state == "EVIDENCE_CAPTURED":
        return "request_human_verdict", "已有运行时证据，等待人类确认根因或标记误报"
    if state == "CONFIRMED":
        return "create_regression_guard", "已确认根因必须生成长期回归守卫"
    return "observe", "当前不需要自动执行"


def _next_actions(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    active = [item for item in items if str(item.get("state")) not in TERMINAL_STATES]
    ranked = sorted(active, key=lambda row: (-float(row.get("priority_score") or 0), str(row.get("item_id"))))
    result: list[dict[str, Any]] = []
    seen_clusters: set[str] = set()
    for item in ranked:
        cluster = str(item.get("root_cause_cluster") or "")
        if cluster and cluster in seen_clusters:
            continue
        action, why = _action_for(item)
        result.append({
            "rank": len(result) + 1,
            "item_id": item.get("item_id"),
            "action": action,
            "why_now": why,
            "priority_score": item.get("priority_score"),
            "information_gain": item.get("information_gain"),
            "severity": item.get("severity"),
            "state": item.get("state"),
            "execution_policy": item.get("execution_policy"),
            "source": item.get("source"),
            "title": item.get("title"),
        })
        seen_clusters.add(cluster)
        if len(result) >= max(1, limit):
            break
    return result



def _graph_frontier_actions(
    project_id: str,
    root: Path,
    *,
    environment_id: str = "test",
    active_policy: dict[str, Any] | None = None,
    limit: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read-only Phase91 bridge from the typed graph into Agent Loop planning.

    This function intentionally never dispatches an HTTP request or sandbox write.
    It turns graph-frontier choices into planning-only next actions that existing
    safe-read / approved-sandbox executors may consume later.
    """
    try:
        from .cognitive_memory_graph import CognitiveMemoryGraph, RiskFrontierPlanner
        graph = CognitiveMemoryGraph(project_id, environment_id, root)
        ranked = RiskFrontierPlanner(graph).rank(active_policy=active_policy, limit=max(1, int(limit)))
        actions: list[dict[str, Any]] = []
        for row in ranked:
            if not row.get("execution_allowed"):
                continue
            target = dict(row.get("target") or {})
            actions.append({
                "rank": len(actions) + 1,
                "item_id": str(row.get("risk_surface_id") or ""),
                "action": "plan_graph_frontier_discovery",
                "why_now": "; ".join(str(x) for x in (row.get("priority_reasons") or [])) or "graph-derived uncovered risk surface",
                "priority_score": row.get("priority_score"),
                "information_gain": None,
                "severity": None,
                "state": row.get("state"),
                "execution_policy": "planning_only_delegated_to_existing_safety_gates",
                "source": "phase91_cognitive_memory_graph",
                "title": f"{target.get('api') or 'business surface'} · {target.get('risk_type') or 'unknown risk'}",
                "risk_frontier": row,
            })
        return actions, {"available": True, "stats": graph.stats(), "ranked_count": len(ranked), "environment_id": environment_id}
    except Exception as exc:  # Graph must never prevent the canonical loop from planning.
        return [], {"available": False, "error": f"{type(exc).__name__}: {exc}", "environment_id": environment_id}

def _export(paths: dict[str, Path], items: list[dict[str, Any]]) -> None:
    paths["output"].mkdir(parents=True, exist_ok=True)
    fields = [
        "item_id", "item_type", "title", "source", "source_ref", "oracle_family", "risk_type", "severity", "state",
        "execution_policy", "safety_status", "human_review_state", "information_gain", "priority_score", "evidence_strength",
        "root_cause_cluster", "created_at_utc", "updated_at_utc",
    ]
    with paths["spreadsheet"].open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            writer.writerow({key: item.get(key, "") for key in fields})


def build_agent_discovery_loop(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a planning iteration without executing any target requests.

    Existing engines remain responsible for safe-read and sandbox execution.
    This function centralises durable state and produces the next-best-action
    manifest they consume.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = dict(options or {})
    actor = str(options.get("actor") or "agent_loop")
    deployment_config = resolve_deployment_config(project_id=project, root=root, overrides=options.get("deployment_config") or options)
    deployment_snapshot = build_deployment_config_snapshot(deployment_config)
    previous_deployment_snapshot = load_deployment_config_snapshot(project, root)
    deployment_drift = detect_deployment_config_drift(deployment_snapshot, previous_deployment_snapshot)
    deployment_unlock = evaluate_deployment_drift_unlock(deployment_snapshot, deployment_drift, root=root)
    paths = _paths(project, root)
    with closing(_connect(paths["ledger"])) as connection:
        if not verify_agent_discovery_ledger(project, root).get("passed"):
            raise ValueError("canonical discovery ledger integrity check failed; refusing to append")
        source_items = (
            _world_model_items(project, root)
            + _document_items(project, root)
            + _sandbox_items(project, root)
            + _finding_items(options.get("candidate_findings") or [])
        )
        for item in source_items:
            _upsert_item(connection, project, item, actor)
        _refresh_scores(connection, project)
        _event(
            connection,
            project,
            None,
            "loop_iteration_planned",
            actor,
            {
                "source_item_count": len(source_items),
                "deployment_config": deployment_snapshot,
                "deployment_config_drift": deployment_drift,
                "deployment_drift_unlock": deployment_unlock,
            },
        )
        connection.commit()
        rows = connection.execute("SELECT * FROM loop_items WHERE project_id = ? ORDER BY priority_score DESC, item_id", (project,)).fetchall()
        items = [_row_dict(row) for row in rows]
        events = connection.execute("SELECT COUNT(*) AS count FROM loop_events WHERE project_id = ?", (project,)).fetchone()["count"]
        experiment_rows = connection.execute(
            "SELECT state, COUNT(*) AS count FROM loop_experiments WHERE project_id = ? GROUP BY state", (project,)
        ).fetchall()
        experiment_counts = {str(row["state"]): int(row["count"]) for row in experiment_rows}
    actions = _next_actions(items, int(options.get("max_next_actions") or 12))
    environment_id = str(options.get("environment_id") or options.get("environment") or "test")
    graph_actions, graph_report = _graph_frontier_actions(
        project, root,
        environment_id=environment_id,
        active_policy=dict(options.get("active_policy") or {}),
        limit=int(options.get("max_graph_frontier_actions") or 12),
    )
    # The graph is a planning signal only. Preserve existing canonical actions,
    # de-duplicate by risk-surface id, and never imply that Agent Loop executes it.
    existing_ids = {str(row.get("item_id") or "") for row in actions}
    for graph_action in graph_actions:
        if str(graph_action.get("item_id") or "") not in existing_ids:
            actions.append(graph_action)
    actions = actions[: max(1, int(options.get("max_next_actions") or 12))]
    summary = {
        "item_count": len(items),
        "hypothesis_count": sum(1 for item in items if item.get("state") == "HYPOTHESIS_NEEDS_REVIEW"),
        "safe_read_ready_count": sum(1 for item in items if item.get("state") == "READY_FOR_READONLY"),
        "sandbox_blocked_count": sum(1 for item in items if item.get("state") == "BLOCKED_BY_APPROVAL"),
        "evidence_review_count": sum(1 for item in items if item.get("state") == "EVIDENCE_CAPTURED"),
        "confirmed_count": sum(1 for item in items if item.get("state") == "CONFIRMED"),
        "regression_guard_count": sum(1 for item in items if item.get("state") == "REGRESSION_GUARD"),
        "event_count": int(events),
        "experiment_count": sum(experiment_counts.values()),
        "compiled_experiment_count": int(experiment_counts.get("COMPILED", 0)),
        "blocked_experiment_count": int(experiment_counts.get("BLOCKED_BY_APPROVAL", 0) + experiment_counts.get("BLOCKED_BY_FIXTURE", 0) + experiment_counts.get("BLOCKED", 0)),
        "fixture_blocked_experiment_count": int(experiment_counts.get("BLOCKED_BY_FIXTURE", 0)),
        "executed_experiment_count": int(experiment_counts.get("EXECUTED", 0) + experiment_counts.get("EVIDENCE_CAPTURED", 0)),
        "unknown_bug_count_assumption": False,
    }
    report = {
        "phase": PHASE,
        "project_id": project,
        "generated_at_utc": _now(),
        "deployment_config": deployment_snapshot,
        "deployment_config_drift": deployment_drift,
        "deployment_drift_unlock": deployment_unlock,
        "canonical_store": {
            "kind": "sqlite",
            "path": str(paths["ledger"].relative_to(root)).replace("\\", "/"),
            "human_review_projection": str(paths["spreadsheet"].relative_to(root)).replace("\\", "/"),
            "csv_is_read_only_projection": True,
        },
        "summary": summary,
        "next_best_actions": actions,
        "governance": {
            "does_not_track_known_bug_total": True,
            "static_and_llm_hypotheses_never_become_formal_bugs": True,
            "runtime_evidence_requires_human_verdict": True,
            "sandbox_execution_never_auto_approved": True,
            "production_execution_not_owned_by_loop": True,
            "single_canonical_state_store": True,
        },
        "items": items,
        "cognitive_graph": {
            **graph_report,
            "planning_only": True,
            "frontier_actions_considered": len(graph_actions),
            "frontier_actions_emitted": sum(1 for row in actions if row.get("action") == "plan_graph_frontier_discovery"),
        },
    }
    paths["output"].mkdir(parents=True, exist_ok=True)
    persist_deployment_config_snapshot(deployment_snapshot, root)
    paths["report"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["dispatch"].write_text(
        json.dumps(
            {
                "project_id": project,
                "generated_at_utc": _now(),
                "deployment_config": deployment_snapshot,
                "deployment_config_drift": deployment_drift,
                "deployment_drift_unlock": deployment_unlock,
                "actions": actions,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _export(paths, items)
    return report


def upsert_agent_discovery_item(
    project_id: str,
    *,
    item_type: str,
    title: str,
    source: str,
    source_ref: str,
    severity: str,
    payload: dict[str, Any],
    evidence_strength: str = "contract_inferred",
    root: Path | None = None,
    actor: str = "agent_loop_extension",
) -> dict[str, Any]:
    """Register one grounded workflow item in the canonical discovery ledger.

    Extension modules use this narrow public boundary instead of opening the
    SQLite database themselves.  It preserves the event hash chain, terminal
    state protection and the rule that candidate evidence is never promoted to
    a formal finding merely by being persisted.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    required = {
        "item_type": item_type,
        "title": title,
        "source": source,
        "source_ref": source_ref,
        "severity": severity,
    }
    if not all(str(value or "").strip() for value in required.values()):
        raise ValueError("item_type, title, source, source_ref and severity are required")
    paths = _paths(project, root)
    identifier = f"LOOP_EXT_{_hash([project, source, source_ref], 24)}"
    item = _item_payload(
        item_id=identifier,
        item_type=str(item_type),
        title=str(title)[:500],
        source=str(source),
        source_ref=str(source_ref),
        severity=str(severity).upper(),
        payload=payload,
        evidence_strength=str(evidence_strength),
    )
    with closing(_connect(paths["ledger"])) as connection:
        if not verify_agent_discovery_ledger(project, root).get("passed"):
            raise ValueError("canonical discovery ledger integrity check failed; refusing to append")
        saved = _upsert_item(connection, project, item, str(actor))
        connection.commit()
    return saved


def record_agent_discovery_evidence(
    project_id: str,
    item_id: str,
    observation: dict[str, Any],
    root: Path | None = None,
    actor: str = "executor",
) -> dict[str, Any]:
    """Attach deterministic evidence from an existing executor to one loop item."""
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = _paths(project, root)
    strength = str(observation.get("evidence_strength") or "runtime_observed")
    if strength not in {"runtime_observed", "runtime_strong"}:
        raise ValueError("only deterministic runtime evidence can be attached to the canonical loop")
    with closing(_connect(paths["ledger"])) as connection:
        if not verify_agent_discovery_ledger(project, root).get("passed"):
            raise ValueError("canonical discovery ledger integrity check failed; refusing to append")
        row = connection.execute("SELECT * FROM loop_items WHERE project_id = ? AND item_id = ?", (project, item_id)).fetchone()
        if not row:
            raise KeyError(f"unknown loop item: {item_id}")
        item = _row_dict(row)
        if item.get("state") in TERMINAL_STATES:
            raise ValueError("cannot attach new evidence to terminal loop item")
        evidence = {**(item.get("evidence") or {}), "runtime_observation": _redact(observation)}
        state = "EVIDENCE_CAPTURED"
        info, priority = _scores({**item.get("payload", {}), "severity": item.get("severity"), "evidence_strength": strength}, state)
        connection.execute(
            "UPDATE loop_items SET state=?, safety_status=?, human_review_state=?, evidence_strength=?, evidence_json=?, information_gain=?, priority_score=?, updated_at_utc=? WHERE item_id=?",
            (state, "evidence_requires_human_review", "pending", strength, json.dumps(evidence, ensure_ascii=False), info, priority, _now(), item_id),
        )
        _event(connection, project, item_id, "runtime_evidence_captured", actor, {"evidence_strength": strength, "observation": observation})
        connection.commit()
        return _row_dict(connection.execute("SELECT * FROM loop_items WHERE item_id = ?", (item_id,)).fetchone())


def apply_agent_discovery_review(
    project_id: str,
    item_id: str,
    decision: str,
    reviewer: str,
    root: Path | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Apply a human verdict and create a regression guard only for confirmations."""
    root = root or ROOT
    project = _safe_project_id(project_id)
    decision = str(decision or "").strip().lower()
    if decision not in {"confirmed", "rejected", "needs_investigation"}:
        raise ValueError("decision must be confirmed, rejected, or needs_investigation")
    paths = _paths(project, root)
    with closing(_connect(paths["ledger"])) as connection:
        if not verify_agent_discovery_ledger(project, root).get("passed"):
            raise ValueError("canonical discovery ledger integrity check failed; refusing to append")
        row = connection.execute("SELECT * FROM loop_items WHERE project_id = ? AND item_id = ?", (project, item_id)).fetchone()
        if not row:
            raise KeyError(f"unknown loop item: {item_id}")
        item = _row_dict(row)
        if item.get("state") != "EVIDENCE_CAPTURED":
            raise ValueError("human confirmation requires captured deterministic runtime evidence")
        state = {"confirmed": "CONFIRMED", "rejected": "REJECTED", "needs_investigation": "EVIDENCE_CAPTURED"}[decision]
        review_state = decision
        info, priority = _scores({**item.get("payload", {}), "severity": item.get("severity"), "evidence_strength": item.get("evidence_strength")}, state)
        connection.execute(
            "UPDATE loop_items SET state=?, human_review_state=?, information_gain=?, priority_score=?, updated_at_utc=? WHERE item_id=?",
            (state, review_state, info, priority, _now(), item_id),
        )
        _event(connection, project, item_id, "human_verdict_applied", reviewer, {"decision": decision, "notes": notes[:1000]})
        guard_id = None
        if decision == "confirmed":
            guard_payload = {
                "parent_item_id": item_id,
                "risk_type": item.get("risk_type"),
                "oracle_family": item.get("oracle_family"),
                "severity": item.get("severity"),
                "execution_policy": "safe_read_only" if item.get("execution_policy") == "safe_read_only" else "sandbox_required",
                "evidence": {"confirmed_item_id": item_id},
            }
            guard_id = f"LOOP_REG_{_hash([project, item_id], 24)}"
            guard = _item_payload(
                item_id=guard_id, item_type="regression_guard",
                title=f"回归守卫：{item.get('title')}", source="agent_discovery_loop", source_ref=f"regression:{item_id}",
                severity=str(item.get("severity") or "P2"), payload=guard_payload,
                evidence_strength=str(item.get("evidence_strength") or "runtime_observed"),
            )
            guard["state"] = "REGRESSION_GUARD"
            guard["human_review_state"] = "approved"
            guard["information_gain"], guard["priority_score"] = _scores({**guard_payload, "severity": guard["severity"], "evidence_strength": guard["evidence_strength"]}, "REGRESSION_GUARD")
            _upsert_item(connection, project, guard, reviewer)
        connection.commit()
        result = _row_dict(connection.execute("SELECT * FROM loop_items WHERE item_id = ?", (item_id,)).fetchone())
    return {"item": result, "regression_guard_id": guard_id, "decision": decision}


def load_agent_discovery_loop(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = _paths(project, root)["report"]
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Phase75: Experiment packs live in the same canonical ledger as hypotheses.
# The loop never owns target execution; it only compiles, records and routes
# explicit experiments to existing safe-read / disposable-sandbox executors.
# ---------------------------------------------------------------------------

EXPERIMENT_TERMINAL_STATES = {"EVIDENCE_CAPTURED", "REJECTED", "CANCELLED"}


def _experiment_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("scenario_json", "result_json"):
        try:
            result[key[:-5]] = json.loads(result.pop(key) or "{}")
        except Exception:
            result[key[:-5]] = {}
    return result


def upsert_agent_discovery_experiment(
    project_id: str,
    item_id: str,
    scenario: dict[str, Any],
    *,
    experiment_type: str = "sandbox_business_scenario",
    state: str = "COMPILED",
    executor: str = "agent_experiment_runner",
    root: Path | None = None,
    actor: str = "scenario_compiler",
) -> dict[str, Any]:
    """Persist a deterministic scenario pack in the canonical loop ledger.

    Scenario packs are not target output and never qualify as findings.  They
    only make the pending experiment reproducible across Agent iterations.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    if not isinstance(scenario, dict) or not scenario:
        raise ValueError("scenario must be a non-empty mapping")
    if not str(item_id or "").strip():
        raise ValueError("item_id is required")
    fingerprint = _hash(_redact(scenario), 48)
    paths = _paths(project, root)
    with closing(_connect(paths["ledger"])) as connection:
        if not verify_agent_discovery_ledger(project, root).get("passed"):
            raise ValueError("canonical discovery ledger integrity check failed; refusing to append")
        item = connection.execute(
            "SELECT item_id FROM loop_items WHERE project_id = ? AND item_id = ?", (project, item_id)
        ).fetchone()
        if not item:
            raise KeyError(f"unknown loop item: {item_id}")
        existing = connection.execute(
            "SELECT * FROM loop_experiments WHERE project_id = ? AND item_id = ? AND scenario_fingerprint = ?",
            (project, item_id, fingerprint),
        ).fetchone()
        now = _now()
        if existing:
            connection.execute(
                "UPDATE loop_experiments SET state=?, executor=?, scenario_json=?, updated_at_utc=? WHERE experiment_id=?",
                (str(state), str(executor), json.dumps(_redact(scenario), ensure_ascii=False), now, existing["experiment_id"]),
            )
            experiment_id = str(existing["experiment_id"])
            event_type = "experiment_pack_refreshed"
        else:
            experiment_id = f"LOOP_EXP_{_hash([project, item_id, fingerprint], 28)}"
            connection.execute(
                """INSERT INTO loop_experiments(experiment_id, project_id, item_id, scenario_fingerprint, experiment_type, state,
                executor, scenario_json, result_json, created_at_utc, updated_at_utc)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (experiment_id, project, item_id, fingerprint, str(experiment_type), str(state), str(executor),
                 json.dumps(_redact(scenario), ensure_ascii=False), "{}", now, now),
            )
            event_type = "experiment_pack_compiled"
        _event(connection, project, item_id, event_type, actor, {
            "experiment_id": experiment_id,
            "experiment_type": experiment_type,
            "state": state,
            "scenario_fingerprint": fingerprint,
        })
        connection.commit()
        row = connection.execute("SELECT * FROM loop_experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
    return _experiment_row(row)


def record_agent_discovery_experiment_result(
    project_id: str,
    experiment_id: str,
    result: dict[str, Any],
    *,
    state: str = "EXECUTED",
    root: Path | None = None,
    actor: str = "sandbox_executor",
) -> dict[str, Any]:
    """Append an executor receipt without promoting it to a formal finding."""
    root = root or ROOT
    project = _safe_project_id(project_id)
    if not isinstance(result, dict):
        raise ValueError("result must be a mapping")
    paths = _paths(project, root)
    with closing(_connect(paths["ledger"])) as connection:
        if not verify_agent_discovery_ledger(project, root).get("passed"):
            raise ValueError("canonical discovery ledger integrity check failed; refusing to append")
        row = connection.execute(
            "SELECT * FROM loop_experiments WHERE project_id = ? AND experiment_id = ?", (project, experiment_id)
        ).fetchone()
        if not row:
            raise KeyError(f"unknown experiment: {experiment_id}")
        experiment = _experiment_row(row)
        if str(experiment.get("state")) in EXPERIMENT_TERMINAL_STATES:
            raise ValueError("cannot append to a terminal experiment")
        connection.execute(
            "UPDATE loop_experiments SET state=?, result_json=?, updated_at_utc=? WHERE experiment_id=?",
            (str(state), json.dumps(_redact(result), ensure_ascii=False), _now(), experiment_id),
        )
        _event(connection, project, str(experiment.get("item_id")), "experiment_receipt_recorded", actor, {
            "experiment_id": experiment_id,
            "state": state,
            "result_summary": _redact({
                "status": result.get("status"),
                "finding_count": len(result.get("findings") or []),
                "blocker_count": len(result.get("blockers") or []),
            }),
        })
        connection.commit()
        updated = connection.execute("SELECT * FROM loop_experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
    return _experiment_row(updated)


def load_agent_discovery_experiments(project_id: str = "real_project_demo", root: Path | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = _paths(project, root)
    if not paths["ledger"].exists():
        return []
    with closing(_connect(paths["ledger"])) as connection:
        rows = connection.execute(
            "SELECT * FROM loop_experiments WHERE project_id = ? ORDER BY updated_at_utc DESC, experiment_id", (project,)
        ).fetchall()
    return [_experiment_row(row) for row in rows]
