"""Source-declared parameter-bound performance contracts (REPORT-008 class).

The latency-budget chain (``formal_performance_surface``) verifies "the same
GET/HEAD request must answer within a declared budget".  It cannot express the
other, equally frequent performance contract a source declares: **a scaling
query parameter with an upper bound** (``repeat`` 1~100, ``limit`` max 100,
"报表查询最多返回 500 行").  When the service accepts an out-of-bound value
and the query slows down with it, the defect (unbounded parameter / slow
query / resource-exhaustion exposure) is a performance_latency defect that the
latency-budget chain cannot reach — the contract shape does not exist.

This module closes the contract-shape gap, exactly mirroring the discipline of
``contract_auto_derivation`` / ``scan_performance_contract_overlay`` /
``source_performance_contract_binding``:

- Derivation is extraction, never inference.  A parameter-bound contract is
  emitted only when the visible source material declares the bound: an OpenAPI
  parameter schema ``minimum``/``maximum`` on an integer query parameter, or a
  verbatim text statement naming the parameter with a numeric range/upper
  bound.  Vague statements ("接口必须稳定", "参数不能太大") produce nothing.
- Every contract carries ``source_refs`` (locator always; a verbatim quote
  when the quote is anchored in the available source text) plus an exact
  operation identity and an exact actor identity.  Anything that cannot be
  bound exactly is skipped with a receipt entry — never guessed.
- No defaults masquerade as business facts: probe counts, escalation ratios
  and ceilings are product-owned measurement methodology, fixed, documented
  and marked ``methodology`` in every receipt.  The business-relevant values
  (parameter name, declared minimum/maximum) always come verbatim from the
  source.
- Fail-closed and observable: every skip carries a reason code and the pass
  emits ``qualibug.parameter-bound-contract-derivation.v1``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Any

from . import behavior_ir as _bir

DERIVATION_SCHEMA = "qualibug.parameter-bound-contract-derivation.v1"
OVERLAY_SCHEMA = "qualibug.parameter-bound-contract-overlay.v1"
BINDING_RECEIPT_SCHEMA = "qualibug.source-parameter-contract-binding.v1"

CONTRACT_KIND = "parameter_scale_budget"

_SAFE_METHODS = frozenset({"GET", "HEAD"})
_MAX_CONTRACTS_PER_PASS = 12
_MAX_QUOTE_CHARS = 400

# ---------------------------------------------------------------------------
# Text statement patterns (parameter bounds in visible source text)
# ---------------------------------------------------------------------------

# "repeat 参数 1~100" / "取值范围 1-100" — a numeric range, optionally with a
# parameter reference word.  Both the tilde and the dash spellings are accepted
# only when a scale/range context word appears nearby (参数/范围/取值/次/条/页/
# 行/遍/重复/param/range/limit/repeat/page/size), so "2024-05" or a version
# string never matches.
_RANGE_WITH_CONTEXT = re.compile(
    r"(\d+)\s*[~～]\s*(\d+)"
    r"|(\d+)\s*[-–—]\s*(\d+)",
)
_CONTEXT_NEAR_RANGE = re.compile(
    r"(?:参数|范围|取值|值域|次|条|页|行|遍|重复|数量|上限|"
    r"param|parameter|range|limit|repeat|page|size|count|rows?|times)",
    re.IGNORECASE,
)
# "limit 最大 100" / "repeat 最多 10 次" / "X 上限为 500 条"
_MAX_CLAUSE = re.compile(
    r"(?:最大|上限|最多|至多|不超过|不高于|不得大于|不得高于|不能超过|"
    r"max(?:imum)?|cap|at\s+most|no\s+more\s+than)\s*(?:为|是|of)?\s*"
    r"(\d+)(?:\s*(?:条|次|页|行|个|遍|记录))?",
    re.IGNORECASE,
)
# "X 至少 1" / "最小为 1" / "下限 1"
_MIN_CLAUSE = re.compile(
    r"(?:最小|下限|至少|不少于|不低于|min(?:imum)?|at\s+least)\s*(?:为|是|of)?\s*"
    r"(\d+)(?:\s*(?:条|次|页|行|个|遍|记录))?",
    re.IGNORECASE,
)
# Parameter reference inside an operation-scoped statement: "repeat 参数",
# "参数 repeat", "limit 参数".  Generic identifier shape, no industry terms.
# The negative lookaheads keep "params"/"parameters" from matching as the
# "param" prefix plus a stray identifier tail.
_PARAM_REF = re.compile(
    r"(?:参数|param(?:eter)?(?![A-Za-z0-9_]))\s*[`\"']?"
    r"([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_])"
    r"|[`\"']([A-Za-z_][A-Za-z0-9_]*)['\"`]\s*(?:参数|参数取值|取值范围|上限|最大|最多)",
    re.IGNORECASE,
)

# Latency-budget statements already parsed by the auto-derivation layer; reuse
# the same authoritative grammar so a co-declared latency budget on the same
# operation enters the parameter contract verbatim.
from .contract_auto_derivation import _latency_claims as _source_latency_claims  # noqa: E402


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


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts if _text(part))
    return "bir_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _anchor(quote: str, text: str) -> bool:
    """Verbatim anchoring: the quote must be a real substring of the source."""
    return bool(quote) and quote in text


def _bound_quote(match: re.Match, text: str) -> str:
    start = max(0, match.start() - 60)
    end = min(len(text), match.end() + 30)
    return text[start:end].strip()[:_MAX_QUOTE_CHARS]


def _is_actor(actor: dict[str, Any]) -> bool:
    """Exactly-one executable actor resolution helper (identity, not role)."""
    if not isinstance(actor, dict):
        return False
    return bool(_text(actor.get("id")))


def _actor_executable(actor: dict[str, Any]) -> bool:
    role = _text(actor.get("role") or actor.get("role_key")).lower()
    if role in {"anonymous", "public"}:
        return True
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    return bool(
        secret_ref
        and not secret_ref.lower().startswith("secret_ref:actor:")
        and (
            actor.get("runtime_bound") is True
            or bool(_text(actor.get("account_ref")))
        )
    )


def _actor_role(operation: dict[str, Any], runtime_actors: list[dict[str, Any]]) -> str:
    """Resolve exactly one actor role for an operation; empty when ambiguous."""
    actors = [row for row in _list(runtime_actors) if _is_actor(row)]
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
            return _text(
                matched[0].get("role")
                or matched[0].get("role_key")
                or matched[0].get("name")
            ).strip()
    return ""


# ---------------------------------------------------------------------------
# Parameter extraction from operation declarations
# ---------------------------------------------------------------------------


def _query_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Integer-typed query parameters declared on an operation (dict rows)."""
    output: list[dict[str, Any]] = []
    for row in _list(operation.get("parameters")):
        if not isinstance(row, dict):
            continue
        location = _text(row.get("in") or row.get("location") or "query").lower()
        if location not in {"query", ""}:
            continue
        schema = _dict(row.get("schema"))
        param_type = _text(schema.get("type")).lower() or _text(
            _dict(row.get("items")).get("type")
        ).lower()
        if param_type not in {"integer", "int", "int32", "int64", "number"}:
            continue
        name = _text(row.get("name") or row.get("key") or row.get("param_name"))
        if not name:
            continue
        output.append({
            "name": name,
            "description": _text(row.get("description")),
            "schema": schema,
            "raw": row,
        })
    return output


