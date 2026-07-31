"""Read-only semantic view over a ProcessStepLedger.

Execution keeps the live ledger. Finalization receives this view, which
projects facts from receipts that contain both an exact step identity and an
explicit boolean semantic verdict. Transport execution and business completion
remain separate public sets.
"""
from __future__ import annotations

from typing import Any

from .process_step_execution import ProcessStepLedger
from .process_step_semantic_projection import apply_semantic_verdict, project_step_sets


_VERDICT_KEYS = (
    "target_reached",
    "target_state_reached",
    "postcondition_satisfied",
    "state_transition_satisfied",
    "assertion_passed",
    "passed",
    "satisfied",
)
_RECEIPT_ID_KEYS = (
    "receipt_id",
    "observer_receipt_id",
    "oracle_receipt_id",
    "assertion_receipt_id",
)
_STEP_ID_KEYS = ("step_id", "source_step_id", "subject_id")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _receipt_id(row: dict[str, Any]) -> str:
    for key in _RECEIPT_ID_KEYS:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _explicit_verdict(row: dict[str, Any]) -> bool | None:
    evidence = _dict(row.get("evidence"))
    for source in (row, evidence):
        for key in _VERDICT_KEYS:
            value = source.get(key)
            if isinstance(value, bool):
                return value
    return None


def _scoped_step_ids(row: dict[str, Any], known: set[str]) -> list[str]:
    found: list[str] = []
    evidence = _dict(row.get("evidence"))
    for source in (row, evidence):
        for key in _STEP_ID_KEYS:
            value = _text(source.get(key))
            if value in known and value not in found:
                found.append(value)
        for key in ("step_ids", "source_step_ids", "subject_ids"):
            for raw in _list(source.get(key)):
                value = _text(raw)
                if value in known and value not in found:
                    found.append(value)
        for window in _list(source.get("state_windows")):
            value = _text(_dict(window).get("step_id"))
            if value in known and value not in found:
                found.append(value)
    return found


def _candidates(observations: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for row in _list(observations.get("observer_receipts")):
        if isinstance(row, dict):
            out.append(("observer", row))
    for key in ("process_step_oracle_receipts", "oracle_receipts", "assertion_receipts"):
        for row in _list(observations.get(key)):
            if isinstance(row, dict):
                out.append(("oracle", row))
    oracle = _dict(observations.get("oracle_verdict"))
    if oracle:
        out.append(("oracle", oracle))
        for key in ("assertions", "failed_assertions", "field_oracle_traces"):
            for row in _list(oracle.get(key)):
                if isinstance(row, dict):
                    out.append(("oracle", row))
    return out


class ProcessStepSemanticView:
    """Delegate raw ledger facts and expose strict, non-aliased step sets."""

    def __init__(
        self,
        ledger: ProcessStepLedger,
        observations: dict[str, Any] | None = None,
    ):
        self._ledger = ledger
        self._observations = observations if isinstance(observations, dict) else {}
        self._processed_receipts: set[str] = set()

    @property
    def source_ledger(self) -> ProcessStepLedger:
        return self._ledger

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ledger, name)

    def _synchronize(self) -> None:
        known = {
            _text(row.get("step_id"))
            for row in self._ledger.all_rows()
            if isinstance(row, dict) and _text(row.get("step_id"))
        }
        for source, receipt in _candidates(self._observations):
            receipt_id = _receipt_id(receipt)
            if not receipt_id or receipt_id in self._processed_receipts:
                continue
            self._processed_receipts.add(receipt_id)
            verdict = _explicit_verdict(receipt)
            step_ids = _scoped_step_ids(receipt, known)
            if verdict is None or len(step_ids) != 1:
                continue
            apply_semantic_verdict(
                self._ledger,
                step_id=step_ids[0],
                receipt_step_id=step_ids[0],
                receipt_id=receipt_id,
                source=source,
                target_reached=verdict,
            )

    def attempted_step_ids(self) -> list[str]:
        self._synchronize()
        return project_step_sets(self._ledger)["attempted_step_ids"]

    def executed_step_ids(self) -> list[str]:
        self._synchronize()
        return project_step_sets(self._ledger)["executed_step_ids"]

    def completed_step_ids(self) -> list[str]:
        self._synchronize()
        return project_step_sets(self._ledger)["completed_step_ids"]

    def accepted_step_ids(self) -> list[str]:
        self._synchronize()
        return project_step_sets(self._ledger)["accepted_step_ids"]

    def failed_step_ids(self) -> list[str]:
        self._synchronize()
        return project_step_sets(self._ledger)["failed_step_ids"]

    def successful_write_step_ids(self) -> list[str]:
        self._synchronize()
        return self._ledger.successful_write_step_ids()

    def recorded_step_ids(self) -> list[str]:
        return project_step_sets(self._ledger)["recorded_step_ids"]

    def semantic_projection(self) -> dict[str, list[str]]:
        self._synchronize()
        return project_step_sets(self._ledger)
