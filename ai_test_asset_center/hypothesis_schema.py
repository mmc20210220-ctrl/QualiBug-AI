"""Strict, non-throwing normalization for untrusted LLM hypotheses.

A malformed model response is an instrumentation event, never a reason to abort
an execution stage.  The normalizer gives downstream code a stable shape and
explicitly records whether the hypothesis is executable or needs more binding.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HypothesisValidation:
    valid: bool
    normalized: dict[str, Any]
    verdict: str
    errors: list[str]


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "")[:limit]


def _normalize_vm(value: Any) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if value is None:
        return {}, errors
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}, errors
        try:
            value = json.loads(raw)
        except Exception:
            return {}, ["verification_method_string_not_json"]
    if isinstance(value, list):
        valid_items = [item for item in value if isinstance(item, dict)]
        if not valid_items:
            return {}, ["verification_method_list_has_no_object"]
        value = valid_items[0]
    if not isinstance(value, dict):
        return {}, ["verification_method_not_object"]
    normalized = {str(key): item for key, item in value.items()}
    for key in ("method", "path", "step1", "step2", "step3"):
        if key in normalized and normalized[key] is not None:
            normalized[key] = _text(normalized[key], 500)
    if normalized.get("method"):
        normalized["method"] = normalized["method"].upper()
    return normalized, errors


def validate_hypothesis(value: Any) -> HypothesisValidation:
    """Return a stable execution-safe hypothesis without raising."""
    if not isinstance(value, dict):
        return HypothesisValidation(
            valid=False,
            normalized={"hypothesis_id": "?", "title": "invalid hypothesis"},
            verdict="HYPOTHESIS_INVALID",
            errors=["hypothesis_not_object"],
        )

    vm, vm_errors = _normalize_vm(value.get("verification_method"))
    normalized = dict(value)
    normalized["hypothesis_id"] = _text(value.get("hypothesis_id") or value.get("id") or "?", 160)
    normalized["title"] = _text(value.get("title") or value.get("description") or "untitled hypothesis", 500)
    normalized["entity"] = _text(value.get("entity") or value.get("source_entity"), 160)
    normalized["source_entity"] = _text(value.get("source_entity") or normalized["entity"], 160)
    normalized["risk_type"] = _text(value.get("risk_type") or value.get("category") or "unknown", 120)
    normalized["invariant"] = _text(value.get("invariant") or value.get("expected_behavior"), 500)
    normalized["priority"] = _text(value.get("priority") or value.get("severity") or "P2", 32).upper()
    normalized["severity"] = _text(value.get("severity") or normalized["priority"], 32).upper()
    normalized["expected_behavior"] = _text(value.get("expected_behavior") or normalized["invariant"], 500)
    normalized["verification_method"] = vm

    errors = list(vm_errors)
    if not normalized["title"].strip() or normalized["title"] == "untitled hypothesis":
        errors.append("title_missing")
    # Allow more priority formats: numeric (1-5), lowercase, or standard codes
    prio = normalized["priority"].upper().replace("PRIORITY-", "P").replace("PRIORITY", "P")
    # Normalize numeric priorities: 1→P0, 2→P1, 3→P2, etc.
    if prio.isdigit():
        prio = f"P{min(int(prio) - 1, 3)}"
    normalized["priority"] = prio
    normalized["severity"] = normalized["severity"].upper().replace("PRIORITY-", "P").replace("PRIORITY", "P")
    if normalized["severity"].isdigit():
        normalized["severity"] = f"P{min(int(normalized['severity']) - 1, 3)}"
    if prio not in {"P0", "P1", "P2", "P3", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        # Default to P1 instead of marking invalid — execution is more important than strict validation
        normalized["priority"] = "P1"
        normalized["severity"] = "P1"
    # Empty entity is not syntactically invalid: route binding may still use an
    # explicit path.  It must not be lower-cased or used as a guessed identity.
    if not vm and not any(_text(value.get(key)) for key in ("path", "api_path", "endpoint")):
        errors.append("execution_binding_missing")

    if errors:
        verdict = "BLOCKED_BY_BINDING" if errors == ["execution_binding_missing"] else "HYPOTHESIS_INVALID"
        return HypothesisValidation(False, normalized, verdict, errors)
    return HypothesisValidation(True, normalized, "EXECUTABLE", [])
