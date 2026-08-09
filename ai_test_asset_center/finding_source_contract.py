"""Finding evidence text: source contracts + runtime evidence paragraphs.

Root cause this module fixes (57 'AI判断失败' FN): delivered defect findings
carried only machine-generated English identity (``[ContractOracle]
<kind>: <role> <method> <path>``) and an assertion message.  The evaluator
matches on the finding text blob; without the source contract statement the
obligation was bound to, and without the runtime observations the experiment
produced, there is no semantic signal to align against.

Two evidence paragraphs are stored on dedicated finding fields (never appended
to ``description`` / ``title`` — those must stay byte-identical from the moment
the delivery gate binds ``finding_payload_fingerprint``):

1. ``contract_evidence`` — verbatim statement texts of the rules the obligation
   was bound to, as a ``源契约: ...`` paragraph.  Sources are strictly: (a) the
   compiled rule statements carried on the experiment's assertions
   (``property.expression.raw`` / ``property.description`` /
   ``field_rule_binding.typed_expression.raw`` / ``assertion.description``),
   and (b) rule/permission statement texts resolved from the knowledge asset
   through the finding's own ``source_refs`` (permission-matrix rows, rule
   library, interface contracts).  Nothing is invented: an obligation without
   a bound rule statement simply contributes nothing.
2. ``runtime_observation`` — the observed runtime evidence of the executed
   experiment, as a ``运行时证据: ...`` paragraph: actor role(s), interface
   path, dual-arm comparison outcome (control vs treatment), assertion kinds,
   reproduction steps, observed HTTP status and observed response body
   summary.  This field is always populated when the finding carries runtime
   evidence: runtime observation is legitimate evidence even when the source
   materials never stated the violated rule.

The machine-readable title prefix and the original description are preserved
unchanged; injection is idempotent.  Legacy persisted findings that already
carry ``源契约:`` / ``运行时证据:`` lines inside ``description`` keep them
untouched (they are part of the fingerprinted payload of their own gate) —
new injections land in the dedicated fields only.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


_CONTRACT_PREFIX = "源契约: "
_EVIDENCE_PREFIX = "运行时证据: "


def _normalize_statement(text: str, limit: int = 320) -> str:
    return " ".join(_text(text).split())[:limit]


def _normalize_path_template(locator: str) -> str:
    """Normalize path locators so concrete ids match their source templates.

    ``PUT /v1/items/a1`` and ``PUT /v1/items/{id}`` resolve to the same
    template key ``put /v1/items/{*}/``.  This is generic path-shape
    normalization, never industry vocabulary.
    """
    value = _text(locator)
    parts = value.split(None, 1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        value = f"{parts[0].upper()} {parts[1]}"
    value = re.sub(r"\{[^}]+\}", "{*}", value)
    value = re.sub(r":[a-zA-Z_][a-zA-Z0-9_]*", "{*}", value)
    value = re.sub(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "/{*}",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"/\d+", "/{*}", value)
    return value.casefold()


# ─── Observed request/response material (matching assets) ─────────────────────
# Everything below reads ONLY data the runtime actually observed and persisted
# on the finding (``raw_evidence.steps`` / ``request_raw`` / ``response_raw``):
# the executed path with its materialized query string, the request body that
# actually reached transport, and the response body the target returned.
# Instance identity values (UUIDs, generated long ids, timestamps) are
# normalized to shape tokens so the shape survives across runs; every other
# short literal is kept verbatim because it IS the observation.  Nothing is
# guessed, templated, or filled in.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_HEX32_RE = re.compile(r"[0-9a-fA-F]{32}")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
# Generated instance identifiers (order_no-style long tokens).  Short literals
# like SKU-PHONE-001, prices and negative quantities stay verbatim.
_LONG_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{16,80}$")


def normalize_observed_value(value: Any) -> str:
    """Normalize one real observed scalar: instance ids to shape tokens.

    - UUID (dashed or 32-hex) -> ``<uuid>``
    - ISO datetime -> ``<datetime>``
    - long generated identifier (16+ chars) -> ``<id>``
    - everything else (numbers including negatives, status words, codes,
      prices) is kept verbatim — it is the observed value itself.
    """
    text = _text(value)
    if not text:
        return ""
    if _UUID_RE.fullmatch(text) or _HEX32_RE.fullmatch(text):
        return "<uuid>"
    if _DATETIME_RE.match(text):
        return "<datetime>"
    if _LONG_ID_RE.fullmatch(text):
        return "<id>"
    return text


def query_shape_tokens(path: str) -> list[str]:
    """Parse the query string of an executed path into ``key=shape`` tokens.

    The step executor urlencodes the materialized query into the executed
    path; this function recovers the real key names and value shapes from
    that observed path.  Empty values emit the bare key name.
    """
    tokens: list[str] = []
    if not isinstance(path, str) or "?" not in path:
        return tokens
    query = path.split("?", 1)[1].split("#", 1)[0]
    if not query:
        return tokens
    for key, raw_value in parse_qsl(query, keep_blank_values=True):
        name = _text(key)
        if not name:
            continue
        normalized = normalize_observed_value(raw_value)
        tokens.append(f"{name}={normalized}" if normalized else name)
    return tokens


def _redact_payload(payload: Any) -> Any:
    """Deep-redact a real observed payload via the shared artifact redactor."""
    if payload is None:
        return None
    from .artifact_redactor import redact_artifact

    redacted, _receipt = redact_artifact(payload)
    return redacted


def _flatten_observed_payload(
    value: Any,
    *,
    prefix: str = "",
    depth: int = 0,
    max_pairs: int = 48,
) -> list[str]:
    """Flatten a real observed body into ``key=value`` evidence tokens.

    Key names stay verbatim (they are real); scalar values pass through
    :func:`normalize_observed_value`; nested dicts/lists recurse with a
    dotted prefix up to depth 2; list elements beyond the first three are
    summarized as ``N更多`` so a 500-row collection cannot bloat the text.
    """
    pairs: list[str] = []
    if value is None or value == "":
        return pairs
    if isinstance(value, dict):
        if depth > 2:
            return pairs
        for key, child in value.items():
            name = _text(key)
            if not name:
                continue
            child_prefix = f"{prefix}.{name}" if prefix else name
            pairs.extend(
                _flatten_observed_payload(
                    child,
                    prefix=child_prefix,
                    depth=depth + 1,
                    max_pairs=max_pairs,
                )
            )
            if len(pairs) >= max_pairs:
                break
        return pairs
    if isinstance(value, list):
        if depth > 2:
            return pairs
        for index, item in enumerate(value[:3]):
            child_prefix = f"{prefix}.{index}" if prefix else f"{index}"
            pairs.extend(
                _flatten_observed_payload(
                    item,
                    prefix=child_prefix,
                    depth=depth + 1,
                    max_pairs=max_pairs,
                )
            )
        if len(value) > 3:
            pairs.append(f"{prefix}N={len(value) - 3}更多" if prefix else f"N={len(value) - 3}更多")
        return pairs
    normalized = normalize_observed_value(value)
    if not normalized:
        return pairs
    return [f"{prefix}={normalized}" if prefix else normalized]


def _observed_step_bodies(finding: dict[str, Any]) -> list[tuple[str, Any]]:
    """Real observed payloads on the finding: request bodies, response bodies.

    Only steps that actually reached transport (``status_code > 0``) count —
    a pre-transport blocked step never produced an observation.  Request
    bodies come from the governed-write receipt's ``materialized_request_body``
    (the exact body sent), response bodies from the step's ``body``.
    """
    requests: list[Any] = []
    responses: list[Any] = []
    for step in _list(_dict(finding.get("raw_evidence")).get("steps")):
        if not isinstance(step, dict):
            continue
        if int(step.get("status_code") or 0) <= 0:
            continue
        governed = _dict(step.get("governance_receipt"))
        request_body = governed.get("materialized_request_body")
        if request_body not in (None, "", {}, []):
            requests.append(request_body)
        response_body = step.get("body")
        if response_body not in (None, "", {}, []):
            responses.append(response_body)
    return requests, responses


def build_observed_request_text(finding: dict[str, Any], limit: int = 320) -> str:
    """Evidence text of the real request bodies that reached transport."""
    requests, _responses = _observed_step_bodies(finding)
    seen: set[str] = set()
    tokens: list[str] = []
    for request_body in requests:
        flat = _flatten_observed_payload(_redact_payload(request_body))
        text = " ".join(flat)
        if not text or text in seen:
            continue
        seen.add(text)
        tokens.append(text)
        if len(tokens) >= 3:
            break
    return ";".join(tokens)[:limit]


def build_observed_response_text(finding: dict[str, Any], limit: int = 700) -> str:
    """Evidence text of the real response bodies the target returned.

    Bodies from every transport-reached step (control, treatment, observers)
    are redacted, flattened and deduplicated; the treatment response from
    ``response_raw`` is included when steps are absent.
    """
    _requests, responses = _observed_step_bodies(finding)
    raw = _dict(_dict(finding.get("raw_evidence")).get("response_raw"))
    body = raw.get("body")
    if body not in (None, "", {}, []):
        responses.append(body)
    seen: set[str] = set()
    tokens: list[str] = []
    for response_body in responses:
        flat = _flatten_observed_payload(_redact_payload(response_body))
        text = " ".join(flat)
        if not text or text in seen:
            continue
        seen.add(text)
        tokens.append(text)
        if len(tokens) >= 4:
            break
    return ";".join(tokens)[:limit]


def _extract_quoted_description(evidence: str) -> str:
    """Unwrap ``"description": "…"`` JSON/YAML fragments left by parsers."""
    text = _text(evidence)
    match = re.match(r'^["\']?description["\']?\s*[:=]\s*["\'](.*)["\']\s*,?\s*$', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _statement_from_property(prop: dict[str, Any]) -> str:
    """Best source-rule statement text carried on one assertion property."""
    expression = _dict(prop.get("expression"))
    field_binding = _dict(prop.get("field_rule_binding"))
    typed_expression = _dict(field_binding.get("typed_expression"))
    for candidate in (
        _text(expression.get("raw")),
        _text(field_binding.get("statement")),
        _text(typed_expression.get("raw")),
        _text(prop.get("statement")),
        _text(prop.get("description")),
    ):
        if candidate:
            return candidate
    return ""


def collect_experiment_rule_statements(exp: dict[str, Any]) -> list[str]:
    """All source rule statement texts bound to one experiment.

    Every assertion (not only the first) is scanned, so an obligation bound to
    several rules carries all of them.  Statements are the verbatim compiled
    source text — never translated, never invented.
    """
    statements: list[str] = []
    seen: set[str] = set()
    for raw_assertion in _list(_dict(exp).get("assertions")):
        assertion = _dict(raw_assertion)
        prop = _dict(assertion.get("property"))
        candidates = [
            _statement_from_property(prop),
            _text(assertion.get("description")),
            _text(assertion.get("rule_statement")),
        ]
        for candidate in candidates:
            normalized = _normalize_statement(candidate)
            if normalized and normalized.casefold() not in seen:
                seen.add(normalized.casefold())
                statements.append(normalized)
    return statements


def build_rule_statement_index(asset: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    """Build ``{(kind, key): [statement, ...]}`` from a knowledge asset.

    ``key`` is the locator used by finding ``source_refs``: a role name for
    ``permission_matrix`` refs, ``"METHOD path"`` for ``api_operation`` refs.
    Only statement/source fields that exist in the asset are indexed.
    """
    index: dict[tuple[str, str], list[str]] = {}
    for row in _list(_dict(asset).get("permission_matrix")):
        role = _text(row.get("role") or row.get("actor") or row.get("principal"))
        evidence = _extract_quoted_description(
            _text(row.get("evidence") or row.get("statement") or row.get("description"))
        )
        if role and evidence:
            index.setdefault(("permission_matrix", role.casefold()), []).append(
                _normalize_statement(evidence)
            )
    for rule in _list(_dict(asset).get("rule_library") or _dict(asset).get("rules")):
        statement = _text(
            rule.get("statement") or rule.get("expression") or rule.get("title")
        )
        if not statement:
            continue
        normalized = _normalize_statement(statement)
        locators: list[str] = []
        for container in (rule, _dict(rule.get("operation_binding"))):
            for key in (
                "source_locator",
                "operation_locator",
                "operation_ref",
                "locator",
            ):
                value = _text(container.get(key))
                if value:
                    locators.append(value)
        for value in _list(rule.get("operation_refs")):
            if _text(value):
                locators.append(_text(value))
        for locator in locators:
            index.setdefault(
                ("source_locator", _normalize_path_template(locator)), []
            ).append(normalized)
        rule_id = _text(rule.get("rule_id") or rule.get("id"))
        if rule_id:
            index.setdefault(("rule_id", rule_id.casefold()), []).append(normalized)
    # Interface contracts: method+path -> summary/description text.
    for iface in _list(_dict(asset).get("interfaces")):
        method = _text(iface.get("method")).upper()
        path = _text(iface.get("path") or iface.get("raw_path"))
        text = _text(
            iface.get("description") or iface.get("summary") or iface.get("title")
        )
        if method and path and text:
            index.setdefault(
                ("source_locator", _normalize_path_template(f"{method} {path}")), []
            ).append(_normalize_statement(text))
    return index


def resolve_source_ref_statements(
    source_refs: list[Any],
    index: dict[tuple[str, str], list[str]],
) -> list[str]:
    """Resolve a finding's own ``source_refs`` to asset statement texts."""
    statements: list[str] = []
    seen: set[str] = set()
    for ref in _list(source_refs):
        row = _dict(ref)
        kind = _text(row.get("kind") or row.get("type") or row.get("source_type"))
        locator = _text(
            row.get("locator") or row.get("source_locator") or row.get("ref")
        )
        if not kind or not locator:
            continue
        if kind == "api_operation":
            key = ("source_locator", _normalize_path_template(locator))
        else:
            key = (kind.casefold(), locator.casefold())
        candidates = [key]
        if key[0] == "source_locator":
            # Concrete reproduced paths resolve to their source template:
            # strip the trailing segment and retry with the wildcard shape.
            segments = key[1].split("/")
            if len(segments) > 2 and segments[-1] != "{*}":
                candidates.append((key[0], "/".join(segments[:-1] + ["{*}"])))
        for candidate_key in candidates:
            for statement in _list(index.get(candidate_key)):
                if statement.casefold() not in seen:
                    seen.add(statement.casefold())
                    statements.append(statement)
    return statements


