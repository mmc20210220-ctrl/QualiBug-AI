from __future__ import annotations

"""Phase92R: semantic joiner for multi-observer before/after snapshots.

Phase92Q can capture several read-only observer responses around one write
probe: primary object detail, inventory projection, ledger/history, tenant scope
views, and idempotency collections.  This module turns those heterogeneous
responses into a deterministic business-object graph so the Phase92P invariant
adjudicator can reason across all observed projections instead of only the first
payload.

The implementation is intentionally evidence-first and local:
* it consumes only runtime snapshot responses already captured by the executor;
* no oracle/seed/ground-truth files are read;
* records are linked by observed business identifiers such as id/order_id/sku_id,
  business_key/idempotency_key, tenant_id/user_id/owner_id;
* output is plain JSON so it can be embedded in runtime evidence reports.
"""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

ID_PATH_RE = re.compile(
    r"(?:^|[._\[\]])(?:id|uuid|code|number|no|key|"
    r"order_id|order_no|payment_id|transaction_id|ledger_id|event_id|external_event_id|"
    r"business_key|idempotency_key|request_id|resource_id|object_id|sku_id|product_id|"
    r"tenant_id|org_id|owner_id|owner_user_id|user_id|account_id|merchant_id|shop_id|"
    r"业务单号|单号|租户|组织|归属|用户|商户)(?:$|[._\[\]])",
    re.I,
)
VOLATILE_PATH_RE = re.compile(r"(?:trace|request_id|duration|timestamp|created_at|updated_at|time|date|nonce|random|签名|日志|log)", re.I)
ENVELOPE_LIST_KEYS = ("records", "items", "list", "rows", "results", "content", "data")
ENVELOPE_OBJECT_KEYS = ("data", "result", "payload", "record", "item", "detail")
JOIN_FIELD_PRIORITY = (
    "order_id", "order_no", "business_key", "idempotency_key", "external_event_id", "event_id",
    "payment_id", "transaction_id", "ledger_id", "sku_id", "product_id", "tenant_id",
    "org_id", "owner_user_id", "owner_id", "user_id", "account_id", "merchant_id",
    "shop_id", "resource_id", "object_id", "uuid", "code", "number", "no", "id", "key",
)


@dataclass
class _Entity:
    phase: str
    local_key: str
    semantic_key: str
    observer_kind: str
    path: str
    status_code: Any
    record_index: int
    fields: dict[str, Any]
    join_keys: dict[str, str] = field(default_factory=dict)

    def as_record(self, cluster_key: str = "") -> dict[str, Any]:
        return {
            "entity_key": self.semantic_key,
            "cluster_key": cluster_key or self.semantic_key,
            "observer_kind": self.observer_kind,
            "path": self.path,
            "status_code": self.status_code,
            "record_index": self.record_index,
            "join_keys": self.join_keys,
            "fields": self.fields,
        }


def _payload_from_snapshot(item: dict[str, Any]) -> Any:
    response = item.get("response") if isinstance(item.get("response"), dict) else {}
    return response.get("payload")


def _flatten(value: Any, prefix: str = "", *, max_items: int = 30) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(child, path, max_items=max_items))
        return out
    if isinstance(value, list):
        out[prefix + ".__len__" if prefix else "__len__"] = len(value)
        for idx, child in enumerate(value[:max_items]):
            path = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            out.update(_flatten(child, path, max_items=max_items))
        return out
    out[prefix or "$"] = value
    return out


def _field_name(path: str) -> str:
    last = re.split(r"[.\[\]]+", str(path or ""))[-1]
    return re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff]+", "_", last).strip("_").lower() or "value"