def _schema_bounds(param: dict[str, Any]) -> tuple[int | None, int | None]:
    schema = _dict(param.get("schema"))
    minimum: int | None = None
    maximum: int | None = None
    for key, slot in (("minimum", "minimum"), ("maximum", "maximum")):
        value = schema.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if key == "minimum":
            minimum = parsed
        else:
            maximum = parsed
    return minimum, maximum


def _description_bounds(description: str) -> tuple[int | None, int | None]:
    """Numeric range/upper bound named inside a parameter description."""
    if not description:
        return None, None
    minimum: int | None = None
    maximum: int | None = None
    for match in _RANGE_WITH_CONTEXT.finditer(description):
        lo = int(match.group(1) or match.group(3))
        hi = int(match.group(2) or match.group(4))
        if not _CONTEXT_NEAR_RANGE.search(match.group(0) or ""):
            # The range itself carries no scale context; accept only when the
            # surrounding description names a scale word within the window.
            window = description[
                max(0, match.start() - 40): min(len(description), match.end() + 40)
            ]
            if not _CONTEXT_NEAR_RANGE.search(window):
                continue
        minimum = lo
        maximum = hi
        break
    max_match = _MAX_CLAUSE.search(description)
    if max_match is not None and maximum is None:
        maximum = int(max_match.group(1))
    min_match = _MIN_CLAUSE.search(description)
    if min_match is not None and minimum is None:
        minimum = int(min_match.group(1))
    return minimum, maximum