def build_runtime_evidence_text(
    finding: dict[str, Any],
    description: str | None = None,
    *,
    include_observed_payloads: bool = True,
) -> str:
    """Runtime-evidence paragraph from the finding's own observed evidence.

    Only data the runtime actually observed is used: actor roles, interface
    path, dual-arm comparison outcome, assertion kinds, reproduction steps,
    observed HTTP status and observed response body summary (redaction
    preserved).  Nothing is inferred.

    ``include_observed_payloads`` additionally appends the observed request
    material — executed query parameter shapes, request bodies that reached
    transport, and response bodies the target returned — all redacted and
    truncated, as matching material for the delivered evidence text.
    """
    parts: list[str] = []
    repro = _dict(finding.get("reproduction"))
    actor = _text(
        repro.get("actor") or _dict(finding.get("evidence")).get("actor")
        or finding.get("actor_role")
    )
    method = _text(repro.get("method")).upper()
    path = _text(repro.get("path"))
    evidence = _dict(finding.get("evidence"))
    control_ok = evidence.get("control_succeeded")
    assertion_kinds = [
        _text(row.get("kind"))
        for row in _list(finding.get("failed_assertions"))
        if isinstance(row, dict) and _text(row.get("kind"))
    ]
    description = _text(description) if description is not None else _text(finding.get("description"))
    # 对照摘要 must reflect only the original assertion message — never the
    # appended evidence paragraphs themselves.
    description = "\n".join(
        line
        for line in description.splitlines()
        if not line.startswith(_CONTRACT_PREFIX) and not line.startswith(_EVIDENCE_PREFIX)
    ).strip()
    description = " ".join(description.split())
    if actor:
        parts.append(f"角色 {actor} 对照实验")
    if method and path:
        parts.append(f"{method} {path}")
    if control_ok is not None:
        parts.append("control=成功" if control_ok else "control=失败")
    if assertion_kinds:
        parts.append("treatment=违反断言 " + ",".join(dict.fromkeys(assertion_kinds)))
    if description:
        parts.append("对照摘要: " + description)
    steps = [str(step) for step in _list(repro.get("reproduction_steps"))][:3]
    if steps:
        parts.append("复现: " + " | ".join(steps))
    raw = _dict(finding.get("raw_evidence"))
    response_raw = _dict(raw.get("response_raw"))
    status = response_raw.get("status_code")
    if status is not None:
        parts.append(f"观察HTTP状态={status}")
    if include_observed_payloads:
        # Observed query shapes: executed path (request_raw / reproduction)
        # plus every transport-reached step path, deduplicated.
        query_tokens: list[str] = []
        seen_query: set[str] = set()
        request_raw = _dict(raw.get("request_raw"))
        for candidate in (
            _text(request_raw.get("path")),
            _text(repro.get("path")),
            *[
                _text(step.get("path"))
                for step in _list(raw.get("steps"))
                if isinstance(step, dict) and int(step.get("status_code") or 0) > 0
            ],
        ):
            for token in query_shape_tokens(candidate):
                if token not in seen_query:
                    seen_query.add(token)
                    query_tokens.append(token)
        if query_tokens:
            parts.append("观察query形态=" + ";".join(query_tokens[:14]))
        observed_request = build_observed_request_text(finding)
        if observed_request:
            parts.append("观察请求体=" + observed_request)
        observed_response = build_observed_response_text(finding)
        if observed_response:
            parts.append("观察响应体=" + observed_response)
    else:
        # Legacy summary path: treatment response body, dict bodies only.
        body = response_raw.get("body")
        if isinstance(body, dict) and body:
            body_summary = json.dumps(body, ensure_ascii=False, default=str)[:300]
            parts.append("观察响应体=" + body_summary)
    # A paragraph that merely restates the assertion message is not runtime
    # evidence — require at least one observed component beyond the summary.
    if len(parts) == 1 and parts[0].startswith("对照摘要:"):
        return ""
    return "；".join(parts)


