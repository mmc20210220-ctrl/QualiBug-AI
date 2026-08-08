"""Typed observer capability, receipt, and evidence-sufficiency contracts.

An HTTP response proves that transport was observed. It does not by itself
prove a protected business resource was visible or a write had an effect.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any


SCHEMA_VERSION = "qualibug.observer-receipt.v1"
OBSERVER_STATUSES = frozenset({"OBSERVED", "INDETERMINATE", "FAILED", "UNSUPPORTED"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:20]


_BUSINESS_REJECT_TOKENS = frozenset({
    "fail",
    "failed",
    "failure",
    "error",
    "denied",
    "reject",
    "rejected",
    "invalid",
    "forbidden",
    "unauthorized",
    "insufficient",
    "expired",
    "禁用",
    "拒绝",
    "失败",
    "无效",
    "不足",
})


_IDENTITY_KEY_RE = re.compile(
    r"^(id|uuid|guid|key|code|slug)$"
    r"|.*(_id|_key|_code|_uuid|_ref|_no|_number)$"
    r"|.*([A-Z]id|[A-Z]key|[A-Z]code|[A-Z]uuid|[A-Z]ref|[A-Z]no)$",
    re.IGNORECASE,
)


def _is_identity_key(key: str) -> bool:
    """Industry-generic identity field detection.

    Matches patterns like: id, uuid, *_id, *Id, *_key, *Key, *_code, *Code,
    *_ref, *Ref, *_no, *No, *_number, *Number.
    Never uses project-specific entity names.
    """
    if not key or not isinstance(key, str):
        return False
    return bool(_IDENTITY_KEY_RE.match(key))


def _business_outcome_from_body(body: Any) -> dict[str, Any]:
    """Extract industry-neutral soft-fail / status tokens from a response body."""

    outcome: dict[str, Any] = {
        "business_rejected": False,
        "success_flag": None,
        # Named outcome_status (not *_token) so artifact redaction does not
        # rewrite content-addressed observer receipt evidence.
        "outcome_status": "",
        "identity_overlap": False,
    }
    payload = body
    if isinstance(body, list) and body and isinstance(body[0], dict):
        payload = body[0]
    if not isinstance(payload, dict):
        return outcome

    # Decision-flavoured flags: validation/eligibility endpoints (validate/
    # check/verify/simulate) echo their decision as a boolean field
    # (valid/eligible/approved/usable). A false decision on an accepted
    # transport status is the target DECLARING the rejection — the same soft
    # rejection signal as success:false. "valid" is a generic decision
    # vocabulary word, never an industry term.
    decision_flag_found = False
    for key in ("success", "ok", "accepted", "passed",
                "valid", "eligible", "approved", "usable", "enabled"):
        if key in payload:
            flag = payload.get(key)
            if isinstance(flag, bool):
                outcome["success_flag"] = flag
                decision_flag_found = True
                if flag is False:
                    outcome["business_rejected"] = True
            break

    # Outcome-status extraction. A body that carries a decision flag has an
    # authoritative acceptance signal — its "code" key is the ENTITY's code
    # (a coupon code EXPIRED50), not a rejection status, and substring reject
    # scanning would misread the entity code as a business rejection. Only
    # bodies WITHOUT a decision flag fall back to scanning code/result keys.
    outcome_status = ""
    status_keys = ("status", "state") if decision_flag_found else (
        "status", "state", "code", "errorCode", "error_code", "result",
    )
    for key in status_keys:
        value = payload.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            outcome_status = _text(value)
            if outcome_status:
                break
    error_obj = _dict(payload.get("error"))
    if not outcome_status and not decision_flag_found:
        for key in ("code", "message", "type"):
            value = error_obj.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                outcome_status = _text(value)
                if outcome_status:
                    break
    outcome["outcome_status"] = outcome_status
    status_lower = outcome_status.lower()
    if status_lower and any(marker in status_lower for marker in _BUSINESS_REJECT_TOKENS):
        outcome["business_rejected"] = True
    if error_obj and (
        outcome["success_flag"] is False
        or any(_text(error_obj.get(key)) for key in ("code", "message"))
    ):
        if outcome["success_flag"] is not True:
            outcome["business_rejected"] = True

    identities = []
    for key, value in payload.items():
        if _is_identity_key(key) and value is not None and value != "":
            identities.append(_fingerprint(value))
    data = payload.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            if _is_identity_key(key) and value is not None and value != "":
                identities.append(_fingerprint(value))
    outcome["identity_overlap"] = len(set(identities)) >= 1 and len(identities) > len(set(identities))
    return outcome


def _receipt(
    *,
    observer_id: str,
    status: str,
    reason_code: str = "",
    evidence: dict[str, Any] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    normalized_status = _text(status).upper()
    if normalized_status not in OBSERVER_STATUSES:
        raise ValueError(f"observer_status_invalid:{normalized_status}")
    # Deep-copy so later observation merges cannot mutate fingerprinted evidence.
    safe_evidence = copy.deepcopy(evidence or {})
    receipt_id = "obs_" + _fingerprint({
        "observer_id": observer_id,
        "status": normalized_status,
        "reason_code": reason_code,
        "evidence": safe_evidence,
        "campaign_id": _text(campaign_id),
        "execution_id": _text(execution_id),
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "campaign_id": _text(campaign_id),
        "execution_id": _text(execution_id),
        "observer_id": _text(observer_id),
        "status": normalized_status,
        "reason_code": _text(reason_code),
        "evidence": safe_evidence,
    }


def build_observer_receipt(
    *,
    observer_id: str,
    status: str,
    reason_code: str = "",
    evidence: dict[str, Any] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """Create one content-addressed typed observer receipt."""

    if not _text(observer_id):
        raise ValueError("observer_id_missing")
    return _receipt(
        observer_id=observer_id,
        status=status,
        reason_code=reason_code,
        evidence=evidence,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )


def validate_observer_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate schema, status, identity, and content fingerprint strictly."""

    row = _dict(receipt)
    required_fields = {
        "schema_version",
        "receipt_id",
        "campaign_id",
        "execution_id",
        "observer_id",
        "status",
        "reason_code",
        "evidence",
    }
    if set(row) != required_fields:
        raise ValueError("observer_receipt_fields_invalid")
    if row.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("observer_receipt_schema_invalid")
    observer_id = _text(row.get("observer_id"))
    if not observer_id or not isinstance(row.get("evidence"), dict):
        raise ValueError("observer_receipt_content_invalid")
    expected = _receipt(
        observer_id=observer_id,
        status=_text(row.get("status")),
        reason_code=_text(row.get("reason_code")),
        evidence=dict(row["evidence"]),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
    )
    if row != expected:
        raise ValueError("observer_receipt_fingerprint_invalid")
    return dict(expected)