def _stable_scalar(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    text = str(value).strip()
    if not text or len(text) > 160:
        return ""
    return text


def _extract_join_keys(record: dict[str, Any]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for path, value in _flatten(record).items():
        if VOLATILE_PATH_RE.search(path):
            continue
        if not ID_PATH_RE.search(path):
            continue
        scalar = _stable_scalar(value)
        if not scalar:
            continue
        field = _field_name(path)
        # Preserve the first occurrence of each semantic field; nested duplicate
        # IDs often repeat the same business key in envelopes and child objects.
        keys.setdefault(field, scalar)
    return keys


def _canonical_entity_key(join_keys: dict[str, str], observer_kind: str, path: str, idx: int, record: dict[str, Any]) -> str:
    for field_name in JOIN_FIELD_PRIORITY:
        if field_name in join_keys:
            return f"{field_name}:{join_keys[field_name]}"
    if join_keys:
        field_name = sorted(join_keys)[0]
        return f"{field_name}:{join_keys[field_name]}"
    fallback = f"{observer_kind}|{path}|{idx}"
    digest = hashlib.sha1(str(sorted(_flatten(record).items())[:20]).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"anonymous:{fallback}:{digest}"


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if payload in (None, {}, []):
        return []
    if isinstance(payload, list):
        return [x if isinstance(x, dict) else {"value": x} for x in payload[:100]]
    if not isinstance(payload, dict):
        return [{"value": payload}]

    # Prefer business collection envelopes over the outer wrapper.
    for key in ENVELOPE_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [x if isinstance(x, dict) else {"value": x} for x in value[:100]]
        if key == "data" and isinstance(value, dict):
            nested = _records_from_payload(value)
            if nested:
                return nested

    # Prefer common object envelopes when they look like wrappers.
    for key in ENVELOPE_OBJECT_KEYS:
        value = payload.get(key)
        if isinstance(value, dict) and value is not payload:
            nested = _records_from_payload(value)
            if nested:
                return nested
    return [payload]


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _cluster_key(member_entities: list[_Entity]) -> str:
    merged: dict[str, str] = {}
    for entity in member_entities:
        merged.update(entity.join_keys)
    for field_name in JOIN_FIELD_PRIORITY:
        if field_name in merged:
            return f"{field_name}:{merged[field_name]}"
    if merged:
        field_name = sorted(merged)[0]
        return f"{field_name}:{merged[field_name]}"
    return sorted(e.semantic_key for e in member_entities)[0]


def _phase_items(snapshots: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    raw = snapshots.get(phase) if isinstance(snapshots, dict) else []
    if isinstance(raw, dict):
        raw = [raw]
    return [x for x in (raw or []) if isinstance(x, dict)]


def _entities_for_phase(snapshots: dict[str, Any], phase: str) -> list[_Entity]:
    entities: list[_Entity] = []
    for observer_idx, item in enumerate(_phase_items(snapshots, phase)):
        response = item.get("response") if isinstance(item.get("response"), dict) else {}
        status = response.get("status_code")
        try:
            if status is not None and not (200 <= int(status) < 300):
                continue
        except Exception:
            continue
        payload = _payload_from_snapshot(item)
        kind = str(item.get("observer_kind") or "unclassified_observer")
        path = str(item.get("path") or "")
        for idx, record in enumerate(_records_from_payload(payload)):
            if not isinstance(record, dict):
                record = {"value": record}
            join_keys = _extract_join_keys(record)
            semantic_key = _canonical_entity_key(join_keys, kind, path, idx, record)
            local_key = f"{phase}:{observer_idx}:{idx}:{semantic_key}"
            entities.append(_Entity(
                phase=phase,
                local_key=local_key,
                semantic_key=semantic_key,
                observer_kind=kind,
                path=path,
                status_code=status,
                record_index=idx,
                fields=record,
                join_keys=join_keys,
            ))
    entities.sort(key=lambda e: (e.semantic_key, e.observer_kind, e.path, e.record_index))
    return entities


def _build_clusters(entities: list[_Entity]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    uf = _UnionFind()
    by_join_value: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entity in entities:
        uf.add(entity.local_key)
        for field_name, value in entity.join_keys.items():
            by_join_value[(field_name, value)].append(entity.local_key)
    for local_keys in by_join_value.values():
        if len(local_keys) < 2:
            continue
        first = local_keys[0]
        for other in local_keys[1:]:
            uf.union(first, other)

    grouped: dict[str, list[_Entity]] = defaultdict(list)
    by_local = {e.local_key: e for e in entities}
    for local_key in by_local:
        grouped[uf.find(local_key)].append(by_local[local_key])

    local_to_cluster: dict[str, str] = {}
    clusters: list[dict[str, Any]] = []
    for members in grouped.values():
        key = _cluster_key(members)
        join_keys: dict[str, list[str]] = defaultdict(list)
        for member in members:
            local_to_cluster[member.local_key] = key
            for field_name, value in member.join_keys.items():
                if value not in join_keys[field_name]:
                    join_keys[field_name].append(value)
        clusters.append({
            "cluster_key": key,
            "member_entity_keys": sorted({m.semantic_key for m in members}),
            "observer_kinds": sorted({m.observer_kind for m in members}),
            "join_keys": {k: sorted(v) for k, v in sorted(join_keys.items())},
        })
    clusters.sort(key=lambda x: str(x.get("cluster_key") or ""))
    return local_to_cluster, clusters


def _joined_payload(entities: list[_Entity], local_to_cluster: dict[str, str], clusters: list[dict[str, Any]]) -> dict[str, Any]:
    records = [e.as_record(local_to_cluster.get(e.local_key, e.semantic_key)) for e in entities]
    records.sort(key=lambda r: (str(r.get("cluster_key") or ""), str(r.get("entity_key") or ""), str(r.get("observer_kind") or ""), str(r.get("path") or ""), int(r.get("record_index") or 0)))
    return {
        "records": records,
        "clusters": clusters,
        "entity_count": len(records),
        "cluster_count": len(clusters),
        "observer_kinds": sorted({str(r.get("observer_kind")) for r in records if r.get("observer_kind")}),
    }


def _fingerprints(payload: dict[str, Any]) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        key = str(record.get("entity_key") or "") + "|" + str(record.get("observer_kind") or "") + "|" + str(record.get("path") or "") + "|" + str(record.get("record_index") or 0)
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        digest = hashlib.sha1(str(sorted(_flatten(fields).items())).encode("utf-8", errors="ignore")).hexdigest()
        fingerprints[key] = digest
    return fingerprints


def join_snapshot_observer_responses(snapshots: dict[str, Any]) -> dict[str, Any]:
    """Return a business-object graph synthesized from before/after snapshots."""
    before_entities = _entities_for_phase(snapshots, "before")
    after_entities = _entities_for_phase(snapshots, "after")
    all_entities = before_entities + after_entities
    local_to_cluster, clusters = _build_clusters(all_entities)
    before_payload = _joined_payload(before_entities, local_to_cluster, clusters)
    after_payload = _joined_payload(after_entities, local_to_cluster, clusters)

    before_fp = _fingerprints(before_payload)
    after_fp = _fingerprints(after_payload)
    before_keys = set(before_fp)
    after_keys = set(after_fp)
    changed = sorted(k for k in (before_keys & after_keys) if before_fp.get(k) != after_fp.get(k))

    return {
        "engine": "observer_response_semantic_joiner_v1_phase92r",
        "joined_payloads": {"before": before_payload, "after": after_payload},
        "observer_response_counts": {
            "before": len(_phase_items(snapshots, "before")),
            "after": len(_phase_items(snapshots, "after")),
        },
        "entity_counts": {"before": len(before_payload.get("records") or []), "after": len(after_payload.get("records") or [])},
        "cluster_count": len(clusters),
        "clusters": clusters[:50],
        "added_entity_fingerprints": sorted(after_keys - before_keys)[:50],
        "removed_entity_fingerprints": sorted(before_keys - after_keys)[:50],
        "changed_entity_fingerprints": changed[:50],
        "join_key_fields": sorted({field for entity in all_entities for field in entity.join_keys}),
        "coverage": sorted({e.observer_kind for e in all_entities if e.observer_kind}),
        "snapshot_evidence_present": bool(before_entities and after_entities),
    }