def _valid_bounds(minimum: int | None, maximum: int | None) -> bool:
    if minimum is None and maximum is None:
        return False
    if maximum is not None and maximum < 0:
        return False
    if minimum is not None and maximum is not None and minimum >= maximum:
        return False
    return True


def _statement_bounds(statement: str, param_name: str) -> tuple[int | None, int | None]:
    """Numeric range/upper bound for a named parameter inside one statement."""
    maximum: int | None = None
    minimum: int | None = None
    for match in _RANGE_WITH_CONTEXT.finditer(statement):
        lo = int(match.group(1) or match.group(3))
        hi = int(match.group(2) or match.group(4))
        window = statement[
            max(0, match.start() - 60): min(len(statement), match.end() + 40)
        ]
        if not _CONTEXT_NEAR_RANGE.search(window):
            continue
        if not (re.search(re.escape(param_name), statement, re.IGNORECASE)):
            continue
        minimum = lo
        maximum = hi
        break
    if maximum is None:
        max_match = _MAX_CLAUSE.search(statement)
        if max_match is not None and re.search(re.escape(param_name), statement, re.IGNORECASE):
            maximum = int(max_match.group(1))
    if minimum is None:
        min_match = _MIN_CLAUSE.search(statement)
        if min_match is not None and re.search(re.escape(param_name), statement, re.IGNORECASE):
            minimum = int(min_match.group(1))
    return minimum, maximum


# ---------------------------------------------------------------------------
# Contract row builder
# ---------------------------------------------------------------------------


def _contract_row(
    *,
    operation: dict[str, Any],
    parameter_name: str,
    declared_min: int | None,
    declared_max: int | None,
    max_latency_ms: float | None,
    actor_role: str,
    source_id: str,
    locator: str,
    quote: str,
    quote_anchored: bool,
) -> dict[str, Any]:
    contract_id = "auto_psb_" + _digest(
        operation.get("method"), operation.get("path"), parameter_name,
        declared_min, declared_max, quote,
    )
    source_refs = [{
        "source_id": source_id,
        "locator": locator,
        "kind": "formal_performance_contract",
        "quote": quote if quote_anchored else "",
        "quote_hash": _quote_hash(quote) if quote_anchored else "",
    }]
    row: dict[str, Any] = {
        "schema_version": "qualibug.formal-performance-contract.v1",
        "contract_kind": CONTRACT_KIND,
        "contract_id": contract_id,
        "source_refs": source_refs,
        "source_id": source_id,
        "source_locator": locator,
        "method": _text(operation.get("method")).upper(),
        "operation_path": _text(operation.get("path")),
        "actor_role": actor_role,
        "parameter_name": parameter_name,
        "declared_min": declared_min,
        "declared_max": declared_max,
        "status": "accepted",
        "derivation": "auto_detected_from_source",
        "origin": "source_parameter_bound_contracts",
        "confidence": 1.0,
    }
    if max_latency_ms is not None:
        row["max_latency_ms"] = round(float(max_latency_ms), 3)
    return row


# ---------------------------------------------------------------------------
# Derivation pass
# ---------------------------------------------------------------------------


