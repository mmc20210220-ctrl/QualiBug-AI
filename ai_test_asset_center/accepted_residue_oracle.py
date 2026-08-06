"""Accepted-residue activation support for the canonical Contract Oracle facade.

Accepted residue is an explicit degradation rung for declared non-production
systems. It proves that cleanup was intentionally not run; it never claims
restoration. The receipt is trusted only with the same evidence boundary used
by the customer-delivery gate.
"""
from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def install_accepted_residue_oracle(core: Any) -> None:
    """Install the residue proof rule on the canonical mechanics authority."""
    current = core.build_contract_oracle_activation_receipt
    if getattr(current, "_qualibug_accepts_residue", False):
        return

    def build_contract_oracle_activation_receipt(
        *, experiment: dict[str, Any], evidence: dict[str, Any]
    ) -> dict[str, Any]:
        activation = current(experiment=experiment, evidence=evidence)
        if _text(activation.get("status")).upper() != "BLOCKED":
            return activation

        valid_receipts: dict[str, str] = {}
        for raw in evidence.get("contract_evidence_receipts") or []:
            if not isinstance(raw, dict):
                continue
            if _text(raw.get("kind")).lower() != "cleanup":
                continue
            if _text(raw.get("status")).upper() != "RESIDUE_ACCEPTED":
                continue
            receipt_evidence = raw.get("evidence") or {}
            if not isinstance(receipt_evidence, dict):
                continue
            if receipt_evidence.get("residue") is not True:
                continue
            if _text(receipt_evidence.get("reason_code")) != "ACCEPTED_RESIDUE_NO_CLEANUP":
                continue
            subject = _text(raw.get("subject_id"))
            receipt_id = _text(raw.get("receipt_id"))
            if subject and receipt_id:
                valid_receipts[subject] = receipt_id

        if not valid_receipts:
            return activation

        removable = {
            f"CLEANUP_RESTORATION_NOT_PROVEN:{subject}"
            for subject in valid_receipts
        }
        reason_codes = [
            _text(code)
            for code in activation.get("reason_codes") or []
            if _text(code) and _text(code) not in removable
        ]
        if len(reason_codes) == len(activation.get("reason_codes") or []):
            return activation

        verified = {
            key: list(values or [])
            for key, values in (activation.get("verified_receipt_ids") or {}).items()
        }
        verified.setdefault("cleanup", [])
        verified["cleanup"] = sorted(
            set([*verified["cleanup"], *valid_receipts.values()])
        )

        payload = {
            key: value
            for key, value in activation.items()
            if key != "receipt_id"
        }
        payload["reason_codes"] = sorted(set(reason_codes))
        payload["verified_receipt_ids"] = verified
        payload["status"] = "BLOCKED" if reason_codes else "ACTIVE"
        return core._content_receipt("activation_", payload)

    build_contract_oracle_activation_receipt._qualibug_accepts_residue = True
    core.build_contract_oracle_activation_receipt = build_contract_oracle_activation_receipt
