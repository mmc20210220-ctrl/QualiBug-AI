"""Test Obligation model — minimal planning unit for discovery.

Obligations are compiled from Behavior IR only. They never embed customer
instance answers or benchmark ground truth.
"""
from __future__ import annotations

import hashlib
from typing import Any


SCHEMA_VERSION = "qualibug.test-obligation.v1"
RISK_FAMILIES = (
    "authorization",
    "isolation",
    "state",
    "conservation",
    "idempotency",
    "concurrency",
    "validation",
    "visibility",
    "temporal",
    "privacy",
)
COMPILE_STATUSES = ("PENDING", "COMPILED", "BLOCKED", "UNSUPPORTED")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def stable_obligation_id(*parts: Any) -> str:
    raw = "|".join(_text(p) for p in parts if _text(p))
    return f"obl_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def make_obligation(
    *,
    risk_family: str,
    subject_refs: list[str],
    property_spec: dict[str, Any],
    required_actors: list[str] | None = None,
    required_operations: list[str] | None = None,
    required_fixtures: list[str] | None = None,
    required_observers: list[str] | None = None,
    cleanup_requirement: dict[str, Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    relation_refs: list[str] | None = None,
    confidence: float = 0.5,
    compile_status: str = "PENDING",
    obligation_id: str | None = None,
) -> dict[str, Any]:
    family = _text(risk_family).lower()
    if family not in RISK_FAMILIES:
        family = "validation"
    status = compile_status if compile_status in COMPILE_STATUSES else "PENDING"
    oid = _text(obligation_id) or stable_obligation_id(
        family,
        ",".join(sorted(_text(x) for x in subject_refs if _text(x))),
        json_fingerprint(property_spec),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "obligation_id": oid,
        "risk_family": family,
        "subject_refs": [ _text(x) for x in subject_refs if _text(x) ],
        "property": dict(property_spec or {}),
        "required_actors": [ _text(x) for x in (required_actors or []) if _text(x) ],
        "required_operations": [ _text(x) for x in (required_operations or []) if _text(x) ],
        "required_fixtures": [ _text(x) for x in (required_fixtures or []) if _text(x) ],
        "required_observers": [ _text(x) for x in (required_observers or []) if _text(x) ],
        "cleanup_requirement": dict(cleanup_requirement or {}),
        "source_refs": list(source_refs or []),
        "relation_refs": [_text(x) for x in (relation_refs or []) if _text(x)],
        "confidence": max(0.0, min(1.0, float(confidence))),
        "compile_status": status,
    }


def json_fingerprint(value: Any) -> str:
    import json

    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def dedupe_obligations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _text(item.get("obligation_id")) or json_fingerprint(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
