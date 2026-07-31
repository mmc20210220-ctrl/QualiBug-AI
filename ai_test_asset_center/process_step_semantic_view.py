"""Semantic finalization view over a ProcessStepLedger.

Execution keeps the live ledger. Finalization receives this view, which binds
observation, oracle, and cleanup receipts only when each receipt declares one
exact recorded step. It also normalizes an absent optional fixture identity to
the formal ``NOT_APPLICABLE`` value before any receipt bundle is sealed.
Semantic completion additionally requires an explicit boolean verdict.
Transport execution and business completion remain separate.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .process_step_execution import ProcessStepLedger
from .process_step_receipt_scope import (
    extract_receipt_step_scope,
    receipt_id as _scope_receipt_id,
    synchronize_scoped_receipts_from_observations,
)
from .process_step_semantic_projection import apply_semantic_verdict, project_step_sets


_NOT_APPLICABLE = "NOT_APPLICABLE"
_VERDICT_KEYS = (
    "target_reached",
    "target_state_reached",
    "postcondition_satisfied",
    "state_transition_satisfied",
    "assertion_passed",
    "passed",
    "satisfied",
)
_LEGACY_UNSCOPED_EVIDENCE_FIELDS = frozenset(
    {
        "observer_receipt_ids",
        "observation_receipt_ids",
        "oracle_receipt_ids",
        "cleanup_receipt_ids",
    }
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _explicit_verdict(row: dict[str, Any]) -> bool | None:
    payload = _dict(row.get("payload"))
    sources = (
        row,
        payload,
        _dict(row.get("evidence")),
        _dict(payload.get("evidence")),
        _dict(row.get("verdict")),
        _dict(payload.get("verdict")),
    )
    for source in sources:
        for key in _VERDICT_KEYS:
            value = source.get(key)
            if isinstance(value, bool):
                return value
    return None


def _candidates(observations: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for key in (
        "observer_receipts",
        "observation_receipts",
        "process_step_observation_receipts",
    ):
        for row in _list(observations.get(key)):
            if isinstance(row, dict):
                out.append(("observer", row))
    for key in (
        "process_step_oracle_receipts",
        "oracle_invocation_receipts",
        "oracle_receipts",
        "assertion_receipts",
    ):
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
    """Expose strict, non-aliased step sets for final receipt sealing."""

    def __init__(
        self,
        ledger: ProcessStepLedger,
        observations: dict[str, Any] | None = None,
    ):
        self._ledger = ledger
        self._observations = observations if isinstance(observations, dict) else {}
        self._processed_receipts: set[str] = set()
        self._normalize_optional_fixture_identity()

    def _normalize_optional_fixture_identity(self) -> None:
        """Seal fixture absence as an identity value, never an empty coordinate.

        A fixtureless experiment is valid when its treatment creates disposable
        state and its cleanup removes it. Canonical Receipt Envelopes require all
        identity coordinates to be explicit, using ``NOT_APPLICABLE`` for an
        optional coordinate. The execution ledger was historically created with
        an empty fixture id, which made its payload disagree with the canonical
        bundle envelope and made the reconstructed ledger hash diverge.

        Normalization happens once, before this finalization view publishes any
        process-step receipt. Existing real fixture identities are untouched.
        """
        if _text(getattr(self._ledger, "fixture_id", "")):
            return
        self._ledger.fixture_id = _NOT_APPLICABLE
        raw_rows = getattr(self._ledger, "_rows", {})
        if isinstance(raw_rows, dict):
            for row in raw_rows.values():
                if isinstance(row, dict) and not _text(row.get("fixture_id")):
                    row["fixture_id"] = _NOT_APPLICABLE
        seed = "|".join(
            [
                _text(getattr(self._ledger, "experiment_id", "")),
                _NOT_APPLICABLE,
                _text(getattr(self._ledger, "campaign_id", "")),
                _text(getattr(self._ledger, "run_id", "")),
                _text(getattr(self._ledger, "obligation_id", "")),
                _text(getattr(self._ledger, "protocol_id", "")),
            ]
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        self._ledger.ledger_id = f"psl_{digest}"
        # ``attach_lifecycle_ledger`` populated these keys before wrapping the
        # ledger. Publish the normalized identity immediately so the Finalizer's
        # first live-vs-observed identity check sees one authority, not a stale
        # pre-normalization snapshot.
        self._observations.update(
            {
                "process_step_ledger_id": self._ledger.ledger_id,
                "process_step_ledger_hash": self._ledger.compute_hash(),
                "process_step_receipts": list(self._ledger.all_rows()),
            }
        )

    @property
    def source_ledger(self) -> ProcessStepLedger:
        return self._ledger

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ledger, name)

    def append_receipt_ref(
        self,
        step_id: str,
        field: str,
        receipt_id: str,
    ) -> bool:
        """Keep legacy Finalizer broadcasts from mutating exact-scope authority.

        The old Finalizer still calls ``append_receipt_ref`` for observation,
        oracle and cleanup evidence after evaluating a result. The underlying
        ledger intentionally records those calls as identity violations because
        the receipt did not repeat its own step identity. This semantic view
        already synchronizes exact-scoped receipts through
        ``append_scoped_receipt_ref``; therefore the legacy broadcast is a
        compatibility no-op, not a second authority and not a rejection event.
        Scalar transport/state receipts continue to delegate unchanged.
        """
        if _text(field) in _LEGACY_UNSCOPED_EVIDENCE_FIELDS:
            return False
        return self._ledger.append_receipt_ref(step_id, field, receipt_id)

    def _synchronize(self) -> None:
        synchronize_scoped_receipts_from_observations(
            self._ledger,
            self._observations,
        )
        known = list(self._ledger.recorded_step_ids())
        for source, receipt in _candidates(self._observations):
            rid = _scope_receipt_id(receipt)
            if not rid or rid in self._processed_receipts:
                continue
            self._processed_receipts.add(rid)
            verdict = _explicit_verdict(receipt)
            scope = extract_receipt_step_scope(receipt, known_step_ids=known)
            if verdict is None or scope.get("status") != "EXACT":
                continue
            step_id = _text(scope.get("step_id"))
            apply_semantic_verdict(
                self._ledger,
                step_id=step_id,
                receipt_step_id=step_id,
                receipt_id=rid,
                source=source,
                target_reached=verdict,
            )

    def _projection(self) -> dict[str, list[str]]:
        self._synchronize()
        return project_step_sets(self._ledger)

    def _publish_receipt_declarations(self) -> tuple[list[dict[str, Any]], str]:
        """Publish reconstructable ledger declarations on every receipt."""

        projection = self._projection()
        fact_snapshot = self._ledger.build_fact_snapshot()
        ledger_hash = self._ledger.compute_hash()
        declaration = {
            "ledger_recorded_step_ids": list(projection["recorded_step_ids"]),
            "ledger_required_step_ids": list(fact_snapshot["required_step_ids"]),
            "ledger_attempted_step_ids": list(fact_snapshot["attempted_step_ids"]),
            "ledger_executed_step_ids": list(fact_snapshot["executed_step_ids"]),
            "ledger_accepted_step_ids": list(fact_snapshot["accepted_step_ids"]),
            "ledger_completed_step_ids": list(fact_snapshot["completed_step_ids"]),
            "ledger_failed_step_ids": list(fact_snapshot["failed_step_ids"]),
            "ledger_pending_semantic_step_ids": list(
                projection["pending_semantic_step_ids"]
            ),
            "ledger_receipt_scope_rejections": list(
                fact_snapshot["receipt_scope_rejections"]
            ),
            "ledger_receipt_count": len(projection["recorded_step_ids"]),
        }
        rows = [{**row, **declaration} for row in self._ledger.all_rows()]
        self._observations.update(
            {
                "process_step_ledger_id": self._ledger.ledger_id,
                "process_step_ledger_hash": ledger_hash,
                "process_step_receipts": rows,
                "required_step_ids": list(self._ledger.required_step_ids),
                "recorded_step_ids": list(projection["recorded_step_ids"]),
                "attempted_step_ids": list(projection["attempted_step_ids"]),
                "executed_step_ids": list(projection["executed_step_ids"]),
                "accepted_step_ids": list(projection["accepted_step_ids"]),
                "completed_step_ids": list(projection["completed_step_ids"]),
                "failed_step_ids": list(projection["failed_step_ids"]),
            }
        )
        return rows, ledger_hash

    def compute_hash(self) -> str:
        """Seal only after all exact-scoped semantic receipts are projected."""
        _, ledger_hash = self._publish_receipt_declarations()
        return ledger_hash

    @property
    def ledger_hash(self) -> str:
        return self.compute_hash()

    def all_rows(self) -> list[dict[str, Any]]:
        rows, _ = self._publish_receipt_declarations()
        return rows

    def to_authority_dict(self) -> dict[str, Any]:
        self._publish_receipt_declarations()
        return self._ledger.to_authority_dict()

    def attempted_step_ids(self) -> list[str]:
        return self._projection()["attempted_step_ids"]

    def executed_step_ids(self) -> list[str]:
        return self._projection()["executed_step_ids"]

    def completed_step_ids(self) -> list[str]:
        return self._projection()["completed_step_ids"]

    def accepted_step_ids(self) -> list[str]:
        return self._projection()["accepted_step_ids"]

    def failed_step_ids(self) -> list[str]:
        return self._projection()["failed_step_ids"]

    def successful_write_step_ids(self) -> list[str]:
        self._synchronize()
        return self._ledger.successful_write_step_ids()

    def recorded_step_ids(self) -> list[str]:
        return self._projection()["recorded_step_ids"]

    def semantic_projection(self) -> dict[str, list[str]]:
        return self._projection()
