"""Read-only semantic view over a ProcessStepLedger.

Execution code keeps the live ledger instance. Finalization receives this view,
which delegates raw facts while projecting completion-related step sets from
explicit semantic receipts.
"""
from __future__ import annotations

from typing import Any

from .process_step_execution import ProcessStepLedger
from .process_step_semantic_projection import project_step_sets


class ProcessStepSemanticView:
    """Delegate ledger facts and expose strict semantic step sets."""

    def __init__(self, ledger: ProcessStepLedger):
        self._ledger = ledger

    @property
    def source_ledger(self) -> ProcessStepLedger:
        return self._ledger

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ledger, name)

    def executed_step_ids(self) -> list[str]:
        return project_step_sets(self._ledger)["completed_step_ids"]

    def failed_step_ids(self) -> list[str]:
        return project_step_sets(self._ledger)["failed_step_ids"]

    def successful_write_step_ids(self) -> list[str]:
        return project_step_sets(self._ledger)["accepted_step_ids"]

    def recorded_step_ids(self) -> list[str]:
        return project_step_sets(self._ledger)["recorded_step_ids"]

    def semantic_projection(self) -> dict[str, list[str]]:
        return project_step_sets(self._ledger)
