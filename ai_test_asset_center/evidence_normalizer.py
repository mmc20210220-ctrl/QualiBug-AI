"""Evidence Normalizer — real call receipts to source-neutral runtime evidence.

The normalizer does not invent tenant, actor, entity or snapshots.  Every
binding must be visible in the execution receipt; otherwise it remains missing
and the existing gate keeps the finding out of customer delivery.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Iterable

from .evidence_models import RawProbeEvidence, NormalizedRuntimeEvidence, SemanticVerificationEvidence, SourcedValue

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_ID_KEY_RE = re.compile(r"(?:^|_)(?:id|uuid|identifier|key)$|(?:id|uuid)$", re.I)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _call_text(call: dict[str, Any]) -> str:
    value = str(call.get("call") or "").strip()
    if value:
        return value
    request = _as_dict(call.get("request"))
    method = str(request.get("method") or call.get("method") or "").upper()
    path = str(request.get("path") or request.get("url") or call.get("path") or "")
    return f"{method} {path}".strip()


def _method_path(call: dict[str, Any]) -> tuple[str, str]:
    text = _call_text(call)
    parts = text.split(None, 1)
    if len(parts) == 2:
        return parts[0].upper(), parts[1]
    request = _as_dict(call.get("request"))
    return str(request.get("method") or call.get("method") or "").upper(), str(request.get("path") or request.get("url") or call.get("path") or "")


def _iter_scalars(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _iter_scalars(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value[:50]):
            yield from _iter_scalars(item, f"{prefix}[{index}]")
    elif value not in (None, "", [], {}):
        yield prefix, value


def _body_from_result(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body")
    if isinstance(body, dict):
        return body
    response = _as_dict(result.get("response"))
    return response.get("body") if isinstance(response.get("body"), dict) else {}


def _result_rows(call: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    results = _as_dict(call.get("results"))
    rows = [(str(name), value) for name, value in results.items() if isinstance(value, dict)]
    if rows:
        return rows
    response = _as_dict(call.get("response"))
    if response:
        return [(str(call.get("actor") or call.get("actor_id") or "execution"), response)]
    return []


def _primary_result(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    rows = _result_rows(call)
    if not rows:
        return "", {}
    explicit = str(call.get("primary_actor") or call.get("actor") or call.get("actor_id") or "")
    for label, row in rows:
        if explicit and label == explicit:
            return label, row
    return rows[0]


def _header_value(call: dict[str, Any], *names: str) -> tuple[Any, str]:
    wanted = {name.lower().replace("-", "_") for name in names}
    for container_name, container in (("request.headers", _as_dict(_as_dict(call.get("request")).get("headers"))), ("headers", _as_dict(call.get("headers")))):
        for key, value in container.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in wanted and value not in (None, ""):
                return value, f"{container_name}.{key}"
    for label, result in _result_rows(call):
        headers = _as_dict(result.get("headers"))
        for key, value in headers.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in wanted and value not in (None, ""):
                return value, f"results.{label}.headers.{key}"
    return None, ""


def _payload_value(call: dict[str, Any], predicate) -> tuple[Any, str]:
    request = _as_dict(call.get("request"))
    containers: list[tuple[str, Any]] = [("request.body", request.get("body")), ("call.body", call.get("body"))]
    for label, result in _result_rows(call):
        containers.append((f"results.{label}.body", _body_from_result(result)))
    for prefix, body in containers:
        for path, value in _iter_scalars(body):
            final_key = path.rsplit(".", 1)[-1].split("[", 1)[0]
            if predicate(final_key, value):
                return value, f"{prefix}.{path}" if path else prefix
    return None, ""


def _source_value(value: Any, source: str, confidence: str = "evidenced") -> SourcedValue:
    return SourcedValue(value, source, confidence) if value not in (None, "") else SourcedValue.missing(source or "not_observed")


def _resource_entity(path: str) -> str:
    segments = [item for item in str(path or "").split("?", 1)[0].strip("/").split("/") if item and not item.startswith("{") and not item.startswith(":")]
    while segments and (segments[0].lower() == "api" or re.fullmatch(r"v\d+", segments[0].lower())):
        segments.pop(0)
    return segments[0].rstrip("s") if segments else ""


def _snapshot(call: dict[str, Any], label: str, role: str, result: dict[str, Any]) -> dict[str, Any]:
    method, path = _method_path(call)
    return {"receipt_ref": _call_text(call), "method": method, "path": path, "observer": role, "status": int(result.get("status") or result.get("status_code") or 0), "body_snapshot": _body_from_result(result), "source": label}


class EvidenceNormalizer:
    VERDICT_TO_SEMANTIC = {
        "confirmed": "SEMANTIC_CONFIRMED",
        "falsified": "SEMANTIC_FALSIFIED",
        "inconclusive": "SEMANTIC_INCONCLUSIVE",
        "execution_error": "SEMANTIC_ERROR",
        "needs_more_evidence": "SEMANTIC_PENDING",
        "validated_candidate": "SEMANTIC_CONFIRMED",
    }

    def build_raw_probe_evidences(self, calls: list[dict], run_id: str = "", hypothesis_id: str = "") -> list[RawProbeEvidence]:
        items: list[RawProbeEvidence] = []
        for index, call in enumerate(calls or []):
            actor, result = _primary_result(call)
            method, path = _method_path(call)
            request = _as_dict(call.get("request"))
            items.append(RawProbeEvidence(
                run_id=run_id or str(call.get("run_id") or ""),
                hypothesis_id=hypothesis_id,
                flow_id=str(call.get("flow_id") or ""),
                probe_id=str(call.get("probe_id") or f"receipt_{index + 1}"),
                request={"method": method, "path": path, "actor_ref": actor, "raw_call": _call_text(call), **request},
                response=_body_from_result(result),
                status_code=int(result.get("status") or result.get("status_code") or 0),
                headers=_as_dict(result.get("headers")),
                body=_body_from_result(result),
                timestamp=float(call.get("timestamp") or time.time()),
                duration_ms=float(call.get("duration_ms") or 0),
                error=str(result.get("error") or result.get("_error") or call.get("error") or ""),
                raw_call_refs=[_call_text(call)],
            ))
        return items

    def normalize(self, finding: Any, calls: list[dict], hypothesis: dict | None = None) -> NormalizedRuntimeEvidence:
        ev = NormalizedRuntimeEvidence()
        calls = [item for item in (calls or []) if isinstance(item, dict)]
        ev.raw_evidence_refs = [_call_text(call) for call in calls[:50] if _call_text(call)]
        ev.call_chain_refs = list(ev.raw_evidence_refs)
        first = calls[0] if calls else {}
        method, path = _method_path(first)
        ev.method = _source_value(method, "receipt[0].request.method")
        ev.resource_path = _source_value(path, "receipt[0].request.path")
        entity_type = _resource_entity(path)
        ev.entity_type = _source_value(entity_type, "receipt[0].request.path.resource")

        entity_id, entity_source = _payload_value(first, lambda key, value: bool(_ID_KEY_RE.search(str(key))) and isinstance(value, (str, int)) and not isinstance(value, bool))
        if entity_id in (None, ""):
            for call in calls:
                candidate, source = _payload_value(call, lambda key, value: bool(_ID_KEY_RE.search(str(key))) and isinstance(value, (str, int)) and not isinstance(value, bool))
                if candidate not in (None, ""):
                    entity_id, entity_source = candidate, source
                    break
        ev.entity_id = _source_value(entity_id, entity_source or "entity_identifier_not_observed")

        tenant, tenant_source = _payload_value(first, lambda key, value: any(part in str(key).lower() for part in ("tenant", "workspace", "organization", "org")) and isinstance(value, (str, int)))
        ev.tenant_id = _source_value(tenant, tenant_source or "scope_not_observed")
        owner, owner_source = _payload_value(first, lambda key, value: "owner" in str(key).lower() and isinstance(value, (str, int)))
        ev.owner_id = _source_value(owner, owner_source or "owner_not_observed")
        actor, actor_source = _payload_value(first, lambda key, value: str(key).lower() in {"actor", "actor_id", "actorid", "principal", "principal_id", "subject", "subject_id"} and isinstance(value, (str, int)))
        if actor in (None, ""):
            label, _ = _primary_result(first)
            actor, actor_source = (label, "receipt[0].result_label") if label else (None, "actor_not_observed")
            ev.actor_id = _source_value(actor, actor_source, "inferred" if actor else "missing")
        else:
            ev.actor_id = _source_value(actor, actor_source)

        request_id, request_source = _header_value(first, "x-request-id", "request-id")
        trace_id, trace_source = _header_value(first, "x-trace-id", "trace-id", "traceparent")
        correlation_id, correlation_source = _header_value(first, "x-correlation-id", "correlation-id")
        idem_key, idem_source = _header_value(first, "idempotency-key", "x-idempotency-key")
        ev.request_id = _source_value(request_id, request_source or "request_id_not_observed")
        ev.trace_id = _source_value(trace_id, trace_source or "trace_id_not_observed")
        ev.correlation_id = _source_value(correlation_id, correlation_source or "correlation_id_not_observed")
        ev.idempotency_key = _source_value(idem_key, idem_source or "idempotency_key_not_observed")

        action_index = next((index for index, call in enumerate(calls) if _method_path(call)[0] in _WRITE_METHODS), None)
        if action_index is not None:
            action = calls[action_index]
            ev.action_ref = _source_value(_call_text(action), f"receipt[{action_index}]")
            before = next((calls[index] for index in range(action_index - 1, -1, -1) if _method_path(calls[index])[0] in {"GET", "HEAD"}), None)
            after = next((calls[index] for index in range(action_index + 1, len(calls)) if _method_path(calls[index])[0] in {"GET", "HEAD"}), None)
            if before:
                label, result = _primary_result(before)
                if result:
                    ev.before_candidates.append(_snapshot(before, f"receipt[{calls.index(before)}]", label, result))
            if after:
                label, result = _primary_result(after)
                if result:
                    ev.after_candidates.append(_snapshot(after, f"receipt[{calls.index(after)}]", label, result))
        for index, call in enumerate(calls):
            method, path = _method_path(call)
            for label, result in _result_rows(call):
                ev.observer_refs.append({"receipt_index": index, "observer": label, "method": method, "path": path, "status": int(result.get("status") or result.get("status_code") or 0), "receipt_ref": _call_text(call)})
        ev.timing_window = {"total_receipts": len(calls), "write_receipt_index": action_index, "normalized_at": time.time()}
        if action_index is not None and ev.entity_id.confidence == "missing":
            ev.missing_requirements.append("ENTITY_BINDING_MISSING")
        if action_index is not None and not ev.before_candidates:
            ev.missing_requirements.append("BEFORE_SNAPSHOT_MISSING")
        if action_index is not None and not ev.after_candidates:
            ev.missing_requirements.append("AFTER_SNAPSHOT_MISSING")
        return ev

    def normalize_semantic(self, finding: Any) -> SemanticVerificationEvidence:
        source = finding if isinstance(finding, dict) else finding.__dict__ if hasattr(finding, "__dict__") else {}
        raw_verdict = str(source.get("verdict") or "inconclusive")
        evidence = _as_dict(source.get("evidence"))
        semantic = SemanticVerificationEvidence()
        semantic.semantic_verdict = self.VERDICT_TO_SEMANTIC.get(raw_verdict, "SEMANTIC_INCONCLUSIVE")
        semantic._original_verdict = raw_verdict
        semantic.expected_behavior = str(source.get("expected") or source.get("expected_behavior") or "")
        semantic.observed_behavior = str(source.get("actual") or source.get("actual_behavior") or "")
        semantic.violated_invariant = str(evidence.get("violated_invariant") or source.get("violated_invariant") or "")
        semantic.verifier_rule = str(evidence.get("verifier_rule") or "")
        try:
            semantic.semantic_confidence = float(source.get("confidence") or 0)
        except (TypeError, ValueError):
            semantic.semantic_confidence = 0.0
        semantic.verifier_trace_ref = str(evidence.get("verifier_trace_ref") or f"stage_verify:{time.time()}:{raw_verdict}")
        return semantic


def normalize_finding_evidence(finding: Any, calls: list[dict] | None = None, hypothesis: dict | None = None) -> dict[str, Any]:
    normalizer = EvidenceNormalizer()
    calls = calls or []
    source = finding if isinstance(finding, dict) else finding.__dict__ if hasattr(finding, "__dict__") else {}
    hypothesis_id = str(source.get("hypothesis_id") or "")
    run_id = str(source.get("run_id") or "")
    runtime = normalizer.normalize(finding, calls, hypothesis)
    semantic = normalizer.normalize_semantic(finding)
    raw_probes = normalizer.build_raw_probe_evidences(calls, run_id=run_id, hypothesis_id=hypothesis_id)
    return {"runtime": runtime.to_dict(), "semantic": semantic.to_dict(), "raw_probes": [item.to_dict() for item in raw_probes], "normalized_at": time.time()}
