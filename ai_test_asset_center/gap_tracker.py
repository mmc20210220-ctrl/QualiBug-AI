"""Gap Tracker — Cross-scan capability gap state tracking.

Tracks each capability gap's lifecycle: open → in_progress → resolved → (possibly) reopened.
Persists state to platform_outputs/_benchmark/ for cross-scan continuity.

Design principles (per AGENTS.md):
  - No fake data: state transitions only happen when preflight actually changes
  - Observable: every state change is logged to a JSONL ledger
  - Pure JSON output: no HTML beautification
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ═════════════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════════════

GAP_TRACKER_VERSION = "gap_tracker.v1"
MAX_HISTORY_ENTRIES = 500


class GapState(str, Enum):
    """Lifecycle states for a capability gap."""
    OPEN = "open"               # Detected, not yet addressed
    IN_PROGRESS = "in_progress"  # Customer is working on it
    RESOLVED = "resolved"        # Preflight now passes for this gap
    REOPENED = "reopened"        # Was resolved, now failing again
    BLOCKED = "blocked"          # Cannot be resolved (e.g., production target)
    DISMISSED = "dismissed"      # Acknowledged but intentionally not fixed


# ═════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class GapRecord:
    """A single gap's tracked state over time."""
    gap_id: str
    root_cause: str
    state: GapState
    priority: str
    summary: str
    first_seen_at: str
    last_updated_at: str
    resolved_at: str | None = None
    reopened_count: int = 0
    affected_families: list[str] = field(default_factory=list)
    config_task_title: str = ""
    state_history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GapSnapshot:
    """Snapshot of all gaps at one point in time."""
    snapshot_id: str
    timestamp: str
    project_id: str
    total_gaps: int
    open_count: int
    resolved_count: int
    blocked_count: int
    gaps: list[GapRecord]


# ═════════════════════════════════════════════════════════════════════════════
# Gap Tracker
# ═════════════════════════════════════════════════════════════════════════════

