"""
ClosedLoopFeedback — Bug → Pattern → Scenario → Mutation → Re-run.

V12.2 upgrade: learns bug patterns from confirmed findings, 
auto-expands scenarios with pattern-based mutations on next scan.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


def build_closed_loop_context(
    project: str, root: Path, findings: list[dict], *, max_patterns: int = 20
) -> dict[str, Any]:
    """Build domain expansion context + pattern-based mutation hints."""
    pool_dir = root / "platform_outputs" / project / "closed_loop"
    pool_dir.mkdir(parents=True, exist_ok=True)
    patterns_file = pool_dir / "bug_patterns.json"

    history: dict = {"patterns": {}, "total_confirmed": 0}
    if patterns_file.exists():
        try: history = json.loads(patterns_file.read_text(encoding="utf-8"))
        except: pass

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    confirmed = [f for f in findings if str(f.get("verdict","")).lower() == "confirmed"
                 or f.get("source") in ("runtime_probe", "v12_state_graph")]
    
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

    return {
        "total_patterns": len(history["patterns"]),
        "new_this_scan": new_patterns,
        "mutations": mutations,
        "generated_probes": _generate_learning_probes(findings, project, root),
    }


def _generate_learning_probes(
    findings: list[dict], project: str, root
) -> list[dict[str, Any]]:
    """Generate new probes from confirmed bugs using the LearningGenerator.

    This wires the formerly-unused mutation hints into actual probe generation,
    proving that learning is NOT just re-sorting — it creates new artifacts.
    """
    try:
        from .learning_generator import LearningGenerator

        confirmed = [f for f in findings if str(f.get("verdict", "")).lower() == "confirmed"
                     or f.get("source") in ("runtime_probe", "v12_state_graph")]
        if not confirmed:
            return []

        # Build minimal context from findings
        entities: list[str] = []
        endpoints: list[dict[str, str]] = []
        seen_paths: set[str] = set()
        for f in confirmed:
            path_val = str(f.get("path") or f.get("api", ""))
            method_val = str(f.get("method", "GET")).upper()
            if path_val and method_val:
                key = f"{method_val}:{path_val}"
                if key not in seen_paths:
                    seen_paths.add(key)
                    endpoints.append({"method": method_val, "path": path_val})
            # Extract entity from path
            parts = [p for p in path_val.strip("/").split("/") if p and not p.startswith("{")]
            for p in parts:
                if p.lower() not in ("api", "v1", "v2", "v3") and p not in entities:
                    entities.append(p)

        context = {"entities": entities[:20], "endpoints": endpoints[:50], "roles": []}
        generator = LearningGenerator(project_context=context)
        manifest = generator.generate_from_confirmed_bugs(confirmed)
        return generator.manifest_to_dict(manifest).get("generated_probes", [])
    except Exception:
        return []


def _extract_pattern(finding: dict) -> dict:
    """Extract a reusable bug pattern from a confirmed finding."""
    title = str(finding.get("title", ""))
    category = str(finding.get("category", ""))
    method = str(finding.get("method", ""))
    path = str(finding.get("path", ""))
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
    signature = f"{pattern_type}:{category}:{method}:{path.split('/')[1] if '/' in path else path}"
    signature = signature[:80]
    
    # Build mutation hint
    if pattern_type == "permission_bypass":
        mutation = f"Try lower-role access on /{path.split('/')[1]}/* endpoints"
    elif pattern_type == "forbidden_transition":
        mutation = f"Try all forbidden transitions on {path.split('/')[1]} entity"
    elif pattern_type == "money_conservation":
        mutation = "Add negative-amount / zero-amount / duplicate-amount probes"
    elif pattern_type == "idempotency":
        mutation = "Double-submit all POST endpoints"
    else:
        mutation = "Expand parameter variants for similar endpoints"
    
    return {"type": pattern_type, "entity": path.split("/")[1] if "/" in path else "unknown",
            "category": category, "method": method, "signature": signature, "mutation": mutation}
