from __future__ import annotations

from typing import Any


def required_receipt_checks(method: str) -> list[str]:
    checks = ["document_grounding", "executed_request", "runtime_receipt", "assertion"]
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        checks.append("before_after_observation")
    return checks


def missing_receipt_checks(receipts: dict[str, Any], method: str) -> list[str]:
    return [name for name in required_receipt_checks(method) if not bool(receipts.get(name))]