def derive_parameter_bound_contracts(
    asset: dict[str, Any] | None,
    *,
    api_operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    prd_text: str = "",
    api_spec_text: str = "",
    enabled: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Detect source-declared parameter bounds and bind them into the asset.

    Returns ``(asset, receipt)``.  The asset is returned unchanged when the
    pass is disabled or finds nothing; the receipt always records what was
    attempted, derived, and skipped (with reason codes).
    """
    receipt: dict[str, Any] = {
        "schema_version": DERIVATION_SCHEMA,
        "enabled": True,
        "derived_count": 0,
        "skipped": [],
        "methodology": {
            "per_pass_contract_cap": _MAX_CONTRACTS_PER_PASS,
            "probe_count_and_escalation": (
                "owned by formal_parameter_scale_surface; the business values "
                "(parameter name, declared min/max) come verbatim from source"
            ),
        },
    }
    merged = dict(_dict(asset))
    operations = [row for row in _list(api_operations) if isinstance(row, dict)]
    if not operations:
        receipt.update({
            "enabled": bool(enabled if enabled is not None else True),
            "status": "SKIPPED",
            "reason": "no_operations",
        })
        return merged, receipt
    if enabled is None:
        try:
            from .policy_wiring import get_policy_value

            enabled = bool(get_policy_value(
                "parameter_bound_contracts", "derivation_enabled", True,
            ))
        except Exception:
            enabled = True
    if str(os.environ.get(
        "QUALIBUG_DISABLE_PARAMETER_BOUND_CONTRACTS", ""
    )).lower() in {"1", "true", "yes", "on"}:
        enabled = False
    receipt["enabled"] = enabled
    if not enabled:
        receipt.update({
            "status": "DISABLED",
            "reason": "operator_policy_or_env",
        })
        return merged, receipt

    actors = _list(runtime_actors)
    existing_keys: set[tuple[str, str, str]] = set()
    for row in _list(merged.get("performance_parameter_contracts")):
        if not isinstance(row, dict):
            continue
        key = (
            _text(row.get("method") or row.get("http_method")).upper(),
            _text(row.get("operation_path") or row.get("api_path") or row.get("endpoint")),
            _text(row.get("parameter_name")),
        )
        if key[0] and key[1] and key[2]:
            existing_keys.add(key)

    rows: list[dict[str, Any]] = []
    operation_skips: list[dict[str, Any]] = []

    for operation in operations:
        if _text(operation.get("method")).upper() not in _SAFE_METHODS:
            continue
        path = _text(operation.get("path"))
        source_id = _text(operation.get("source_id")) or "api_spec"
        locator = _text(operation.get("operation_id")) or f"{_text(operation.get('method')).upper()} {path}"
        actor_role = _actor_role(operation, actors)
        op_text = " ".join([
            _text(operation.get("summary")),
            _text(operation.get("description")),
        ]).strip()

        for param in _query_parameters(operation):
            name = param["name"]
            if not name:
                continue
            minimum, maximum = _schema_bounds(param)
            if maximum is None and minimum is None:
                # No schema bound: check the parameter's own description and
                # the operation-scoped text for a declared numeric range.
                desc_min, desc_max = _description_bounds(param["description"])
                if desc_max is not None or desc_min is not None:
                    minimum = desc_min
                    maximum = desc_max
                else:
                    statement_min = None
                    statement_max = None
                    if op_text:
                        statement_min, statement_max = _statement_bounds(op_text, name)
                    if statement_max is None and statement_min is None:
                        operation_skips.append({
                            "kind": "parameter_bound",
                            "locator": locator,
                            "parameter_name": name,
                            "reason": "no_declared_upper_bound",
                        })
                        continue
                    minimum = statement_min
                    maximum = statement_max
            if not _valid_bounds(minimum, maximum):
                operation_skips.append({
                    "kind": "parameter_bound",
                    "locator": locator,
                    "parameter_name": name,
                    "reason": "invalid_declared_bounds",
                    "declared_min": minimum,
                    "declared_max": maximum,
                })
                continue
            if maximum is None:
                operation_skips.append({
                    "kind": "parameter_bound",
                    "locator": locator,
                    "parameter_name": name,
                    "reason": "upper_bound_not_declared",
                })
                continue
            if not actor_role:
                operation_skips.append({
                    "kind": "parameter_bound",
                    "locator": locator,
                    "parameter_name": name,
                    "reason": "actor_unresolved",
                })
                continue
            key = (
                _text(operation.get("method")).upper(),
                path,
                name,
            )
            if key in existing_keys:
                operation_skips.append({
                    "kind": "parameter_bound",
                    "locator": locator,
                    "parameter_name": name,
                    "reason": "already_declared_contract",
                })
                continue
            # Optional co-declared latency budget on the same operation.
            max_latency_ms: float | None = None
            if op_text:
                claims = _source_latency_claims(op_text)
                if claims:
                    max_latency_ms = float(claims[0]["max_latency_ms"])
            quote = param["description"]
            quote_anchored = bool(quote) and (
                _anchor(quote, api_spec_text)
                or _anchor(quote, op_text)
                or _anchor(quote, param["description"])
            )
            rows.append(_contract_row(
                operation=operation,
                parameter_name=name,
                declared_min=minimum,
                declared_max=maximum,
                max_latency_ms=max_latency_ms,
                actor_role=actor_role,
                source_id=source_id,
                locator=locator,
                quote=quote,
                quote_anchored=quote_anchored,
            ))
            existing_keys.add(key)

    # Text-scoped pass: statements that name method + path + parameter range
    # bind to the matching operation (mirrors the latency text pass).
    text_skips: list[dict[str, Any]] = []
    for source_id, text in (
        ("api_spec", _text(api_spec_text)),
        ("prd", _text(prd_text)),
    ):
        if not text:
            continue
        for match in _PARAM_REF.finditer(text):
            param_name = _text(match.group(1) or match.group(2))
            if not param_name:
                continue
            bounds = _statement_bounds(text, param_name)
            minimum, maximum = bounds
            if maximum is None or not _valid_bounds(minimum, maximum):
                continue
            path_match = re.search(
                r"(?:GET|HEAD)\s+(/[\w\-/{}.]+(?:\?[\w\-=&]+)?)",
                text[max(0, match.start() - 160): match.end() + 160],
                re.IGNORECASE,
            )
            if not path_match:
                text_skips.append({
                    "kind": "parameter_bound",
                    "reason": "no_explicit_path_in_statement",
                    "parameter_name": param_name,
                    "quote": _bound_quote(match, text),
                })
                continue
            method = path_match.group(0).split()[0].upper()
            path = path_match.group(1)
            from .behavior_ir_core import _path_shape

            candidates = [
                row
                for row in operations
                if _text(row.get("method")).upper() == method
                and _path_shape(_text(row.get("path"))) == _path_shape(path)
            ]
            if len(candidates) != 1:
                text_skips.append({
                    "kind": "parameter_bound",
                    "reason": "operation_not_found_or_ambiguous",
                    "parameter_name": param_name,
                    "quote": _bound_quote(match, text),
                })
                continue
            operation = candidates[0]
            if _text(operation.get("method")).upper() not in _SAFE_METHODS:
                text_skips.append({
                    "kind": "parameter_bound",
                    "reason": "non_get_head_operation",
                    "parameter_name": param_name,
                    "quote": _bound_quote(match, text),
                })
                continue
            if param_name not in {
                row.get("name") for row in _query_parameters(operation)
            }:
                text_skips.append({
                    "kind": "parameter_bound",
                    "reason": "parameter_not_declared_on_operation",
                    "parameter_name": param_name,
                    "quote": _bound_quote(match, text),
                })
                continue
            actor_role = _actor_role(operation, actors)
            if not actor_role:
                text_skips.append({
                    "kind": "parameter_bound",
                    "reason": "actor_unresolved",
                    "parameter_name": param_name,
                    "quote": _bound_quote(match, text),
                })
                continue
            key = (_text(operation.get("method")).upper(), _text(operation.get("path")), param_name)
            if key in existing_keys:
                continue
            quote = _bound_quote(match, text)
            rows.append(_contract_row(
                operation=operation,
                parameter_name=param_name,
                declared_min=minimum,
                declared_max=maximum,
                max_latency_ms=None,
                actor_role=actor_role,
                source_id=source_id,
                locator=_text(operation.get("operation_id")) or f"{_text(operation.get('method')).upper()} {_text(operation.get('path'))}",
                quote=quote,
                quote_anchored=_anchor(quote, text),
            ))
            existing_keys.add(key)

    accepted = rows[:_MAX_CONTRACTS_PER_PASS]
    if accepted:
        merged["performance_parameter_contracts"] = [
            *[
                copy.deepcopy(row)
                for row in _list(merged.get("performance_parameter_contracts"))
                if isinstance(row, dict)
            ],
            *accepted,
        ]
        receipt["derived_count"] = len(accepted)
    receipt["skipped"].extend(operation_skips)
    receipt["skipped"].extend(text_skips)
    receipt["status"] = "CONSUMED" if accepted else "NO_CONTRACTS_DERIVED"
    return merged, receipt


# ---------------------------------------------------------------------------
# Scan-context overlay (mirrors scan_performance_contract_overlay)
# ---------------------------------------------------------------------------


def _source_refs(row: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        copy.deepcopy(ref)
        for ref in _list(row.get("source_refs"))
        if isinstance(ref, dict) and _text(ref.get("source_id"))
    ]
    if refs:
        return refs
    source_id = _text(row.get("source_id"))
    if not source_id:
        return []
    return [{
        "source_id": source_id,
        "version": _text(row.get("source_version")),
        "locator": _text(row.get("source_locator")),
        "kind": "formal_performance_contract",
        "quote_hash": _text(row.get("quote_hash")),
    }]


def _gap(contract_id: str, reason_code: str, refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "gap_type": "parameter_bound_contract_not_source_bound",
        "reason_code": reason_code,
        "contract_id": contract_id,
        "source_id": _text(_dict((refs or [{}])[0]).get("source_id")) if refs else "",
        "description": "Declared parameter-bound contract could not enter formal performance authority",
        "status": "unsupported",
    }


def _normalize_parameter_contract(
    raw: dict[str, Any],
    *,
    index: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    row = copy.deepcopy(_dict(raw))
    contract_id = _text(row.get("contract_id") or row.get("id")) or _stable_id(
        "scan_param_contract", index, row,
    )
    refs = _source_refs(row)
    if not refs:
        return None, [_gap(contract_id, "PARAMETER_CONTRACT_SOURCE_REF_MISSING")]
    if _text(row.get("contract_kind")) not in {"", CONTRACT_KIND}:
        return None, [_gap(contract_id, "PARAMETER_CONTRACT_KIND_INVALID", refs)]
    operation_ref = _text(row.get("operation_ref") or row.get("operation_id"))
    method = _text(row.get("method") or row.get("http_method")).upper()
    operation_path = _text(row.get("operation_path") or row.get("api_path") or row.get("endpoint"))
    if not operation_ref and not (method and operation_path):
        return None, [_gap(contract_id, "PARAMETER_CONTRACT_OPERATION_IDENTITY_MISSING", refs)]
    if not operation_ref and method not in _SAFE_METHODS:
        return None, [_gap(contract_id, "PARAMETER_CONTRACT_GET_OR_HEAD_REQUIRED", refs)]
    actor_ref = _text(row.get("actor_ref") or row.get("actor_id"))
    actor_role = _text(row.get("actor_role") or row.get("role"))
    if not actor_ref and not actor_role:
        return None, [_gap(contract_id, "PARAMETER_CONTRACT_ACTOR_IDENTITY_MISSING", refs)]
    parameter_name = _text(row.get("parameter_name"))
    if not parameter_name:
        return None, [_gap(contract_id, "PARAMETER_CONTRACT_PARAMETER_NAME_MISSING", refs)]
    try:
        declared_min = (
            int(row.get("declared_min"))
            if row.get("declared_min") is not None
            else None
        )
        declared_max = (
            int(row.get("declared_max"))
            if row.get("declared_max") is not None
            else None
        )
    except (TypeError, ValueError):
        return None, [_gap(contract_id, "PARAMETER_CONTRACT_BOUND_INVALID", refs)]
    if declared_max is None:
        return None, [_gap(contract_id, "PARAMETER_CONTRACT_UPPER_BOUND_REQUIRED", refs)]
    if not _valid_bounds(declared_min, declared_max):
        return None, [_gap(contract_id, "PARAMETER_CONTRACT_BOUND_INVALID", refs)]
    max_latency_ms: float | None = None
    if row.get("max_latency_ms") is not None:
        try:
            max_latency_ms = float(row.get("max_latency_ms"))
        except (TypeError, ValueError):
            return None, [_gap(contract_id, "PARAMETER_CONTRACT_LATENCY_INVALID", refs)]
        if not 0 < max_latency_ms <= 120_000:
            return None, [_gap(contract_id, "PARAMETER_CONTRACT_LATENCY_INVALID", refs)]

    normalized = {
        **row,
        "schema_version": "qualibug.formal-performance-contract.v1",
        "contract_kind": CONTRACT_KIND,
        "contract_id": contract_id,
        "source_refs": refs,
        "source_id": _text(refs[0].get("source_id")),
        "source_locator": _text(refs[0].get("locator")),
        "parameter_name": parameter_name,
        "declared_min": declared_min,
        "declared_max": declared_max,
        "status": "accepted",
        "derivation": "explicit",
        "confidence": 1.0,
    }
    if max_latency_ms is not None:
        normalized["max_latency_ms"] = max_latency_ms
    if operation_ref:
        normalized["operation_ref"] = operation_ref
    else:
        normalized["method"] = method
        normalized["operation_path"] = operation_path
    if actor_ref:
        normalized["actor_ref"] = actor_ref
    else:
        normalized["actor_role"] = actor_role
    return normalized, []


def overlay_scan_parameter_contracts(
    asset: dict[str, Any] | None,
    campaign_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Overlay explicitly typed parameter-bound contracts from scan context."""
    merged = copy.deepcopy(_dict(asset))
    context = _dict(campaign_context)
    raw_contracts = [
        copy.deepcopy(row)
        for row in _list(context.get("performance_parameter_contracts"))
        if isinstance(row, dict)
    ]
    existing = [
        copy.deepcopy(row)
        for row in _list(merged.get("performance_parameter_contracts"))
        if isinstance(row, dict)
    ]
    by_id = {
        _text(row.get("contract_id")): row
        for row in existing
        if _text(row.get("contract_id"))
    }
    gaps: list[dict[str, Any]] = []
    added = 0
    for index, raw in enumerate(raw_contracts, start=1):
        contract, row_gaps = _normalize_parameter_contract(raw, index=index)
        gaps.extend(row_gaps)
        if contract is None:
            continue
        contract_id = _text(contract.get("contract_id"))
        if contract_id in by_id:
            gaps.append(_gap(contract_id, "PARAMETER_CONTRACT_ID_DUPLICATE", _source_refs(contract)))
            continue
        by_id[contract_id] = contract
        added += 1
    merged["performance_parameter_contracts"] = list(by_id.values())
    merged["coverage_gaps"] = [
        *[
            copy.deepcopy(row)
            for row in _list(merged.get("coverage_gaps"))
            if isinstance(row, dict)
        ],
        *gaps,
    ]
    receipt = {
        "schema_version": OVERLAY_SCHEMA,
        "status": "OVERLAID" if added else "BLOCKED" if raw_contracts else "NOT_REQUESTED",
        "scan_contract_count": len(raw_contracts),
        "contract_added_count": added,
        "coverage_gap_count": len(gaps),
    }
    merged["scan_parameter_contract_overlay_receipt"] = receipt
    return merged, receipt


# ---------------------------------------------------------------------------
# Behavior IR binding (mirrors source_performance_contract_binding)
# ---------------------------------------------------------------------------


def _contracts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        copy.deepcopy(row)
        for row in _list(_dict(asset).get("performance_parameter_contracts"))
        if isinstance(row, dict)
    ]
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        contract_id = _text(row.get("contract_id"))
        if contract_id:
            deduped.setdefault(contract_id, row)
    return list(deduped.values())


