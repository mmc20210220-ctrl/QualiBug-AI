"""Structured runtime binding store (SPEC §22).

A bounded, order-independent container for runtime binding resolutions. The
runtime materializer records each binding it proves against a source response;
this store keeps those proofs as first-class objects with scope isolation
(step vs scenario), idempotent re-resolution, and fail-closed conflict
detection — a second resolution of the same binding name to a different value
is a CONFLICT that never silently overwrites the first.

Industry-neutral: it stores opaque binding names, values, and entity types with
no business vocabulary.
"""
from __future__ import annotations

from typing import Any

RESOLVED = "RESOLVED"
NOT_FOUND = "NOT_FOUND"
CONFLICT = "CONFLICT"
SCOPE_STEP = "step"
SCOPE_SCENARIO = "scenario"
DEFAULT_SCOPE = SCOPE_SCENARIO


class BindingResolution:
    """One binding proof (resolved, not-found, or conflict)."""

    def __init__(
        self,
        binding_name: str,
        status: str,
        *,
        value: Any = "",
        entity_type: str = "",
        identity_field: str = "",
        source_operation: str = "",
        source_response_path: str = "",
        source_status_code: int = 0,
        matched_by: list[str] | None = None,
        confidence: float | None = None,
        scope: str = DEFAULT_SCOPE,
        evidence: dict[str, Any] | None = None,
        failure_detail: str = "",
    ) -> None:
        self.binding_name = binding_name
        self.status = status
        self.value = value
        self.entity_type = entity_type
        self.identity_field = identity_field
        self.source_operation = source_operation
        self.source_response_path = source_response_path
        self.source_status_code = source_status_code
        self.matched_by = list(matched_by or [])
        self.confidence = confidence
        self.scope = scope
        self.evidence = dict(evidence or {})
        self.failure_detail = failure_detail

    @property
    def is_resolved(self) -> bool:
        return self.status == RESOLVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_name": self.binding_name,
            "status": self.status,
            "value": self.value,
            "entity_type": self.entity_type,
            "identity_field": self.identity_field,
            "source_operation": self.source_operation,
            "source_response_path": self.source_response_path,
            "source_status_code": self.source_status_code,
            "matched_by": list(self.matched_by),
            "confidence": self.confidence,
            "scope": self.scope,
            "evidence": dict(self.evidence),
            "failure_detail": self.failure_detail,
        }


class RuntimeBindingStore:
    """Scope-keyed binding resolution store with conflict detection."""

    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], BindingResolution] = {}

    @staticmethod
    def _key(binding_name: str, scope: str | None) -> tuple[str, str]:
        return (scope or DEFAULT_SCOPE, binding_name)

    def resolve(
        self,
        binding_name: str,
        value: Any,
        *,
        entity_type: str = "",
        identity_field: str = "",
        source_operation: str = "",
        source_response_path: str = "",
        source_status_code: int = 0,
        matched_by: list[str] | None = None,
        confidence: float | None = None,
        scope: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> BindingResolution:
        """Record a resolution; idempotent for the same value, conflict otherwise."""
        key = self._key(binding_name, scope)
        existing = self._bindings.get(key)
        if existing is not None and existing.value == value:
            return existing
        if existing is not None:
            return BindingResolution(
                binding_name,
                CONFLICT,
                value=existing.value,
                entity_type=entity_type,
                identity_field=identity_field,
                scope=scope or DEFAULT_SCOPE,
                evidence={"conflicting_value": value},
            )
        resolution = BindingResolution(
            binding_name,
            RESOLVED,
            value=value,
            entity_type=entity_type,
            identity_field=identity_field,
            source_operation=source_operation,
            source_response_path=source_response_path,
            source_status_code=source_status_code,
            matched_by=matched_by,
            confidence=confidence,
            scope=scope or DEFAULT_SCOPE,
            evidence=evidence,
        )
        self._bindings[key] = resolution
        return resolution

    def fail(
        self,
        binding_name: str,
        status: str,
        *,
        entity_type: str = "",
        failure_detail: str = "",
        scope: str | None = None,
    ) -> BindingResolution:
        """Record a non-resolved terminal (NOT_FOUND, etc.)."""
        resolution = BindingResolution(
            binding_name,
            status,
            entity_type=entity_type,
            scope=scope or DEFAULT_SCOPE,
            failure_detail=failure_detail,
        )
        self._bindings[self._key(binding_name, scope)] = resolution
        return resolution

    def get(self, binding_name: str, *, scope: str | None = None) -> BindingResolution | None:
        return self._bindings.get(self._key(binding_name, scope))

    def get_value(self, binding_name: str, *, scope: str | None = None) -> Any:
        resolution = self.get(binding_name, scope=scope)
        return resolution.value if resolution is not None else None

    def snapshot(self) -> dict[str, Any]:
        return {
            "bindings": {
                name if scope == DEFAULT_SCOPE else f"{scope}:{name}": resolution.to_dict()
                for (scope, name), resolution in self._bindings.items()
            }
        }


__all__ = [
    "RESOLVED",
    "NOT_FOUND",
    "CONFLICT",
    "SCOPE_STEP",
    "SCOPE_SCENARIO",
    "DEFAULT_SCOPE",
    "BindingResolution",
    "RuntimeBindingStore",
]
