"""
Source-contract auto-derivation (four-link breadth closure, 档位 C).

The four-link reachability chain (obligation risk family -> assertion kind ->
observer -> experiment protocol) is fully installed for performance
(``performance_latency``), stability (``stability_reliability``), event
(``event_delivery_consistency``) and UI (``ui_state_consistency``) — but each
class was reachable only when the *source material* declared a formal contract
in a specific JSON shape.  This module closes that gap: it detects the
contracts that ALREADY exist in the source text (operation descriptions, PRD /
API-spec statements), anchors every derived contract to a verbatim quote, and
writes normalized rows into the asset so the existing binder -> obligation ->
protocol -> observer chain becomes reachable without a manually declared JSON
contract.

Discipline (identical to the regex rule-candidate layer):
- Derivation is extraction, never inference.  A contract is emitted only when
  the source text states an explicit, numeric/structural requirement
  (latency budget with a value, error-rate/availability percentage, or an
  event statement naming path + event type + fields).  Vague statements
  ("接口必须稳定") produce nothing.
- Every contract carries ``source_refs`` with a verbatim ``quote`` that is a
  real substring of the source text, plus an exact operation identity
  (method + path) and an exact actor identity resolved from the runtime actor
  registry.  Anything that cannot be bound exactly is skipped with a receipt
  entry — never guessed.
- No defaults masquerade as business facts: measurement methodology
  (sample counts, percentile, observation window) is explicit, fixed,
  documented in the receipt, and marked ``methodology_default``.  The
  business-relevant value (latency ms, error-rate %, event type/fields)
  always comes verbatim from the source.
- UI is deliberately excluded: an executable Playwright plan (steps, start
  URL, interaction cleanup) cannot be derived from free text without
  inventing steps, which the evidence rules prohibit; structured UI contract
  extraction already exists for declared UI material.
- Fail-closed and observable: every skip carries a reason code; the whole
  pass emits ``qualibug.contract-auto-derivation.v1`` and can be disabled by
  operator policy or ``QUALIBUG_DISABLE_CONTRACT_AUTO_DERIVATION=1``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

DERIVATION_SCHEMA = "qualibug.contract-auto-derivation.v1"

# Methodology defaults (product-owned measurement configuration, not business
# facts).  Documented here and repeated in every receipt entry so auto-derived
# contracts are never mistaken for source-declared ones.
_PERF_SAMPLE_COUNT = 5
_PERF_WARMUP_COUNT = 1
_PERF_PERCENTILE = "p95"
_STABILITY_SAMPLE_COUNT = 10
_EVENT_MIN_COUNT = 1
_EVENT_MAX_COUNT = 1
_EVENT_WINDOW_MS = 10_000

_MAX_CONTRACTS_PER_KIND = 8
_MAX_QUOTE_CHARS = 400

_SAFE_METHODS = frozenset({"GET", "HEAD"})
_EVENT_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

_PATH_RE = re.compile(r"(?:GET|POST|PUT|PATCH|DELETE)\s+(/[\w\-/{}.]+(?:\?[\w\-=&]+)?)")
_BARE_PATH_RE = re.compile(r"(/[\w\-/{}.]+)")
_SNAKE_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{1,40}")

# ---------------------------------------------------------------------------
# Latency statements (performance contracts)
# ---------------------------------------------------------------------------

_LATENCY_PLAIN = re.compile(
    r"(?:响应时间|响应耗时|延迟|时延|耗时|latency|response\s*time|RT)\s*"
    r"(?:不超过|不高于|小于|低于|不得高于|不得低于|应小于|应低于|≤|<=|<|within|under|"
    r"must\s+not\s+exceed|should\s+not\s+exceed|must\s+be\s+(?:under|below|within)|"
    r"should\s+be\s+(?:under|below|within))\s*"
    r"(\d+(?:\.\d+)?)\s*(ms|毫秒|秒|s)\b",
    re.IGNORECASE,
)
_LATENCY_PERCENTILE = re.compile(
    r"\b(P50|P90|P95|P99|p50|p90|p95|p99)\s*(?:响应时间|响应耗时|延迟|时延|latency|response\s*time)?\s*"
    r"(?:不超过|不高于|小于|低于|≤|<=|<|within|under)\s*"
    r"(\d+(?:\.\d+)?)\s*(ms|毫秒|秒|s)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Stability statements (error-rate / availability)
# ---------------------------------------------------------------------------

_STABILITY_ERROR_RATE = re.compile(
    r"(?:错误率|失败率|error\s*rate|failure\s*rate|fault\s*rate)\s*"
    r"(?:不超过|不高于|小于|低于|≤|<=|<|must\s+not\s+exceed|should\s+not\s+exceed)\s*"
    r"(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
_STABILITY_AVAILABILITY = re.compile(
    r"(?:可用性|可用率|availability)\s*"
    r"(?:不低于|大于|高于|≥|>=|>|must\s+be\s+(?:at\s+least|above|over)|should\s+be\s+(?:at\s+least|above|over))\s*"
    r"(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Event statements (strict: path + event type + fields must all be named)
# ---------------------------------------------------------------------------

_EVENT_KIND_HINT = re.compile(
    r"(?:事件|消息|通知|回调|webhook|event|message|notification|queue|mq|kafka|"
    r"事件流|消息队列)",
    re.IGNORECASE,
)
_EVENT_TYPE_QUOTED = re.compile(
    r"[\"']([a-zA-Z][\w.]*(?:\.[\w.]+)*)[\"']\s*(?:事件|消息|通知|event|message)",
    re.IGNORECASE,
)
_EVENT_TYPE_UNQUOTED = re.compile(
    r"([a-zA-Z][\w]*\.[\w.]+)\s*(?:事件|消息|通知|event|message)",
    re.IGNORECASE,
)
_EVENT_TYPE_NAMED = re.compile(
    r"(?:事件类型|event\s*type|type)\s*[:=]\s*([a-zA-Z][\w.]*(?:\.[\w.]+)*)",
    re.IGNORECASE,
)
_EVENT_FIELDS = re.compile(
    r"(?:字段|包含|含|包括|fields?|contains?|with)\s*[:：]?\s*"
    r"([\w_]+(?:\s*[、,，\s]\s*[\w_]+){1,12})",
    re.IGNORECASE,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _digest(*parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _quote_hash(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()[:24]


def _anchor(quote: str, text: str) -> bool:
    """Verbatim anchoring: the quote must be a real substring of the source."""
    return bool(quote) and quote in text


def _bound_quote(match: re.Match, text: str) -> str:
    start = max(0, match.start() - 60)
    end = min(len(text), match.end() + 30)
    return text[start:end].strip()[: _MAX_QUOTE_CHARS]


def _unit_to_ms(value: float, unit: str) -> float:
    unit = _text(unit).lower()
    if unit in {"s", "秒"}:
        return value * 1000.0
    return value  # ms / 毫秒


# ---------------------------------------------------------------------------
# Statement extraction
# ---------------------------------------------------------------------------


def _latency_claims(text: str) -> list[dict[str, Any]]:
    """All explicit latency budgets in one text, each with verbatim quote."""
    claims: list[dict[str, Any]] = []
    for match in _LATENCY_PLAIN.finditer(text):
        claims.append({
            "quote": _bound_quote(match, text),
            "max_latency_ms": _unit_to_ms(float(match.group(1)), match.group(2)),
            "percentile": _PERF_PERCENTILE,
        })
    for match in _LATENCY_PERCENTILE.finditer(text):
        claims.append({
            "quote": _bound_quote(match, text),
            "max_latency_ms": _unit_to_ms(float(match.group(2)), match.group(3)),
            "percentile": _text(match.group(1)).lower(),
        })
    return claims


def _stability_claims(text: str) -> list[dict[str, Any]]:
    """Explicit error-rate / availability requirements, verbatim anchored."""
    claims: list[dict[str, Any]] = []
    for match in _STABILITY_ERROR_RATE.finditer(text):
        claims.append({
            "quote": _bound_quote(match, text),
            "error_rate_pct": float(match.group(1)),
        })
    for match in _STABILITY_AVAILABILITY.finditer(text):
        availability = float(match.group(1))
        if availability <= 0 or availability >= 100:
            continue
        claims.append({
            "quote": _bound_quote(match, text),
            "error_rate_pct": round(100.0 - availability, 4),
        })
    return claims


def _event_fields_named(statement: str) -> list[str]:
    """Snake-case field identifiers named in a fields/contains clause."""
    match = _EVENT_FIELDS.search(statement)
    if not match:
        return []
    segment = match.group(1)
    tokens = _SNAKE_TOKEN_RE.findall(segment)
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return seen[:12]


def _event_statement(statement: str) -> dict[str, Any] | None:
    """Parse one event statement into a complete event contract row, or None.

    Strict: every field the event protocol requires must be derivable from
    the statement itself; any missing field means the statement is not a
    complete event contract and is skipped (fail-closed, never partial).
    """
    if not _EVENT_KIND_HINT.search(statement):
        return None
    paths = [match.group(1) for match in _PATH_RE.finditer(statement)]
    if not paths:
        return None
    event_type = None
    type_match = (
        _EVENT_TYPE_QUOTED.search(statement)
        or _EVENT_TYPE_NAMED.search(statement)
        or _EVENT_TYPE_UNQUOTED.search(statement)
    )
    if type_match:
        event_type = type_match.group(1)
    fields = _event_fields_named(statement)
    event_id_field = next((f for f in fields if f == "event_id"), None) or next(
        (f for f in fields if f.endswith("_id") and f.startswith("event")), None
    )
    event_type_field = next((f for f in fields if f in {"event_type", "type"}), None)
    correlation_field = next(
        (f for f in fields if f not in {event_id_field, event_type_field}), None
    )
    observer_path = paths[0].split("?")[0]
    query = paths[0].split("?", 1)[1] if "?" in paths[0] else ""
    query_params = [
        part.split("=")[0].strip()
        for part in query.split("&")
        if part.strip() and "=" in part
    ]
    correlation_query_parameter = next(
        (p for p in query_params if correlation_field and p == correlation_field),
        None,
    ) or (query_params[0] if query_params else None)
    required = {
        "observer_path": observer_path,
        "events_path": observer_path,
        "event_id_field": event_id_field,
        "event_type_field": event_type_field,
        "correlation_field": correlation_field,
        "correlation_query_parameter": correlation_query_parameter,
        "expected_event_type": event_type,
    }
    if any(not value for value in required.values()):
        return None
    return {
        "quote": statement[:_MAX_QUOTE_CHARS],
        **required,
        "correlation_source": {"location": "query", "path": correlation_field},
        "expected_min_count": _EVENT_MIN_COUNT,
        "expected_max_count": _EVENT_MAX_COUNT,
        "observation_window_ms": _EVENT_WINDOW_MS,
    }


# ---------------------------------------------------------------------------
# Operation / actor binding
# ---------------------------------------------------------------------------


def _operation_match(operations: list[dict[str, Any]], method: str, path: str) -> dict[str, Any] | None:
    """Exact method + path-shape match; ambiguous or missing -> None."""
    from .behavior_ir_core import _path_shape

    method = _text(method).upper()
    path = _text(path).strip()
    candidates = [
        row
        for row in operations
        if isinstance(row, dict)
        and _text(row.get("method")).upper() == method
        and _path_shape(_text(row.get("path"))) == _path_shape(path)
    ]
    if len(candidates) != 1:
        return None
    return candidates[0]


def _actor_role(operation: dict[str, Any], runtime_actors: list[dict[str, Any]]) -> str:
    """Resolve exactly one actor role for an operation; empty when ambiguous."""
    actors = [row for row in runtime_actors if isinstance(row, dict)]
    if not actors:
        return ""
    roles = [
        _text(row.get("role") or row.get("role_key") or row.get("name") or "").strip()
        for row in actors
    ]
    if len(actors) == 1 and roles[0]:
        return roles[0]
    required_roles = [
        _text(role).strip()
        for role in _list(operation.get("required_roles"))
        if _text(role).strip()
    ]
    if required_roles:
        matched = [
            row
            for row in actors
            if any(
                _text(role).casefold()
                in {
                    _text(row.get("role") or "").casefold(),
                    _text(row.get("role_key") or "").casefold(),
                    _text(row.get("name") or "").casefold(),
                }
                for role in required_roles
            )
        ]
        if len(matched) == 1:
            return _text(matched[0].get("role") or matched[0].get("role_key") or matched[0].get("name")).strip()
    return ""


# ---------------------------------------------------------------------------
# Contract row builders (normalized shape the binders consume)
# ---------------------------------------------------------------------------


def _source_refs(source_id: str, locator: str, quote: str, kind: str) -> list[dict[str, Any]]:
    return [{
        "source_id": source_id,
        "locator": locator,
        "kind": kind,
        "quote": quote,
        "quote_hash": _quote_hash(quote),
    }]


def _performance_row(operation: dict[str, Any], claim: dict[str, Any], *, source_id: str, actor_role: str) -> dict[str, Any]:
    quote = claim["quote"]
    contract_id = "auto_perf_" + _digest(
        operation.get("method"), operation.get("path"), claim["max_latency_ms"], quote
    )
    return {
        "schema_version": "qualibug.formal-performance-contract.v1",
        "contract_id": contract_id,
        "source_refs": _source_refs(source_id, _text(operation.get("operation_id")), quote, "formal_performance_contract"),
        "source_id": source_id,
        "source_locator": _text(operation.get("operation_id")),
        "method": _text(operation.get("method")).upper(),
        "operation_path": _text(operation.get("path")),
        "actor_role": actor_role,
        "sample_count": _PERF_SAMPLE_COUNT,
        "warmup_count": _PERF_WARMUP_COUNT,
        "percentile": claim["percentile"],
        "max_latency_ms": round(float(claim["max_latency_ms"]), 3),
        "max_error_rate": 0.0,
        "expected_status_class": 2,
        "status": "accepted",
        "derivation": "auto_detected_from_source",
        "origin": "contract_auto_derivation",
        "confidence": 1.0,
        "derived_source_value": round(float(claim["max_latency_ms"]), 3),
    }


def _stability_row(operation: dict[str, Any], claim: dict[str, Any], *, source_id: str, actor_role: str) -> dict[str, Any]:
    quote = claim["quote"]
    error_rate = float(claim["error_rate_pct"])
    contract_id = "auto_stab_" + _digest(
        operation.get("method"), operation.get("path"), error_rate, quote
    )
    allowed_failures = max(0, int(_STABILITY_SAMPLE_COUNT * error_rate / 100.0))
    return {
        "schema_version": "qualibug.formal-stability-contract.v1",
        "contract_id": contract_id,
        "source_refs": _source_refs(source_id, _text(operation.get("operation_id")), quote, "formal_stability_contract"),
        "source_id": source_id,
        "source_locator": _text(operation.get("operation_id")),
        "method": _text(operation.get("method")).upper(),
        "operation_path": _text(operation.get("path")),
        "actor_role": actor_role,
        "sample_count": _STABILITY_SAMPLE_COUNT,
        "max_failed_samples": allowed_failures,
        "max_retried_samples": 0,
        "expected_status_class": 2,
        "status": "accepted",
        "derivation": "auto_detected_from_source",
        "origin": "contract_auto_derivation",
        "confidence": 1.0,
        "derived_source_value": float(claim["error_rate_pct"]),
    }


def _event_row(operation: dict[str, Any], parsed: dict[str, Any], *, source_id: str, actor_role: str) -> dict[str, Any]:
    quote = parsed["quote"]
    contract_id = "auto_evt_" + _digest(
        operation.get("method"), operation.get("path"),
        parsed["observer_path"], parsed["expected_event_type"], quote,
    )
    return {
        "schema_version": "qualibug.formal-event-contract.v1",
        "contract_id": contract_id,
        "source_refs": _source_refs(source_id, _text(operation.get("operation_id")), quote, "formal_event_contract"),
        "source_id": source_id,
        "source_locator": _text(operation.get("operation_id")),
        "method": _text(operation.get("method")).upper(),
        "operation_path": _text(operation.get("path")),
        "actor_role": actor_role,
        "observer_path": parsed["observer_path"],
        "events_path": parsed["events_path"],
        "event_id_field": parsed["event_id_field"],
        "event_type_field": parsed["event_type_field"],
        "correlation_field": parsed["correlation_field"],
        "correlation_query_parameter": parsed["correlation_query_parameter"],
        "expected_event_type": parsed["expected_event_type"],
        "correlation_source": parsed["correlation_source"],
        "expected_min_count": parsed["expected_min_count"],
        "expected_max_count": parsed["expected_max_count"],
        "observation_window_ms": parsed["observation_window_ms"],
        "status": "accepted",
        "derivation": "auto_detected_from_source",
        "origin": "contract_auto_derivation",
        "confidence": 1.0,
    }


# ---------------------------------------------------------------------------
# Main derivation pass
# ---------------------------------------------------------------------------


def _existing_operation_keys(asset: dict[str, Any], key: str) -> set[tuple[str, str]]:
    covered: set[tuple[str, str]] = set()
    for row in _list(asset.get(key)):
        if not isinstance(row, dict):
            continue
        method = _text(row.get("method") or row.get("http_method")).upper()
        path = _text(row.get("operation_path") or row.get("api_path") or row.get("endpoint"))
        if method and path:
            covered.add((method, path))
    return covered


def derive_source_contracts(
    asset: dict[str, Any] | None,
    *,
    prd_text: str = "",
    api_spec_text: str = "",
    operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Detect explicit source contracts and bind them into the asset.

    Returns ``(asset, receipt)``.  The asset is returned unchanged when the
    pass is disabled or finds nothing; the receipt always records what was
    attempted, derived, and skipped (with reason codes).
    """
    receipt: dict[str, Any] = {
        "schema_version": DERIVATION_SCHEMA,
        "enabled": True,
        "derived": {"performance": 0, "stability": 0, "event": 0},
        "skipped": [],
        "methodology_defaults": {
            "performance": {
                "sample_count": _PERF_SAMPLE_COUNT,
                "warmup_count": _PERF_WARMUP_COUNT,
                "percentile": _PERF_PERCENTILE,
                "note": "measurement methodology only; latency budget comes verbatim from source",
            },
            "stability": {
                "sample_count": _STABILITY_SAMPLE_COUNT,
                "max_retried_samples": 0,
                "note": "failure budget = floor(sample_count * verbatim error-rate); retry budget is methodology zero",
            },
            "event": {
                "expected_min_count": _EVENT_MIN_COUNT,
                "expected_max_count": _EVENT_MAX_COUNT,
                "observation_window_ms": _EVENT_WINDOW_MS,
                "note": "correlation_source derived structurally (query + correlation field); no values invented",
            },
        },
    }
    merged = dict(_dict(asset))
    if not operations:
        receipt.update({"enabled": bool(enabled if enabled is not None else True), "status": "SKIPPED", "reason": "no_operations"})
        return merged, receipt

    if enabled is None:
        try:
            from .policy_wiring import get_policy_value

            enabled = bool(get_policy_value("contract_auto_derivation", "enabled", True))
        except Exception:
            enabled = True
    if str(os.environ.get("QUALIBUG_DISABLE_CONTRACT_AUTO_DERIVATION", "")).lower() in {"1", "true", "yes", "on"}:
        enabled = False
    receipt["enabled"] = enabled
    if not enabled:
        receipt.update({"status": "DISABLED", "reason": "operator_policy_or_env"})
        return merged, receipt

    actors = _list(runtime_actors)
    source_texts: list[tuple[str, str]] = []
    for label, text in (("api_spec", api_spec_text), ("prd", prd_text)):
        clean = _text(text)
        if clean:
            source_texts.append((label, clean))

    # 1) Operation-scoped pass: statements inside an operation's own
    #    summary/description bind to that operation with zero ambiguity.
    operation_rows: dict[str, list[dict[str, Any]]] = {
        "performance": [], "stability": [], "event": [],
    }
    operation_skips: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        method = _text(operation.get("method")).upper()
        op_text = " ".join([
            _text(operation.get("summary")),
            _text(operation.get("description")),
        ])
        op_text = _text(op_text)
        if not op_text:
            continue
        source_id = _text(operation.get("source_id")) or "api_spec"
        locator = _text(operation.get("operation_id")) or f"{method}:{_text(operation.get('path'))}"
        for claim in _latency_claims(op_text):
            if method not in _SAFE_METHODS:
                operation_skips.append({"kind": "performance", "locator": locator, "reason": "non_get_head_operation", "quote": claim["quote"][:120]})
                continue
            if not _anchor(claim["quote"], op_text):
                operation_skips.append({"kind": "performance", "locator": locator, "reason": "quote_not_anchored"})
                continue
            actor_role = _actor_role(operation, actors)
            if not actor_role:
                operation_skips.append({"kind": "performance", "locator": locator, "reason": "actor_unresolved", "quote": claim["quote"][:120]})
                continue
            operation_rows["performance"].append(
                _performance_row(operation, claim, source_id=source_id, actor_role=actor_role)
            )
        for claim in _stability_claims(op_text):
            if method not in _SAFE_METHODS:
                operation_skips.append({"kind": "stability", "locator": locator, "reason": "non_get_head_operation", "quote": claim["quote"][:120]})
                continue
            if not _anchor(claim["quote"], op_text):
                operation_skips.append({"kind": "stability", "locator": locator, "reason": "quote_not_anchored"})
                continue
            actor_role = _actor_role(operation, actors)
            if not actor_role:
                operation_skips.append({"kind": "stability", "locator": locator, "reason": "actor_unresolved", "quote": claim["quote"][:120]})
                continue
            operation_rows["stability"].append(
                _stability_row(operation, claim, source_id=source_id, actor_role=actor_role)
            )
        if _EVENT_KIND_HINT.search(op_text):
            parsed = _event_statement(op_text)
            if parsed and _anchor(parsed["quote"], op_text):
                actor_role = _actor_role(operation, actors)
                if not actor_role:
                    operation_skips.append({"kind": "event", "locator": locator, "reason": "actor_unresolved", "quote": parsed["quote"][:120]})
                else:
                    operation_rows["event"].append(
                        _event_row(operation, parsed, source_id=source_id, actor_role=actor_role)
                    )
            elif parsed is None:
                operation_skips.append({"kind": "event", "locator": locator, "reason": "event_fields_incomplete"})

    # 2) Text-scoped pass: latency/stability statements that name an explicit
    #    method+path (e.g. "GET /api/orders 响应时间不超过 200ms") bind to the
    #    matching operation.  Only for performance/stability; event statements
    #    need operation-scoped anchoring to stay exact.
    text_skips: list[dict[str, Any]] = []
    for source_id, text in source_texts:
        for claim in [*_latency_claims(text), *_stability_claims(text)]:
            if not _anchor(claim["quote"], text):
                continue
            path_match = _PATH_RE.search(claim["quote"])
            if not path_match:
                text_skips.append({"kind": "stability" if "error_rate_pct" in claim else "performance", "reason": "no_explicit_path_in_statement", "quote": claim["quote"][:120]})
                continue
            method = path_match.group(0).split()[0].upper()
            path = path_match.group(1)
            operation = _operation_match(operations, method, path)
            if operation is None:
                text_skips.append({"kind": "stability" if "error_rate_pct" in claim else "performance", "reason": "operation_not_found_or_ambiguous", "quote": claim["quote"][:120]})
                continue
            if _text(operation.get("method")).upper() not in _SAFE_METHODS:
                text_skips.append({"kind": "stability" if "error_rate_pct" in claim else "performance", "reason": "non_get_head_operation", "quote": claim["quote"][:120]})
                continue
            actor_role = _actor_role(operation, actors)
            if not actor_role:
                text_skips.append({"kind": "stability" if "error_rate_pct" in claim else "performance", "reason": "actor_unresolved", "quote": claim["quote"][:120]})
                continue
            if "error_rate_pct" in claim:
                operation_rows["stability"].append(
                    _stability_row(operation, claim, source_id=source_id, actor_role=actor_role)
                )
            else:
                operation_rows["performance"].append(
                    _performance_row(operation, claim, source_id=source_id, actor_role=actor_role)
                )

    # 3) Dedup against existing declared contracts and within the derived set,
    #    then write into the asset for the binder chain.  Conflicting values
    #    for the same operation are recorded visibly, never silently dropped.
    for kind, asset_key in (
        ("performance", "performance_formal_contracts"),
        ("stability", "stability_formal_contracts"),
        ("event", "event_formal_contracts"),
    ):
        existing_keys = _existing_operation_keys(merged, asset_key)
        seen: set[tuple[str, str]] = set()
        prior_value_by_key: dict[tuple[str, str], Any] = {}
        accepted: list[dict[str, Any]] = []
        for row in operation_rows.get(kind, [])[:_MAX_CONTRACTS_PER_KIND]:
            key = (_text(row.get("method")).upper(), _text(row.get("operation_path")))
            if key in existing_keys:
                receipt["skipped"].append({"kind": kind, "reason": "already_declared_contract", "operation": "/".join(key)})
                continue
            if key in seen:
                prior_value = prior_value_by_key.get(key)
                if prior_value is not None and prior_value != row.get("derived_source_value"):
                    receipt["skipped"].append({
                        "kind": kind,
                        "reason": "conflicting_derived_claims",
                        "operation": "/".join(key),
                        "prior_value": prior_value,
                        "skipped_value": row.get("derived_source_value"),
                    })
                continue
            seen.add(key)
            prior_value_by_key[key] = row.get("derived_source_value")
            accepted.append(row)
        if accepted:
            merged[asset_key] = [*_list(merged.get(asset_key)), *accepted]
            receipt["derived"][kind] = len(accepted)

    receipt["skipped"].extend(operation_skips)
    receipt["skipped"].extend(text_skips)
    receipt["status"] = (
        "CONSUMED" if sum(receipt["derived"].values()) else "NO_CONTRACTS_DERIVED"
    )
    return merged, receipt
