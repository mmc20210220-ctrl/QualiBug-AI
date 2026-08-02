"""Bridge runtime-registered observer receipts into the formal Oracle input.

``observe_experiment_requirements`` can dispatch a registered non-HTTP observer and return a
valid typed receipt.  The Finalizer historically copied receipt evidence into ``observations``
only for a hardcoded list of built-in HTTP observers.  A registered observer therefore
executed, appeared in the receipt bundle, and its registered assertion kind still saw no
input.  This module closes that shared bridge without weakening receipt validation.

Only evidence from an ``OBSERVED`` typed receipt is copied.  Conflicting keys are removed and
recorded rather than overwritten, so two observers cannot silently race to define one Oracle
fact.  The sealed receipt itself is never mutated.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from . import experiment_outcome_finalizer as _finalizer
from . import observer_contracts_base as _observers

_INSTALL_MARKER = "_qualibug_registered_observer_evidence_bridge_installed"
_ORIGINAL_MARKER = "_qualibug_original_observe_experiment_requirements"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def _merge_registered_receipt_evidence(
    observations: dict[str, Any],
    receipts: list[dict[str, Any]],
) -> None:
    conflicts = [
        dict(row)
        for row in _list(observations.get("registered_observer_evidence_conflicts"))
        if isinstance(row, dict)
    ]
    for receipt in receipts:
        row = _dict(receipt)
        observer_id = _text(row.get("observer_id"))
        contract = _dict(_observers.OBSERVER_REGISTRY.get(observer_id))
        if not observer_id or contract.get("registered_at_runtime") is not True:
            continue
        if _text(row.get("status")).upper() != "OBSERVED":
            continue

        evidence = _dict(row.get("evidence"))
        observations[observer_id + "_observer_receipt"] = copy.deepcopy(row)
        for key, value in evidence.items():
            evidence_key = _text(key)
            if not evidence_key:
                continue
            if evidence_key not in observations or observations.get(evidence_key) in (
                None,
                {},
                [],
                "",
            ):
                observations[evidence_key] = copy.deepcopy(value)
                continue
            if _fingerprint(observations.get(evidence_key)) == _fingerprint(value):
                continue

            # A collision is not a winner-takes-all situation.  Remove the disputed fact so
            # any assertion requiring it becomes INDETERMINATE, and retain only fingerprints
            # for diagnosis rather than copying potentially sensitive values again.
            existing = observations.pop(evidence_key, None)
            conflicts.append({
                "observer_id": observer_id,
                "evidence_key": evidence_key,
                "existing_fingerprint": _fingerprint(existing),
                "incoming_fingerprint": _fingerprint(value),
                "reason_code": "REGISTERED_OBSERVER_EVIDENCE_CONFLICT",
            })
    if conflicts:
        observations["registered_observer_evidence_conflicts"] = conflicts


def install_registered_observer_evidence_bridge() -> None:
    """No-op since the registered-observer evidence merge became first-class.

    ``observer_contracts_base.observe_experiment_requirements`` now merges OBSERVED
    evidence from runtime-registered observers into the observations dict inside the
    dispatch authority itself. This installer previously replaced the finalizer's
    imported dispatcher; that method replacement is retired. Kept as an idempotent
    no-op so a stale caller cannot re-introduce the wrapper.
    """
    return None


__all__ = [
    "install_registered_observer_evidence_bridge",
]
