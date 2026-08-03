"""
ClosedLoopFeedback — Bug → Pattern → Scenario → Mutation → Re-run.

V12.2 upgrade: learns bug patterns from confirmed findings, 
auto-expands scenarios with pattern-based mutations on next scan.

V12.3 upgrade: integrates with SQLite knowledge base via LearningPatternBridge
for enterprise-grade storage and cross-round knowledge transfer.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .customer_delivery_gate import is_customer_deliverable_defect
from .learning_pattern_bridge import LearningPatternBridge


def load_learned_scan_context(project: str, *, limit: int = 20) -> dict:
    """READ side of the closed loop: load SQLite-learned knowledge for a scan.

    Called at scan start so patterns learned in previous rounds are consumed
    by the next scan. Returns an explicit, inspectable payload; failures stay
    visible via the "load_failure" field instead of being swallowed.
    """
    bridge = LearningPatternBridge(project=project)
    return bridge.load_learned_context(limit=limit)


def build_closed_loop_context(
    project: str, root: Path, findings: list[dict], *, max_patterns: int = 20
) -> dict[str, Any]:
    """Build domain expansion context + pattern-based mutation hints.
    
    V12.3: Stores patterns in SQLite knowledge base via LearningPatternBridge
    for enterprise-grade storage and cross-round knowledge transfer.
    """
    pool_dir = root / "platform_outputs" / project / "closed_loop"
    pool_dir.mkdir(parents=True, exist_ok=True)
    patterns_file = pool_dir / "bug_patterns.json"

    history: dict = {"patterns": {}, "total_confirmed": 0}
    if patterns_file.exists():
        try:
            history = json.loads(patterns_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(f"closed_loop_history_invalid:{patterns_file}:{type(exc).__name__}") from exc

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    confirmed = [f for f in findings if is_customer_deliverable_defect(f)]
    
    new_patterns = 0
    for f in confirmed:
        pattern = _extract_pattern(f)
        key = pattern["signature"]
        if key not in history["patterns"]:
            history["patterns"][key] = {"pattern": pattern, "count": 1, "first_seen": now, "last_seen": now}
            new_patterns += 1
        else:
            history["patterns"][key]["count"] += 1
            history["patterns"][key]["last_seen"] = now

    history["total_confirmed"] += new_patterns
    history["updated_at_utc"] = now
    patterns_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    # Generate mutation hints for next scan
    top = sorted(history["patterns"].values(), key=lambda p: p["count"], reverse=True)[:max_patterns]
    mutations = []
    for p in top:
        pat = p["pattern"]
        mutations.append({
            "pattern": pat["type"],
            "entity": pat["entity"],
            "count": p["count"],
            "mutation_hint": pat.get("mutation", ""),
        })
    
    # Store patterns in SQLite knowledge base.
    # NOTE: bridge keys entries by "signature"; mutations dicts have no unique
    # signature, so build signature-qualified pattern dicts from history to
    # avoid every pattern collapsing onto a single key.
    sqlite_patterns = []
    for key, record in history["patterns"].items():
        pat = record.get("pattern", {})
        sqlite_patterns.append({
            "signature": key,
            "type": pat.get("type", "unknown"),
            "entity": pat.get("entity"),
            "mutation_hint": pat.get("mutation", ""),
            "count": record.get("count", 1),
        })
    bridge = LearningPatternBridge(project=project)
    stored_count = bridge.store_patterns(sqlite_patterns, scan_id="current_scan", confidence=0.85)
    
    # Migrate legacy patterns to SQLite if needed
    migrated_count = bridge.migrate_legacy_patterns_to_sqlite()

    return {
        "total_patterns": len(history["patterns"]),
        "new_this_scan": new_patterns,
        "mutations": mutations,
        "generated_probes": _generate_learning_probes(findings, project, root),
        "sqlite_storage": {
            "patterns_stored": stored_count,
            "legacy_migrated": migrated_count,
            "storage_type": "SQLite_enterprise"
        }
    }


def _generate_learning_probes(
    findings: list[dict], project: str, root
) -> list[dict[str, Any]]:
    """Generate new probes from confirmed bugs using the LearningGenerator.

    This wires the formerly-unused mutation hints into actual probe generation,
    proving that learning is NOT just re-sorting — it creates new artifacts.
    """
    from .learning_generator import LearningGenerator

    confirmed = [f for f in findings if is_customer_deliverable_defect(f)]
    if not confirmed:
        return []

    # Build minimal context from findings
    entities: list[str] = []
    endpoints: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for f in confirmed:
        method_val, path_val, _entity = _finding_operation(f)
        if path_val and method_val:
            key = f"{method_val}:{path_val}"
            if key not in seen_paths:
                seen_paths.add(key)
                endpoints.append({"method": method_val, "path": path_val})
        parts = [p for p in path_val.strip("/").split("/") if p and not p.startswith("{")]
        for p in parts:
            if p.lower() not in ("api", "v1", "v2", "v3") and p not in entities:
                entities.append(p)

    context = {"entities": entities[:20], "endpoints": endpoints[:50], "roles": []}
    generator = LearningGenerator(project_context=context)
    manifest = generator.generate_from_confirmed_bugs(confirmed)
    return generator.manifest_to_dict(manifest).get("generated_probes", [])


def _finding_operation(finding: dict[str, Any]) -> tuple[str, str, str]:
    reproduction = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    request_raw = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
    method = str(finding.get("method") or reproduction.get("method") or request_raw.get("method") or "GET").upper()
    path = str(finding.get("path") or finding.get("api") or reproduction.get("path") or request_raw.get("path") or "").strip()
    if not path:
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
        request_text = str(evidence.get("request") or "").strip()
        if " " in request_text:
            request_method, request_path = request_text.split(" ", 1)
            if request_path.startswith("/"):
                method = request_method.upper()
                path = request_path
    segments = [
        part for part in path.split("?")[0].strip("/").split("/")
        if part and not part.startswith("{") and part.lower() not in {"api", "v1", "v2", "v3"}
    ]
    return method, path, (segments[0] if segments else "unknown")


def _extract_pattern(finding: dict) -> dict:
    """Extract a reusable bug pattern from a confirmed finding."""
    title = str(finding.get("title", ""))
    category = str(finding.get("category", ""))
    method, path, entity = _finding_operation(finding)
    oracle = finding.get("oracle", {})
    
    # Map to pattern type
    pattern_type = "state_violation"
    if "permission" in category or "acces" in category.lower():
        pattern_type = "permission_bypass"
    elif "concurrency" in category or "race" in title.lower():
        pattern_type = "race_condition"
    elif "money" in category or "amount" in title.lower() or "refund" in title.lower():
        pattern_type = "money_conservation"
    elif "idempot" in title.lower():
        pattern_type = "idempotency"
    elif "forbidden" in title.lower() or "禁止" in title:
        pattern_type = "forbidden_transition"
    
    # Build signature for dedup
    signature = f"{pattern_type}:{category}:{method}:{entity}"
    signature = signature[:80]
    
    # Build mutation hint
    if pattern_type == "permission_bypass":
        mutation = f"Try lower-role access on source-declared {entity} endpoints"
    elif pattern_type == "forbidden_transition":
        mutation = f"Try all source-declared forbidden transitions on {entity} entity"
    elif pattern_type == "money_conservation":
        mutation = "Add negative-amount / zero-amount / duplicate-amount probes"
    elif pattern_type == "idempotency":
        mutation = "Double-submit all POST endpoints"
    else:
        mutation = "Expand parameter variants for similar endpoints"
    
    return {"type": pattern_type, "entity": entity,
            "category": category, "method": method, "signature": signature, "mutation": mutation}