def bind_observer_receipt_lineage(
    receipt: dict[str, Any],
    *,
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    """Bind a typed observer receipt to exactly one runtime execution."""

    resolved_campaign_id = _text(campaign_id)
    resolved_execution_id = _text(execution_id)
    if not resolved_campaign_id or not resolved_execution_id:
        raise ValueError("observer_receipt_lineage_missing")
    validated = validate_observer_receipt(receipt)
    existing_campaign_id = _text(validated.get("campaign_id"))
    existing_execution_id = _text(validated.get("execution_id"))
    if existing_campaign_id and existing_campaign_id != resolved_campaign_id:
        raise ValueError("observer_receipt_campaign_mismatch")
    if existing_execution_id and existing_execution_id != resolved_execution_id:
        raise ValueError("observer_receipt_execution_mismatch")
    return _receipt(
        observer_id=_text(validated.get("observer_id")),
        status=_text(validated.get("status")),
        reason_code=_text(validated.get("reason_code")),
        evidence=dict(validated["evidence"]),
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )


# ``implemented`` means the current candidate runtime emits a typed receipt for
# this observer. Declaring a surface in Behavior IR is not implementation.
#
# RESIDUAL BREADTH CEILING (V1.6.2): every built-in observer below still uses
# adapter ``http_api``. DB / UI / message-queue / timing observers are not on the
# executor → observer → assertion-DSL → contract-oracle chain. Do not claim those
# defect classes are supported until a real non-HTTP observer is wired end-to-end.
OBSERVER_REGISTRY: dict[str, dict[str, Any]] = {
    "http_response": {
        "surface": "http_api",
        "adapter": "http_api",
        "implemented": True,
    },
    "actor_identity": {
        "surface": "identity_context",
        "adapter": "http_api",
        "implemented": True,
    },
    "authorization_comparison": {
        "surface": "http_api",
        "adapter": "http_api",
        "implemented": True,
    },
    "resource_ownership": {
        "surface": "http_api",
        "adapter": "http_api",
        "implemented": True,
    },
    "business_effect": {
        "surface": "business_effect",
        "adapter": "http_api",
        "implemented": True,
    },
    "entity_state": {
        "surface": "business_state",
        "adapter": "http_api",
        "implemented": True,
    },
    "before_state": {
        "surface": "business_state",
        "adapter": "http_api",
        "implemented": True,
    },
    "after_state": {
        "surface": "business_state",
        "adapter": "http_api",
        "implemented": True,
    },
    "final_state": {
        "surface": "business_state",
        "adapter": "http_api",
        "implemented": True,
    },
    "barrier_timeline": {
        "surface": "concurrency_barrier",
        "adapter": "http_api",
        "implemented": True,
    },
    "typed_assertion": {
        "surface": "contract_assertion",
        "adapter": "http_api",
        "implemented": True,
    },
    "source_invariant": {
        "surface": "source_contract",
        "adapter": "http_api",
        "implemented": True,
    },
    "temporal_window": {
        "surface": "temporal_convergence",
        "adapter": "http_api",
        "implemented": True,
    },
    # "write_observer" was registered here with implemented=True and no dispatch
    # branch in observe_experiment_requirements, so anything requiring it would pass
    # compile_observer_requirements (which only checks implemented is True) and then
    # fall to the else branch at runtime as UNSUPPORTED /
    # OBSERVER_IMPLEMENTATION_MISSING -- a compiled experiment that spends real target
    # requests and can never yield an observation.
    #
    # It was never an observation surface: "write_observer" is the name of the
    # per-write governance CALLBACK parameter (sandbox_write_executor_base.py:1059
    # inspects it by parameter name) and a human-readable detail string on a
    # BLOCKED_MISSING_OBSERVER receipt (experiment_compiler_obligation.py:695).
    # Nothing requires it as an observer id. Removed so that if anything ever does,
    # compile_observer_requirements blocks it visibly at compile time.
    #
    # tests/test_assertion_evidence_contract.py asserts every implemented=True entry
    # has a dispatch branch, so this class of misregistration cannot return.
}


# ── Observer registration entry point ───────────────────────────────────────
#
# The registry above is the built-in set. Every one of its 13 entries declares
# adapter "http_api", which is the product's hardest breadth ceiling: a defect class
# whose evidence lives in a database, a message queue, a rendered view or a timing
# series has no observer that can measure it, so it is unreachable no matter how well
# the business is understood.
#
# register_observer is the extension point. It is deliberately ADDITIVE -- the built-in
# if/elif dispatch keeps working untouched and registered handlers are consulted where
# that dispatch would otherwise return UNSUPPORTED. Rewriting a working 100-line
# dispatch was not necessary to remove the ceiling, and would have risked the paths
# that currently produce every real receipt.
#
# Handlers receive ONE typed envelope rather than positional arguments. The built-in
# handlers each take a different shape (some take steps, some take an assertion, some
# take the raw evidence dict), which is precisely why a non-http observer had no way to
# be passed its own surface's evidence. A single dict keyed by surface concern is
# adapter-agnostic and extensible without changing the call site.
#
# What registration does NOT change: _receipt / validate_observer_receipt /
# bind_observer_receipt_lineage are already content-addressed over an arbitrary evidence
# dict and fully adapter-agnostic, so a new observer must be built onto them rather than
# beside them. That is the one core layer needing no work.
_REGISTERED_OBSERVER_HANDLERS: dict[str, Any] = {}


def register_observer(
    observer_id: str,
    *,
    surface: str,
    adapter: str,
    handler: Any,
    evidence_keys: "tuple[str, ...] | list[str]" = (),
) -> str:
    """Register an observer and its handler. Returns the observer id.

    ``handler(envelope) -> dict`` must return a receipt built with ``_receipt`` (or an
    equivalent that ``validate_observer_receipt`` accepts). ``envelope`` carries
    experiment, observations, assertion, property, control and treatment.

    ``evidence_keys`` names the observation keys this observer WRITES, so the
    kind-to-evidence contract in assertion_dsl_base can tell whether an assertion kind
    is satisfiable. Declaring them is what lets a new assertion kind stop being
    permanently indeterminate.

    Rejects a registration that cannot work rather than deferring the failure:
    ``compile_observer_requirements`` only checks ``implemented is True``, so an entry
    without a usable handler would compile, spend real target requests, and then fall
    through as UNSUPPORTED. That is exactly how ``write_observer`` shipped.
    """
    resolved_id = _text(observer_id)
    if not resolved_id:
        raise ValueError("register_observer requires a non-empty observer_id")
    if not _text(surface):
        raise ValueError(f"observer {resolved_id!r} requires a surface")
    if not _text(adapter):
        raise ValueError(f"observer {resolved_id!r} requires an adapter")
    if not callable(handler):
        raise ValueError(
            f"observer {resolved_id!r} requires a callable handler; an entry without "
            "one compiles and then returns UNSUPPORTED at observation time"
        )
    existing = OBSERVER_REGISTRY.get(resolved_id)
    if isinstance(existing, dict) and resolved_id not in _REGISTERED_OBSERVER_HANDLERS:
        raise ValueError(
            f"observer {resolved_id!r} is a built-in with its own dispatch branch; "
            "choose a distinct id rather than shadowing it"
        )
    OBSERVER_REGISTRY[resolved_id] = {
        "surface": _text(surface),
        "adapter": _text(adapter),
        "implemented": True,
        "evidence_keys": tuple(_text(key) for key in evidence_keys if _text(key)),
        "registered_at_runtime": True,
    }
    _REGISTERED_OBSERVER_HANDLERS[resolved_id] = handler
    return resolved_id


def registered_observer_ids() -> tuple[str, ...]:
    """Observer ids added through register_observer, in registration order."""
    return tuple(_REGISTERED_OBSERVER_HANDLERS)


def observer_produced_evidence_keys() -> frozenset[str]:
    """Every observation key the registered observers declare they write."""
    produced: set[str] = set()
    for contract in OBSERVER_REGISTRY.values():
        if isinstance(contract, dict):
            produced.update(contract.get("evidence_keys") or ())
    return frozenset(produced)


def compile_observer_requirements(
    observer_ids: list[str],
    *,
    risk_family: str,
    available_adapters: set[str],
    require_authorization_comparison: bool = True,
) -> tuple[list[dict[str, Any]], str, str]:
    """Return typed requirements or one stable compile blocker.

    Authorization/isolation/visibility normally require ``authorization_comparison``
    (control vs treatment). Permit-only single-actor invocations compile with an
    empty control plan and ``http_status_class`` — forcing comparison there yields
    INDETERMINATE and falsely surfaces as ``BLOCKED_MISSING_OBSERVER``.
    """

    required = [_text(value) for value in observer_ids if _text(value)]
    if (
        require_authorization_comparison
        and _text(risk_family) in {"authorization", "isolation", "visibility"}
    ):
        required.append("authorization_comparison")
    required = list(dict.fromkeys(required))
    if not required:
        return [], "BLOCKED_MISSING_OBSERVER", "none"

    compiled: list[dict[str, Any]] = []
    for observer_id in required:
        contract = OBSERVER_REGISTRY.get(observer_id)
        if not contract or contract.get("implemented") is not True:
            return [], "BLOCKED_MISSING_OBSERVER", observer_id
        adapter = _text(contract.get("adapter"))
        if adapter not in available_adapters:
            return [], "BLOCKED_UNSUPPORTED_ADAPTER", adapter or observer_id
        compiled.append({
            "observer_id": observer_id,
            "surface": _text(contract.get("surface")),
            "adapter": adapter,
            "receipt_schema": SCHEMA_VERSION,
            "required_status": "OBSERVED",
        })
    return compiled, "", ""


def validate_observer_declarations(
    observers: list[dict[str, Any]],
    *,
    risk_family: str,
    available_adapters: set[str],
    require_authorization_comparison: bool = True,
) -> tuple[str, str]:
    """Validate persisted/manual experiments against the current registry."""

    declared = [
        _text(_dict(observer).get("observer_id"))
        for observer in observers
        if _text(_dict(observer).get("observer_id"))
    ]
    compiled, reason_code, detail = compile_observer_requirements(
        declared,
        risk_family=risk_family,
        available_adapters=available_adapters,
        require_authorization_comparison=require_authorization_comparison,
    )
    if reason_code:
        return reason_code, detail
    declared_set = set(declared)
    for requirement in compiled:
        observer_id = _text(requirement.get("observer_id"))
        if observer_id not in declared_set:
            return "BLOCKED_MISSING_OBSERVER", observer_id
    return "", ""


def _response_status(observation: dict[str, Any]) -> int:
    try:
        return int(_dict(observation).get("status_code") or 0)
    except (TypeError, ValueError):
        return 0


def _raw_http_status(observation: dict[str, Any]) -> int:
    row = _dict(observation)
    try:
        return int(row.get("status_code") or row.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def _is_success(observation: dict[str, Any]) -> bool:
    return 200 <= _response_status(observation) < 300


def _is_error_envelope(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    normalized_keys = {
        re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        for key in value
    }
    error_keys = {"error", "errors", "message", "detail", "error_code"}
    identity_keys = {
        key for key in normalized_keys
        if key in {"id", "uuid", "key"} or key.endswith("_id")
    }
    return bool(normalized_keys.intersection(error_keys)) and not identity_keys


def _meaningful_resource_body(observation: dict[str, Any]) -> bool:
    method = _text(observation.get("method")).upper()
    if method == "HEAD" or _response_status(observation) == 204:
        return False
    body = observation.get("body")
    if isinstance(body, dict):
        return bool(body) and not _is_error_envelope(body)
    if isinstance(body, list):
        return bool(body)
    return False


def _normalize_field_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _identity_fingerprints(
    value: Any,
    identity_keys: Iterable[str] | None = None,
) -> set[str]:
    """Fingerprint the field that identifies the resource in an observed body.

    ``identity_keys`` are the field names the source declared as primary or
    unique keys for the entity under observation. They take precedence because
    they are what the source says identifies a row. The name heuristic below is
    a fallback for sources that never declared a key; it recognizes only a fixed
    English vocabulary and misses identity fields named in any other way.
    """
    declared = {_normalize_field_name(k) for k in (identity_keys or []) if str(k).strip()}
    result: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            scalar_identities: list[tuple[str, Any]] = []
            declared_identities: list[tuple[str, Any]] = []
            for key, child in node.items():
                normalized_key = _normalize_field_name(key)
                if isinstance(child, (str, int, float)) and str(child).strip():
                    if normalized_key in declared:
                        declared_identities.append((normalized_key, child))
                    if (
                        normalized_key in {
                            "id", "uuid", "key", "code", "slug",
                            "ref", "reference", "number",
                        }
                        or normalized_key.endswith(("_code", "_ref", "_number"))
                    ):
                        scalar_identities.append((normalized_key, child))
            if declared_identities:
                key, child = sorted(declared_identities)[0]
                result.add(_fingerprint({"key": key, "value": child}))
                return
            if not scalar_identities:
                fallback_identities = []
                for key, child in node.items():
                    normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
                    if (
                        (
                            normalized_key.endswith(("_id", "_code", "_ref", "_number"))
                            or normalized_key in {
                                "code", "slug", "ref", "reference", "number",
                            }
                        )
                        and isinstance(child, (str, int, float))
                        and str(child).strip()
                    ):
                        fallback_identities.append((normalized_key, child))
                if len(fallback_identities) == 1:
                    scalar_identities = fallback_identities
            if scalar_identities:
                key, child = sorted(
                    scalar_identities,
                    key=lambda item: ({"id": 0, "uuid": 1, "key": 2}.get(item[0], 3), item[0]),
                )[0]
                result.add(_fingerprint({"key": key, "value": child}))
                return
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result


def _server_managed_field(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    return normalized in {
        "createdat",
        "updatedat",
        "createdtime",
        "updatedtime",
        "modifiedat",
        "modifiedtime",
        "timestamp",
    }


def _without_server_managed_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_server_managed_fields(child)
            for key, child in sorted(value.items())
            if not _server_managed_field(key)
        }
    if isinstance(value, list):
        return sorted(
            (_without_server_managed_fields(child) for child in value),
            key=_canonical,
        )
    return value


_COLLECTION_WRAPPER_KEYS = ("records", "data", "items", "results", "rows")

# Pagination / list-envelope metadata. Industry-neutral vocabulary for detecting
# a collection wrapper that should unwrap to nested rows — not a primary entity.
_COLLECTION_ENVELOPE_META_KEYS = frozenset({
    "page",
    "pages",
    "total",
    "count",
    "size",
    "limit",
    "offset",
    "next",
    "previous",
    "prev",
    "cursor",
    "has_more",
    "meta",
    "links",
    "pagination",
    "total_count",
    "totalcount",
    "page_size",
    "pagesize",
    "page_number",
    "pagenumber",
    "per_page",
    "perpage",
    "number_of_elements",
    "numberofelements",
})


def _dict_has_resource_identity(value: dict[str, Any]) -> bool:
    """True when the object carries a conventional resource identity scalar."""
    for key, child in value.items():
        if (
            isinstance(child, bool)
            or not isinstance(child, (str, int, float))
            or not str(child).strip()
        ):
            continue
        raw_key = str(key).strip()
        normalized = re.sub(r"[^a-z0-9]+", "", raw_key.lower())
        if (
            normalized in {"id", "uuid", "guid", "key"}
            or raw_key.lower().endswith(("_id", "-id"))
            or raw_key.endswith(("Id", "ID"))
        ):
            return True
    return False


def _dict_has_primary_entity_scalars(
    value: dict[str, Any],
    *,
    skip_key: str,
) -> bool:
    """True when parent has non-envelope business scalars beyond a nested list.

    Distinguishes ``{id, status, discount_amount, items:[...]}`` (primary entity
    with embedded children) from ``{items:[...], total: N}`` (collection envelope).
    """
    for key, child in value.items():
        if key == skip_key:
            continue
        if isinstance(child, (dict, list, bool)) or child is None:
            continue
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if (
            normalized in {"id", "uuid", "key"}
            or normalized.endswith("_id")
            or normalized in _COLLECTION_ENVELOPE_META_KEYS
            or _server_managed_field(key)
        ):
            continue
        if isinstance(child, (str, int, float)) and str(child).strip():
            return True
    return False


def _entity_rows(value: Any) -> list[dict[str, Any]]:
    """Project an observed body into entity row dicts for field extraction.

    Collection envelopes (``{items: [...], total: N}``) unwrap to nested rows.
    Primary resources that embed a child collection (``{id, status, amounts,
    items: [...]}``) keep the parent row and also expose nested children so
    conservation/state terms can resolve on either surface. Silently discarding
    the parent caused CONSERVATION_VALUES_MISSING when terms lived on the
    parent while ``items`` triggered unwrap.
    """
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in _COLLECTION_WRAPPER_KEYS:
            nested = value.get(key)
            if isinstance(nested, list):
                nested_rows = [dict(item) for item in nested if isinstance(item, dict)]
                if _dict_has_resource_identity(value) and _dict_has_primary_entity_scalars(
                    value,
                    skip_key=key,
                ):
                    return [dict(value), *nested_rows]
                return nested_rows
        return [dict(value)]
    return []


def _entity_identity_key(entity: dict[str, Any]) -> str:
    scalar_identities: list[tuple[str, Any]] = []
    for key, child in entity.items():
        normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if isinstance(child, (str, int, float)) and str(child).strip():
            if normalized_key in {"id", "uuid", "key"}:
                scalar_identities.append((normalized_key, child))
    if not scalar_identities:
        fallback_identities = []
        for key, child in entity.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if (
                normalized_key.endswith("_id")
                and isinstance(child, (str, int, float))
                and str(child).strip()
            ):
                fallback_identities.append((normalized_key, child))
        if len(fallback_identities) == 1:
            scalar_identities = fallback_identities
    if not scalar_identities:
        return ""
    key, child = sorted(
        scalar_identities,
        key=lambda item: ({"id": 0, "uuid": 1, "key": 2}.get(item[0], 3), item[0]),
    )[0]
    return _fingerprint({"key": key, "value": child})


def _identity_value_fingerprints(value: Any) -> set[str]:
    """Return runtime binding fingerprints for source-visible identity scalars."""

    result: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
                if (
                    isinstance(child, (str, int, float))
                    and str(child).strip()
                    and (
                        normalized_key in {
                            "id", "uuid", "key", "code", "slug",
                            "ref", "reference", "number",
                        }
                        or normalized_key.endswith(("_id", "_code", "_ref", "_number"))
                    )
                ):
                    result.add(
                        hashlib.sha256(str(child).encode("utf-8")).hexdigest()[:12]
                    )
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result


def _empty_collection_body(value: Any) -> bool:
    return isinstance(value, list) and not value


def _source_declared_control_fixture_absence(
    *,
    control: dict[str, Any],
    treatment: dict[str, Any],
    binding_materialization_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    control_actor_ref = _text(control.get("actor_ref"))
    control_path = _text(control.get("path")).split("?", 1)[0]
    treatment_path = _text(treatment.get("path")).split("?", 1)[0]
    if (
        _text(control.get("method")).upper() != "GET"
        or _text(treatment.get("method")).upper() != "GET"
        or not control_path
        or control_path != treatment_path
        or not _empty_collection_body(treatment.get("body"))
    ):
        return {}
    control_identity_hashes = _identity_value_fingerprints(control.get("body"))
    if not control_identity_hashes:
        return {}
    treatment_identity_hashes = _identity_value_fingerprints(treatment.get("body"))
    for receipt in binding_materialization_receipts:
        row = _dict(receipt)
        value_fingerprint = _text(row.get("value_fingerprint"))
        if not (
            _text(row.get("status")).upper() == "BOUND"
            and _text(row.get("fixture_id")) == "control_resource"
            and _text(row.get("fixture_setup_status")) == "completed"
            and _text(row.get("ownership_proof_status")) == "OBSERVED"
            and value_fingerprint
            and value_fingerprint in control_identity_hashes
            and value_fingerprint not in treatment_identity_hashes
        ):
            continue
        owner_actor_ref = _text(row.get("owner_actor_ref"))
        if control_actor_ref and owner_actor_ref and owner_actor_ref != control_actor_ref:
            continue
        cleanup_status = _text(row.get("fixture_cleanup_status")).lower()
        if cleanup_status and cleanup_status not in {"pending", "completed"}:
            continue
        return {
            "fixture_id": _text(row.get("fixture_id")),
            "fixture_value_fingerprint": value_fingerprint,
            "fixture_owner_actor_ref": owner_actor_ref,
            "fixture_cleanup_status": cleanup_status,
        }
    return {}


def _business_field_change_count(before_body: Any, after_body: Any) -> int:
    before_rows = {
        key: _without_server_managed_fields(row)
        for row in _entity_rows(before_body)
        for key in [_entity_identity_key(row)]
        if key
    }
    after_rows = {
        key: _without_server_managed_fields(row)
        for row in _entity_rows(after_body)
        for key in [_entity_identity_key(row)]
        if key
    }
    shared_keys = set(before_rows).intersection(after_rows)
    if shared_keys:
        return sum(
            1
            for key in shared_keys
            if _canonical(before_rows[key]) != _canonical(after_rows[key])
        )
    before_clean = _without_server_managed_fields(before_body)
    after_clean = _without_server_managed_fields(after_body)
    if (
        not before_rows
        and not after_rows
        and isinstance(before_clean, (dict, list))
        and isinstance(after_clean, (dict, list))
        and _canonical(before_clean) != _canonical(after_clean)
    ):
        return 1
    return 0


def observe_authorization_comparison(
    *,
    control: dict[str, Any],
    treatment: dict[str, Any],
    require_same_resource: bool,
    business_effect: dict[str, Any] | None = None,
    binding_materialization_receipts: list[dict[str, Any]] | None = None,
    identity_keys: Iterable[str] | None = None,
    comparison_dimension: str = "",
) -> dict[str, Any]:
    """Compare authorized and restricted observations without status-only proof.

    ``identity_keys`` carries the source-declared primary/unique key names for
    the entity being addressed, so resource identity is decided by what the
    source says identifies a row rather than by field-name vocabulary.
    ``comparison_dimension`` (ROLE_PERMISSION / OWNERSHIP_RELATION /
    TENANT_SCOPE) declares whether the gate under test restricts the operation
    itself (status equality is decisive) or partitions per-owner resources
    (body evidence is required).
    """

    control_row = _dict(control)
    treatment_row = _dict(treatment)
    base_evidence: dict[str, Any] = {
        "control_status": _response_status(control_row),
        "treatment_status": _response_status(treatment_row),
        "control_body_fingerprint": _fingerprint(control_row.get("body")),
        "treatment_body_fingerprint": _fingerprint(treatment_row.get("body")),
        "same_resource_required": bool(require_same_resource),
        "same_resource_proven": False,
        "owner_can_access": None,
        "viewer_can_access": None,
        "leak_detected": None,
    }
    if not control_row or not _is_success(control_row):
        # A control that never succeeded cannot establish that owner and viewer
        # addressed the same resource, so owner/viewer access and leak cannot be
        # inferred from status codes alone.
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="AUTHORIZED_CONTROL_FAILED",
            evidence=base_evidence,
        )
    if _text(control_row.get("method")).upper() != "GET":
        # When both control (authorized) and treatment (unauthorized) writes
        # are accepted (2xx), the HTTP response IS the authorization evidence:
        # the restricted actor could perform the same mutation as the owner.
        # No business-effect readback is needed to prove this leak.
        if _is_success(control_row) and _is_success(treatment_row):
            base_evidence.update({
                "same_resource_proven": True,
                "resource_match_basis": "dual_accepted_write_status_comparison",
                "owner_can_access": True,
                "viewer_can_access": True,
                "leak_detected": True,
            })
            return _receipt(
                observer_id="authorization_comparison",
                status="OBSERVED",
                evidence=base_evidence,
            )
        effect = _dict(business_effect)
        control_effect = effect.get("control_effect_count")
        treatment_effect = effect.get("treatment_effect_count")
        base_evidence.update({
            "control_effect_count": control_effect,
            "treatment_effect_count": treatment_effect,
        })
        # An explicit rejection of the restricted treatment write is itself
        # the authorization observation: the same mutation the authorized
        # control performed was denied for the viewer, so no business-effect
        # readback is needed to prove that denial.  This mirrors the GET
        # branch below (explicit 401/403/404 => viewer denied) and must run
        # before the business-effect requirement, otherwise a control write
        # whose readback could not be observed would turn a proven denial
        # into INDETERMINATE.
        if _response_status(treatment_row) in {401, 403, 404}:
            control_path = _text(control_row.get("path")).split("?", 1)[0]
            treatment_path = _text(treatment_row.get("path")).split("?", 1)[0]
            same_path = bool(control_path and control_path == treatment_path)
            base_evidence.update({
                "same_resource_proven": same_path,
                "resource_match_basis": (
                    "same_requested_resource_path_and_explicit_deny"
                    if same_path
                    else "explicit_deny_different_resource"
                ),
                "owner_can_access": True,
                "viewer_can_access": False,
                "leak_detected": False,
            })
            return _receipt(
                observer_id="authorization_comparison",
                status="OBSERVED",
                evidence=base_evidence,
            )
        if effect.get("business_effect_observed") is not True or control_effect is None:
            return _receipt(
                observer_id="authorization_comparison",
                status="INDETERMINATE",
                reason_code="WRITE_EFFECT_EVIDENCE_REQUIRED",
                evidence=base_evidence,
            )
        if int(control_effect) <= 0:
            return _receipt(
                observer_id="authorization_comparison",
                status="INDETERMINATE",
                reason_code="AUTHORIZED_CONTROL_EFFECT_MISSING",
                evidence=base_evidence,
            )
        if treatment_effect is not None and int(treatment_effect) > 0:
            base_evidence.update({
                "same_resource_proven": True,
                "resource_match_basis": "source_grounded_business_effect",
                "owner_can_access": True,
                "viewer_can_access": True,
                "leak_detected": True,
            })
            return _receipt(
                observer_id="authorization_comparison",
                status="OBSERVED",
                evidence=base_evidence,
            )
        if _is_success(treatment_row) and int(treatment_effect or 0) == 0:
            base_evidence.update({
                "same_resource_proven": True,
                "resource_match_basis": "source_grounded_business_effect_absence",
                "owner_can_access": True,
                "viewer_can_access": False,
                "viewer_request_accepted": True,
                "viewer_business_effect_observed": False,
                "leak_detected": False,
            })
            return _receipt(
                observer_id="authorization_comparison",
                status="OBSERVED",
                evidence=base_evidence,
            )
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="WRITE_EFFECT_EVIDENCE_REQUIRED",
            evidence=base_evidence,
        )
    # V1.8: Role-permission comparisons gate the OPERATION itself, not a
    # user-partitioned row set. A role gate returns 403 for denied roles
    # regardless of response content — so control 2xx + treatment 2xx on the
    # same path proves the gate is absent even when both bodies are empty or
    # byte-identical (empty audit logs, zeroed aggregates). This mirrors the
    # write branch's dual-accepted-write proof; owner/tenant comparisons keep
    # the strict body-evidence path below.
    if (
        _text(comparison_dimension).upper() == "ROLE_PERMISSION"
        and _is_success(treatment_row)
        and (
            _text(control_row.get("path")).split("?", 1)[0]
            == _text(treatment_row.get("path")).split("?", 1)[0]
        )
    ):
        base_evidence.update({
            "same_resource_proven": True,
            "resource_match_basis": "role_permission_dual_success_status_comparison",
            "owner_can_access": True,
            "viewer_can_access": True,
            "leak_detected": True,
        })
        return _receipt(
            observer_id="authorization_comparison",
            status="OBSERVED",
            evidence=base_evidence,
        )
    if not _meaningful_resource_body(control_row):
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="CONTROL_RESOURCE_EVIDENCE_MISSING",
            evidence=base_evidence,
        )
    base_evidence["owner_can_access"] = True

    treatment_status = _response_status(treatment_row)
    if treatment_status in {401, 403, 404}:
        same_path = (
            _text(control_row.get("path")).split("?", 1)[0]
            == _text(treatment_row.get("path")).split("?", 1)[0]
        )
        if require_same_resource and not same_path:
            return _receipt(
                observer_id="authorization_comparison",
                status="INDETERMINATE",
                reason_code="SAME_RESOURCE_NOT_PROVEN",
                evidence=base_evidence,
            )
        base_evidence.update({
            "same_resource_proven": same_path,
            "resource_match_basis": "same_requested_resource_path",
            "viewer_can_access": False,
            "leak_detected": False,
        })
        return _receipt(
            observer_id="authorization_comparison",
            status="OBSERVED",
            evidence=base_evidence,
        )
    if not _is_success(treatment_row):
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="TREATMENT_REJECTION_AMBIGUOUS",
            evidence=base_evidence,
        )
    if not _meaningful_resource_body(treatment_row):
        fixture_absence = _source_declared_control_fixture_absence(
            control=control_row,
            treatment=treatment_row,
            binding_materialization_receipts=[
                dict(row)
                for row in _list(binding_materialization_receipts)
                if isinstance(row, dict)
            ],
        )
        if require_same_resource and fixture_absence:
            base_evidence.update({
                "same_resource_proven": True,
                "resource_match_basis": (
                    "source_declared_control_fixture_absent_from_treatment_collection"
                ),
                "owner_can_access": True,
                "viewer_can_access": False,
                "leak_detected": False,
                **fixture_absence,
            })
            return _receipt(
                observer_id="authorization_comparison",
                status="OBSERVED",
                evidence=base_evidence,
            )
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="TREATMENT_RESOURCE_EVIDENCE_MISSING",
            evidence=base_evidence,
        )

    same_path = (
        _text(control_row.get("path")).split("?", 1)[0]
        == _text(treatment_row.get("path")).split("?", 1)[0]
    )
    bodies_equal = _canonical(control_row.get("body")) == _canonical(treatment_row.get("body"))
    identity_overlap = bool(
        _identity_fingerprints(control_row.get("body"), identity_keys)
        .intersection(_identity_fingerprints(treatment_row.get("body"), identity_keys))
    )
    # V1.8: Aggregate documents (reports, totals, counts, configuration) carry
    # no row identity — the document itself IS the resource. When both actors
    # observe the byte-identical non-error document on the same path and no
    # identity-bearing field exists anywhere in either body, equality proves
    # the viewer received the owner's aggregate: identity-overlap is
    # structurally impossible for aggregates and must not block the leak.
    # Row-shaped bodies (any identity field present) keep the strict
    # identity-overlap rule — equal payloads alone never prove resource
    # identity for entity collections.
    _aggregate_document = (
        bodies_equal
        and not _identity_fingerprints(control_row.get("body"), identity_keys)
        and not _identity_fingerprints(treatment_row.get("body"), identity_keys)
    )
    # Equal payloads alone are not resource identity: a shared login/error
    # document can be byte-identical for both actors. Require a structural
    # identity value from the observed JSON on the same requested path.
    same_resource_proven = same_path and (identity_overlap or _aggregate_document)
    if require_same_resource and not same_resource_proven:
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="SAME_RESOURCE_NOT_PROVEN",
            evidence=base_evidence,
        )

    base_evidence.update({
        "same_resource_proven": same_resource_proven or not require_same_resource,
        "resource_match_basis": (
            "identity_overlap"
            if identity_overlap
            else "canonical_body_match_without_identity"
            if bodies_equal
            else "not_required"
        ),
        "viewer_can_access": True,
        "leak_detected": True,
    })
    return _receipt(
        observer_id="authorization_comparison",
        status="OBSERVED",
        evidence=base_evidence,
    )


