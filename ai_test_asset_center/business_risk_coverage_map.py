"""Persistent business-risk coverage map.

The map makes exploration cumulative: prior confirmed/rejected/blocked outcomes
change which probes are worth spending a future test-environment budget on. It
stores only normalized fingerprints and verdict metadata, never raw customer
payloads or credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_project(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "project"))[:96] or "project"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def _template_path(value: Any) -> str:
    path = str(value or "/").split("?", 1)[0]
    path = re.sub(r"https?://[^/]+", "", path)
    path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
    return path if path.startswith("/") else "/" + path


def coverage_key(item: dict[str, Any]) -> str:
    parts = {
        "entity": _norm(item.get("entity") or item.get("entity_type") or item.get("resource") or ""),
        "method": str(item.get("method") or item.get("action_method") or "GET").upper(),
        "path": _template_path(item.get("path") or item.get("endpoint") or item.get("api") or "/"),
        "risk": _norm(item.get("risk_type") or item.get("invariant_kind") or item.get("mutation") or "unknown"),
        "state": _norm(item.get("state_transition") or item.get("lifecycle") or ""),
        "boundary": _norm(item.get("permission_boundary") or item.get("tenant_boundary") or item.get("actor_role") or ""),
        "relation": _norm(item.get("relation") or item.get("cross_object_relation") or ""),
    }
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class BusinessRiskCoverageMap:
    """Durable coverage/outcome memory used to rank future probes safely."""

    def __init__(self, project_id: str = "real_project_demo", root: Path | str | None = None):
        self.project_id = _safe_project(project_id)
        self.root = Path(root or ".")
        self.path = self.root / "platform_workspace" / self.project_id / "defect_discovery" / "business_risk_coverage_map.json"
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"schema_version": "phase90-v1", "project_id": self.project_id, "entries": {}, "updated_at": ""}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state["updated_at"] = _now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def record_outcomes(self, outcomes: Iterable[dict[str, Any]]) -> dict[str, Any]:
        count = 0
        for raw in outcomes:
            if not isinstance(raw, dict):
                continue
            key = coverage_key(raw)
            entry = self._state["entries"].setdefault(key, {
                "fingerprint": key,
                "entity": str(raw.get("entity") or raw.get("entity_type") or ""),
                "method": str(raw.get("method") or raw.get("action_method") or "GET").upper(),
                "path": _template_path(raw.get("path") or raw.get("endpoint") or raw.get("api") or "/"),
                "risk_type": str(raw.get("risk_type") or raw.get("invariant_kind") or raw.get("mutation") or "unknown"),
                "attempts": 0,
                "outcomes": {},
                "last_verdict": "UNEXPLORED",
                "last_seen_at": "",
                "needs_evidence": False,
                "blocked_reason": "",
            })
            verdict = str(raw.get("verdict") or raw.get("status") or "EVIDENCE_CAPTURED").upper()
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
            entry["outcomes"][verdict] = int(entry["outcomes"].get(verdict) or 0) + 1
            entry["last_verdict"] = verdict
            entry["last_seen_at"] = _now()
            entry["needs_evidence"] = verdict in {"NEEDS_MORE_EVIDENCE", "EVIDENCE_CAPTURED", "SCHEMA_INVALID"}
            entry["blocked_reason"] = str(raw.get("blocker") or raw.get("reason") or "")[:300] if verdict.startswith("BLOCKED") else ""
            count += 1
        self._save()
        return self.summary()

    def prioritize(self, probes: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        for raw in probes:
            if not isinstance(raw, dict):
                continue
            probe = dict(raw)
            key = coverage_key(probe)
            prior = self._state["entries"].get(key)
            base = float(probe.get("priority_score") or 0.0)
            delta = 0.0
            reasons: list[str] = []
            if prior is None:
                delta += 0.22
                reasons.append("未覆盖业务风险面")
                coverage_state = "UNEXPLORED"
            else:
                coverage_state = str(prior.get("last_verdict") or "UNEXPLORED")
                attempts = int(prior.get("attempts") or 0)
                if prior.get("needs_evidence"):
                    delta += 0.14
                    reasons.append("历史高价值候选缺证据")
                elif coverage_state in {"CONFIRMED_BY_HUMAN", "REJECTED", "DISPROVED"}:
                    delta -= min(0.24, 0.06 + attempts * 0.03)
                    reasons.append("历史已结论，避免重复探索")
                elif coverage_state.startswith("BLOCKED"):
                    delta += 0.05
                    reasons.append("历史阻断，等待条件变化后复查")
                else:
                    delta += 0.08
                    reasons.append("已有探索但未形成稳定结论")
            probe["coverage_fingerprint"] = key
            probe["coverage_state"] = coverage_state
            probe["coverage_priority_delta"] = round(delta, 6)
            probe["priority_score"] = round(max(0.01, min(1.0, base + delta)), 6)
            probe["priority_reasons"] = [*list(probe.get("priority_reasons") or []), *reasons][:10]
            ranked.append(probe)
        ranked.sort(key=lambda item: (-float(item.get("priority_score") or 0), str(item.get("risk_type") or ""), str(item.get("path") or "")))
        return ranked, self.summary()

    def summary(self) -> dict[str, Any]:
        entries = list(self._state.get("entries", {}).values())
        counts: dict[str, int] = {}
        for entry in entries:
            verdict = str(entry.get("last_verdict") or "UNEXPLORED")
            counts[verdict] = counts.get(verdict, 0) + 1
        return {
            "project_id": self.project_id,
            "entry_count": len(entries),
            "verdict_distribution": counts,
            "needs_evidence_count": sum(1 for entry in entries if entry.get("needs_evidence")),
            "path": str(self.path),
        }