class GapTracker:
    """Track capability gaps across scan runs.

    Usage::

        tracker = GapTracker("my_project")
        tracker.record_gaps(new_gaps)
        tracker.mark_resolved("GAP-...")
        snapshot = tracker.current_snapshot()
    """

    def __init__(
        self,
        project_id: str = "default",
        *,
        root: str | Path | None = None,
    ) -> None:
        self.project_id = project_id

        if root is None:
            root = Path(os.environ.get(
                "QUALIBUG_WORKSPACE_ROOT",
                str(Path(__file__).resolve().parents[1])
            ))
        self.root = Path(root)

        # Storage paths
        self._tracker_dir = self.root / "platform_outputs" / "_benchmark"
        self._tracker_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._tracker_dir / f"gap_tracker_{project_id}.json"
        self._ledger_path = self._tracker_dir / f"gap_ledger_{project_id}.jsonl"

        # In-memory state
        self._gaps: dict[str, GapRecord] = self._load_state()

    # ── Recording ───────────────────────────────────────────────────────

    def record_gaps(
        self,
        gaps: list[Any],  # CapabilityGap from capability_gap_resolver
    ) -> dict[str, Any]:
        """Record newly detected gaps, merging with existing state.

        Args:
            gaps: List of CapabilityGap objects from CapabilityGapResolver.

        Returns:
            Summary of changes (new, updated, resolved).
        """
        from .capability_gap_resolver import CapabilityGap

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        new_count = 0
        updated_count = 0
        resolved_count = 0

        # Build set of incoming gap root causes
        incoming_causes: set[str] = set()

        for gap in gaps:
            if isinstance(gap, CapabilityGap):
                cause = gap.root_cause.value
            elif isinstance(gap, dict):
                cause = str(gap.get("root_cause") or gap.get("gap_id", ""))
            else:
                continue

            incoming_causes.add(cause)

            if cause in self._gaps:
                existing = self._gaps[cause]
                if existing.state == GapState.RESOLVED:
                    # Was resolved, now detected again → reopened
                    existing.state = GapState.REOPENED
                    existing.reopened_count += 1
                    existing.last_updated_at = timestamp
                    existing.state_history.append({
                        "from": "resolved", "to": "reopened", "at": timestamp,
                    })
                    updated_count += 1
                    self._append_ledger("gap_reopened", cause, {"reopen_count": existing.reopened_count})
                elif existing.state != GapState.OPEN:
                    # Update existing open gap
                    existing.last_updated_at = timestamp
                    updated_count += 1
            else:
                # New gap
                if isinstance(gap, CapabilityGap):
                    record = GapRecord(
                        gap_id=gap.gap_id,
                        root_cause=cause,
                        state=GapState.OPEN,
                        priority=gap.priority,
                        summary=gap.summary,
                        first_seen_at=timestamp,
                        last_updated_at=timestamp,
                        affected_families=gap.affected_defect_families,
                        config_task_title=gap.config_task.title if gap.config_task else "",
                        state_history=[{"from": "none", "to": "open", "at": timestamp}],
                    )
                else:
                    record = GapRecord(
                        gap_id=str(gap.get("gap_id", cause)),
                        root_cause=cause,
                        state=GapState.OPEN,
                        priority=str(gap.get("priority", "P1")),
                        summary=str(gap.get("summary", "")),
                        first_seen_at=timestamp,
                        last_updated_at=timestamp,
                        state_history=[{"from": "none", "to": "open", "at": timestamp}],
                    )
                self._gaps[cause] = record
                new_count += 1
                self._append_ledger("gap_detected", cause, {"priority": record.priority})

        # Mark gaps as resolved if they were open but no longer detected
        for cause, record in self._gaps.items():
            if cause not in incoming_causes and record.state in (GapState.OPEN, GapState.IN_PROGRESS, GapState.REOPENED):
                record.state = GapState.RESOLVED
                record.resolved_at = timestamp
                record.last_updated_at = timestamp
                record.state_history.append({
                    "from": record.state.value, "to": "resolved", "at": timestamp,
                })
                resolved_count += 1
                self._append_ledger("gap_resolved", cause, {})

        self._persist_state()

        return {
            "new_gaps": new_count,
            "updated_gaps": updated_count,
            "resolved_gaps": resolved_count,
            "total_tracked": len(self._gaps),
        }

    def mark_resolved(self, gap_id: str) -> bool:
        """Manually mark a gap as resolved (e.g., after customer configures)."""
        for cause, record in self._gaps.items():
            if record.gap_id == gap_id or cause == gap_id:
                if record.state == GapState.RESOLVED:
                    return True  # Already resolved
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                record.state = GapState.RESOLVED
                record.resolved_at = timestamp
                record.last_updated_at = timestamp
                record.state_history.append({
                    "from": record.state.value, "to": "resolved", "at": timestamp,
                })
                self._persist_state()
                self._append_ledger("gap_manually_resolved", cause, {})
                return True
        return False

    def mark_blocked(self, gap_id: str, reason: str = "") -> bool:
        """Mark a gap as permanently blocked."""
        for cause, record in self._gaps.items():
            if record.gap_id == gap_id or cause == gap_id:
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                record.state = GapState.BLOCKED
                record.last_updated_at = timestamp
                record.summary = reason or record.summary
                record.state_history.append({
                    "from": record.state.value, "to": "blocked", "at": timestamp,
                })
                self._persist_state()
                self._append_ledger("gap_blocked", cause, {"reason": reason})
                return True
        return False

    # ── Querying ────────────────────────────────────────────────────────

    def current_snapshot(self) -> GapSnapshot:
        """Return a snapshot of current gap state."""
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        open_gaps = [g for g in self._gaps.values() if g.state in (GapState.OPEN, GapState.IN_PROGRESS, GapState.REOPENED)]
        resolved = [g for g in self._gaps.values() if g.state == GapState.RESOLVED]
        blocked = [g for g in self._gaps.values() if g.state == GapState.BLOCKED]

        return GapSnapshot(
            snapshot_id=f"SNAP-{self.project_id}-{int(time.time())}",
            timestamp=timestamp,
            project_id=self.project_id,
            total_gaps=len(self._gaps),
            open_count=len(open_gaps),
            resolved_count=len(resolved),
            blocked_count=len(blocked),
            gaps=list(self._gaps.values()),
        )

    def get_open_gaps(self) -> list[GapRecord]:
        """Return currently open gaps."""
        return [
            g for g in self._gaps.values()
            if g.state in (GapState.OPEN, GapState.IN_PROGRESS, GapState.REOPENED)
        ]

    def get_resolved_gaps(self) -> list[GapRecord]:
        """Return resolved gaps."""
        return [g for g in self._gaps.values() if g.state == GapState.RESOLVED]

    def get_new_gaps_since(self, since_timestamp: str) -> list[GapRecord]:
        """Return gaps first seen after a given timestamp."""
        return [
            g for g in self._gaps.values()
            if g.first_seen_at > since_timestamp
        ]

    def get_reopened_gaps(self) -> list[GapRecord]:
        """Return gaps that were resolved but reopened."""
        return [g for g in self._gaps.values() if g.state == GapState.REOPENED]

    # ── Summary ─────────────────────────────────────────────────────────

    def build_summary(self) -> dict[str, Any]:
        """Build a JSON-safe summary for dashboard / scan output."""
        snapshot = self.current_snapshot()
        by_cause: dict[str, dict[str, Any]] = {}
        for gap in self._gaps.values():
            entry = by_cause.setdefault(gap.root_cause, {
                "root_cause": gap.root_cause,
                "state": gap.state.value,
                "first_seen": gap.first_seen_at,
                "resolved_at": gap.resolved_at,
                "reopen_count": gap.reopened_count,
            })
            # If there are multiple gaps for the same cause, keep the most severe
            if gap.priority == "P0" and entry.get("priority") != "P0":
                entry["priority"] = gap.priority
                entry["state"] = gap.state.value

        open_gaps = self.get_open_gaps()

        return {
            "schema_version": GAP_TRACKER_VERSION,
            "project_id": self.project_id,
            "generated_at": snapshot.timestamp,
            "total_gaps_ever": snapshot.total_gaps,
            "currently_open": snapshot.open_count,
            "resolved": snapshot.resolved_count,
            "blocked": snapshot.blocked_count,
            "reopened_count": len(self.get_reopened_gaps()),
            "open_gaps": [
                {
                    "gap_id": g.gap_id,
                    "root_cause": g.root_cause,
                    "state": g.state.value,
                    "priority": g.priority,
                    "summary": g.summary,
                    "first_seen": g.first_seen_at,
                    "reopened_count": g.reopened_count,
                    "config_task": g.config_task_title,
                }
                for g in open_gaps
            ],
            "by_root_cause": by_cause,
        }

    # ── Persistence ─────────────────────────────────────────────────────

    def _load_state(self) -> dict[str, GapRecord]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8") or "{}")
            gaps_raw = data.get("gaps", {}) if isinstance(data, dict) else {}
            result: dict[str, GapRecord] = {}
            for cause, raw in gaps_raw.items():
                if not isinstance(raw, dict):
                    continue
                result[cause] = GapRecord(
                    gap_id=raw.get("gap_id", ""),
                    root_cause=raw.get("root_cause", cause),
                    state=GapState(raw.get("state", "open")),
                    priority=raw.get("priority", "P1"),
                    summary=raw.get("summary", ""),
                    first_seen_at=raw.get("first_seen_at", ""),
                    last_updated_at=raw.get("last_updated_at", ""),
                    resolved_at=raw.get("resolved_at"),
                    reopened_count=raw.get("reopened_count", 0),
                    affected_families=raw.get("affected_families", []),
                    config_task_title=raw.get("config_task_title", ""),
                    state_history=raw.get("state_history", []),
                )
            return result
        except Exception:
            return {}

    def _persist_state(self) -> None:
        payload = {
            "schema_version": GAP_TRACKER_VERSION,
            "project_id": self.project_id,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "gaps": {
                cause: {
                    "gap_id": g.gap_id,
                    "root_cause": g.root_cause,
                    "state": g.state.value,
                    "priority": g.priority,
                    "summary": g.summary,
                    "first_seen_at": g.first_seen_at,
                    "last_updated_at": g.last_updated_at,
                    "resolved_at": g.resolved_at,
                    "reopened_count": g.reopened_count,
                    "affected_families": g.affected_families,
                    "config_task_title": g.config_task_title,
                    "state_history": g.state_history[-20:],  # Keep last 20 transitions
                }
                for cause, g in self._gaps.items()
            },
        }
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _append_ledger(self, event: str, cause: str, extra: dict[str, Any]) -> None:
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "project_id": self.project_id,
            "event": event,
            "root_cause": cause,
            **extra,
        }
        with open(self._ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