def _observe_http_response(
    control: dict[str, Any],
    treatment: dict[str, Any],
) -> dict[str, Any]:
    rows = [row for row in (control, treatment) if row]
    statuses = [_response_status(row) for row in rows]
    if not rows or any(status <= 0 for status in statuses):
        # Governed writes blocked before transport leave status_code=0 with
        # write_request_attempt_count=0. That is missing observation, not a
        # harness HTTP failure — keep FAILED only when transport was attempted.
        missing_rows = [
            row
            for row, status in zip(rows, statuses)
            if isinstance(row, dict) and status <= 0
        ]
        zero_transport_only = bool(missing_rows) and all(
            isinstance(row.get("governance_receipt"), dict)
            and int(
                _dict(row.get("governance_receipt")).get(
                    "write_request_attempt_count"
                )
                or 0
            )
            == 0
            for row in missing_rows
        )
        return _receipt(
            observer_id="http_response",
            status="INDETERMINATE" if zero_transport_only else "FAILED",
            reason_code=(
                "HTTP_RESPONSE_NOT_ATTEMPTED"
                if zero_transport_only
                else "HTTP_RESPONSE_MISSING"
            ),
            evidence={"statuses": statuses},
        )
    outcomes = [
        _business_outcome_from_body(row.get("body"))
        for row in rows
    ]
    primary = outcomes[-1] if outcomes else {}
    return _receipt(
        observer_id="http_response",
        status="OBSERVED",
        evidence={
            "statuses": statuses,
            "response_fingerprints": [_fingerprint(row.get("body")) for row in rows],
            "business_outcomes": outcomes,
            "business_outcome": primary,
            "business_rejected": bool(primary.get("business_rejected")),
            "outcome_status": _text(primary.get("outcome_status")),
            "success_flag": primary.get("success_flag"),
        },
    )


