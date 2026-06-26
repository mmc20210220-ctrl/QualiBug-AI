"""Finding Deduplicator — merges findings with same root cause, tracks history.

Multiple Reasoner/Flow engines may report the same root cause from different angles.
This deduplicator:
1. Clusters findings by root_cause_fingerprint
2. Merges evidence from multiple sources
3. Only runs adversarial validation once per cluster
4. Tracks rejected findings to prevent re-submission
5. Writes deduplication history to persistent memory

Fingerprint dimensions: project_id + entity_type + entity_id + invariant_kind +
                       flow_path + affected_action + tenant boundary
"""
from __future__ import annotations
import hashlib
import json as _json
from pathlib import Path
from typing import Any


def _norm(s: Any) -> str:
    return " ".join(str(s or "").lower().replace("/", " ").replace("_", " ").split())


def build_fingerprint(finding: dict[str, Any]) -> str:
    """Build a root-cause fingerprint for deduplication."""
    entity = finding.get("entity_binding") or {}
    invariant = finding.get("violated_invariant") or {}
    entrypoint = finding.get("entrypoint") or {}

    parts = [
        finding.get("project_id", ""),
        entity.get("entity_type", ""),
        entity.get("entity_id", ""),
        invariant.get("kind", ""),
        entrypoint.get("action_type", ""),
        entity.get("tenant_id", ""),
    ]
    raw = _json.dumps([_norm(p) for p in parts], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def cluster_findings(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group findings into clusters by root-cause fingerprint."""
    clusters: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        fp = build_fingerprint(f)
        clusters.setdefault(fp, []).append(f)
    return clusters


def merge_evidence(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge evidence from multiple findings in a cluster into a single enriched finding.

    The first finding serves as the base; additional findings contribute evidence.
    """
    if not cluster:
        return {}
    if len(cluster) == 1:
        return dict(cluster[0])

    base = dict(cluster[0])
    # Merge observer refs
    all_observers: list[str] = list(base.get("observer_refs") or [])
    # Merge evidence refs
    all_evidence: list[str] = list(base.get("evidence_refs") or [])
    # Collect counterarguments from all
    all_counterargs: list[str] = []
    # Track all sources
    all_sources: list[str] = [base.get("hypothesis_id", "")]

    for f in cluster[1:]:
        obs = f.get("observer_refs") or []
        for o in obs:
            if o not in all_observers:
                all_observers.append(o)
        ev = f.get("evidence_refs") or []
        for e in ev:
            if e not in all_evidence:
                all_evidence.append(e)
        adv = f.get("adversarial_validation") or {}
        for ca in adv.get("counterarguments", []):
            if ca not in all_counterargs:
                all_counterargs.append(ca)
        hid = f.get("hypothesis_id", "")
        if hid and hid not in all_sources:
            all_sources.append(hid)

    base["observer_refs"] = all_observers
    base["evidence_refs"] = all_evidence
    base["_merged_from"] = all_sources
    base["_cluster_size"] = len(cluster)
    base["_cluster_fingerprint"] = build_fingerprint(base)

    if all_counterargs:
        adv = base.get("adversarial_validation", {})
        if isinstance(adv, dict):
            adv["counterarguments"] = all_counterargs[:5]
            base["adversarial_validation"] = adv

    return base


# ── History-based dedup ──

def load_rejection_history(memory_path: str) -> dict[str, Any]:
    """Load history of rejected findings from persistent store."""
    mp = Path(memory_path)
    if not mp.exists():
        return {"entries": {}}
    try:
        return _json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": {}}


def save_rejection_history(memory_path: str, history: dict[str, Any]) -> None:
    """Persist rejection history."""
    mp = Path(memory_path)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(_json.dumps(history, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def check_against_history(
    finding: dict[str, Any],
    history: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a finding has been previously rejected.

    Returns (is_duplicate, reason).
    """
    fp = build_fingerprint(finding)
    entries = history.get("entries", {})
    title = finding.get("title", "")

    for entry_id, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        # Exact fingerprint match
        if entry.get("fingerprint") == fp and entry.get("verdict") == "REJECTED":
            return True, f"Fingerprint matches rejected finding '{entry_id}': {entry.get('reason', '')}"
        # Title similarity
        if _norm(entry.get("title", "")) == _norm(title) and entry.get("verdict") == "REJECTED":
            return True, f"Title matches rejected finding '{entry_id}'"
        # Same entity + invariant kind
        entity = finding.get("entity_binding") or {}
        hist_entity = entry.get("entity") or {}
        invariant = finding.get("violated_invariant") or {}
        hist_inv = entry.get("invariant") or {}
        if (entity.get("entity_id") == hist_entity.get("entity_id")
                and entity.get("entity_type") == hist_entity.get("entity_type")
                and invariant.get("kind") == hist_inv.get("kind")
                and entry.get("verdict") == "REJECTED"):
            return True, f"Same entity+invariant as rejected finding '{entry_id}'"

    return False, ""


def record_rejection(
    finding: dict[str, Any],
    history: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Record a rejected finding in the history."""
    fp = build_fingerprint(finding)
    entry_id = f"REJ_{fp[:12]}"
    entity = finding.get("entity_binding") or {}
    invariant = finding.get("violated_invariant") or {}
    entries = history.setdefault("entries", {})
    entries[entry_id] = {
        "fingerprint": fp,
        "verdict": "REJECTED",
        "title": finding.get("title", ""),
        "reason": reason,
        "entity": {
            "entity_id": entity.get("entity_id"),
            "entity_type": entity.get("entity_type"),
        },
        "invariant": {
            "kind": invariant.get("kind"),
        },
        "rejected_at": _now(),
    }
    return history


def _now() -> str:
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())


# ── Full dedup pipeline ──

def deduplicate_and_validate(
    findings: list[dict[str, Any]],
    *,
    rejection_memory_path: str = "",
    validate_fn=None,  # callable: (finding) -> validated_finding
) -> list[dict[str, Any]]:
    """Full deduplication pipeline:
    1. Cluster by fingerprint
    2. Merge evidence per cluster
    3. Check against rejection history
    4. Run adversarial validation (if validate_fn provided)
    5. Return deduplicated findings
    """
    clusters = cluster_findings(findings)
    history: dict[str, Any] = {}
    if rejection_memory_path:
        history = load_rejection_history(rejection_memory_path)

    result: list[dict[str, Any]] = []
    for fp, cluster in clusters.items():
        merged = merge_evidence(cluster)

        # Check history
        is_dup, dup_reason = check_against_history(merged, history)
        if is_dup:
            merged["verdict"] = "REJECTED"
            merged["_rejection_reason"] = dup_reason
            merged["_dedup_skipped"] = True
            result.append(merged)
            continue

        # Run adversarial validation
        if validate_fn:
            merged = validate_fn(merged)

        # If rejected, record in history
        if merged.get("verdict") == "REJECTED" and rejection_memory_path:
            reason = "; ".join(
                (merged.get("adversarial_validation") or {}).get("counterarguments", [])
            ) or "Deterministic disproof"
            history = record_rejection(merged, history, reason)
            save_rejection_history(rejection_memory_path, history)

        result.append(merged)

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python finding_deduplicator.py <findings.json> [--history <path>]")
        sys.exit(1)
    path = Path(sys.argv[1])
    findings = _json.loads(path.read_text(encoding="utf-8"))
    history_path = ""
    if "--history" in sys.argv:
        idx = sys.argv.index("--history")
        history_path = sys.argv[idx + 1]
    result = deduplicate_and_validated(findings, rejection_memory_path=history_path)
    print(_json.dumps({"clusters": len(cluster_findings(findings)), "findings": len(result), "results": result}, indent=2, ensure_ascii=False, default=str))