def _resolve_operation(
    contract: dict[str, Any],
    operations: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    explicit = _text(contract.get("operation_ref") or contract.get("operation_id"))
    method = _text(contract.get("method") or contract.get("http_method")).upper()
    path = _text(contract.get("operation_path") or contract.get("api_path") or contract.get("endpoint"))
    if explicit:
        candidates = [
            row
            for row in operations
            if explicit in {
                _text(row.get("id")),
                _text(row.get("operation_id")),
                *[_text(value) for value in _list(row.get("source_operation_refs"))],
            }
        ]
    elif method and path:
        candidates = [
            row
            for row in operations
            if _text(row.get("method")).upper() == method
            and _bir._path_shape(row.get("path")) == _bir._path_shape(path)
        ]
    else:
        return None, "PARAMETER_CONTRACT_OPERATION_IDENTITY_MISSING"
    if len(candidates) != 1:
        return None, (
            "PARAMETER_CONTRACT_OPERATION_AMBIGUOUS"
            if len(candidates) > 1
            else "PARAMETER_CONTRACT_OPERATION_NOT_FOUND"
        )
    operation = candidates[0]
    if _text(operation.get("method")).upper() not in _SAFE_METHODS:
        return None, "PARAMETER_CONTRACT_GET_OR_HEAD_REQUIRED"
    return operation, ""


def _resolve_actor(
    contract: dict[str, Any],
    actors: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    explicit = _text(contract.get("actor_ref") or contract.get("actor_id"))
    role = _text(contract.get("actor_role") or contract.get("role")).casefold()
    if explicit:
        candidates = [row for row in actors if _text(row.get("id")) == explicit]
    elif role:
        candidates = [
            row
            for row in actors
            if role in {
                _text(row.get("role")).casefold(),
                _text(row.get("role_key")).casefold(),
            }
        ]
        executable = [row for row in candidates if _actor_executable(row)]
        if executable:
            candidates = executable
    else:
        return None, "PARAMETER_CONTRACT_ACTOR_IDENTITY_MISSING"
    if len(candidates) != 1:
        return None, (
            "PARAMETER_CONTRACT_ACTOR_AMBIGUOUS"
            if len(candidates) > 1
            else "PARAMETER_CONTRACT_ACTOR_NOT_FOUND"
        )
    if not _actor_executable(candidates[0]):
        return None, "PARAMETER_CONTRACT_ACTOR_NOT_EXECUTABLE"
    return candidates[0], ""


def _binding_gap(contract: dict[str, Any], reason_code: str) -> dict[str, Any]:
    contract_id = _text(contract.get("contract_id")) or "unknown_parameter_contract"
    return _bir._fact_node(
        node_id=_stable_id("gap", "parameter_bound_contract", contract_id, reason_code),
        typed_fields={
            "gap_type": "parameter_bound_contract_not_executable",
            "reason_code": reason_code,
            "contract_id": contract_id,
            "description": reason_code,
        },
        source_refs=_source_refs(contract),
        confidence=1.0,
        derivation="explicit",
        status="unsupported",
    )


def bind_source_parameter_contracts(
    behavior_ir: dict[str, Any],
    asset: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind exact parameter-bound contracts into Behavior IR invariants."""
    model = copy.deepcopy(_dict(behavior_ir))
    contracts = _contracts(_dict(asset))
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    actors = [row for row in _list(model.get("actors")) if isinstance(row, dict)]
    existing = {
        _text(row.get("performance_contract_id"))
        for row in _list(model.get("invariants"))
        if isinstance(row, dict) and _text(row.get("performance_contract_id"))
    }
    relation_ids = {
        _text(row.get("id"))
        for row in _list(model.get("relations"))
        if isinstance(row, dict)
    }
    bound = 0
    gaps = 0
    reason_counts: dict[str, int] = {}

    for contract in contracts:
        contract_id = _text(contract.get("contract_id"))
        if not contract_id or contract_id in existing:
            continue
        refs = _source_refs(contract)
        operation, operation_reason = _resolve_operation(contract, operations)
        actor, actor_reason = _resolve_actor(contract, actors)
        reason = (
            "PARAMETER_CONTRACT_SOURCE_REF_MISSING"
            if not refs
            else operation_reason or actor_reason
        )
        if reason:
            model.setdefault("coverage_gaps", []).append(_binding_gap(contract, reason))
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            gaps += 1
            continue
        assert operation is not None and actor is not None
        operation_ref = _text(operation.get("id")) or _text(operation.get("operation_id"))
        actor_ref = _text(actor.get("id"))
        invariant_id = _stable_id("inv", "parameter_bound_contract", contract_id)
        parameter_name = _text(contract.get("parameter_name"))
        invariant = _bir._fact_node(
            node_id=invariant_id,
            typed_fields={
                "description": (
                    _text(contract.get("title"))
                    or f"parameter {parameter_name} bound contract on {_text(operation.get('path'))}"
                ),
                "expression": {
                    "kind": "parameter_scale_budget_contract",
                    "operator": "must_respect_declared_parameter_bound",
                    "operands": [parameter_name],
                    "raw": f"{parameter_name} bound {_text(contract.get('declared_min'))}..{_text(contract.get('declared_max'))}",
                },
                "operation_refs": [operation_ref],
                "source_rule_refs": [contract_id],
                "performance_contract_id": contract_id,
                "performance_contract": copy.deepcopy(contract),
                "performance_actor_ref": actor_ref,
                "binding_status": "source_identity_bound",
            },
            source_refs=refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
        )
        relation = _bir._relation_node(
            relation_type="observes",
            from_ref=invariant_id,
            to_ref=operation_ref,
            operation_ref=operation_ref,
            actor_ref=actor_ref,
            preconditions=[{
                "kind": "source_declared_parameter_scale_probing",
                "parameter_name": parameter_name,
                "declared_min": contract.get("declared_min"),
                "declared_max": contract.get("declared_max"),
            }],
            effects=[],
            source_refs=refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
            source_relationship_ref=contract_id,
        )
        model.setdefault("invariants", []).append(invariant)
        if _text(relation.get("id")) not in relation_ids:
            model.setdefault("relations", []).append(relation)
            relation_ids.add(_text(relation.get("id")))
        existing.add(contract_id)
        bound += 1

    receipt = {
        "schema_version": BINDING_RECEIPT_SCHEMA,
        "status": "BOUND" if bound else "BLOCKED" if contracts else "NOT_REQUESTED",
        "contract_count": len(contracts),
        "bound_invariant_count": bound,
        "coverage_gap_count": gaps,
        "reason_counts": dict(sorted(reason_counts.items())),
        "binding_basis": "exact_source_identity_only",
        "load_capacity_claimed": False,
    }
    model["source_parameter_contract_binding_receipt"] = receipt
    errors = _bir.validate_behavior_ir(model, require_explicit_relations=True)
    if errors:
        raise _bir.BehaviorIRError(
            "source_parameter_contract_binding_invalid:" + ",".join(errors[:12])
        )
    model["model_id"] = _bir._content_addressed_id(model)
    return model, receipt


__all__ = [
    "CONTRACT_KIND",
    "DERIVATION_SCHEMA",
    "bind_source_parameter_contracts",
    "derive_parameter_bound_contracts",
    "overlay_scan_parameter_contracts",
]