def _observe_actor_identity(
    control_actor_ref: str,
    treatment_actor_ref: str,
) -> dict[str, Any]:
    actor_refs = [
        value
        for value in dict.fromkeys((_text(control_actor_ref), _text(treatment_actor_ref)))
        if value
    ]
    if not actor_refs:
        return _receipt(
            observer_id="actor_identity",
            status="FAILED",
            reason_code="ACTOR_IDENTITY_MISSING",
            evidence={"actor_ref_fingerprints": []},
        )
    return _receipt(
        observer_id="actor_identity",
        status="OBSERVED",
        evidence={
            "actor_ref_fingerprints": [_fingerprint(value) for value in actor_refs],
            "distinct_actor_count": len(actor_refs),
        },
    )


def _observe_resource_ownership(
    binding_receipts: list[dict[str, Any]],
    *,
    expected_owner_actor_ref: str,
) -> dict[str, Any]:
    proof = next((
        receipt
        for receipt in binding_receipts
        if isinstance(receipt, dict)
        and _text(receipt.get("ownership_proof_status")) == "OBSERVED"
        and _text(receipt.get("owner_actor_ref")) == _text(expected_owner_actor_ref)
        and _text(receipt.get("value_fingerprint"))
    ), None)
    if not isinstance(proof, dict):
        return _receipt(
            observer_id="resource_ownership",
            status="INDETERMINATE",
            reason_code="RESOURCE_OWNERSHIP_PROOF_MISSING",
            evidence={
                "owner_actor_ref_fingerprint": (
                    _fingerprint(expected_owner_actor_ref)
                    if _text(expected_owner_actor_ref)
                    else ""
                ),
                "resource_identity_fingerprint": "",
            },
        )
    return _receipt(
        observer_id="resource_ownership",
        status="OBSERVED",
        evidence={
            "fixture_id": _text(proof.get("fixture_id")),
            "owner_actor_ref_fingerprint": _fingerprint(expected_owner_actor_ref),
            "resource_identity_fingerprint": _text(proof.get("value_fingerprint")),
            "ownership_proof_ref": _text(proof.get("ownership_proof_ref")),
        },
    )


