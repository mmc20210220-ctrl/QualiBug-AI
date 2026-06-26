"""
Phase77: State Observer Registry — Canonical State Snapshots

Captures structured observations of business objects from HTTP responses,
fixtures, or explicit flow variables. Every snapshot preserves entity identity,
source, timing, and a semantic projection of business-relevant fields.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class CanonicalStateSnapshot:
    """A single observation of a business object at a point in time."""

    snapshot_id: str = ""
    entity_type: str = "generic_business_object"
    entity_alias: str = "primary"
    entity_id: str = ""
    correlation_id: str = ""
    tenant_id: str = ""

    source: dict = field(default_factory=lambda: {
        "observer_id": "",
        "source_type": "http_api",  # http_api | event | fixture | static
        "endpoint_or_reference": "",
    })

    observed_at: str = ""
    version: str = ""
    etag: str = ""

    projection: dict = field(default_factory=lambda: {
        "lifecycle_state": None,
        "attributes": {},
        "amounts": {},
        "quantities": {},
        "relations": {},
    })

    raw_payload_hash: str = ""
    raw_status_code: int = 0
    redacted_payload_reference: str = ""

    def __post_init__(self):
        if not self.observed_at:
            self.observed_at = datetime.now(timezone.utc).isoformat()
        if not self.snapshot_id:
            self.snapshot_id = f"snap_{int(time.time()*1000)}_{self.entity_alias}"


class StateObserver:
    """Captures canonical snapshots from HTTP responses and flow context."""

    SENSITIVE_KEYS = {
        "token", "password", "secret", "api_key", "apikey",
        "authorization", "cookie", "set-cookie", "access_token",
        "refresh_token", "private_key", "credential",
    }

    def __init__(self, redact_sensitive: bool = True):
        self.redact_sensitive = redact_sensitive

    def observe_from_http(
        self,
        response_body: dict | None,
        status_code: int,
        endpoint: str,
        observer_id: str = "",
        entity_alias: str = "primary",
        entity_id: str = "",
        correlation_id: str = "",
        tenant_id: str = "",
        version_hint: str = "",
    ) -> CanonicalStateSnapshot:
        """Create a snapshot from an HTTP API response."""
        body = response_body or {}
        redacted = self._redact(body) if self.redact_sensitive else body
        raw_hash = self._hash(json.dumps(body, sort_keys=True, default=str))

        snapshot = CanonicalStateSnapshot(
            entity_alias=entity_alias,
            entity_id=str(entity_id) if entity_id else "",
            correlation_id=str(correlation_id) if correlation_id else "",
            tenant_id=str(tenant_id) if tenant_id else "",
            source={
                "observer_id": observer_id or f"http_{endpoint}",
                "source_type": "http_api",
                "endpoint_or_reference": endpoint,
            },
            version=version_hint or "",
            projection={
                "lifecycle_state": None,
                "attributes": {},
                "amounts": {},
                "quantities": {},
                "relations": {},
            },
            raw_payload_hash=raw_hash,
            raw_status_code=status_code,
            redacted_payload_reference=f"snapshot://{raw_hash[:16]}",
        )
        return snapshot

    def observe_from_fixture(
        self,
        fixture_data: dict,
        entity_alias: str = "primary",
        fixture_name: str = "",
    ) -> CanonicalStateSnapshot:
        """Create a snapshot from fixture/preloaded data."""
        redacted = self._redact(fixture_data) if self.redact_sensitive else fixture_data
        raw_hash = self._hash(json.dumps(fixture_data, sort_keys=True, default=str))

        return CanonicalStateSnapshot(
            entity_alias=entity_alias,
            source={
                "observer_id": f"fixture_{fixture_name}",
                "source_type": "fixture",
                "endpoint_or_reference": fixture_name,
            },
            projection={
                "lifecycle_state": None,
                "attributes": {},
                "amounts": {},
                "quantities": {},
                "relations": {},
            },
            raw_payload_hash=raw_hash,
            redacted_payload_reference=f"fixture://{fixture_name}",
        )

    def observe_from_flow_context(
        self,
        flow_context: dict,
        entity_alias: str = "primary",
    ) -> CanonicalStateSnapshot:
        """Create a snapshot from flow execution context (variable bindings)."""
        raw_hash = self._hash(json.dumps(flow_context, sort_keys=True, default=str))

        return CanonicalStateSnapshot(
            entity_alias=entity_alias,
            entity_id=str(flow_context.get("entity_id", "")),
            correlation_id=str(flow_context.get("correlation_id", "")),
            source={
                "observer_id": "flow_context",
                "source_type": "static",
                "endpoint_or_reference": "flow_variable_binding",
            },
            projection={
                "lifecycle_state": flow_context.get("lifecycle_state"),
                "attributes": flow_context.get("attributes", {}),
                "amounts": flow_context.get("amounts", {}),
                "quantities": flow_context.get("quantities", {}),
                "relations": flow_context.get("relations", {}),
            },
            raw_payload_hash=raw_hash,
            redacted_payload_reference=f"flow://{raw_hash[:16]}",
        )

    def apply_projection(
        self,
        snapshot: CanonicalStateSnapshot,
        projection_map: dict,
        response_body: dict | None = None,
    ) -> CanonicalStateSnapshot:
        """Apply a field projection map to populate lifecycle_state, amounts, etc."""
        from .state_projection_engine import StateProjectionEngine  # late import to avoid circular

        engine = StateProjectionEngine()
        proj = snapshot.projection.copy()

        if response_body:
            # lifecycle_state
            if "lifecycle_state" in projection_map:
                path = projection_map["lifecycle_state"]
                proj["lifecycle_state"] = engine.extract(response_body, path)

            # amounts
            for field_name, path in projection_map.get("amounts", {}).items():
                val = engine.extract(response_body, path)
                if val is not None:
                    proj["amounts"][field_name] = engine.to_number(val)

            # quantities
            for field_name, path in projection_map.get("quantities", {}).items():
                val = engine.extract(response_body, path)
                if val is not None:
                    proj["quantities"][field_name] = engine.to_number(val)

            # attributes
            for field_name, path in projection_map.get("attributes", {}).items():
                val = engine.extract(response_body, path)
                if val is not None:
                    proj["attributes"][field_name] = val

            # relations (e.g., parent_id, order_id from response)
            for field_name, path in projection_map.get("relations", {}).items():
                val = engine.extract(response_body, path)
                if val is not None:
                    proj["relations"][field_name] = str(val)

        # Assign entity_id from projection if not already set
        if not snapshot.entity_id and projection_map.get("entity_id"):
            eid = engine.extract(response_body or {}, projection_map["entity_id"])
            if eid:
                snapshot.entity_id = str(eid)

        snapshot.projection = proj
        return snapshot

    # ── helpers ──

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    @classmethod
    def _redact(cls, data: dict) -> dict:
        """Recursively remove sensitive keys from a dict."""
        if not isinstance(data, dict):
            return data
        result = {}
        for k, v in data.items():
            lower = k.lower().replace("-", "_").replace(" ", "_")
            if any(sk in lower for sk in cls.SENSITIVE_KEYS):
                result[k] = "[REDACTED]"
            elif isinstance(v, dict):
                result[k] = cls._redact(v)
            elif isinstance(v, list):
                result[k] = [cls._redact(i) if isinstance(i, dict) else i for i in v]
            else:
                result[k] = v
        return result


# ── snapshot comparison ──

def snapshot_diff(
    before: CanonicalStateSnapshot,
    after: CanonicalStateSnapshot,
    fields: list[str] | None = None,
) -> dict:
    """Compare two snapshots and return structured diff."""
    diffs = {}

    # entity identity
    if before.entity_id and after.entity_id and before.entity_id != after.entity_id:
        diffs["entity_id_changed"] = {"before": before.entity_id, "after": after.entity_id}

    # lifecycle_state
    if not fields or "lifecycle_state" in fields:
        bs = before.projection.get("lifecycle_state")
        as_ = after.projection.get("lifecycle_state")
        if bs != as_:
            diffs["lifecycle_state"] = {"before": bs, "after": as_}

    # amounts
    for key in before.projection.get("amounts", {}):
        if fields and f"amounts.{key}" not in fields:
            continue
        bv = before.projection["amounts"].get(key)
        av = after.projection["amounts"].get(key)
        if bv != av:
            try:
                delta = (av or 0) - (bv or 0)
            except Exception:
                delta = None
            diffs[f"amounts.{key}"] = {"before": bv, "after": av, "delta": delta}

    # quantities
    for key in before.projection.get("quantities", {}):
        if fields and f"quantities.{key}" not in fields:
            continue
        bv = before.projection["quantities"].get(key)
        av = after.projection["quantities"].get(key)
        if bv != av:
            try:
                delta = (av or 0) - (bv or 0)
            except Exception:
                delta = None
            diffs[f"quantities.{key}"] = {"before": bv, "after": av, "delta": delta}

    # attributes
    for key in before.projection.get("attributes", {}):
        if fields and f"attributes.{key}" not in fields:
            continue
        bv = before.projection["attributes"].get(key)
        av = after.projection["attributes"].get(key)
        if bv != av:
            diffs[f"attributes.{key}"] = {"before": bv, "after": av}

    # payload hash
    if before.raw_payload_hash != after.raw_payload_hash:
        diffs["payload_changed"] = True

    return diffs