def _paragraph_statements(text: str) -> list[str]:
    """Normalized statement list inside one ``源契约: ...`` paragraph body.

    Used to merge statements already carried on the finding — either in the
    dedicated ``contract_evidence`` field (current scheme) or in legacy
    ``源契约:`` lines inside ``description`` (persisted pre-fix findings).
    """
    statements: list[str] = []
    body = _text(text)
    if body.startswith(_CONTRACT_PREFIX):
        body = body[len(_CONTRACT_PREFIX):]
    for statement in body.split("; "):
        normalized = _normalize_statement(statement)
        if normalized:
            statements.append(normalized)
    return statements


def attach_evidence_paragraphs(
    finding: dict[str, Any],
    *,
    exp: dict[str, Any] | None = None,
    statements: list[str] | None = None,
    with_runtime_evidence: bool = True,
    include_observed_payloads: bool = True,
) -> dict[str, Any]:
    """Store ``源契约`` and ``运行时证据`` paragraphs on dedicated fields.

    The paragraphs are written to ``contract_evidence`` and
    ``runtime_observation`` — never appended to ``description``/``title``.
    ``description`` and ``title`` are the core evidence payload the delivery
    gate binds with ``finding_payload_fingerprint`` at gate-build time; any
    later mutation (e.g. the asset-index enrichment pass that runs after the
    batch returns) would re-derive a different fingerprint and fail delivery
    with ``finding_payload_fingerprint_mismatch``.  Dedicated fields keep the
    injected matching material out of the fingerprinted payload while still
    feeding the evaluator blob (benchmark_evaluator ``_finding_text_blob``
    reads them).

    - ``源契约`` (``contract_evidence``) is populated only when bound rule
      statement texts exist (from ``statements`` or collected from ``exp``) —
      nothing is fabricated for obligations without rule associations.
    - ``运行时证据`` (``runtime_observation``) is populated when the finding
      carries runtime evidence.
    - ``include_observed_payloads`` controls whether the observed request
      query shapes / request bodies / response bodies are included as
      matching material (default on).
    - Original ``title``/``description`` are preserved byte-for-byte; appends
      are idempotent.  Legacy ``源契约:``/``运行时证据:`` lines already present
      in ``description`` (pre-fix persisted findings) are left untouched and
      only merged into the fields, never rewritten into the description.
    """
    if not isinstance(finding, dict):
        return finding
    description = _text(finding.get("description"))
    new_statements = list(statements or [])
    if exp is not None:
        new_statements = list(dict.fromkeys(
            [*new_statements, *collect_experiment_rule_statements(exp)]
        ))
    new_statements = list(dict.fromkeys(s for s in new_statements if _text(s)))

    # Merge statements already carried on the finding — the dedicated field
    # (current scheme) and legacy description paragraphs (old scheme) — so a
    # later pass extends rather than duplicates.  Existing statements come
    # first so the paragraph order is deterministic across passes (idempotent).
    existing_statements: list[str] = []
    existing_field = _text(finding.get("contract_evidence"))
    if existing_field:
        existing_statements.extend(_paragraph_statements(existing_field))
    if _CONTRACT_PREFIX in description:
        for line in description.splitlines():
            if line.startswith(_CONTRACT_PREFIX):
                existing_statements.extend(_paragraph_statements(line))
    source_statements = list(dict.fromkeys(
        [*existing_statements, *new_statements]
    ))

    contract_text = ""
    if source_statements:
        contract_text = _CONTRACT_PREFIX + "; ".join(source_statements)

    runtime_text = ""
    if (
        with_runtime_evidence
        and not _text(finding.get("runtime_observation"))
        and _EVIDENCE_PREFIX not in description
    ):
        evidence_text = build_runtime_evidence_text(
            finding,
            description=description,
            include_observed_payloads=include_observed_payloads,
        )
        if evidence_text:
            runtime_text = _EVIDENCE_PREFIX + evidence_text

    enriched = dict(finding)
    changed = False
    if contract_text and contract_text != _text(enriched.get("contract_evidence")):
        enriched["contract_evidence"] = contract_text
        changed = True
    if runtime_text and runtime_text != _text(enriched.get("runtime_observation")):
        enriched["runtime_observation"] = runtime_text
        changed = True
    return enriched if changed else finding


def enrich_governed_result(
    governed: dict[str, Any],
    *,
    exp: dict[str, Any] | None = None,
    statements: list[str] | None = None,
    with_runtime_evidence: bool = True,
    include_observed_payloads: bool = True,
) -> dict[str, Any]:
    """Apply :func:`attach_evidence_paragraphs` to every finding occurrence."""
    result = dict(governed)
    findings = [
        dict(row)
        for row in _list(governed.get("findings"))
        if isinstance(row, dict)
    ]
    if not findings and _dict(governed.get("finding")):
        findings = [dict(_dict(governed.get("finding")))]
    if not findings:
        return result
    enriched = [
        attach_evidence_paragraphs(
            row,
            exp=exp,
            statements=statements,
            with_runtime_evidence=with_runtime_evidence,
            include_observed_payloads=include_observed_payloads,
        )
        for row in findings
    ]
    result["findings"] = enriched
    if _dict(governed.get("finding")):
        result["finding"] = enriched[0]
    return result