def _effect_window(steps: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    if not steps:
        return {}, "BUSINESS_EFFECT_WRITE_MISSING"
    first_governance = _dict(steps[0].get("governance_receipt"))
    last_governance = _dict(steps[-1].get("governance_receipt"))
    before = _dict(first_governance.get("before"))
    after = _dict(last_governance.get("after"))
    write = _dict(last_governance.get("write"))
    write_status = _raw_http_status(write)
    response_bound_after = _dict(last_governance.get("response_bound_after"))
    if not (
        200 <= _raw_http_status(before) < 300
        and 200 <= _raw_http_status(after) < 300
    ):
        if write_status > 0 and not (200 <= write_status < 300):
            return {
                "before_identity_count": 0,
                "after_identity_count": 0,
                "identity_effect_count": 0,
                "business_field_change_count": 0,
                "effect_count": 0,
                "before_fingerprint": _fingerprint(before.get("body")),
                "after_fingerprint": _fingerprint(after.get("body")),
                "effect_basis": "write_not_accepted",
            }, ""
        if (
            200 <= write_status < 300
            and 200 <= _raw_http_status(response_bound_after) < 300
            and _identity_value_fingerprints(write.get("body")).intersection(
                _identity_value_fingerprints(response_bound_after.get("body"))
            )
        ):
            after_ids = _identity_fingerprints(response_bound_after.get("body"))
            return {
                "before_identity_count": 0,
                "after_identity_count": len(after_ids),
                "identity_effect_count": 1,
                "business_field_change_count": 0,
                "effect_count": 1,
                "before_fingerprint": _fingerprint(before.get("body")),
                "after_fingerprint": _fingerprint(response_bound_after.get("body")),
                "effect_basis": "response_bound_create_observer",
                "response_bound_after_ref": _text(
                    last_governance.get("response_bound_after_ref")
                ),
            }, ""
        # A failed observation is not an observation of zero effect. Reporting
        # all-zero counts here would make fabricated evidence indistinguishable
        # from a real "nothing changed" reading.
        return {}, "BUSINESS_EFFECT_OBSERVATION_FAILED"
    if not isinstance(before.get("body"), (dict, list)) or not isinstance(after.get("body"), (dict, list)):
        return {}, "BUSINESS_EFFECT_BODY_UNSUPPORTED"
    before_ids = _identity_fingerprints(before.get("body"))
    after_ids = _identity_fingerprints(after.get("body"))
    identity_effect_count = len(after_ids - before_ids)
    business_field_change_count = _business_field_change_count(
        before.get("body"),
        after.get("body"),
    )
    return {
        "before_identity_count": len(before_ids),
        "after_identity_count": len(after_ids),
        "identity_effect_count": identity_effect_count,
        "business_field_change_count": business_field_change_count,
        "effect_count": identity_effect_count + business_field_change_count,
        "before_fingerprint": _fingerprint(before.get("body")),
        "after_fingerprint": _fingerprint(after.get("body")),
    }, ""


def _observe_business_effect(
    execution_steps: list[dict[str, Any]],
    *,
    aggregate_control_treatment: bool = False,
    require_treatment_window: bool = False,
) -> dict[str, Any]:
    write_steps = [
        step
        for step in execution_steps
        if isinstance(step, dict)
        and _text(step.get("phase")) in {"control", "treatment"}
        and _text(step.get("method")).upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and _dict(step.get("governance_receipt"))
    ]
    evidence: dict[str, Any] = {
        "http_statuses": [_response_status(step) for step in write_steps],
        "business_effect_observed": False,
    }
    if not write_steps:
        return _receipt(
            observer_id="business_effect",
            status="INDETERMINATE",
            reason_code="BUSINESS_EFFECT_WRITE_MISSING",
            evidence=evidence,
        )

    phase_windows: dict[str, dict[str, Any]] = {}
    phase_reasons: dict[str, str] = {}
    for phase in ("control", "treatment"):
        phase_steps = [step for step in write_steps if _text(step.get("phase")) == phase]
        if not phase_steps:
            continue
        window, reason = _effect_window(phase_steps)
        if reason:
            # A failed observation for one phase must not erase an observed
            # window for the other: validation and authorization assertions
            # consume per-phase effect counts, and a rejected treatment write
            # (write_not_accepted) is a complete zero-side-effect observation
            # even when the control readback could not be captured.
            phase_reasons[phase] = reason
            continue
        phase_windows[phase] = window
        evidence[f"{phase}_effect_count"] = window["effect_count"]

    if require_treatment_window and "treatment" not in phase_windows:
        # Replay-window semantics (idempotency/effect-cardinality): the
        # property is measured on the TREATMENT window — the replayed input
        # must add zero new effect. A missing treatment phase means the
        # replay never executed; falling back to the control window would
        # report the control's own effect as a violation. Not observing the
        # replay is not an observation of a violation.
        return _receipt(
            observer_id="business_effect",
            status="INDETERMINATE",
            reason_code="BUSINESS_EFFECT_TREATMENT_WINDOW_MISSING",
            evidence=evidence,
        )

    combined_window: dict[str, Any] = {}
    if aggregate_control_treatment:
        combined_window, combined_reason = _effect_window(write_steps)
        if combined_reason:
            combined_window = {}
        else:
            evidence["combined_effect_count"] = combined_window["effect_count"]
    selected = (
        combined_window
        or phase_windows.get("treatment")
        or phase_windows.get("control")
    )
    if not selected:
        return _receipt(
            observer_id="business_effect",
            status="INDETERMINATE",
            reason_code=(
                phase_reasons.get("control")
                or phase_reasons.get("treatment")
                or "BUSINESS_EFFECT_WINDOW_MISSING"
            ),
            evidence=evidence,
        )
    evidence.update({
        "effect_count": selected["effect_count"],
        "identity_effect_count": selected["identity_effect_count"],
        "business_field_change_count": selected["business_field_change_count"],
        "before_identity_count": selected["before_identity_count"],
        "after_identity_count": selected["after_identity_count"],
        "before_fingerprint": selected["before_fingerprint"],
        "after_fingerprint": selected["after_fingerprint"],
        "business_effect_observed": True,
        "zero_effect_on_accepted_write": (
            int(selected["effect_count"] or 0) == 0
            and any(
                200 <= _response_status(step) < 300
                for step in write_steps
            )
        ),
    })
    write_bodies = [
        _dict(step.get("body") if "body" in step else None)
        or _dict(_dict(step.get("governance_receipt")).get("write")).get("body")
        for step in write_steps
    ]
    treatment_body = None
    for step in reversed(write_steps):
        if _text(step.get("phase")) == "treatment":
            treatment_body = step.get("body")
            if treatment_body is None:
                treatment_body = _dict(
                    _dict(step.get("governance_receipt")).get("write")
                ).get("body")
            break
    if treatment_body is None and write_bodies:
        treatment_body = write_bodies[-1]
    outcome = _business_outcome_from_body(treatment_body)
    evidence["business_outcome"] = outcome
    evidence["business_rejected"] = bool(outcome.get("business_rejected"))
    evidence["outcome_status"] = _text(outcome.get("outcome_status"))
    if _text(selected.get("effect_basis")):
        evidence["effect_basis"] = _text(selected.get("effect_basis"))
    if _text(selected.get("response_bound_after_ref")):
        evidence["response_bound_after_ref"] = _text(
            selected.get("response_bound_after_ref")
        )
    return _receipt(
        observer_id="business_effect",
        status="OBSERVED",
        evidence=evidence,
    )


def _conservation_terms_from_experiment(experiment: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for assertion in _list(experiment.get("assertions")):
        if not isinstance(assertion, dict):
            continue
        prop = _dict(assertion.get("property"))
        expression = _dict(assertion.get("expression") or prop.get("expression"))
        equation = _dict(
            assertion.get("equation")
            or prop.get("equation")
            or expression.get("equation")
        )
        kind = _text(
            assertion.get("kind")
            or assertion.get("type")
            or prop.get("kind")
            or expression.get("kind")
        ).lower()
        if kind in {"field_delta", "postcondition"}:
            # V1.6.0: observe only rule-declared field operands (cf_* or name).
            for field_spec in _list(
                assertion.get("fields")
                or assertion.get("operands")
                or expression.get("operands")
            ):
                if isinstance(field_spec, dict):
                    _f = _text(field_spec.get("field_id") or field_spec.get("field"))
                    if _f:
                        terms.append(_f)
        elif kind != "conservation" and kind not in ("cross_entity_consistency", "limit_constraint") and not equation:
            continue
        for term in _list(equation.get("terms") or equation.get("fields")):
            if isinstance(term, dict):
                # Prefer JSON field name over cf_* so observer evidence keys match bodies.
                _tf = _text(term.get("field") or term.get("field_id"))
                if _tf:
                    terms.append(_tf)
            elif _text(term):
                raw_term = _text(term)
                mapped = False
                for field_spec in _list(
                    assertion.get("operands") or expression.get("operands")
                ):
                    if not isinstance(field_spec, dict):
                        continue
                    if _text(field_spec.get("field_id")) == raw_term and _text(
                        field_spec.get("field")
                    ):
                        terms.append(_text(field_spec.get("field")))
                        mapped = True
                        break
                if not mapped:
                    terms.append(raw_term)
        # ── Extract fields from structured_expression (multi-entity resolver output) ──
        structured = _dict(assertion.get("structured_expression"))
        if structured:
            for side_key in ("left", "right"):
                side = _dict(structured.get(side_key))
                _sf = _text(side.get("field"))
                if _sf:
                    terms.append(_sf)
            # Also extract from condition/constraint (state consistency)
            for part_key in ("condition", "constraint"):
                part = _dict(structured.get(part_key))
                _pf = _text(part.get("field"))
                if _pf:
                    terms.append(_pf)
            # Extract from observer_requirements
            for req in _list(assertion.get("observer_requirements")):
                if isinstance(req, dict):
                    for rf in _list(req.get("fields")):
                        if _text(rf):
                            terms.append(_text(rf))
    return list(dict.fromkeys(terms))


def _numeric_snapshot_values(
    body: Any,
    terms: list[str],
    *,
    allow_unscoped_numeric: bool = False,
) -> dict[str, int | float]:
    """Extract numeric values for explicitly required field terms.

    V1.6.0 conservation/oracle path: empty ``terms`` must fail closed — never
    invent a formula by scanning every JSON numeric. Final-state fingerprinting
    may pass ``allow_unscoped_numeric=True`` for a non-oracle identity marker only.
    """
    term_lookup = {_normalized_field_name(term): term for term in terms if _text(term)}
    if not term_lookup and not allow_unscoped_numeric:
        return {}
    values: dict[str, int | float] = {}
    for row in _entity_rows(body):
        cleaned = _without_server_managed_fields(row)
        if not isinstance(cleaned, dict):
            continue
        for key, child in cleaned.items():
            normalized = _normalized_field_name(key)
            if not normalized or normalized in {"id", "uuid", "key"} or normalized.endswith("_id"):
                continue
            if isinstance(child, bool):
                continue
            if not isinstance(child, (int, float)):
                # Try to parse string numbers (common in JSON APIs)
                if isinstance(child, str):
                    try:
                        child = float(child) if "." in child else int(child)
                    except (ValueError, TypeError):
                        continue
                else:
                    continue
            if term_lookup and normalized not in term_lookup:
                continue
            out_key = term_lookup.get(normalized, normalized)
            values[out_key] = values.get(out_key, 0) + child
    return {
        key: values[key]
        for key in sorted(values)
    }


def _observe_entity_state(
    execution_steps: list[dict[str, Any]],
    *,
    experiment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    write_steps = [
        step
        for step in execution_steps
        if isinstance(step, dict)
        and _text(step.get("phase")) in {"control", "treatment"}
        and _text(step.get("method")).upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and _dict(step.get("governance_receipt"))
    ]
    evidence: dict[str, Any] = {
        "http_statuses": [_response_status(step) for step in write_steps],
        "entity_state_observed": False,
        "state_change_count": 0,
    }
    if not write_steps:
        return _receipt(
            observer_id="entity_state",
            status="INDETERMINATE",
            reason_code="ENTITY_STATE_WRITE_MISSING",
            evidence=evidence,
        )

    state_windows: list[dict[str, Any]] = []
    for step in write_steps:
        window, reason = _effect_window([step])
        if reason:
            return _receipt(
                observer_id="entity_state",
                status="INDETERMINATE",
                reason_code=reason.replace("BUSINESS_EFFECT", "ENTITY_STATE"),
                evidence=evidence,
            )
        state_changed = window["before_fingerprint"] != window["after_fingerprint"]
        state_windows.append({
            "phase": _text(step.get("phase")),
            "step_id": _text(step.get("step_id")),
            "before_identity_count": window["before_identity_count"],
            "after_identity_count": window["after_identity_count"],
            "effect_count": window["effect_count"],
            "before_fingerprint": window["before_fingerprint"],
            "after_fingerprint": window["after_fingerprint"],
            "state_changed": state_changed,
        })

    conservation_terms = _conservation_terms_from_experiment(_dict(experiment))
    # Prefer compile-time field observation contracts when present.
    for _obs in _list(_dict(experiment).get("observers")):
        if not isinstance(_obs, dict):
            continue
        for _fid in _list(_obs.get("required_field_ids")):
            if _text(_fid):
                conservation_terms.append(_text(_fid))
        _foc = _dict(_obs.get("field_observation_contract"))
        for _fid in _list(_foc.get("required_field_ids")):
            if _text(_fid):
                conservation_terms.append(_text(_fid))
    conservation_terms = list(dict.fromkeys(conservation_terms))
    first_governance = _dict(write_steps[0].get("governance_receipt"))
    last_governance = _dict(write_steps[-1].get("governance_receipt"))
    before_values = _numeric_snapshot_values(
        _dict(first_governance.get("before")).get("body"),
        conservation_terms,
    )
    after_values = _numeric_snapshot_values(
        _dict(last_governance.get("after")).get("body"),
        conservation_terms,
    )
    if before_values or after_values:
        evidence.update({
            "conservation_terms": conservation_terms,
            "before_values": before_values,
            "after_values": after_values,
        })
    elif conservation_terms:
        # Terms specified but not found in snapshots — keep terms visible.
        evidence.update({
            "conservation_terms": conservation_terms,
            "before_values": {},
            "after_values": {},
        })
        return _receipt(
            observer_id="entity_state",
            status="INDETERMINATE",
            reason_code="CONSERVATION_VALUES_MISSING",
            evidence=evidence,
        )
    else:
        # No terms specified and no values extracted — still provide empty dicts
        evidence.update({
            "conservation_terms": [],
            "before_values": {},
            "after_values": {},
        })

    evidence.update({
        "entity_state_observed": True,
        "state_change_count": sum(1 for row in state_windows if row["state_changed"]),
        "effect_count": sum(int(row["effect_count"]) for row in state_windows),
        "state_windows": state_windows,
    })
    return _receipt(
        observer_id="entity_state",
        status="OBSERVED",
        evidence=evidence,
    )


_STATE_FIELD_NAMES = {
    "state",
    "status",
    "stage",
    "phase",
    "lifecycle_state",
    "workflow_state",
    "resource_state",
    "resource_status",
}


def _normalized_field_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(value).lower()).strip("_")


def _state_field_priority(field_name: str) -> int:
    normalized = _normalized_field_name(field_name)
    if normalized in _STATE_FIELD_NAMES:
        return 0
    if normalized.endswith("_state") or normalized.endswith("_status"):
        return 1
    return 2


def _extract_state_value(value: Any) -> tuple[str, str]:
    candidates: list[tuple[int, str, str]] = []
    for row in _entity_rows(value):
        for key, child in row.items():
            normalized_key = _normalized_field_name(key)
            if normalized_key not in _STATE_FIELD_NAMES and not (
                normalized_key.endswith("_state")
                or normalized_key.endswith("_status")
            ):
                continue
            if isinstance(child, (str, int, float, bool)) and _text(child):
                candidates.append((_state_field_priority(str(key)), str(key), _text(child)))
    if not candidates:
        return "", ""
    selected = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0]
    same_field_values = {
        value
        for priority, field, value in candidates
        if priority == selected[0] and field == selected[1]
    }
    if len(same_field_values) != 1:
        return "", selected[1]
    return selected[2], selected[1]


def _final_entity_snapshot_marker(value: Any) -> tuple[str, str]:
    """Observe a final entity snapshot when no lifecycle state/status field exists.

    Concurrency final invariants often bind quantity-bearing resources (stock,
    balance, quota) whose effect reads return numeric business fields rather than
    workflow status. Prefer a deterministic fingerprint of observed numeric
    business fields; otherwise a single stable scalar business field.
    Lifecycle before/after observers must not use this fallback.
    """

    numeric_values = _numeric_snapshot_values(value, [], allow_unscoped_numeric=True)
    if numeric_values:
        fingerprint = _fingerprint(numeric_values)
        return fingerprint, "entity_numeric_snapshot"
    scalar_fields: list[tuple[str, str]] = []
    for row in _entity_rows(value):
        cleaned = _without_server_managed_fields(row)
        if not isinstance(cleaned, dict):
            continue
        for key, child in cleaned.items():
            normalized = _normalized_field_name(key)
            if (
                not normalized
                or normalized in {"id", "uuid", "key"}
                or normalized.endswith("_id")
            ):
                continue
            if isinstance(child, bool):
                continue
            if isinstance(child, (str, int, float)) and _text(child):
                scalar_fields.append((str(key), _text(child)))
    if len(scalar_fields) != 1:
        return "", ""
    return scalar_fields[0][1], scalar_fields[0][0]


def _main_governed_write_steps(execution_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        step
        for step in execution_steps
        if isinstance(step, dict)
        and _text(step.get("phase")) in {"control", "treatment"}
        and _text(step.get("method")).upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and _dict(step.get("governance_receipt"))
    ]


def _state_snapshot_evidence(
    step: dict[str, Any],
    *,
    snapshot_key: str,
    state_key: str,
) -> tuple[dict[str, Any], str]:
    governance = _dict(step.get("governance_receipt"))
    snapshot = _dict(governance.get(snapshot_key))
    evidence: dict[str, Any] = {
        "snapshot_phase": snapshot_key,
        "source_step_id": _text(step.get("step_id")),
        "source_phase": _text(step.get("phase")),
        "source_operation_ref": _text(step.get("operation_ref")),
        "snapshot_http_status": _raw_http_status(snapshot),
        "snapshot_body_fingerprint": _fingerprint(snapshot.get("body")),
        "snapshot_identity_count": len(_identity_fingerprints(snapshot.get("body"))),
    }
    if not (200 <= _raw_http_status(snapshot) < 300):
        return evidence, "STATE_SNAPSHOT_OBSERVATION_FAILED"
    if not isinstance(snapshot.get("body"), (dict, list)):
        return evidence, "STATE_SNAPSHOT_BODY_UNSUPPORTED"
    state_value, state_field = _extract_state_value(snapshot.get("body"))
    evidence["state_field"] = state_field
    evidence[state_key] = state_value
    if not state_value and state_key == "final_state":
        state_value, state_field = _final_entity_snapshot_marker(snapshot.get("body"))
        if state_value:
            evidence["state_field"] = state_field
            evidence[state_key] = state_value
            evidence["final_state_kind"] = "entity_snapshot"
            before_body = _dict(governance.get("before")).get("body")
            after_body = snapshot.get("body")
            # Fingerprint-only context: not a conservation formula. Oracle
            # evaluation must still require explicit required_field_ids.
            before_values = _numeric_snapshot_values(
                before_body, [], allow_unscoped_numeric=True,
            )
            after_values = _numeric_snapshot_values(
                after_body, [], allow_unscoped_numeric=True,
            )
            if before_values or after_values:
                evidence["before_values"] = before_values
                evidence["after_values"] = after_values
    if not state_value:
        return evidence, "STATE_SNAPSHOT_VALUE_MISSING"
    return evidence, ""


def _cleanup_phase_steps(execution_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return cleanup-phase steps that carry a governance after snapshot."""
    rows: list[dict[str, Any]] = []
    for step in execution_steps:
        if not isinstance(step, dict):
            continue
        if _text(step.get("phase")) != "cleanup":
            continue
        gov = _dict(step.get("governance_receipt"))
        after = _dict(gov.get("after"))
        if after and int(after.get("status") or 0) > 0:
            rows.append(step)
    return rows


def _observe_state_snapshot(
    observer_id: str,
    execution_steps: list[dict[str, Any]],
    *,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    main_steps = _main_governed_write_steps(execution_steps)
    if not main_steps:
        return _receipt(
            observer_id=observer_id,
            status="INDETERMINATE",
            reason_code="STATE_SNAPSHOT_WRITE_MISSING",
            evidence={"write_step_count": 0},
        )
    if observer_id == "before_state":
        step = main_steps[0]
        snapshot_key = "before"
        state_key = "before_state"
        cleanup_phase_excluded = True
    elif observer_id == "after_state":
        step = main_steps[-1]
        snapshot_key = "after"
        state_key = "after_state"
        cleanup_phase_excluded = True
    elif observer_id == "final_state":
        # Final state must reflect post-cleanup reality when cleanup ran.
        cleanup_steps = _cleanup_phase_steps(execution_steps)
        sealed = _dict((_dict(observations).get("after_cleanup_observation")))
        if cleanup_steps:
            step = cleanup_steps[-1]
            snapshot_key = "after"
            state_key = "final_state"
            cleanup_phase_excluded = False
        elif sealed and (
            sealed.get("body") is not None
            or int(sealed.get("status_code") or sealed.get("status") or 0) > 0
        ):
            # Synthetic governance wrapper so _state_snapshot_evidence can run.
            step = {
                "phase": "cleanup",
                "governance_receipt": {
                    "after": {
                        "status": int(
                            sealed.get("status_code") or sealed.get("status") or 0
                        ),
                        "body": sealed.get("body"),
                    },
                    "observation_path": _text(sealed.get("path")),
                },
                "observation_path": _text(sealed.get("path")),
            }
            snapshot_key = "after"
            state_key = "final_state"
            cleanup_phase_excluded = False
        else:
            step = main_steps[-1]
            snapshot_key = "after"
            state_key = "final_state"
            cleanup_phase_excluded = True
    else:
        raise ValueError(f"state_snapshot_observer_invalid:{observer_id}")
    evidence, reason = _state_snapshot_evidence(
        step,
        snapshot_key=snapshot_key,
        state_key=state_key,
    )
    evidence["write_step_count"] = len(main_steps)
    evidence["cleanup_phase_excluded"] = cleanup_phase_excluded
    if not cleanup_phase_excluded:
        evidence["status_code"] = int(
            _dict(_dict(step.get("governance_receipt")).get(snapshot_key)).get("status")
            or evidence.get("status_code")
            or 0
        )
        if evidence.get("body") is None:
            evidence["body"] = _dict(
                _dict(step.get("governance_receipt")).get(snapshot_key)
            ).get("body")
        evidence["path"] = _text(
            _dict(step.get("governance_receipt")).get("observation_path")
            or step.get("observation_path")
            or evidence.get("path")
        )
    if reason:
        return _receipt(
            observer_id=observer_id,
            status="INDETERMINATE",
            reason_code=reason,
            evidence=evidence,
        )
    return _receipt(
        observer_id=observer_id,
        status="OBSERVED",
        evidence=evidence,
    )


def _raw_barrier_events(observations: dict[str, Any]) -> list[Any]:
    for key in ("barrier_timeline", "barrier_events", "concurrency_timeline"):
        value = observations.get(key)
        if value is not None:
            return _list(value)
    return []


def _event_time_ms(event: dict[str, Any], index: int) -> int:
    for key in ("at_ms", "timestamp_ms", "offset_ms", "elapsed_ms"):
        value = event.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return index
    return index


def _normalize_barrier_timeline(events: list[Any]) -> tuple[list[dict[str, Any]], str]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(events):
        if not isinstance(raw, dict):
            return [], "BARRIER_EVENT_INVALID"
        event_name = _text(raw.get("event") or raw.get("phase") or raw.get("kind")).lower()
        participant = _text(
            raw.get("participant")
            or raw.get("slot")
            or raw.get("actor_ref")
            or raw.get("request_id")
        )
        if not event_name:
            return [], "BARRIER_EVENT_INVALID"
        normalized.append({
            "event": event_name,
            "participant": participant,
            "at_ms": _event_time_ms(raw, index),
        })
    if not normalized:
        return [], "BARRIER_TIMELINE_MISSING"
    return sorted(normalized, key=lambda item: (int(item["at_ms"]), item["event"], item["participant"])), ""


def _observe_barrier_timeline(observations: dict[str, Any]) -> dict[str, Any]:
    raw_events = _raw_barrier_events(observations)
    events, reason = _normalize_barrier_timeline(raw_events)
    if reason:
        return _receipt(
            observer_id="barrier_timeline",
            status="INDETERMINATE",
            reason_code=reason,
            evidence={"event_count": 0},
        )

    release_events = [
        event
        for event in events
        if event["event"] in {"release", "barrier_release", "start", "released"}
    ]
    participants = sorted({
        event["participant"]
        for event in events
        if event["participant"] and event["participant"].lower() != "all"
    })
    evidence = {
        "event_count": len(events),
        "release_event_count": len(release_events),
        "participant_count": len(participants),
        "barrier_released": bool(release_events),
        "timeline_fingerprint": _fingerprint(events),
        "participants_fingerprint": _fingerprint(participants),
        "first_event": dict(events[0]),
        "last_event": dict(events[-1]),
    }
    if not release_events:
        return _receipt(
            observer_id="barrier_timeline",
            status="INDETERMINATE",
            reason_code="BARRIER_RELEASE_MISSING",
            evidence=evidence,
        )
    if len(participants) < 2:
        return _receipt(
            observer_id="barrier_timeline",
            status="INDETERMINATE",
            reason_code="BARRIER_PARTICIPANT_COUNT_INSUFFICIENT",
            evidence=evidence,
        )
    return _receipt(
        observer_id="barrier_timeline",
        status="OBSERVED",
        evidence=evidence,
    )


def _observe_temporal_window(
    observations: dict[str, Any],
    *,
    assertion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe convergence only from explicit, source-bounded timeline facts."""

    timeline = _list(observations.get("temporal_timeline"))
    evidence: dict[str, Any] = {
        "timeline_event_count": len(timeline),
        "converged": False,
        "within_window": None,
        "elapsed_ms": None,
        "window_ms": None,
    }
    if not timeline:
        return _receipt(
            observer_id="temporal_window",
            status="INDETERMINATE",
            reason_code="TEMPORAL_TIMELINE_MISSING",
            evidence=evidence,
        )

    assertion_row = _dict(assertion)
    prop = _dict(assertion_row.get("property"))
    window_ms: int | None = None
    for key in ("window_ms", "max_ms", "timeout_ms", "convergence_window_ms"):
        raw = prop.get(key) if prop.get(key) is not None else assertion_row.get(key)
        if raw is None:
            continue
        try:
            window_ms = int(raw)
        except (TypeError, ValueError):
            return _receipt(
                observer_id="temporal_window",
                status="FAILED",
                reason_code="TEMPORAL_WINDOW_INVALID",
                evidence=evidence,
            )
        break
    if window_ms is None or window_ms <= 0:
        return _receipt(
            observer_id="temporal_window",
            status="INDETERMINATE",
            reason_code="TEMPORAL_WINDOW_NOT_SPECIFIED",
            evidence=evidence,
        )
    evidence["window_ms"] = window_ms

    trigger_events = [
        row for row in timeline
        if isinstance(row, dict) and _text(row.get("event")) == "trigger"
    ]
    final_events = [
        row for row in timeline
        if isinstance(row, dict) and _text(row.get("event")) == "final_observed"
    ]
    if not trigger_events:
        return _receipt(
            observer_id="temporal_window",
            status="INDETERMINATE",
            reason_code="TEMPORAL_TRIGGER_EVENT_MISSING",
            evidence=evidence,
        )
    if not final_events:
        return _receipt(
            observer_id="temporal_window",
            status="INDETERMINATE",
            reason_code="TEMPORAL_FINAL_EVENT_MISSING",
            evidence=evidence,
        )
    try:
        trigger_at = min(int(_dict(row).get("at_ms")) for row in trigger_events)
        final_at = max(int(_dict(row).get("at_ms")) for row in final_events)
    except (TypeError, ValueError):
        return _receipt(
            observer_id="temporal_window",
            status="FAILED",
            reason_code="TEMPORAL_TIMELINE_INVALID",
            evidence=evidence,
        )
    if final_at < trigger_at:
        return _receipt(
            observer_id="temporal_window",
            status="FAILED",
            reason_code="TEMPORAL_TIMELINE_ORDER_INVALID",
            evidence=evidence,
        )

    elapsed_ms = final_at - trigger_at
    evidence.update({
        "elapsed_ms": elapsed_ms,
        "trigger_at_ms": trigger_at,
        "final_at_ms": final_at,
        "converged": True,
        "within_window": elapsed_ms <= window_ms,
    })
    return _receipt(
        observer_id="temporal_window",
        status="OBSERVED",
        evidence=evidence,
    )


_TYPED_ASSERTION_KINDS = {
    "authorization",
    "cardinality",
    "concurrency",
    "concurrency_final_invariant",
    "conservation",
    "cross_surface_consistency",
    "delta",
    "equality",
    "eventual_consistency",
    "field_delta",
    "forbidden_state_transition",
    "http_status",
    "http_status_class",
    "idempotency",
    "idempotency_effect",
    "isolation",
    "json_path_compare",
    "json_path_exists",
    "json_path_type",
    "non_negative",
    "owner_tenant_visibility",
    "postcondition",
    "privacy",
    "response_field_absent",
    "response_rows_state_filter",
    "state",
    "state_transition",
    "temporal",
    "validation",
    "validation_rejection",
    "visibility",
}


def _primary_assertion(experiment: dict[str, Any]) -> dict[str, Any]:
    return _dict(
        _list(_dict(experiment).get("assertions"))[0]
        if _list(_dict(experiment).get("assertions"))
        else {}
    )


def _observe_typed_assertion(experiment: dict[str, Any]) -> dict[str, Any]:
    assertion = _primary_assertion(experiment)
    kind = _text(assertion.get("kind") or assertion.get("type"))
    assertion_id = _text(assertion.get("assertion_id") or assertion.get("id"))
    evidence = {
        "assertion_id": assertion_id,
        "assertion_kind": kind,
        "typed_assertion": False,
        "property_fingerprint": _fingerprint(_dict(assertion.get("property"))),
        "expected_from": _text(assertion.get("expected_from")),
    }
    if not assertion:
        return _receipt(
            observer_id="typed_assertion",
            status="INDETERMINATE",
            reason_code="TYPED_ASSERTION_MISSING",
            evidence=evidence,
        )
    if kind not in _TYPED_ASSERTION_KINDS:
        return _receipt(
            observer_id="typed_assertion",
            status="INDETERMINATE",
            reason_code="TYPED_ASSERTION_UNSUPPORTED",
            evidence=evidence,
        )
    evidence["typed_assertion"] = True
    return _receipt(
        observer_id="typed_assertion",
        status="OBSERVED",
        evidence=evidence,
    )


def _source_refs_from_experiment(experiment: dict[str, Any], assertion: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        dict(item)
        for item in _list(_dict(experiment).get("source_refs"))
        if isinstance(item, dict)
    ]
    refs.extend(
        dict(item)
        for item in _list(_dict(assertion).get("source_refs"))
        if isinstance(item, dict)
    )
    deduped: dict[str, dict[str, Any]] = {}
    for ref in refs:
        key = _fingerprint(ref)
        deduped.setdefault(key, ref)
    return list(deduped.values())


def _observe_source_invariant(experiment: dict[str, Any]) -> dict[str, Any]:
    assertion = _primary_assertion(experiment)
    prop = _dict(assertion.get("property"))
    invariant_ref = _text(
        prop.get("invariant_ref")
        or assertion.get("invariant_ref")
        or prop.get("rule_ref")
        or assertion.get("rule_ref")
    )
    expression = prop.get("expression") or assertion.get("expression")
    source_refs = _source_refs_from_experiment(experiment, assertion)
    evidence = {
        "invariant_ref": invariant_ref,
        "expression_fingerprint": _fingerprint(expression),
        "source_ref_count": len(source_refs),
        "source_ref_fingerprints": [_fingerprint(ref) for ref in source_refs],
    }
    if not invariant_ref and not expression:
        return _receipt(
            observer_id="source_invariant",
            status="INDETERMINATE",
            reason_code="SOURCE_INVARIANT_REF_MISSING",
            evidence=evidence,
        )
    if not source_refs:
        return _receipt(
            observer_id="source_invariant",
            status="INDETERMINATE",
            reason_code="SOURCE_INVARIANT_SOURCE_REF_MISSING",
            evidence=evidence,
        )
    return _receipt(
        observer_id="source_invariant",
        status="OBSERVED",
        evidence=evidence,
    )


def observe_experiment_requirements(
    experiment: dict[str, Any],
    *,
    observations: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> list[dict[str, Any]]:
    """Emit one typed receipt for every observer declared by the experiment."""

    exp = _dict(experiment)
    resolved_campaign_id = _text(campaign_id or exp.get("campaign_id"))
    resolved_execution_id = _text(execution_id or exp.get("execution_id"))
    if bool(resolved_campaign_id) != bool(resolved_execution_id):
        raise ValueError("observer_receipt_lineage_incomplete")
    evidence = _dict(observations)
    control = _dict(evidence.get("control_observation"))
    treatment = _dict(evidence.get("treatment_observation"))
    assertion = _dict(_list(exp.get("assertions"))[0] if _list(exp.get("assertions")) else {})
    prop = _dict(assertion.get("property"))
    receipts: list[dict[str, Any]] = []
    observer_rows = [
        observer
        for observer in _list(exp.get("observers"))
        if isinstance(observer, dict)
    ]
    business_effect_receipt: dict[str, Any] = {}
    if any(_text(observer.get("observer_id")) == "business_effect" for observer in observer_rows):
        # Idempotency/effect-cardinality assertions measure the REPLAY
        # window, not the aggregate: a correct target must add zero new
        # effect on the repeated write, whether the replay is accepted as a
        # no-op (treatment window 0) or refused outright by an enforced
        # quota (write_not_accepted window 0). The aggregate window instead
        # collapses to 0 on a refused replay and would report an enforced
        # quota as a violation. The treatment window is required — a replay
        # that never executed is INDETERMINATE, never a violation.
        business_effect_receipt = _observe_business_effect(
            [
                step
                for step in _list(evidence.get("execution_steps"))
                if isinstance(step, dict)
            ],
            aggregate_control_treatment=False,
            require_treatment_window=(
                _text(assertion.get("kind") or assertion.get("type"))
                in {"idempotency", "idempotency_effect"}
            ),
        )
    business_effect_evidence = (
        _dict(business_effect_receipt.get("evidence"))
        if _text(business_effect_receipt.get("status")) == "OBSERVED"
        else {}
    )
    for observer in observer_rows:
        observer_id = _text(_dict(observer).get("observer_id"))
        if observer_id == "http_response":
            receipt = _observe_http_response(control, treatment)
        elif observer_id == "actor_identity":
            receipt = _observe_actor_identity(
                _text(evidence.get("control_actor_ref")),
                _text(evidence.get("treatment_actor_ref")),
            )
        elif observer_id == "resource_ownership":
            receipt = _observe_resource_ownership(
                [
                    row
                    for row in _list(evidence.get("binding_materialization_receipts"))
                    if isinstance(row, dict)
                ],
                expected_owner_actor_ref=_text(
                    prop.get("owner_actor_ref") or prop.get("control_actor_ref")
                ),
            )
        elif observer_id == "authorization_comparison":
            from .authorization_comparison_contract import resolve_comparison_dimension
            receipt = observe_authorization_comparison(
                control=control,
                treatment=treatment,
                require_same_resource=bool(prop.get("require_same_resource", True)),
                business_effect=business_effect_evidence,
                binding_materialization_receipts=[
                    row
                    for row in _list(evidence.get("binding_materialization_receipts"))
                    if isinstance(row, dict)
                ],
                identity_keys=[
                    _text(name)
                    for name in _list(exp.get("source_identity_fields"))
                    if _text(name)
                ],
                comparison_dimension=resolve_comparison_dimension(
                    _text(prop.get("risk_family") or exp.get("risk_family")),
                    prop,
                ),
            )
        elif observer_id == "business_effect":
            receipt = business_effect_receipt
        elif observer_id == "entity_state":
            receipt = _observe_entity_state(
                [
                    step
                    for step in _list(evidence.get("execution_steps"))
                    if isinstance(step, dict)
                ],
                experiment=exp,
            )
        elif observer_id in {"before_state", "after_state", "final_state"}:
            receipt = _observe_state_snapshot(
                observer_id,
                [
                    step
                    for step in _list(evidence.get("execution_steps"))
                    if isinstance(step, dict)
                ],
                observations=evidence if isinstance(evidence, dict) else {},
            )
        elif observer_id == "barrier_timeline":
            receipt = _observe_barrier_timeline(evidence)
        elif observer_id == "temporal_window":
            receipt = _observe_temporal_window(evidence, assertion=assertion)
        elif observer_id == "typed_assertion":
            receipt = _observe_typed_assertion(exp)
        elif observer_id == "source_invariant":
            receipt = _observe_source_invariant(exp)
        elif observer_id in _REGISTERED_OBSERVER_HANDLERS:
            # Runtime-registered observer. Handed a single typed envelope so a handler on
            # a non-http surface can receive its own evidence -- the built-in handlers
            # each take a different positional shape, which is why nothing outside this
            # module could previously be dispatched at all.
            #
            # A handler that raises is reported as a harness failure on that observer
            # rather than allowed to abort every other observer's receipt: one broken
            # extension must not erase the evidence the rest of the chain did collect.
            try:
                receipt = _REGISTERED_OBSERVER_HANDLERS[observer_id]({
                    "observer_id": observer_id,
                    "experiment": exp,
                    "observations": evidence,
                    "assertion": assertion,
                    "property": prop,
                    "control_observation": control,
                    "treatment_observation": treatment,
                    "execution_steps": _list(evidence.get("execution_steps")),
                    "campaign_id": resolved_campaign_id,
                    "execution_id": resolved_execution_id,
                })
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                receipt = _receipt(
                    observer_id=observer_id,
                    status="INDETERMINATE",
                    reason_code="OBSERVER_HANDLER_FAILED",
                    evidence={"error": f"{type(exc).__name__}: {exc}"},
                )
            if not isinstance(receipt, dict):
                receipt = _receipt(
                    observer_id=observer_id,
                    status="INDETERMINATE",
                    reason_code="OBSERVER_HANDLER_RETURNED_NON_RECEIPT",
                    evidence={"returned_type": type(receipt).__name__},
                )
        else:
            receipt = _receipt(
                observer_id=observer_id,
                status="UNSUPPORTED",
                reason_code="OBSERVER_IMPLEMENTATION_MISSING",
                evidence={},
            )
        if resolved_campaign_id:
            receipt = bind_observer_receipt_lineage(
                receipt,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
            )
        receipts.append(receipt)
    # First-class evidence merge for runtime-registered observers. The finalizer
    # historically copied evidence into observations only for a hardcoded list of
    # built-in HTTP observers; a registered observer executed and returned a valid
    # receipt while its registered assertion kind saw no input. Done here, in the
    # dispatch authority itself, so no wrapper around this function is needed.
    if isinstance(observations, dict):
        _merge_registered_observer_evidence(observations, receipts)
    return receipts


def _merge_registered_observer_evidence(
    observations: dict[str, Any],
    receipts: list[dict[str, Any]],
) -> None:
    """Copy OBSERVED evidence from runtime-registered observers into observations.

    Only evidence from an OBSERVED typed receipt is copied. Conflicting keys are
    removed and recorded rather than overwritten, so two observers cannot silently
    race to define one Oracle fact. The sealed receipt itself is never mutated.
    """
    conflicts = [
        dict(row)
        for row in _list(observations.get("registered_observer_evidence_conflicts"))
        if isinstance(row, dict)
    ]
    for receipt in receipts:
        row = _dict(receipt)
        observer_id = _text(row.get("observer_id"))
        contract = _dict(OBSERVER_REGISTRY.get(observer_id))
        if not observer_id or contract.get("registered_at_runtime") is not True:
            continue
        if _text(row.get("status")).upper() != "OBSERVED":
            continue
        evidence = _dict(row.get("evidence"))
        observations[observer_id + "_observer_receipt"] = copy.deepcopy(row)
        for key, value in evidence.items():
            evidence_key = _text(key)
            if not evidence_key:
                continue
            if (
                evidence_key not in observations
                or observations.get(evidence_key) in (None, {}, [], "")
            ):
                observations[evidence_key] = copy.deepcopy(value)
                continue
            if _fingerprint(observations.get(evidence_key)) == _fingerprint(value):
                continue
            existing = observations.pop(evidence_key, None)
            conflicts.append({
                "observer_id": observer_id,
                "evidence_key": evidence_key,
                "existing_fingerprint": _fingerprint(existing),
                "incoming_fingerprint": _fingerprint(value),
                "reason_code": "REGISTERED_OBSERVER_EVIDENCE_CONFLICT",
            })
    if conflicts:
        observations["registered_observer_evidence_conflicts"] = conflicts


# ── Authorization override (merged from observer_contracts.py) ──────────

_original_authorization_comparison = observe_authorization_comparison


def _dict_obs(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_obs(value: Any) -> str:
    return str(value or "").strip()


def _status_obs(value: Any) -> int:
    row = _dict_obs(value)
    try:
        return int(row.get("status_code") or row.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def observe_authorization_comparison(
    *,
    control: dict[str, Any],
    treatment: dict[str, Any],
    require_same_resource: bool,
    business_effect: dict[str, Any] | None = None,
    binding_materialization_receipts: list[dict[str, Any]] | None = None,
    identity_keys: Iterable[str] | None = None,
    comparison_dimension: str = "",
) -> dict[str, Any]:
    baseline = _original_authorization_comparison(
        control=control,
        treatment=treatment,
        require_same_resource=require_same_resource,
        business_effect=business_effect,
        binding_materialization_receipts=binding_materialization_receipts,
        identity_keys=identity_keys,
        comparison_dimension=comparison_dimension,
    )
    control_row = _dict_obs(control)
    treatment_row = _dict_obs(treatment)
    if _text_obs(control_row.get("method")).upper() == "GET":
        return baseline

    effect = _dict_obs(business_effect)
    control_effect = effect.get("control_effect_count")
    treatment_effect = effect.get("treatment_effect_count")
    try:
        proven_control = (
            effect.get("business_effect_observed") is True
            and control_effect is not None
            and int(control_effect) > 0
        )
        proven_treatment_effect = (
            treatment_effect is not None
            and int(treatment_effect) > 0
        )
    except (TypeError, ValueError):
        return baseline
    if not (
        proven_control
        and proven_treatment_effect
        and 200 <= _status_obs(treatment_row) < 300
    ):
        return baseline

    control_path = _text_obs(control_row.get("path")).split("?", 1)[0]
    treatment_path = _text_obs(treatment_row.get("path")).split("?", 1)[0]
    same_path = bool(control_path and control_path == treatment_path)
    evidence = dict(_dict_obs(baseline.get("evidence")))
    evidence.update({
        "control_effect_count": int(control_effect),
        "treatment_effect_count": int(treatment_effect),
        "viewer_request_accepted": True,
        "viewer_business_effect_observed": True,
    })
    if require_same_resource and not same_path:
        evidence.update({
            "same_resource_proven": False,
            "resource_match_basis": "requested_resource_path_mismatch",
            "owner_can_access": True,
            "viewer_can_access": None,
            "leak_detected": None,
        })
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="SAME_RESOURCE_NOT_PROVEN",
            evidence=evidence,
        )

    evidence.update({
        "same_resource_proven": same_path or not require_same_resource,
        "resource_match_basis": (
            "same_requested_resource_path_and_explicit_deny"
            if same_path
            else "explicit_deny_operation_scope"
        ),
        "owner_can_access": True,
        "viewer_can_access": True,
        "leak_detected": True,
        "authorization_failure_mode": "restricted_write_request_accepted",
    })
    return _receipt(
        observer_id="authorization_comparison",
        status="OBSERVED",
        evidence=evidence,
    )
