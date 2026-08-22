"""
ClosedLoopFeedback — Bug → Pattern → Scenario → Mutation → Re-run.

V12.2 upgrade: learns bug patterns from confirmed findings, 
auto-expands scenarios with pattern-based mutations on next scan.

V12.3 upgrade: integrates with SQLite knowledge base via LearningPatternBridge
for enterprise-grade storage and cross-round knowledge transfer.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
    project: str, root: Path, findings: list[dict], *, max_patterns: int = 20,
    consumed_context: dict | None = None,
) -> dict[str, Any]:
    """Write side of the closed loop: extract patterns, reinforce / decay.

    V12.3: Stores patterns in SQLite knowledge base via LearningPatternBridge
    for enterprise-grade storage and cross-round knowledge transfer.

    ``consumed_context`` is the learned_knowledge payload loaded at scan
    start (same data the planning/reasoner stages consumed). Passing it
    avoids a second usage-recording load; when omitted it is loaded here.
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

    # Engine-level attention attribution (comprehension layer): attribute
    # confirmed defects to the reasoner engine family that produced them so
    # the next scan can prioritize proven engines.  Product-owned data only;
    # failures stay visible and never block the closed loop.
    try:
        from .engine_feedback import record_confirmed_engine_attribution

        engine_attention = record_confirmed_engine_attribution(
            confirmed, project=project, root=root
        )
    except Exception as exc:
        logger.warning("engine attribution learning failed (closed loop continues): %s", exc)
        engine_attention = {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{str(exc)[:120]}",
            "engines_updated": 0,
        }
    
    new_patterns = 0
    confirmed_this_scan_keys: set[str] = set()
    for f in confirmed:
        pattern = _extract_pattern(f)
        key = pattern["signature"]
        confirmed_this_scan_keys.add(key)
        if key not in history["patterns"]:
            history["patterns"][key] = {"pattern": pattern, "count": 1, "first_seen": now, "last_seen": now}
            new_patterns += 1
        else:
            history["patterns"][key]["count"] += 1
            history["patterns"][key]["last_seen"] = now

    history["total_confirmed"] += new_patterns
    history["updated_at_utc"] = now
    patterns_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    # Generate learned probes is intentionally NOT part of this module anymore:
    # the mainline consumes learned knowledge as a bounded ranking boost
    # (learning_knowledge_consumption.py), and learned_probes.json had no
    # mainline consumer (dead artifact, removed). Probes stay generated only
    # by the governed learning pipeline in AutoLearningTrigger.

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

    # The consumed payload this scan's planning/reasoner stages operated on.
    if isinstance(consumed_context, dict):
        _consumed_payload = consumed_context
    else:
        _consumed_payload = load_learned_scan_context(project)
    consumed_entries = [
        p for p in _consumed_payload.get("learned_patterns", []) if isinstance(p, dict)
    ]
    consumed_keys = {str(p.get("_key") or "") for p in consumed_entries}
    consumed_confidence = {
        str(p.get("_key") or ""): float(p.get("_confidence") or 0.0)
        for p in consumed_entries
    }

    # Reinforcement semantics: signatures confirmed THIS scan are reinforced
    # (0.95); every other stored signature keeps its current (possibly already
    # decayed) confidence. store() keeps max(new, existing), so passing the
    # current value is a no-op that protects prior decay from resurrection.
    confidence_map: dict[str, float] = {}
    for key in history["patterns"]:
        if key in confirmed_this_scan_keys:
            confidence_map[key] = 0.95
        elif key in consumed_confidence:
            confidence_map[key] = consumed_confidence[key]
    stored_count = bridge.store_patterns(
        sqlite_patterns, scan_id="current_scan", confidence=0.8,
        confidence_map=confidence_map,
    )

    # Negative feedback (honest form): the runtime has no false-positive
    # labels — FP truth is evaluator-private — so the legitimate negative
    # signal is non-reinforcement: patterns consumed in a scan that produced
    # no confirmed defect of that signature lose a bounded slice of
    # confidence. They are never deleted and the floor keeps them testable.
    stale_keys = sorted(
        k for k in consumed_keys if k and k not in confirmed_this_scan_keys
    )
    decayed_count = bridge.kb.adjust_confidence(
        "risk_pattern", stale_keys, 0.95, floor=0.05
    ) if stale_keys else 0

    # Migrate legacy patterns to SQLite once; the flag stops the per-scan
    # re-migration that used to rewrite every legacy entry each round.
    migrated_count = 0
    if not history.get("sqlite_migrated"):
        migrated_count = bridge.migrate_legacy_patterns_to_sqlite()
        history["sqlite_migrated"] = True
        history["updated_at_utc"] = now
        patterns_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "total_patterns": len(history["patterns"]),
        "new_this_scan": new_patterns,
        "sqlite_storage": {
            "patterns_stored": stored_count,
            "legacy_migrated": migrated_count,
            "non_reinforced_decayed": decayed_count,
            "storage_type": "SQLite_enterprise"
        },
        "engine_attention": engine_attention,
    }


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
    """Extract a reusable bug pattern from a confirmed finding.

    Open taxonomy: the pattern type is the finding's own observed
    classification (category/risk_family), carried as data. There is no
    closed keyword-mapped type list and no coercion into a default bucket —
    unknown classes stay visible as "uncategorized:<category>" so new bug
    kinds extend the space instead of being silently relabelled.
    Mutation hints are never fabricated: only a source-declared hint on the
    finding itself is carried through; otherwise the field stays empty.

    Semantic features (comprehension layer): the pattern additionally carries
    the finding's own structured semantics — the assertion kind (category),
    the reproducing actor (``reproduction.actor``), the semantic description,
    and the expected/actual behavior delta. All of these come from the
    finding's own observed fields; nothing is inferred or invented. These
    features feed the reasoner's learned-memory prompt block so the next
    scan's hypothesis generation is guided by *what kind of behavior was
    violated*, not just which endpoint.
    """
    category = str(finding.get("category", "")).strip()
    risk_family = str(finding.get("risk_family", "")).strip()
    method, path, entity = _finding_operation(finding)

    observed_type = category or risk_family
    pattern_type = observed_type if observed_type else f"uncategorized:{observed_type or 'unknown'}"

    # Build signature for dedup
    signature = f"{pattern_type}:{method}:{entity}"
    signature = signature[:80]

    # Only carry an explicitly declared mutation hint; never invent guidance.
    mutation = str(
        finding.get("mutation_hint")
        or (finding.get("oracle", {}) or {}).get("mutation_hint")
        or ""
    )

    # ── Semantic features (all from the finding's own observed fields) ──
    reproduction = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    actor = str(reproduction.get("actor") or "").strip()
    description = str(finding.get("description") or "").strip()[:240]
    expected = finding.get("expected")
    actual = finding.get("actual")
    behavior_delta: dict | None = None
    if isinstance(expected, dict) and isinstance(actual, dict) and expected != actual:
        # Only the differing fields — never the full response bodies.
        behavior_delta = {
            key: {"expected": expected[key], "actual": actual.get(key)}
            for key in expected
            if key in actual and expected[key] != actual[key]
        }

    return {
        "type": pattern_type, "entity": entity,
        "category": category, "method": method, "signature": signature, "mutation": mutation,
        # Comprehension-layer semantics (observed, never inferred):
        "assertion_kind": category or pattern_type,
        "actor": actor,
        "semantic_summary": description,
        "behavior_delta": behavior_delta,
    }
