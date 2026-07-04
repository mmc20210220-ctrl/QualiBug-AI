"""
RiskCluePool — Save blocked/inconclusive findings for future investigation.

Stores findings that couldn't be confirmed due to:
- blocked_requires_sandbox: mutating probe needed
- inconclusive: evidence insufficient
- need_auth: authentication required
- need_data: test data missing
- need_state: target not in correct state

These clues are re-evaluated on subsequent scans when conditions change.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def save_risk_clues(
    project: str,
    root: Path,
    findings: list[dict[str, Any]],
    *,
    max_clues: int = 500,
) -> dict[str, Any]:
    """Save unconfirmed findings as risk clues for future investigation."""
    pool_dir = root / "platform_outputs" / project / "risk_clue_pool"
    pool_dir.mkdir(parents=True, exist_ok=True)
    pool_file = pool_dir / "risk_clues.json"

    # Load existing pool
    existing: dict[str, dict[str, Any]] = {}
    if pool_file.exists():
        try:
            data = json.loads(pool_file.read_text(encoding="utf-8"))
            existing = data.get("clues", {})
        except Exception:
            pass

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    new_count = 0

    for f in findings:
        verdict = str(f.get("verdict", "")).lower()
        status = str(f.get("validation_status", "")).lower()
        title = str(f.get("title", ""))

        # Determine if this is a "clue" worth saving
        clue_reason = _classify_clue(f, verdict, status)
        if not clue_reason:
            continue

        clue_key = _clue_key(title)
        if clue_key in existing:
            # Update existing clue with new scan data
            entry = existing[clue_key]
            entry["seen_count"] = entry.get("seen_count", 1) + 1
            entry["last_seen_utc"] = now
            entry["clue_reason"] = clue_reason
        else:
            existing[clue_key] = {
                "title": title,
                "severity": f.get("severity", "P2"),
                "category": f.get("category", "unknown"),
                "source": f.get("source", "unknown"),
                "clue_reason": clue_reason,
                "first_seen_utc": now,
                "last_seen_utc": now,
                "seen_count": 1,
                "evidence_snippet": str(f.get("evidence", ""))[:500],
                "description": str(f.get("description", ""))[:500],
            }
            new_count += 1

    # Trim to max_clues, keeping most frequently seen
    sorted_clues = sorted(
        existing.values(),
        key=lambda c: (c.get("seen_count", 0), c.get("last_seen_utc", "")),
        reverse=True,
    )[:max_clues]

    clues_dict = {_clue_key(c["title"]): c for c in sorted_clues}

    pool_data = {
        "phase": "risk_clue_pool_v1",
        "project": project,
        "updated_at_utc": now,
        "total_clues": len(clues_dict),
        "new_this_scan": new_count,
        "clues": clues_dict,
    }

    pool_file.write_text(json.dumps(pool_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"total_clues": len(clues_dict), "new_this_scan": new_count}


def get_risk_clues(project: str, root: Path) -> dict[str, Any]:
    """Retrieve risk clues for a project."""
    pool_file = root / "platform_outputs" / project / "risk_clue_pool" / "risk_clues.json"
    if not pool_file.exists():
        return {"total_clues": 0, "clues": {}}
    try:
        return json.loads(pool_file.read_text(encoding="utf-8"))
    except Exception:
        return {"total_clues": 0, "clues": {}}


def _classify_clue(finding: dict[str, Any], verdict: str, status: str) -> str:
    """Classify why this finding should be saved as a clue."""
    if verdict in ("inconclusive", "blocked"):
        return "inconclusive"
    if "sandbox" in status or "blocked_requires_sandbox" in status:
        return "blocked_requires_sandbox"
    if "auth" in status or "blocked_missing" in status:
        return "need_auth"
    if "data" in status or "missing_test_data" in status:
        return "need_data"
    if "state" in status:
        return "need_state"
    if verdict == "falsified":
        return ""  # Don't save falsified findings
    # Save any inconclusive/blocked/unmapped finding
    if status.startswith("blocked_"):
        return "blocked"
    if verdict == "needs_more_evidence":
        return "needs_more_evidence"
    return ""


def _clue_key(title: str) -> str:
    """Generate a stable key from finding title."""
    import hashlib
    return hashlib.md5(title.strip().lower().encode()).hexdigest()[:16]
