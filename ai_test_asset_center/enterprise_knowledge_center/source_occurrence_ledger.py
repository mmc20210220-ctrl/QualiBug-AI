"""Single authority for current source-occurrence state and immutable history."""
from __future__ import annotations

from typing import Any

CURRENT_KEY = "source_occurrence_current"
SNAPSHOT_KEY = "source_occurrence_snapshots"
HISTORY_KEY = "source_occurrence_history"
LEGACY_KEY = "source_occurrences"


class SourceOccurrenceLedgerError(RuntimeError):
    pass


def ensure_ledger(registry: dict[str, Any]) -> dict[str, Any]:
    registry.setdefault(CURRENT_KEY, [])
    registry.setdefault(SNAPSHOT_KEY, [])
    registry.setdefault(HISTORY_KEY, [])
    return registry
