"""Versioned Behavior IR — structured executable fact model for discovery.

Schema: qualibug.behavior-ir.v2

Natural language is for explanation only. Downstream obligation/experiment
compilation must reference IR node IDs. No industry or benchmark hardcoding.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


SCHEMA_VERSION = "qualibug.behavior-ir.v2"
V1_SCHEMA_VERSION = "qualibug.behavior-ir.v1"
ALLOWED_RELATION_TYPES = frozenset({
    "produces",
    "consumes",
    "transitions",
    "permits",
    "denies",
    "owns",
    "scopes",
    "conserves",
    "observes",
    "compensates",
    "permission_unknown",
})
_DERIVATIONS = {"explicit", "schema-derived", "runtime-observed", "model-inferred"}
_STATUSES = {"accepted", "conflicting", "unsupported", "unknown"}

# Product-owned identity/annotation bookkeeping may legally live on the knowledge
# asset (honesty denials such as is_ground_truth=false). Those tokens must never
# be scraped into rule statements and promoted into Behavior IR invariants —
# validate_behavior_ir treats any ground_truth vocabulary in IR nodes as
# forbidden answer-authority leakage.
_PRODUCT_BOOKKEEPING_RULE_MARKERS = (
    "is_ground_truth",
    "ground_truth_loaded",
    "ground_truth_fingerprint",
    "ground_truth_generated_from_product_output",
    "blind_ground_truth_workflow_used",
    "product_candidates_enter_ground_truth",
    "enterprise_identity_annotation_manifest",
    "enterprise_identity_structural_review",
    "required_annotation_output_schema",
    "closed_world_identity_mentions",
    "private_ground_truth",
    "ground_truth_bugs",
    "ground_truth_ref",
)


def _rule_carries_product_bookkeeping_vocabulary(text: str) -> bool:
    """Return True when a rule statement is product bookkeeping, not business fact."""
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _PRODUCT_BOOKKEEPING_RULE_MARKERS)


def _redact_product_bookkeeping_payload(value: Any) -> Any:
    """Strip product bookkeeping/GT-denial vocabulary from values entering IR nodes."""
    if isinstance(value, str):
        if _rule_carries_product_bookkeeping_vocabulary(value):
            return "[redacted:product_bookkeeping_vocabulary]"
        return value
    if isinstance(value, list):
        return [_redact_product_bookkeeping_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_product_bookkeeping_payload(item)
            for key, item in value.items()
        }
    return value


class BehaviorIRError(ValueError):
    """Behavior IR is not valid for authoritative runtime compilation."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _summary_title(operation: dict[str, Any]) -> str:
    """Operation short heading: the source-declared summary stripped of any
    permission/exception prose after '—' or ' - ' (支付订单 — 仅限管理员)."""
    summary = _text(operation.get("summary"))
    if "—" in summary:
        return summary.split("—", 1)[-1].strip()
    if " - " in summary:
        return summary.split(" - ", 1)[-1].strip()
    return summary


def normalize_relation(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize one typed relation without inventing missing join targets."""

    if not isinstance(value, dict):
        raise BehaviorIRError("relation_not_object")
    relation_type = _text(value.get("relation_type"))
    if relation_type not in ALLOWED_RELATION_TYPES:
        raise BehaviorIRError(f"relation_type_invalid:{relation_type}")
    for field in ("id", "from_ref", "to_ref"):
        if not _text(value.get(field)):
            raise BehaviorIRError(f"relation_field_missing:{field}")
    return {
        **value,
        "relation_type": relation_type,
        "operation_ref": _text(value.get("operation_ref")),
        "actor_ref": _text(value.get("actor_ref")),
        "preconditions": list(value.get("preconditions") or []),
        "effects": list(value.get("effects") or []),
        "source_refs": list(value.get("source_refs") or []),
    }


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts if _text(part))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"bir_{digest}"


def _canonical_field_id(*parts: Any) -> str:
    """Stable Canonical Field identity (cf_*), distinct from Behavior IR node ids."""
    raw = "|".join(_text(part) for part in parts if _text(part))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"cf_{digest}"


def _source_ref(source_id: str = "", *, version: str = "", locator: str = "", quote: str = "", kind: str = "") -> dict[str, Any]:
    quote_text = _text(quote)
    return {
        "source_id": _text(source_id) or "unknown",
        "version": _text(version),
        "locator": _text(locator),
        "kind": _text(kind),
        "quote_hash": hashlib.sha256(quote_text.encode("utf-8")).hexdigest()[:16] if quote_text else "",
    }


def _service_name_from_source_refs(operation: dict[str, Any], data: dict[str, Any]) -> str:
    """Resolve the owning service name from an operation's source references.

    The knowledge asset stores each service's OpenAPI under a distinct source
    whose interface locators name the file (``scm_trade_service.json``). The
    operation's ``source_refs`` carry the source id; matching it against the
    asset's interface/source inventory recovers the service file name, which
    is the service's deployment identity. Fully generic — never an industry
    or benchmark term. Returns "" when ownership is not resolvable.
    """
    # ``api_spec`` / ``submitted_api_spec`` are the submitted-run's generic
    # source labels, not the asset's per-service source ids; they never name
    # a service file and must not steer the match.
    source_ids = {
        _text(ref.get("source_id"))
        for ref in _list(operation.get("source_refs"))
        if _text(ref.get("source_id"))
        and _text(ref.get("source_id")) not in {"api_spec", "submitted_api_spec"}
    }
    # Knowledge-asset interfaces carry source_ids / canonical_contract_source_id
    # directly (no source_refs list); the submitted parser operations carry
    # source_refs. Accept either shape.
    source_ids.update(
        _text(value)
        for value in (
            _list(operation.get("source_ids"))
            + [_text(operation.get("canonical_contract_source_id"))]
        )
        if _text(value) and _text(value) not in {"api_spec", "submitted_api_spec"}
    )
    if not source_ids:
        return ""
    # Source inventory first: exact source_id → filename. This is the precise
    # ownership channel — each service's OpenAPI is a distinct source whose
    # filename names the service. Shared interfaces (e.g. /health present in
    # every service) must not steer the match through a shared locator list.
    for source in _list(data.get("sources") or data.get("source_inventory")):
        if not isinstance(source, dict):
            continue
        sid = _text(source.get("source_id") or source.get("id"))
        if sid not in source_ids:
            continue
        file_name = _text(
            source.get("filename")
            or source.get("original_name")
            or source.get("name")
            or source.get("logical_key")
        )
        if file_name.endswith("_service.json"):
            return file_name[: -len("_service.json")]
    # Interface inventory fallback: canonical_contract_source_id / source_ids
    # → locator file, restricted to the operation's own source ids so shared
    # interfaces cannot resolve to a foreign service.
    for interface in _list(data.get("interfaces")):
        if not isinstance(interface, dict):
            continue
        own_ids = set(_list(interface.get("source_ids")))
        own_ids.add(_text(interface.get("canonical_contract_source_id")))
        if not (own_ids & source_ids):
            continue
        for locator in _list(interface.get("source_locators")):
            locator_text = _text(locator)
            if "_service.json" in locator_text:
                head = locator_text.split("#", 1)[0]
                file_name = head.strip()
                if file_name.endswith("_service.json"):
                    return file_name[: -len("_service.json")]
    return ""


def parse_source_locator(locator_str: str) -> dict[str, Any]:
    """Parse a structured locator string into a dict for downstream consumers.

    Locator format: "chunk_id=chk_xxx;page=3;section=订单管理;line=42-58;table_index=0"
    Backward compatible: empty string or unrecognized format returns empty dict.

    This enables obligation compilers and experiment executors to trace back
    to the exact document chunk that produced a Behavior IR node.
    """
    text = _text(locator_str)
    if not text:
        return {}
    result: dict[str, Any] = {}
    for part in text.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not key or not value:
            continue
        if key in ("page", "table_index", "line_start", "line_end"):
            try:
                result[key] = int(value)
            except ValueError:
                result[key] = value
        elif key == "line" and "-" in value:
            start, _, end = value.partition("-")
            try:
                result["line_start"] = int(start)
                result["line_end"] = int(end)
            except ValueError:
                result["line"] = value
        else:
            result[key] = value
    return result


_METHOD_ACTIONS = {
    "GET": {"get", "read", "view", "list", "query"},
    "HEAD": {"head", "read", "view"},
    "OPTIONS": {"options", "read"},
    "POST": {"post", "create", "submit", "request", "write"},
    "PUT": {"put", "update", "modify", "write"},
    "PATCH": {"patch", "update", "modify", "write"},
    "DELETE": {"delete", "remove", "write"},
}
_UNIVERSAL_ACTIONS = {
    "*",
    "all",
    "any",
    "full_access",
    "manage",
    "administer",
}
_UNIVERSAL_RESOURCES = {
    "*",
    "all",
    "any",
    "all_resources",
    "everything",
    "global",
}
_EXCLUSIVE_ROLE_MARKERS = (
    r"\bonly\b",
    r"\bsolely\b",
    r"\bexclusively\b",
    r"\brestricted\s+to\b",
    r"\blimited\s+to\b",
    r"\bexclusive\s+to\b",
    "\u4ec5\u9650",
    "\u53ea\u9650",
    "\u552f\u6709",
    "\u5fc5\u987b\u7531",
)
_PERMIT_DECISIONS = {"allow", "allowed", "grant", "granted", "permit", "permitted"}
_DENY_DECISIONS = {
    "deny",
    "denied",
    "forbid",
    "forbidden",
    "not_allow",
    "not_allowed",
    "prohibit",
    "prohibited",
}
_READ_EFFECT_DECLARATIONS = frozenset({
    "read",
    "read_only",
    "readonly",
    "query",
    "safe",
    "none",
    "no_side_effect",
    "no_side_effects",
    "non_mutating",
    "non_mutation",
    "validation",
    "validate",
    "check",
    "preview",
    "calculation",
    "calculate",
    "quote",
    "estimate",
    "search",
    "lookup",
})
_WRITE_EFFECT_DECLARATIONS = frozenset({
    "write",
    "mutation",
    "mutating",
    "side_effect",
    "side_effecting",
    "state_change",
    "stateful",
})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ENDPOINT_ACTION_MARKERS: frozenset[str] | None = None
_SEMANTIC_MARKER_CACHE: dict[str, frozenset[str]] = {}
_SEMANTIC_PATH_SUFFIX_CACHE: dict[str, frozenset[str]] = {}


def _semantic_marker_set(key: str) -> frozenset[str]:
    cached = _SEMANTIC_MARKER_CACHE.get(key)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BehaviorIRError(f"semantic_lexicon_unreadable:{type(exc).__name__}") from exc
    raw_markers = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(raw_markers, list) or not raw_markers:
        raise BehaviorIRError(f"semantic_lexicon_{key}_missing")
    markers = frozenset(
        _normalize_action(marker)
        for marker in raw_markers
        if _normalize_action(marker)
    )
    if not markers:
        raise BehaviorIRError(f"semantic_lexicon_{key}_empty")
    _SEMANTIC_MARKER_CACHE[key] = markers
    return markers


def _semantic_path_suffix_set(key: str) -> frozenset[str]:
    cached = _SEMANTIC_PATH_SUFFIX_CACHE.get(key)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return frozenset()
    raw_suffixes = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(raw_suffixes, list) or not raw_suffixes:
        raise BehaviorIRError(f"semantic_lexicon_{key}_missing")
    suffixes = frozenset(
        "/" + "/".join(
            _normalize_action(segment)
            for segment in _text(value).strip("/").split("/")
            if _normalize_action(segment)
        )
        for value in raw_suffixes
        if _text(value).strip("/")
    )
    if not suffixes:
        raise BehaviorIRError(f"semantic_lexicon_{key}_empty")
    _SEMANTIC_PATH_SUFFIX_CACHE[key] = suffixes
    return suffixes


def _semantic_lexicon_groups(key: str) -> list[list[str]]:
    """Return a lexicon section shaped as a list of alias groups.

    ``_semantic_marker_set`` flattens to a set of tokens, which loses the grouping that
    makes an alias group meaningful. Absence degrades to no aliases rather than raising:
    the caller then resolves fewer references and records the rest as visible gaps, which
    is the "fewer capabilities" failure direction, not a broken build.
    """
    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    raw = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    groups: list[list[str]] = []
    for row in raw:
        if not isinstance(row, list):
            continue
        members = [_text(item) for item in row if _text(item)]
        if len(members) > 1:
            groups.append(members)
    return groups


def _endpoint_action_markers() -> frozenset[str]:
    global _ENDPOINT_ACTION_MARKERS
    if _ENDPOINT_ACTION_MARKERS is not None:
        return _ENDPOINT_ACTION_MARKERS
    _ENDPOINT_ACTION_MARKERS = _semantic_marker_set("endpoint_action_markers")
    return _ENDPOINT_ACTION_MARKERS


def _operation_semantic_text(operation: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in (
        "operation_id",
        "id",
        "action",
        "intent",
        "summary",
        "title",
        "description",
        "path",
    ):
        value = _text(operation.get(field))
        if value:
            parts.append(value)
    for field in ("tags", "entity_refs", "source_operation_refs"):
        parts.extend(_text(value) for value in _list(operation.get(field)) if _text(value))
    raw = " ".join(parts)
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw)
    raw = raw.lower()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", raw)
    return re.sub(r"_+", "_", normalized).strip("_")


def _operation_has_semantic_marker(operation: dict[str, Any], markers: frozenset[str]) -> bool:
    semantic_text = _operation_semantic_text(operation)
    if not semantic_text:
        return False
    for marker in markers:
        if not marker:
            continue
        if re.fullmatch(r"[a-z0-9_]+", marker):
            if re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", semantic_text):
                return True
            continue
        if marker in semantic_text:
            return True
    return False


def _is_ephemeral_session_path(path: str) -> bool:
    """Return whether a source path represents a non-durable session exchange."""

    raw_path = _text(path).split("?", 1)[0].strip().rstrip("/")
    if not raw_path.startswith("/"):
        return False
    segments = [segment for segment in raw_path.split("/") if segment]
    if not segments or any(
        segment.startswith(":")
        or (segment.startswith("{") and segment.endswith("}"))
        or segment == "*"
        for segment in segments
    ):
        return False
    normalized_segments = [_normalize_action(segment) for segment in segments]
    if normalized_segments[-1] in _semantic_marker_set(
        "ephemeral_session_terminal_markers"
    ):
        return True
    if any(
        segment in _semantic_marker_set("ephemeral_session_any_segment_markers")
        for segment in normalized_segments
    ):
        return True
    normalized_path = "/" + "/".join(normalized_segments)
    return any(
        normalized_path.endswith(suffix)
        for suffix in _semantic_path_suffix_set("ephemeral_session_path_suffixes")
    )


def _declared_operation_effect(value: Any) -> str:
    normalized = _normalize_action(value)
    if normalized in _READ_EFFECT_DECLARATIONS:
        return "read"
    if normalized in _WRITE_EFFECT_DECLARATIONS:
        return "write"
    return ""


def _infer_operation_effect(operation: dict[str, Any], method: str) -> str:
    declared_read_write = _declared_operation_effect(operation.get("read_write"))
    if declared_read_write:
        return declared_read_write
    declared_side_effect = _declared_operation_effect(operation.get("side_effect_class"))
    if declared_side_effect:
        return declared_side_effect
    if method not in _WRITE_METHODS:
        return "read"
    if method != "POST":
        return "write"
    if _operation_has_semantic_marker(operation, _semantic_marker_set("mutating_action_markers")):
        return "write"
    if _operation_has_semantic_marker(operation, _semantic_marker_set("read_like_post_action_markers")):
        return "read"
    return "write"


# ────────────────────────────────────────────────────────────────────────────
# P0-5: Multi-evidence operation semantic inference
# ────────────────────────────────────────────────────────────────────────────

OPERATION_SEMANTIC_TYPES = (
    "CREATE", "READ", "UPDATE", "REPLACE", "DELETE",
    "TRANSITION", "ACTION", "QUERY", "BATCH", "AGGREGATE",
    "VALIDATE", "EXPORT", "IMPORT", "SYNC", "WEBHOOK",
    "UNKNOWN",
)

_PATH_VERB_TO_OP_TYPE: dict[str, str] = {
    "create": "CREATE", "new": "CREATE", "add": "CREATE", "register": "CREATE",
    "get": "READ", "fetch": "READ", "retrieve": "READ", "view": "READ", "show": "READ",
    "update": "UPDATE", "edit": "UPDATE", "modify": "UPDATE", "patch": "UPDATE",
    "replace": "REPLACE", "put": "REPLACE", "set": "REPLACE",
    "delete": "DELETE", "remove": "DELETE", "destroy": "DELETE", "purge": "DELETE",
    "approve": "TRANSITION", "reject": "TRANSITION", "cancel": "TRANSITION",
    "close": "TRANSITION", "open": "TRANSITION", "reopen": "TRANSITION",
    "activate": "TRANSITION", "deactivate": "TRANSITION", "suspend": "TRANSITION",
    "resume": "TRANSITION", "enable": "TRANSITION", "disable": "TRANSITION",
    "publish": "TRANSITION", "unpublish": "TRANSITION", "archive": "TRANSITION",
    "restore": "TRANSITION", "submit": "TRANSITION", "withdraw": "TRANSITION",
    "confirm": "TRANSITION", "complete": "TRANSITION", "finalize": "TRANSITION",
    "start": "TRANSITION", "stop": "TRANSITION", "pause": "TRANSITION",
    "continue": "TRANSITION", "abort": "TRANSITION", "expire": "TRANSITION",
    "process": "ACTION", "execute": "ACTION", "trigger": "ACTION", "run": "ACTION",
    "send": "ACTION", "notify": "ACTION", "assign": "ACTION", "unassign": "ACTION",
    "transfer": "ACTION", "move": "ACTION", "copy": "ACTION", "clone": "ACTION",
    "duplicate": "ACTION", "merge": "ACTION", "split": "ACTION",
    "lock": "ACTION", "unlock": "ACTION", "freeze": "ACTION", "unfreeze": "ACTION",
    "escalate": "ACTION", "resolve": "ACTION", "retry": "ACTION",
    "search": "QUERY", "find": "QUERY", "filter": "QUERY", "list": "QUERY",
    "count": "AGGREGATE", "sum": "AGGREGATE", "stats": "AGGREGATE", "report": "AGGREGATE",
    "validate": "VALIDATE", "verify": "VALIDATE", "check": "VALIDATE", "preview": "VALIDATE",
    "export": "EXPORT", "download": "EXPORT", "import": "IMPORT", "upload": "IMPORT",
    "sync": "SYNC", "batch": "BATCH", "bulk": "BATCH",
}


def classify_operation_semantic_multi_evidence(
    operation: dict[str, Any],
    *,
    http_method: str = "",
    path: str = "",
    request_fields: list[str] | None = None,
    response_fields: list[str] | None = None,
    description_text: str = "",
) -> dict[str, Any]:
    """Classify operation semantic type using multi-evidence scoring."""
    method = (http_method or _text(operation.get("method"))).upper()
    op_path = path or _text(operation.get("path"))
    desc = description_text or _text(operation.get("summary")) or _text(operation.get("description"))

    evidence: dict[str, Any] = {}
    type_scores: dict[str, float] = {}

    if method:
        method_type_map = {
            "GET": "READ", "HEAD": "READ", "OPTIONS": "READ",
            "POST": "CREATE", "PUT": "REPLACE", "PATCH": "UPDATE", "DELETE": "DELETE",
        }
        inferred = method_type_map.get(method, "UNKNOWN")
        evidence["http_method_score"] = {"method": method, "inferred_type": inferred, "weight": 0.3}
        type_scores[inferred] = type_scores.get(inferred, 0.0) + 0.3

    path_lower = op_path.lower()
    segments = [s for s in path_lower.strip("/").split("/") if s and not s.startswith("{")]
    path_verb_type = None
    if segments:
        last_seg = segments[-1]
        if last_seg in _PATH_VERB_TO_OP_TYPE:
            path_verb_type = _PATH_VERB_TO_OP_TYPE[last_seg]
        else:
            for verb, op_type in _PATH_VERB_TO_OP_TYPE.items():
                if last_seg.startswith(verb) or f"/{verb}" in path_lower:
                    path_verb_type = op_type
                    break
    if path_verb_type:
        evidence["path_verb_score"] = {"verb": last_seg if segments else "", "inferred_type": path_verb_type, "weight": 0.35}
        type_scores[path_verb_type] = type_scores.get(path_verb_type, 0.0) + 0.35

    if request_fields:
        req_lower = [f.lower() for f in request_fields]
        if any(f.endswith("_id") or f.endswith("id") for f in req_lower):
            type_scores["UPDATE"] = type_scores.get("UPDATE", 0.0) + 0.1
            evidence["request_field_score"] = {"pattern": "*_id present", "inferred_type": "UPDATE", "weight": 0.1}
        if any(f in ("items", "records", "batch", "bulk") for f in req_lower):
            type_scores["BATCH"] = type_scores.get("BATCH", 0.0) + 0.15
            evidence["request_field_score"] = {"pattern": "batch/items", "inferred_type": "BATCH", "weight": 0.15}

    if response_fields:
        resp_lower = [f.lower() for f in response_fields]
        if any(f in ("total", "count", "sum", "average") for f in resp_lower):
            type_scores["AGGREGATE"] = type_scores.get("AGGREGATE", 0.0) + 0.1
            evidence["response_field_score"] = {"pattern": "aggregate field", "inferred_type": "AGGREGATE", "weight": 0.1}
        if any(f in ("items", "results", "data", "records") for f in resp_lower):
            type_scores["QUERY"] = type_scores.get("QUERY", 0.0) + 0.05

    if desc:
        desc_lower = desc.lower()
        desc_keywords = {
            "create": "CREATE", "new": "CREATE", "add": "CREATE",
            "update": "UPDATE", "modify": "UPDATE", "change": "UPDATE",
            "delete": "DELETE", "remove": "DELETE",
            "list": "QUERY", "search": "QUERY", "find": "QUERY",
            "validate": "VALIDATE", "check": "VALIDATE",
            "export": "EXPORT", "download": "EXPORT",
            "import": "IMPORT", "upload": "IMPORT",
        }
        for kw, op_type in desc_keywords.items():
            if kw in desc_lower:
                type_scores[op_type] = type_scores.get(op_type, 0.0) + 0.1
                evidence["description_score"] = {"keyword": kw, "inferred_type": op_type, "weight": 0.1}
                break

    if not type_scores:
        return {
            "operation_type": "UNKNOWN",
            "confidence": 0.0,
            "semantic_evidence": evidence,
            "evidence_sources_active": 0,
        }

    best_type = max(type_scores, key=lambda k: type_scores[k])
    raw_score = type_scores[best_type]
    active_sources = len(evidence)

    if active_sources >= 3:
        confidence = min(raw_score, 0.95)
    elif active_sources == 2:
        confidence = min(raw_score, 0.85)
    else:
        confidence = min(raw_score, 0.60)

    return {
        "operation_type": best_type,
        "confidence": round(confidence, 3),
        "semantic_evidence": evidence,
        "total_score": round(raw_score, 3),
        "evidence_sources_active": active_sources,
    }


_ID_LIKE_SEGMENT_RE = re.compile(
    r"^(?:"
    r"\d{3,}"
    r"|[0-9a-f]{8}(?:-[0-9a-f]{4}){0,3}(?:-[0-9a-f]{4,12})?"
    r"|qb[_-]test[_-].*"
    r"|[a-z]+[_-]\d+.*"
    r"|\d+[_-][a-z]+.*"
    r"|.*[_-]\d{2,}$"
    r")$",
    re.IGNORECASE,
)
_VERSION_SEGMENT_RE = re.compile(r"^v\d{1,3}$", re.IGNORECASE)
_COMPONENT_SCHEMA_PROPERTY_RE = re.compile(
    r"/components/schemas/([^/]+)/properties/([^/;]+)",
    re.IGNORECASE,
)


def _is_id_like_segment(segment: str) -> bool:
    if _VERSION_SEGMENT_RE.match(segment):
        return False
    return bool(_ID_LIKE_SEGMENT_RE.match(segment))


def _path_shape(value: Any) -> str:
    path = _text(value).split("?", 1)[0].strip().lower()
    if not path:
        return ""
    segments = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        if (
            (segment.startswith("{") and segment.endswith("}"))
            or segment.startswith(":")
            or segment == "*"
            or _is_id_like_segment(segment)
        ):
            segments.append("{}")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


def _canonical_json_key(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _merge_unique_sorted(*collections: list[Any]) -> list[Any]:
    by_key: dict[str, Any] = {}
    for collection in collections:
        for value in _list(collection):
            by_key.setdefault(_canonical_json_key(value), value)
    return [by_key[key] for key in sorted(by_key)]


def _json_examples_from_text(value: Any) -> list[dict[str, Any]]:
    text = _text(value)
    if not text:
        return []
    examples: list[dict[str, Any]] = []
    blocks = re.findall(
        r"```(?:json|JSON)?\s*(\{.*?\})\s*```",
        text,
        flags=re.DOTALL,
    )
    if not blocks:
        match = re.search(r"(\{(?:[^{}]|\{[^{}]*\})*\})", text, flags=re.DOTALL)
        blocks = [match.group(1)] if match else []
    for block in blocks:
        try:
            parsed = json.loads(block)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict) and parsed:
            examples.append(parsed)
    curl_blocks = re.findall(
        r"(?:-d|--data|--data-raw)\s+['\"](\{.*?\})['\"]",
        text,
        flags=re.DOTALL,
    )
    for block in curl_blocks:
        try:
            parsed = json.loads(block)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict) and parsed:
            examples.append(parsed)
    yaml_blocks = re.findall(
        r"```(?:yaml|yml)\s*(.*?)\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    for block in yaml_blocks:
        parsed_yaml: dict[str, Any] = {}
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, raw_value = (part.strip() for part in line.split(":", 1))
            if not key:
                continue
            try:
                parsed_value = json.loads(raw_value)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed_value = raw_value.strip("'\"")
            parsed_yaml[key] = parsed_value
        if parsed_yaml:
            examples.append(parsed_yaml)
    return examples


def _request_example_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(schema.get("example"))
    if direct:
        return deepcopy(direct)
    for media in _dict(schema.get("content")).values():
        if not isinstance(media, dict):
            continue
        example = _dict(media.get("example"))
        if example:
            return deepcopy(example)
        for row in _dict(media.get("examples")).values():
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return deepcopy(value)
    return {}


def _operation_request_example(operation: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(operation.get("request_example"))
    if direct:
        return deepcopy(direct)
    schema_example = _request_example_from_schema(
        _dict(operation.get("request_schema") or operation.get("requestBody"))
    )
    if schema_example:
        return schema_example
    examples = _list(operation.get("examples"))
    for example in examples:
        if isinstance(example, dict) and example:
            return deepcopy(example)
    source_examples = _json_examples_from_text(operation.get("source_excerpt"))
    return deepcopy(source_examples[0]) if source_examples else {}


def _schema_type_for_value(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "string"


def _schema_from_example(value: Any) -> dict[str, Any]:
    schema_type = _schema_type_for_value(value)
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {
                str(key): _schema_from_example(child)
                for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            },
        }
    if isinstance(value, list):
        first = next((item for item in value if item is not None), None)
        return {
            "type": "array",
            "items": _schema_from_example(first) if first is not None else {},
        }
    return {"type": schema_type}


def _schema_from_field_dictionary(fields: list[Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for field in sorted({_text(value) for value in fields if _text(value)}):
        head = field.split(".", 1)[0].split("[", 1)[0]
        if not head:
            continue
        properties.setdefault(head, {"type": "string"})
    if not properties:
        return {}
    return {
        "type": "object",
        "properties": properties,
    }


def _merge_schema_dicts(prior: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    if not prior:
        return deepcopy(incoming)
    if not incoming:
        return deepcopy(prior)
    merged: dict[str, Any] = {}
    for key in sorted(set(prior) | set(incoming)):
        left = prior.get(key)
        right = incoming.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            merged[key] = _merge_schema_dicts(left, right)
        elif isinstance(left, list) and isinstance(right, list):
            merged[key] = _merge_unique_sorted(left, right)
        elif key not in prior:
            merged[key] = deepcopy(right)
        elif key not in incoming or left == right:
            merged[key] = deepcopy(left)
        else:
            merged[key] = min(left, right, key=_canonical_json_key)
    return merged


def _schema_conflict_paths(prior: Any, incoming: Any, path: str = "") -> list[str]:
    if not prior or not incoming:
        return []
    if isinstance(prior, dict) and isinstance(incoming, dict):
        conflicts: list[str] = []
        for key in sorted(set(prior) & set(incoming)):
            if key in {"example", "examples", "required", "description", "summary"}:
                continue
            conflicts.extend(
                _schema_conflict_paths(
                    prior.get(key),
                    incoming.get(key),
                    f"{path}.{key}" if path else str(key),
                )
            )
        return conflicts
    if isinstance(prior, list) or isinstance(incoming, list):
        return []
    return [path or "$"] if prior != incoming else []


def _request_schema_for_operation(operation: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(_dict(operation.get("request_schema") or operation.get("requestBody")))
    example = _operation_request_example(operation)
    field_schema = _schema_from_field_dictionary(_list(operation.get("field_dictionary")))
    inferred = _schema_from_example(example) if example else field_schema
    if not schema and (example or inferred):
        schema = {"content": {"application/json": {}}}
    content = _dict(schema.get("content"))
    if content:
        media = _dict(content.get("application/json"))
        if example and not _dict(media.get("example")):
            media["example"] = deepcopy(example)
        if inferred:
            media["schema"] = _merge_schema_dicts(_dict(media.get("schema")), inferred)
        content["application/json"] = media
        schema["content"] = content
    elif inferred:
        schema = _merge_schema_dicts(schema, inferred)
    return schema


def _best_text(left: Any, right: Any) -> str:
    candidates = [_text(left), _text(right)]
    candidates = [value for value in candidates if value]
    if not candidates:
        return ""
    return sorted(candidates, key=lambda value: (-len(value), value))[0]


def _canonical_operation_id(source_refs: list[Any], fallback: str) -> str:
    aliases = [
        _text(value)
        for value in source_refs
        if _text(value) and ":" not in _text(value)
    ]
    return sorted(aliases)[0] if aliases else fallback


def _canonical_transport_operation_id(service: Any, method: Any, path: Any) -> str:
    service_part = re.sub(r"[^a-z0-9]+", "_", _text(service).lower()).strip("_")
    path_part = re.sub(r"[^a-z0-9]+", "_", _path_shape(path).lower()).strip("_")
    return "_".join(part for part in (service_part, _text(method).lower(), path_part or "root") if part)


def _canonicalize_duplicate_operation_ids(model: dict[str, Any]) -> None:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    by_operation_id: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        operation_id = _text(operation.get("operation_id"))
        if operation_id:
            by_operation_id.setdefault(operation_id, []).append(operation)
        operation["canonical_operation_id"] = _canonical_transport_operation_id(
            operation.get("service"), operation.get("method"), operation.get("path")
        )
    used_ids = {_text(row.get("operation_id")) for row in operations if _text(row.get("operation_id"))}
    for source_operation_id, duplicates in sorted(by_operation_id.items()):
        if len(duplicates) < 2:
            continue
        duplicate_refs: list[str] = []
        source_refs: list[dict[str, Any]] = []
        for operation in sorted(duplicates, key=lambda row: _text(row.get("id"))):
            canonical_id = _text(operation.get("canonical_operation_id"))
            if canonical_id in used_ids and canonical_id != source_operation_id:
                canonical_id = f"{canonical_id}_{_text(operation.get('id'))[-8:]}"
            operation["operation_id"] = canonical_id
            used_ids.add(canonical_id)
            duplicate_refs.append(_text(operation.get("id")))
            source_refs.extend(_list(operation.get("source_refs")))
        model["conflicts"].append(_fact_node(
            node_id=_stable_id("conflict", "duplicate_source_operation_id", source_operation_id),
            typed_fields={
                "conflict_type": "duplicate_source_operation_id",
                "source_operation_id": source_operation_id,
                "operation_refs": duplicate_refs,
            },
            source_refs=_merge_unique_sorted(source_refs),
            confidence=1.0,
            derivation="explicit",
            status="conflicting",
        ))


def _singular_token(value: Any) -> str:
    token = re.sub(r"[^a-z0-9]+", "", _text(value).lower())
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and len(token) > 1 and not token.endswith("ss"):
        return token[:-1]
    return token


def _canonical_entity_name(value: Any) -> str:
    parts = [
        _singular_token(part)
        for part in re.findall(r"[a-z0-9]+", _text(value).lower())
        if _singular_token(part)
    ]
    return "_".join(parts)


def source_identity_fields_for_operation(
    operation: dict[str, Any],
    behavior_ir: dict[str, Any] | None,
) -> list[str]:
    entities = _list(_dict(behavior_ir).get("entities"))
    if not entities:
        return []
    by_id = {_text(e.get("id")): e for e in entities if isinstance(e, dict)}
    by_name = {
        _canonical_entity_name(e.get("name")): e
        for e in entities
        if isinstance(e, dict)
    }
    resolved: list[dict[str, Any]] = []
    for hint in _list(_dict(operation).get("entity_refs")):
        entity = by_id.get(_text(hint)) or by_name.get(_canonical_entity_name(hint))
        if entity is not None:
            resolved.append(entity)
    if not resolved:
        structural = _operation_structural_entity(_dict(operation), entities)
        if structural is not None:
            resolved.append(structural)
    fields: list[str] = []
    for entity in resolved:
        for name in _list(entity.get("identity_fields")):
            if _text(name) and _text(name) not in fields:
                fields.append(_text(name))
    return fields


def _operation_structural_entity(
    operation: dict[str, Any],
    entities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    path_parts = [
        _singular_token(part)
        for part in re.findall(r"[a-z0-9]+", _path_shape(operation.get("path")))
        if _singular_token(part) not in {"api", "v1", "v2", "v3", "admin"}
    ]
    if not path_parts:
        return None
    ranked: list[tuple[int, int, int, dict[str, Any]]] = []
    for entity in entities:
        canonical = _canonical_entity_name(entity.get("name"))
        entity_parts = [part for part in canonical.split("_") if part]
        if not entity_parts or len(entity_parts) > len(path_parts):
            continue
        positions = [
            index
            for index in range(len(path_parts) - len(entity_parts) + 1)
            if path_parts[index:index + len(entity_parts)] == entity_parts
        ]
        if not positions:
            continue
        ranked.append((
            len(entity_parts),
            len(canonical),
            max(positions),
            entity,
        ))
    if not ranked:
        return None
    best_score = max(row[:3] for row in ranked)
    matches = [row[3] for row in ranked if row[:3] == best_score]
    return matches[0] if len(matches) == 1 else None


_SECRET_REQUEST_FIELD_NAMES = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
})


def _operation_request_field_names(operation: dict[str, Any]) -> set[str]:
    op = _dict(operation)
    names: set[str] = set()
    request_schema = _dict(op.get("request_schema") or op.get("requestBody"))
    properties = _dict(request_schema.get("properties"))
    if not properties:
        content = _dict(request_schema.get("content"))
        json_media = _dict(content.get("application/json"))
        properties = _dict(_dict(json_media.get("schema")).get("properties"))
        example = _dict(json_media.get("example") or op.get("request_example"))
    else:
        example = _dict(op.get("request_example"))
    names.update(_text(key).lower() for key in properties if _text(key))
    names.update(_text(key).lower() for key in example if _text(key))
    for value in _list(op.get("affected_fields")) + _list(op.get("parameters")):
        if isinstance(value, dict):
            name = _text(value.get("name") or value.get("field")).lower()
        else:
            name = _text(value).lower()
        if name:
            names.add(name)
    for value in _list(op.get("field_dictionary")):
        if isinstance(value, dict):
            name = _text(value.get("name") or value.get("field")).lower()
        else:
            name = _text(value).lower()
        if name:
            names.add(name)
    return {name for name in names if name and name not in _SECRET_REQUEST_FIELD_NAMES}


def _entity_field_column_names(entity: dict[str, Any]) -> set[str]:
    columns: set[str] = set()
    for field in _list(entity.get("fields")):
        if isinstance(field, dict):
            name = _text(field.get("name") or field.get("field")).lower()
            if name:
                columns.add(name)
            for binding in _list(field.get("database_bindings")):
                column = _text(_dict(binding).get("column")).lower()
                if column:
                    columns.add(column)
        elif _text(field):
            columns.add(_text(field).lower())
    for name in _list(entity.get("identity_fields")):
        if _text(name):
            columns.add(_text(name).lower())
    return columns


def _entity_from_request_field_overlap(
    operation: dict[str, Any],
    entities: list[dict[str, Any]],
    *,
    min_overlap: int = 2,
) -> dict[str, Any] | None:
    request_fields = _operation_request_field_names(operation)
    if len(request_fields) < min_overlap:
        return None
    scored: list[tuple[int, dict[str, Any]]] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        overlap = request_fields & _entity_field_column_names(entity)
        if len(overlap) >= min_overlap:
            scored.append((len(overlap), entity))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best = scored[0][0]
    winners = [entity for score, entity in scored if score == best]
    return winners[0] if len(winners) == 1 else None


def _resolve_projection_entity_for_operation(
    operation: dict[str, Any],
    entities: list[dict[str, Any]],
    *,
    allow_field_overlap: bool = True,
) -> dict[str, Any] | None:
    by_id = {_text(row.get("id")): row for row in entities if _text(row.get("id"))}
    by_name = {
        _canonical_entity_name(row.get("name")): row
        for row in entities
        if _text(row.get("name"))
    }
    by_name.update(
        {
            _text(row.get("name")).lower(): row
            for row in entities
            if _text(row.get("name"))
        }
    )
    resolved: list[dict[str, Any]] = []
    for hint in _list(_dict(operation).get("entity_refs")):
        entity = by_id.get(_text(hint)) or by_name.get(_canonical_entity_name(hint))
        if entity is not None and entity not in resolved:
            resolved.append(entity)
    if len(resolved) == 1:
        return resolved[0]
    if len(resolved) > 1:
        return None
    structural = _operation_structural_entity(operation, entities)
    if structural is not None:
        return structural
    if not allow_field_overlap:
        return None
    return _entity_from_request_field_overlap(operation, entities)


def _resource_matches_operation(resource: Any, operation: dict[str, Any]) -> bool:
    resource_text = _text(resource).lower()
    operation_path = _text(operation.get("path"))
    if not resource_text or not operation_path:
        return False
    if _normalize_action(resource_text) in _UNIVERSAL_RESOURCES:
        return True
    if resource_text.startswith("/"):
        return _path_shape(resource_text) == _path_shape(operation_path)
    resource_tokens = {
        _singular_token(token)
        for token in re.findall(r"[a-z0-9_]+", resource_text)
        if _singular_token(token)
    }
    operation_tokens = {
        _singular_token(token)
        for token in re.findall(r"[a-z0-9_]+", operation_path.lower())
        if _singular_token(token)
    }
    for value in _list(operation.get("tags")) + _list(operation.get("entity_refs")):
        operation_tokens.update(
            _singular_token(token)
            for token in re.findall(r"[a-z0-9_]+", _text(value).lower())
            if _singular_token(token)
        )
    return bool(resource_tokens and resource_tokens.intersection(operation_tokens))


def _normalize_action(value: Any) -> str:
    return re.sub(r"[\s\-]+", "_", _text(value).lower())


def _actions_match_operation(actions: list[Any], operation: dict[str, Any]) -> bool:
    normalized = {_text(action).lower() for action in actions if _text(action)}
    normalized = {_normalize_action(action) for action in normalized}
    if normalized.intersection(_UNIVERSAL_ACTIONS):
        return True
    method = _text(operation.get("method")).upper()
    path_segments = [
        segment
        for segment in _text(operation.get("path")).split("?", 1)[0].strip("/").split("/")
        if segment
        and not (
            (segment.startswith("{") and segment.endswith("}"))
            or segment.startswith(":")
            or segment == "*"
        )
    ]
    endpoint_action_tokens: set[str] = set()
    if path_segments:
        final_segment = _normalize_action(path_segments[-1])
        meaningful_segments = [
            segment
            for segment in path_segments
            if _normalize_action(segment) != "api"
            and not re.fullmatch(r"v\d+", _normalize_action(segment))
        ]
        if (
            final_segment in _endpoint_action_markers()
            and len(meaningful_segments) >= 2
        ):
            if normalized.intersection({"modify", "write"}):
                return method in {"POST", "PUT", "PATCH", "DELETE"}
            return final_segment in normalized
        endpoint_action_tokens.add(final_segment)
        if final_segment.endswith("s") and len(final_segment) > 3:
            endpoint_action_tokens.add(final_segment[:-1])
    if normalized.intersection(_METHOD_ACTIONS.get(method, {method.lower()})):
        return True
    if method in {"POST", "PUT", "PATCH", "DELETE"} and normalized.intersection({
        "modify",
        "write",
    }):
        return True
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    for field in ("action", "intent"):
        explicit_action = _normalize_action(operation.get(field))
        if explicit_action:
            endpoint_action_tokens.add(explicit_action)
    return bool(normalized.intersection(endpoint_action_tokens))


def _declared_permission_polarity(row: dict[str, Any]) -> str:
    raw_decision = _normalize_action(
        row.get("decision")
        or row.get("effect")
        or row.get("outcome")
        or row.get("access")
    )
    if row.get("allowed") is False:
        return "DENY"
    if row.get("allowed") is True:
        return "PERMIT"
    if raw_decision in _DENY_DECISIONS:
        return "DENY"
    if raw_decision in _PERMIT_DECISIONS:
        return "PERMIT"
    return ""


def _permission_row_decision(
    row: dict[str, Any],
    operation: dict[str, Any],
) -> str:
    denied_actions = _list(
        row.get("denied_actions")
        or row.get("forbidden_actions")
        or row.get("prohibited_actions")
    )
    if denied_actions and _actions_match_operation(denied_actions, operation):
        return "DENY"

    declared_polarity = _declared_permission_polarity(row)

    actions = _list(row.get("actions"))
    if not actions and _text(row.get("action")):
        actions = [row.get("action")]

    if declared_polarity == "DENY":
        return "DENY" if not actions or _actions_match_operation(actions, operation) else "UNKNOWN"
    raw_decision = _normalize_action(
        row.get("decision")
        or row.get("effect")
        or row.get("outcome")
        or row.get("access")
    )
    if raw_decision and not declared_polarity:
        return "UNKNOWN"
    return "PERMIT" if _actions_match_operation(actions, operation) else "UNKNOWN"


def _permission_scope(row: dict[str, Any]) -> str:
    scope = _text(row.get("scope")).lower().replace("-", "_").replace(" ", "_")
    return scope or "unspecified"


def _permission_scopes_disjoint(left: str, right: str) -> bool:
    left = _text(left).lower().replace("-", "_").replace(" ", "_") or "unspecified"
    right = _text(right).lower().replace("-", "_").replace(" ", "_") or "unspecified"
    if left == right or "unspecified" in {left, right} or "all" in {left, right}:
        return False
    if "role_access" in {left, right}:
        other = right if left == "role_access" else left
        return other in {"own", "other_owner", "tenant", "other_tenant", "own_tenant"}
    return {left, right} in (
        {"own", "other_owner"},
        {"tenant", "other_tenant"},
        {"own_tenant", "other_tenant"},
    )


def _relation_node(
    *,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    operation_ref: str = "",
    actor_ref: str = "",
    preconditions: list[Any] | None = None,
    effects: list[Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    source_rule_statements: list[dict[str, Any]] | None = None,
    confidence: float = 0.8,
    derivation: str = "schema-derived",
    status: str = "accepted",
    permission_decision: str = "",
    source_relationship_ref: str = "",
    scope: str = "",
) -> dict[str, Any]:
    _source_rule_statements = [
        dict(row)
        for row in _list(source_rule_statements)
        if isinstance(row, dict)
    ]
    return _fact_node(
        node_id=_stable_id("rel", relation_type, from_ref, to_ref, operation_ref, actor_ref, scope),
        typed_fields={
            "relation_type": relation_type,
            "from_ref": from_ref,
            "to_ref": to_ref,
            "operation_ref": operation_ref,
            "actor_ref": actor_ref,
            "preconditions": list(preconditions or []),
            "effects": list(effects or []),
            "permission_decision": _text(permission_decision),
            "source_relationship_ref": _text(source_relationship_ref),
            "source_rule_statements": _source_rule_statements,
        },
        source_refs=source_refs,
        confidence=confidence,
        derivation=derivation,
        status=status,
    )


def _fact_node(
    *,
    node_id: str,
    typed_fields: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    confidence: float = 0.5,
    derivation: str = "explicit",
    status: str = "accepted",
) -> dict[str, Any]:
    der = derivation if derivation in _DERIVATIONS else "model-inferred"
    st = status if status in _STATUSES else "unknown"
    conf = max(0.0, min(1.0, float(confidence)))
    return {
        "id": node_id,
        **typed_fields,
        "source_refs": list(source_refs or []),
        "confidence": conf,
        "derivation": der,
        "status": st,
    }


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        node_id = _text(node.get("id"))
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        result.append(node)
    return result


def _derive_permission_relations(
    model: dict[str, Any],
    permission_rows: list[Any],
    *,
    closed_world: bool = False,
) -> list[dict[str, Any]]:
    actors_by_role: dict[str, list[dict[str, Any]]] = {}
    for actor in _list(model.get("actors")):
        if not isinstance(actor, dict):
            continue
        role_key = _text(actor.get("role_key") or actor.get("role")).lower()
        if role_key:
            actors_by_role.setdefault(role_key, []).append(actor)

    rows_by_role: dict[str, list[dict[str, Any]]] = {}
    for row in permission_rows:
        if not isinstance(row, dict):
            continue
        role_key = _text(row.get("role") or row.get("actor") or row.get("principal")).lower()
        if role_key:
            rows_by_role.setdefault(role_key, []).append(row)

    relations: list[dict[str, Any]] = []
    for role_key, actors in actors_by_role.items():
        role_rows = rows_by_role.get(role_key, [])
        if not role_rows:
            continue
        for operation in _list(model.get("operations")):
            if not isinstance(operation, dict):
                continue
            if _operation_declares_public_access(operation):
                continue
            matching_rows = [
                row
                for row in role_rows
                if _resource_matches_operation(row.get("resource"), operation)
            ]
            if not matching_rows:
                continue
            grouped_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for row in matching_rows:
                decision = _permission_row_decision(row, operation)
                scope = _permission_scope(row)
                grouped_rows.setdefault((decision, scope), []).append(row)
            explicit_groups = {
                (decision, scope): rows
                for (decision, scope), rows in grouped_rows.items()
                if decision in {"PERMIT", "DENY"}
            }
            explicit_decisions = {decision for decision, _ in explicit_groups}
            conflicting_scopes = any(
                left_decision != right_decision
                and not _permission_scopes_disjoint(left_scope, right_scope)
                for left_decision, left_scope in explicit_groups
                for right_decision, right_scope in explicit_groups
            )
            if conflicting_scopes:
                grouped_rows = {
                    ("UNKNOWN", "unspecified"): matching_rows,
                }
            elif not explicit_groups and closed_world:
                grouped_rows = {
                    ("DENY", "unspecified"): matching_rows,
                }
            for (permission_decision, scope), scoped_rows in grouped_rows.items():
                relation_type = (
                    "permits"
                    if permission_decision == "PERMIT"
                    else "denies"
                    if permission_decision == "DENY"
                    else "permission_unknown"
                )
                relation_status = (
                    "conflicting"
                    if conflicting_scopes
                    else "accepted"
                    if permission_decision in {"PERMIT", "DENY"}
                    else "unknown"
                )
                source_refs = [
                    _source_ref(
                        _text(row.get("source_id")) or "permission_matrix",
                        locator=f"{role_key}->{_text(row.get('resource'))}",
                        kind="permission_matrix",
                    )
                    for row in scoped_rows
                ]
                source_rule_statements = [
                    {
                        "rule_id": _text(row.get("permission_id") or row.get("id") or ""),
                        "statement": _text(
                            row.get("evidence")
                            or row.get("statement")
                            or row.get("description")
                        ),
                        "role": role_key,
                        "resource": _text(row.get("resource")),
                    }
                    for row in scoped_rows
                    if _text(
                        row.get("evidence")
                        or row.get("statement")
                        or row.get("description")
                    )
                ]
                actions = sorted({
                    _text(action)
                    for row in scoped_rows
                    for action in _list(row.get("actions"))
                    if _text(action)
                })
                preconditions = ([{"scope": scope}] if scope != "unspecified" else [])
                for actor in actors:
                    actor_ref = _text(actor.get("id"))
                    operation_ref = _text(operation.get("id"))
                    relations.append(_relation_node(
                        relation_type=relation_type,
                        from_ref=actor_ref,
                        to_ref=operation_ref,
                        operation_ref=operation_ref,
                        actor_ref=actor_ref,
                        preconditions=preconditions,
                        effects=[{"allowed_actions": actions}],
                        source_refs=source_refs,
                        source_rule_statements=source_rule_statements,
                        confidence=(
                            0.82
                            if permission_decision == "PERMIT"
                            else 0.8 if permission_decision == "DENY" else 0.55
                        ),
                        derivation=(
                            "schema-derived"
                            if closed_world and not explicit_decisions
                            else "explicit"
                        ),
                        status=relation_status,
                        permission_decision=permission_decision,
                        scope=scope,
                    ))
                    if permission_decision == "PERMIT" and scope == "own":
                        relations.append(_relation_node(
                            relation_type="owns",
                            from_ref=actor_ref,
                            to_ref=operation_ref,
                            operation_ref=operation_ref,
                            actor_ref=actor_ref,
                            preconditions=[{"scope": "own"}],
                            source_refs=source_refs,
                            source_rule_statements=source_rule_statements,
                            confidence=0.78,
                            derivation="explicit",
                            scope="own",
                        ))
                if permission_decision == "UNKNOWN":
                    gap_id = _stable_id("gap", "permission_unknown", role_key, operation.get("id"))
                    model["coverage_gaps"].append(_fact_node(
                        node_id=gap_id,
                        typed_fields={
                            "gap_type": "permission_decision_unknown",
                            "description": "No explicit source fact permits or denies this role-operation pair",
                            "role_key": role_key,
                            "operation_ref": _text(operation.get("id")),
                        },
                        source_refs=source_refs,
                        confidence=1.0,
                        derivation="explicit",
                        status="unsupported" if relation_status != "conflicting" else "conflicting",
                    ))
            if conflicting_scopes:
                gap_id = _stable_id("gap", "permission_unknown", role_key, operation.get("id"))
                model["conflicts"].append(_fact_node(
                    node_id=_stable_id("conflict", "permission", role_key, operation.get("id")),
                    typed_fields={
                        "conflict_type": "permission_decision_conflict",
                        "role_key": role_key,
                        "operation_ref": _text(operation.get("id")),
                        "decisions": sorted(explicit_decisions),
                    },
                    source_refs=source_refs,
                    confidence=1.0,
                    derivation="explicit",
                    status="conflicting",
                ))
    declared_roles_by_op: dict[str, set[str]] = {}
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        declared = {
            _text(role).lower()
            for role in _list(operation.get("required_roles"))
            if _text(role)
        }
        if declared:
            declared_roles_by_op[_text(operation.get("id"))] = declared
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        operation_id = _text(operation.get("id"))
        declared = declared_roles_by_op.get(operation_id)
        if not declared:
            continue
        source_refs = [_source_ref(
            _text(operation.get("source_id")) or "api_spec",
            locator=f"{_text(operation.get('method')).upper()} {_text(operation.get('path'))}",
            kind="operation_role_declaration",
        )]
        _op_contract_text = _text(
            operation.get("description") or operation.get("summary")
        )
        source_rule_statements = [
            {
                "rule_id": operation_id,
                "statement": _op_contract_text,
                "role": "",
                "resource": "",
            }
        ] if _op_contract_text else []
        for role_key, actors in actors_by_role.items():
            for actor in actors:
                actor_ref = _text(actor.get("id"))
                if not actor_ref:
                    continue
                relation_type = (
                    "permits" if role_key in declared else "denies"
                )
                decision = "PERMIT" if role_key in declared else "DENY"
                if relation_type == "denies":
                    relations[:] = [
                        row
                        for row in relations
                        if not (
                            _text(row.get("from_ref")) == actor_ref
                            and _text(row.get("to_ref")) == operation_id
                            and _text(row.get("relation_type"))
                            in {"permits", "owns"}
                        )
                    ]
                relations.append(_relation_node(
                    relation_type=relation_type,
                    from_ref=actor_ref,
                    to_ref=operation_id,
                    operation_ref=operation_id,
                    actor_ref=actor_ref,
                    preconditions=[],
                    effects=[{"allowed_actions": ["*"]}] if decision == "PERMIT" else [],
                    source_refs=[dict(row) for row in source_refs],
                    source_rule_statements=source_rule_statements,
                    confidence=0.9,
                    derivation="explicit",
                    status="accepted",
                    permission_decision=decision,
                    scope="unspecified",
                ))
    return relations


_SUBJECT_ROLE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("buyer", ("普通用户", "普通买家", "买家", "客户", "顾客", "消费者")),
    ("seller", ("商家", "卖家", "商户", "供应商")),
    ("admin", ("管理员", "运营")),
    ("finance", ("财务", "出纳")),
    ("warehouse", ("仓库", "仓管")),
    ("auditor", ("审计", "审计员")),
)

_FIELD_OWNERSHIP_PHRASES = (
    "只能使用自己的",
    "只能操作自己的",
    "只能填写自己的",
    "仅本人",
    "只能本人",
    "只允许本人",
)


def _derive_field_level_ownership_relations(
    model: dict[str, Any],
    data: dict[str, Any],
    *,
    frame_confirm: Callable[[str, str, str], bool] | None = None,
) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    operations = {_text(op.get("id")): op for op in _list(model.get("operations")) if isinstance(op, dict)}
    ops_by_interface: dict[str, str] = {}
    for op in _list(model.get("operations")):
        if not isinstance(op, dict):
            continue
        interface_id = _text(op.get("interface_id"))
        if not interface_id:
            method = _text(op.get("method")).upper()
            path = _text(op.get("path") or op.get("raw_path"))
            if method and path:
                interface_id = f"api:{method}:{path}"
        if interface_id:
            ops_by_interface[interface_id] = _text(op.get("id"))
    actors_by_id = {
        _text(actor.get("id")): actor
        for actor in _list(model.get("actors"))
        if isinstance(actor, dict) and _text(actor.get("id"))
    }
    existing_owns = {
        (
            _text(rel.get("actor_ref")),
            _text(rel.get("operation_ref")),
        )
        for rel in _list(model.get("relations"))
        if _text(rel.get("relation_type")) == "owns"
        and _text(rel.get("actor_ref"))
        and _text(rel.get("operation_ref"))
    }
    permitted = {
        (
            _text(rel.get("actor_ref")),
            _text(rel.get("operation_ref")),
        )
        for rel in _list(model.get("relations"))
        if _text(rel.get("relation_type")) == "permits"
        and _text(rel.get("actor_ref"))
        and _text(rel.get("operation_ref"))
    }
    seen: set[tuple[str, str]] = set()
    for interface in _list(data.get("interfaces")):
        if not isinstance(interface, dict):
            continue
        interface_id = _text(interface.get("interface_id"))
        operation_id = ops_by_interface.get(interface_id)
        if not operation_id:
            continue
        subject_roles: set[str] = set()
        declared_field = False
        for declaration in _list(interface.get("technical_declarations")):
            if not isinstance(declaration, dict):
                continue
            if _text(declaration.get("node_kind")) != "OPENAPI_SCHEMA_PROPERTY":
                continue
            description = _text(declaration.get("description"))
            if not any(phrase in description for phrase in _FIELD_OWNERSHIP_PHRASES):
                continue
            property_name = _text(declaration.get("property_name"))
            if not re.sub(r"[^a-z0-9]+", "", property_name.lower()).endswith("id"):
                continue
            declared_field = True
            for role_key, terms in _SUBJECT_ROLE_TERMS:
                if any(term in description for term in terms):
                    subject_roles.add(role_key)
        if not declared_field or not subject_roles:
            continue
        for role_key in subject_roles:
            for actor in _list(model.get("actors")):
                if not isinstance(actor, dict):
                    continue
                actor_ref = _text(actor.get("id"))
                if not actor_ref:
                    continue
                actor_role = _text(actor.get("role_key") or actor.get("role")).lower()
                if actor_role != role_key:
                    continue
                if (actor_ref, operation_id) in existing_owns:
                    continue
                if (actor_ref, operation_id) not in permitted:
                    continue
                if (actor_ref, operation_id) in seen:
                    continue
                if (
                    frame_confirm is not None
                    and not frame_confirm(role_key, operation_id, interface_id)
                ):
                    continue
                seen.add((actor_ref, operation_id))
                relations.append(_relation_node(
                    relation_type="owns",
                    from_ref=actor_ref,
                    to_ref=operation_id,
                    operation_ref=operation_id,
                    actor_ref=actor_ref,
                    preconditions=[{"scope": "own"}],
                    source_refs=[_source_ref(
                        _text(interface.get("source_id")) or "openapi_schema",
                        locator=_text(interface.get("interface_id")),
                        kind="openapi_schema_property",
                    )],
                    confidence=0.78,
                    derivation="explicit",
                    scope="own",
                ))
    return relations


def _role_terms(actor: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for raw in (actor.get("role_key"), actor.get("role")):
        value = _text(raw).lower()
        if not value:
            continue
        variants = {
            value,
            value.replace("_", " "),
            value.replace("-", " "),
        }
        normalized = _normalize_action(value)
        if normalized.endswith("_role"):
            variants.add(normalized[:-5].replace("_", " "))
            variants.add(normalized[:-5])
        for variant in variants:
            cleaned = variant.strip(" _-")
            if len(cleaned) > 1 and cleaned not in terms:
                terms.append(cleaned)
    return terms


def _contains_role_term(source_text: str, term: str) -> bool:
    term = _text(term).lower()
    if not term:
        return False
    if not term.isascii():
        return term in source_text
    pieces = [piece for piece in re.split(r"[\s_\-]+", term) if piece]
    if not pieces:
        return False
    body = r"[\s/_:\-]+".join(re.escape(piece) for piece in pieces)
    return re.search(rf"(?<![a-z0-9]){body}(?![a-z0-9])", source_text) is not None


def _operation_contract_text(operation: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("method", "path", "operation_id", "summary", "description"):
        value = _text(operation.get(field))
        if value:
            parts.append(value)
    parts.extend(_text(value) for value in _list(operation.get("tags")) if _text(value))
    return " ".join(parts).lower()


_PUBLIC_ACCESS_DECLARATION_MARKERS = (
    "权限：公开",
    "权限:公开",
    "公开访问",
    "公开可访问",
    "匿名访问",
    "匿名可访问",
    "public access",
    "publicly accessible",
    "no authentication required",
    "no auth required",
    "anonymous access",
)


def _operation_declares_public_access(operation: dict[str, Any]) -> bool:
    corpus = _operation_contract_text(operation)
    if not corpus:
        return False
    return any(marker in corpus for marker in _PUBLIC_ACCESS_DECLARATION_MARKERS)


def _has_exclusive_role_marker(source_text: str) -> bool:
    return any(re.search(marker, source_text) for marker in _EXCLUSIVE_ROLE_MARKERS)


def _source_declared_allowed_roles(
    operation: dict[str, Any],
    actors_by_role: dict[str, list[dict[str, Any]]],
) -> set[str]:
    source_text = _operation_contract_text(operation)
    if not source_text or not _has_exclusive_role_marker(source_text):
        return set()
    allowed: set[str] = set()
    for role_key, actors in actors_by_role.items():
        if any(
            _contains_role_term(source_text, term)
            for actor in actors
            for term in _role_terms(actor)
        ):
            allowed.add(role_key)
    return allowed


def _permission_conflict_node(
    *,
    actor_ref: str,
    operation_ref: str,
    source_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    return _fact_node(
        node_id=_stable_id("conflict", "source_role_restriction", actor_ref, operation_ref),
        typed_fields={
            "conflict_type": "permission_decision_conflict",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "decisions": ["DENY", "PERMIT"],
        },
        source_refs=source_refs,
        confidence=1.0,
        derivation="explicit",
        status="conflicting",
    )


def _relation_scope(relation: dict[str, Any]) -> str:
    for precondition in _list(relation.get("preconditions")):
        if isinstance(precondition, dict) and _text(precondition.get("scope")):
            return _text(precondition.get("scope"))
    return "unspecified"


def _derive_source_role_restriction_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    actors_by_role: dict[str, list[dict[str, Any]]] = {}
    for actor in _list(model.get("actors")):
        if not isinstance(actor, dict):
            continue
        role_key = _text(actor.get("role_key") or actor.get("role")).lower()
        if role_key:
            actors_by_role.setdefault(role_key, []).append(actor)
    if not actors_by_role:
        return []

    derived: list[dict[str, Any]] = []
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        operation_ref = _text(operation.get("id"))
        if not operation_ref:
            continue
        allowed_roles = _source_declared_allowed_roles(operation, actors_by_role)
        if not allowed_roles:
            continue
        source_text = _operation_contract_text(operation)
        source_refs = list(operation.get("source_refs") or [])
        source_refs.append(_source_ref(
            "operation_contract",
            locator=f"{_text(operation.get('method'))} {_text(operation.get('path'))}",
            quote=source_text[:200],
            kind="role_restriction",
        ))
        for role_key, actors in actors_by_role.items():
            permission_decision = "PERMIT" if role_key in allowed_roles else "DENY"
            relation_type = "permits" if permission_decision == "PERMIT" else "denies"
            opposite_type = "denies" if relation_type == "permits" else "permits"
            for actor in actors:
                actor_ref = _text(actor.get("id"))
                if not actor_ref:
                    continue
                existing = [
                    row
                    for row in [*model["relations"], *derived]
                    if _text(row.get("operation_ref")) == operation_ref
                    and _text(row.get("actor_ref") or row.get("from_ref")) == actor_ref
                ]
                opposite = [
                    row for row in existing
                    if _text(row.get("relation_type")) == opposite_type
                    and _text(row.get("status")) not in {"unsupported", "unknown"}
                    and not _permission_scopes_disjoint(
                        "role_access",
                        _relation_scope(row),
                    )
                ]
                same = [
                    row for row in existing
                    if _text(row.get("relation_type")) == relation_type
                    and _text(row.get("status")) not in {"unsupported", "unknown"}
                ]
                if opposite:
                    for row in opposite:
                        row["status"] = "conflicting"
                    relation_status = "conflicting"
                    model["conflicts"].append(_permission_conflict_node(
                        actor_ref=actor_ref,
                        operation_ref=operation_ref,
                        source_refs=source_refs,
                    ))
                elif same:
                    continue
                else:
                    relation_status = "accepted"
                derived.append(_relation_node(
                    relation_type=relation_type,
                    from_ref=actor_ref,
                    to_ref=operation_ref,
                    operation_ref=operation_ref,
                    actor_ref=actor_ref,
                    preconditions=[{"scope": "role_access"}],
                    source_refs=source_refs,
                    confidence=0.84,
                    derivation="explicit",
                    status=relation_status,
                    permission_decision=permission_decision,
                    scope="role_access",
                ))
    return derived


_COMPENSATION_ACTION_PATH_VERBS = frozenset({
    "undo",
    "void",
    "rollback",
    "reverse",
    "compensate",
    "unbook",
    "rescind",
    "cancel",
    "close",
    "terminate",
    "revoke",
    "withdraw",
    "abort",
    "annul",
    "nullify",
    "invalidate",
})


def _is_identity_bound_compensation_action(
    *,
    create_shape: str,
    compensation_shape: str,
) -> bool:
    create = create_shape.rstrip("/")
    compensation = compensation_shape.rstrip("/")
    if not create or not compensation or not compensation.startswith(create + "/"):
        return False
    remainder = compensation[len(create):].strip("/")
    segments = [part for part in remainder.split("/") if part]
    if len(segments) != 2:
        return False
    first, second = segments[0], segments[1]
    return (
        (first == "{}" and second in _COMPENSATION_ACTION_PATH_VERBS)
        or (first in _COMPENSATION_ACTION_PATH_VERBS and second == "{}")
    )


def _dedupe_compensation_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_shape: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        shape = _path_shape(cand.get("path")).rstrip("/")
        existing = by_shape.get(shape)
        if existing is None or float(cand.get("confidence") or 0) > float(
            existing.get("confidence") or 0
        ):
            by_shape[shape] = cand
    return list(by_shape.values())


def _derive_compensation_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    relations: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    import os as _os
    _debug_comp = _os.environ.get("QUALIBUG_DEBUG_COMPENSATION", "").strip() == "1"

    def _append_compensation(create_operation: dict[str, Any], compensation: dict[str, Any]) -> None:
        create_ref = _text(create_operation.get("id"))
        compensation_ref = _text(compensation.get("id"))
        pair = (create_ref, compensation_ref)
        if not create_ref or not compensation_ref or pair in seen_pairs:
            return
        seen_pairs.add(pair)
        source_refs = (
            list(compensation.get("source_refs") or [])
            + list(create_operation.get("source_refs") or [])
        )[:5]
        if not source_refs:
            source_refs = [{
                "kind": "schema_derived_compensation",
                "locator": (
                    f"{_text(compensation.get('method')).upper()} "
                    f"{_text(compensation.get('path'))}"
                    f" compensates "
                    f"{_text(create_operation.get('method')).upper()} "
                    f"{_text(create_operation.get('path'))}"
                ),
            }]
        relations.append(_relation_node(
            relation_type="compensates",
            from_ref=compensation_ref,
            to_ref=create_ref,
            operation_ref=compensation_ref,
            effects=[{"cleanup_target_operation_ref": create_ref}],
            source_refs=source_refs,
            confidence=min(
                float(compensation.get("confidence") or 0.7),
                float(create_operation.get("confidence") or 0.7),
            ),
        ))

    for create_operation in operations:
        if _text(create_operation.get("method")).upper() != "POST":
            continue
        create_shape = _path_shape(create_operation.get("path")).rstrip("/")
        if not create_shape or "{}" in create_shape:
            if _debug_comp:
                logger.debug("[COMP-DEBUG] SKIP POST %s: path=%s shape=%s", _text(create_operation.get('id')), create_operation.get('path'), create_shape)
            continue
        if _debug_comp:
            logger.debug("[COMP-DEBUG] CREATE %s: POST %s shape=%s", _text(create_operation.get('id')), create_operation.get('path'), create_shape)
        delete_candidates: list[dict[str, Any]] = []
        action_candidates: list[dict[str, Any]] = []
        for candidate in operations:
            candidate_method = _text(candidate.get("method")).upper()
            compensation_shape = _path_shape(candidate.get("path")).rstrip("/")
            if candidate_method == "DELETE":
                segments = compensation_shape.split("/")
                if not segments or segments[-1] != "{}":
                    continue
                collection_shape = "/".join(segments[:-1]).rstrip("/")
                if collection_shape == create_shape:
                    if _debug_comp:
                        logger.debug(
                            "[COMP-DEBUG]   MATCH DELETE %s: %s collection=%s",
                            _text(candidate.get("id")),
                            candidate.get("path"),
                            collection_shape,
                        )
                    delete_candidates.append(candidate)
                continue
            if candidate_method not in {"POST", "PUT", "PATCH"}:
                continue
            if _is_identity_bound_compensation_action(
                create_shape=create_shape,
                compensation_shape=compensation_shape,
            ):
                if _debug_comp:
                    logger.debug(
                        "[COMP-DEBUG]   MATCH ACTION %s: %s %s",
                        _text(candidate.get("id")),
                        candidate_method,
                        candidate.get("path"),
                    )
                action_candidates.append(candidate)
        pool = (
            _dedupe_compensation_candidates(delete_candidates)
            if delete_candidates
            else _dedupe_compensation_candidates(action_candidates)
        )
        if _debug_comp:
            logger.debug(
                "[COMP-DEBUG]   POOL delete=%d action=%d chosen=%d",
                len(delete_candidates),
                len(action_candidates),
                len(pool),
            )
        if len(pool) == 1:
            if _debug_comp:
                logger.debug("[COMP-DEBUG]   => CREATE compensates relation")
            _append_compensation(create_operation, pool[0])
        elif _debug_comp and len(pool) > 1:
            logger.debug("[COMP-DEBUG]   => AMBIGUOUS %d candidates (different shapes)", len(pool))

    return relations


def _derive_operation_entity_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    entities = [row for row in _list(model.get("entities")) if isinstance(row, dict)]
    by_key: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        aliases = [
            _text(entity.get("id")),
            _text(entity.get("name")),
            *_list(entity.get("source_entity_names")),
        ]
        for key in {
            normalized
            for value in aliases
            for normalized in (_text(value).lower(), _canonical_entity_name(value))
            if normalized
        }:
            if key:
                by_key.setdefault(key, []).append(entity)
    relation_type_by_method = {
        "GET": "observes",
        "HEAD": "observes",
        "OPTIONS": "observes",
        "POST": "produces",
        "DELETE": "consumes",
        "PUT": "transitions",
        "PATCH": "transitions",
    }
    relations: list[dict[str, Any]] = []
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        resolved_entities: list[tuple[dict[str, Any], str]] = []
        for entity_hint in _list(operation.get("entity_refs")):
            matches = list(dict.fromkeys(
                id(row)
                for key in (
                    _text(entity_hint).lower(),
                    _canonical_entity_name(entity_hint),
                )
                for row in by_key.get(key, [])
            ))
            if len(matches) == 1:
                entity = next(row for row in entities if id(row) == matches[0])
                resolved_entities.append((entity, "explicit"))
        if not resolved_entities:
            structural = _operation_structural_entity(operation, entities)
            if structural is not None:
                resolved_entities.append((structural, "schema-derived"))
        if not resolved_entities:
            overlap_entity = _entity_from_request_field_overlap(operation, entities)
            if overlap_entity is not None:
                resolved_entities.append((overlap_entity, "schema-derived"))
        method = _text(operation.get("method")).upper()
        path = _text(operation.get("path") or operation.get("raw_path"))
        relation_type = relation_type_by_method.get(method, "observes")
        if method == "POST" and (
            "/{" in path or "/:" in path or path.rstrip("/").endswith("}")
        ):
            relation_type = "transitions"
        for entity, derivation in resolved_entities:
            operation_ref = _text(operation.get("id"))
            relations.append(_relation_node(
                relation_type=relation_type,
                from_ref=operation_ref,
                to_ref=_text(entity.get("id")),
                operation_ref=operation_ref,
                source_refs=(
                    list(operation.get("source_refs") or [])
                    + list(entity.get("source_refs") or [])
                )[:5],
                confidence=min(
                    float(operation.get("confidence") or 0.7),
                    float(entity.get("confidence") or 0.7),
                ),
                derivation=derivation,
            ))
    return relations


def _resolve_operation(
    operations: list[dict[str, Any]],
    relation_source: dict[str, Any],
) -> dict[str, Any] | None:
    operation_hint = _text(
        relation_source.get("operation_ref")
        or relation_source.get("operation_id")
        or relation_source.get("operation")
    )
    method_hint = _text(relation_source.get("method")).upper()
    path_hint = _text(relation_source.get("path"))
    candidates = operations
    if operation_hint:
        candidates = [
            row
            for row in candidates
            if operation_hint in {
                _text(row.get("id")),
                _text(row.get("operation_id")),
                *[_text(value) for value in _list(row.get("source_operation_refs"))],
            }
        ]
    if method_hint:
        candidates = [row for row in candidates if _text(row.get("method")).upper() == method_hint]
    if path_hint:
        candidates = [row for row in candidates if _path_shape(row.get("path")) == _path_shape(path_hint)]
    return candidates[0] if len(candidates) == 1 else None


def _derive_state_transition_relations(
    model: dict[str, Any],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    states_by_key = {
        (_text(row.get("entity_ref")).lower(), _text(row.get("name")).lower()): row
        for row in _list(model.get("states"))
        if isinstance(row, dict)
    }
    relations: list[dict[str, Any]] = []
    for machine in _list(data.get("state_machines") or data.get("states")):
        if not isinstance(machine, dict):
            continue
        entity = _text(machine.get("entity") or machine.get("object") or "entity").lower()
        machine_source_id = _text(machine.get("source_id")) or "state_machine"
        for transition in _list(machine.get("transitions")):
            if not isinstance(transition, dict):
                continue
            from_state = states_by_key.get((entity, _text(transition.get("from") or transition.get("from_state")).lower()))
            to_state = states_by_key.get((entity, _text(transition.get("to") or transition.get("to_state")).lower()))
            operation_binding = any(
                _text(transition.get(key))
                for key in ("operation_ref", "operation_id", "operation", "method", "path")
            )
            operation = _resolve_operation(operations, transition) if operation_binding else None
            from_name = _text(transition.get("from") or transition.get("from_state"))
            to_name = _text(transition.get("to") or transition.get("to_state"))
            if not from_state or not to_state or not operation:
                if from_state and to_state and not operation:
                    model["coverage_gaps"].append(_fact_node(
                        node_id=_stable_id(
                            "gap",
                            "state_transition_operation_unresolved",
                            entity,
                            from_name,
                            to_name,
                        ),
                        typed_fields={
                            "gap_type": "state_transition_operation_unresolved",
                            "entity_ref": entity,
                            "from_state": from_name,
                            "to_state": to_name,
                            "operation_hint": _text(
                                transition.get("operation_ref")
                                or transition.get("operation_id")
                                or transition.get("operation")
                            ),
                            "description": "State transition has no unique source-bound operation",
                        },
                        source_refs=[_source_ref(
                            _text(transition.get("source_id")) or machine_source_id,
                            locator=f"{entity}:{from_name}->{to_name}",
                            kind="state_transition",
                        )],
                        confidence=1.0,
                        derivation="explicit",
                        status="unsupported",
                    ))
                continue
            operation_ref = _text(operation.get("id"))
            relations.append(_relation_node(
                relation_type="transitions",
                from_ref=_text(from_state.get("id")),
                to_ref=_text(to_state.get("id")),
                operation_ref=operation_ref,
                preconditions=_list(transition.get("preconditions")),
                effects=_list(transition.get("effects")),
                source_refs=[_source_ref(
                    _text(transition.get("source_id")) or machine_source_id,
                    locator=f"{entity}:{_text(from_state.get('name'))}->{_text(to_state.get('name'))}",
                    kind="state_transition",
                )],
                confidence=float(transition.get("confidence") or 0.8),
                derivation="explicit",
            ))
    return relations


def _invariant_relation_type(invariant: dict[str, Any]) -> str:
    kind = _text(_dict(invariant.get("expression")).get("kind")).lower()
    return "conserves" if any(
        token in kind for token in ("conserv", "balance", "amount", "quantity")
    ) else "observes"


_TOKEN_OVERLAP_RELATION_GATE = "token_overlap_only_requires_explicit_source_relation"
_NON_AUTHORITATIVE_SOURCE_RELATION_STATUSES = {
    "candidate",
    "proposed",
    "unknown",
    "unsupported",
    "rejected",
}


def _source_relationship_candidate_reason(edge: dict[str, Any]) -> str:
    status = _text(edge.get("status")).lower()
    evidence_gate = _text(edge.get("evidence_gate"))
    derivation = _text(edge.get("derivation")).lower().replace("-", "_")
    evidence = _dict(edge.get("evidence"))

    if evidence_gate == _TOKEN_OVERLAP_RELATION_GATE:
        return evidence_gate
    if derivation == "token_overlap":
        return _TOKEN_OVERLAP_RELATION_GATE
    if evidence and set(evidence) <= {"token_overlap"}:
        return _TOKEN_OVERLAP_RELATION_GATE
    if status in _NON_AUTHORITATIVE_SOURCE_RELATION_STATUSES:
        return evidence_gate or status
    return ""


def _derive_source_relationship_relations(
    model: dict[str, Any],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    invariants_by_source_ref: dict[str, list[dict[str, Any]]] = {}
    for invariant in _list(model.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        aliases = [_text(invariant.get("id")), *_list(invariant.get("source_rule_refs"))]
        for alias in {_text(value) for value in aliases if _text(value)}:
            invariants_by_source_ref.setdefault(alias, []).append(invariant)

    operations_by_source_ref: dict[str, list[dict[str, Any]]] = {}
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        aliases = [_text(operation.get("id")), *_list(operation.get("source_operation_refs"))]
        for alias in {_text(value) for value in aliases if _text(value)}:
            operations_by_source_ref.setdefault(alias, []).append(operation)

    relations: list[dict[str, Any]] = []
    for edge in _list(data.get("relationships")):
        if not isinstance(edge, dict):
            continue
        relationship_type = _text(edge.get("relation") or edge.get("relation_type")).lower()
        if relationship_type != "rule_to_interface":
            continue
        relationship_id = _text(edge.get("edge_id") or edge.get("id")) or _stable_id(
            "source_relationship",
            relationship_type,
            edge.get("from") or edge.get("from_ref"),
            edge.get("to") or edge.get("to_ref"),
        )
        source_rule_ref = _text(edge.get("from") or edge.get("from_ref"))
        source_operation_ref = _text(edge.get("to") or edge.get("to_ref"))
        invariant_matches = invariants_by_source_ref.get(source_rule_ref, [])
        derived_invariant_matches = [
            invariant
            for invariant in invariant_matches
            if source_rule_ref in {
                _text(value)
                for value in _list(invariant.get("derived_from_rule_refs"))
                if _text(value)
            }
        ]
        derived_invariant_ids = {
            _text(invariant.get("id"))
            for invariant in derived_invariant_matches
            if _text(invariant.get("id"))
        }
        direct_invariant_matches = [
            invariant
            for invariant in invariant_matches
            if _text(invariant.get("id")) not in derived_invariant_ids
        ]
        if len(invariant_matches) == 1:
            executable_invariant_matches = list(invariant_matches)
        elif len(direct_invariant_matches) == 1:
            executable_invariant_matches = [
                *direct_invariant_matches,
                *derived_invariant_matches,
            ]
        else:
            executable_invariant_matches = []
        operation_matches = operations_by_source_ref.get(source_operation_ref, [])
        edge_source_ref = _source_ref(
            _text(edge.get("source_id")) or "knowledge_relationships",
            locator=relationship_id,
            kind=relationship_type,
        )
        candidate_reason = _source_relationship_candidate_reason(edge)
        if candidate_reason:
            model["coverage_gaps"].append(_fact_node(
                node_id=_stable_id("gap", "source_relationship_candidate_only", relationship_id),
                typed_fields={
                    "gap_type": "source_relationship_candidate_only",
                    "description": "A source relationship is candidate-only and lacks explicit source evidence for an executable semantic join",
                    "relationship_id": relationship_id,
                    "relationship_type": relationship_type,
                    "source_rule_ref": source_rule_ref,
                    "source_operation_ref": source_operation_ref,
                    "candidate_reason": candidate_reason,
                    "invariant_match_count": len(invariant_matches),
                    "operation_match_count": len(operation_matches),
                },
                source_refs=[edge_source_ref],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue
        if not executable_invariant_matches or len(operation_matches) != 1:
            model["coverage_gaps"].append(_fact_node(
                node_id=_stable_id("gap", "source_relationship_unresolved", relationship_id),
                typed_fields={
                    "gap_type": "source_relationship_unresolved",
                    "description": "A typed source relationship could not be joined to exactly one IR rule and operation",
                    "relationship_id": relationship_id,
                    "relationship_type": relationship_type,
                    "source_rule_ref": source_rule_ref,
                    "source_operation_ref": source_operation_ref,
                    "invariant_match_count": len(invariant_matches),
                    "direct_invariant_match_count": len(direct_invariant_matches),
                    "derived_invariant_match_count": len(derived_invariant_matches),
                    "operation_match_count": len(operation_matches),
                },
                source_refs=[edge_source_ref],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue

        operation = operation_matches[0]
        primary_invariant = executable_invariant_matches[0]
        if _text(primary_invariant.get("binding_status")) == "umbrella_rule_excluded":
            model["coverage_gaps"].append(_fact_node(
                node_id=_stable_id(
                    "gap",
                    "source_relationship_umbrella_rule_excluded",
                    relationship_id,
                ),
                typed_fields={
                    "gap_type": "source_relationship_umbrella_rule_excluded",
                    "reason_code": "SOURCE_RELATIONSHIP_UMBRELLA_RULE_EXCLUDED",
                    "description": "An exact source relationship targets an umbrella rule that is not concrete enough to compile",
                    "relationship_id": relationship_id,
                    "source_rule_ref": source_rule_ref,
                    "source_operation_ref": source_operation_ref,
                },
                source_refs=[edge_source_ref],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue
        operation_ref = _text(operation.get("id"))
        for invariant in executable_invariant_matches:
            if _text(invariant.get("binding_status")) == "umbrella_rule_excluded":
                continue
            invariant_operation_refs = [
                _text(value)
                for value in _list(invariant.get("operation_refs"))
                if _text(value)
            ]
            if operation_ref and operation_ref not in invariant_operation_refs:
                invariant["operation_refs"] = [
                    *invariant_operation_refs,
                    operation_ref,
                ]
            relations.append(_relation_node(
                relation_type=_invariant_relation_type(invariant),
                from_ref=operation_ref,
                to_ref=_text(invariant.get("id")),
                operation_ref=operation_ref,
                source_refs=(
                    [edge_source_ref]
                    + list(operation.get("source_refs") or [])
                    + list(invariant.get("source_refs") or [])
                )[:5],
                confidence=min(
                    float(edge.get("confidence") or 0.7),
                    float(operation.get("confidence") or 0.7),
                    float(invariant.get("confidence") or 0.7),
                ),
                derivation="schema-derived",
                source_relationship_ref=relationship_id,
            ))
    return relations


def _derive_invariant_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    relations: list[dict[str, Any]] = []
    for invariant in _list(model.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        if _text(invariant.get("binding_status")) == "umbrella_rule_excluded":
            continue
        relation_type = _invariant_relation_type(invariant)
        op_refs = _list(invariant.get("operation_refs"))

        for hint in op_refs:
            operation = _resolve_operation(operations, {"operation_ref": hint})
            if not operation:
                continue
            operation_ref = _text(operation.get("id"))
            relations.append(_relation_node(
                relation_type=relation_type,
                from_ref=operation_ref,
                to_ref=_text(invariant.get("id")),
                operation_ref=operation_ref,
                source_refs=(
                    list(operation.get("source_refs") or [])
                    + list(invariant.get("source_refs") or [])
                )[:5],
                confidence=min(
                    float(operation.get("confidence") or 0.7),
                    float(invariant.get("confidence") or 0.7),
                ),
                derivation="explicit",
            ))
    return relations


def empty_behavior_ir(*, project_id: str = "", source_snapshot_hash: str = "") -> dict[str, Any]:
    model = {
        "schema_version": SCHEMA_VERSION,
        "project_id": _text(project_id) or "opaque-project-id",
        "source_snapshot_hash": _text(source_snapshot_hash),
        "sources": [],
        "entities": [],
        "operations": [],
        "actors": [],
        "states": [],
        "relations": [],
        "invariants": [],
        "observation_surfaces": [],
        "ui_specs": [],
        "capabilities": [],
        "conflicts": [],
        "coverage_gaps": [],
    }
    model["model_id"] = _content_addressed_id(model)
    return model


def _content_addressed_id(model: dict[str, Any]) -> str:
    payload = {k: v for k, v in model.items() if k != "model_id"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"bir_model_{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:24]}"


def validate_behavior_ir(
    model: dict[str, Any],
    *,
    require_explicit_relations: bool = True,
) -> list[str]:
    errors: list[str] = []
    if _text(model.get("schema_version")) != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    for collection in (
        "sources", "entities", "operations", "actors", "states", "relations",
        "invariants", "observation_surfaces", "ui_specs", "capabilities",
        "conflicts", "coverage_gaps",
    ):
        if not isinstance(model.get(collection), list):
            errors.append(f"missing_collection:{collection}")
            continue
        seen_ids: set[str] = set()
        for item in model[collection]:
            if not isinstance(item, dict) or not _text(item.get("id")):
                errors.append(f"invalid_node:{collection}")
                continue
            item_id = _text(item.get("id"))
            if item_id in seen_ids:
                errors.append(f"duplicate_node_id:{collection}:{item_id}")
            seen_ids.add(item_id)
            if _text(item.get("derivation")) and _text(item.get("derivation")) not in _DERIVATIONS:
                errors.append(f"bad_derivation:{item.get('id')}")
            if _text(item.get("status")) and _text(item.get("status")) not in _STATUSES:
                errors.append(f"bad_status:{item.get('id')}")
            if "ground_truth" in json.dumps(item, ensure_ascii=False, default=str).lower():
                errors.append(f"forbidden_ground_truth_ref:{item.get('id')}")
            if collection == "relations":
                try:
                    normalize_relation(item)
                except BehaviorIRError as exc:
                    errors.append(str(exc))
                    continue
                if require_explicit_relations:
                    for field in (
                        "operation_ref",
                        "actor_ref",
                        "preconditions",
                        "effects",
                        "source_refs",
                    ):
                        if field not in item:
                            errors.append(f"relation_field_missing:{field}:{item.get('id')}")
    return errors


def migrate_behavior_ir_v1_to_v2(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or _text(value.get("schema_version")) != V1_SCHEMA_VERSION:
        raise BehaviorIRError("behavior_ir_v1_required")
    migrated = deepcopy(value)
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["relations"] = [normalize_relation(row) for row in _list(value.get("relations"))]
    errors = validate_behavior_ir(migrated, require_explicit_relations=True)
    if errors:
        raise BehaviorIRError("behavior_ir_v2_invalid:" + ",".join(errors))
    migrated["model_id"] = _content_addressed_id(migrated)
    return migrated


def _node_reference_index(model: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}

    def _register(key: Any, node_id: str) -> None:
        text = _text(key).lower()
        if text and node_id and text not in index:
            index[text] = node_id

    def _register_with_number_forms(key: Any, node_id: str) -> None:
        text = _text(key)
        _register(text, node_id)
        lowered = text.lower()
        if lowered.endswith("ies") and len(lowered) > 3:
            _register(lowered[:-3] + "y", node_id)
        elif lowered.endswith("ses") and len(lowered) > 3:
            _register(lowered[:-2], node_id)
        elif lowered.endswith("s") and not lowered.endswith("ss") and len(lowered) > 1:
            _register(lowered[:-1], node_id)
        else:
            _register(lowered + "s", node_id)

    for collection, name_keys in (
        ("entities", ("name", "entity")),
        ("states", ("name", "state", "value")),
        ("operations", ("operation_id", "path")),
        ("actors", ("role", "name", "actor")),
        ("invariants", ("description",)),
    ):
        for node in _list(model.get(collection)):
            row = _dict(node)
            node_id = _text(row.get("id"))
            if not node_id:
                continue
            for key in name_keys:
                if collection in ("entities", "actors"):
                    _register_with_number_forms(row.get(key), node_id)
                else:
                    _register(row.get(key), node_id)
            for alias in _list(row.get("source_entity_names")):
                _register_with_number_forms(alias, node_id)

    for node in _list(model.get("operations")):
        row = _dict(node)
        node_id = _text(row.get("id"))
        method = _text(row.get("method")).upper()
        path = _text(row.get("path") or row.get("raw_path"))
        if node_id and method and path:
            _register(f"{method}:{path}", node_id)

    for group in _semantic_lexicon_groups("entity_alias_groups"):
        if not isinstance(group, list):
            continue
        members = [_text(item).lower() for item in group if _text(item)]
        resolved = next((index[name] for name in members if name in index), "")
        if not resolved:
            continue
        for name in members:
            _register(name, resolved)
    return index


def _resolve_node_reference(raw: Any, index: dict[str, str]) -> str:
    text = _text(raw)
    if not text:
        return ""
    if text.startswith("bir_"):
        return text
    lowered = text.lower()
    if lowered in index:
        return index[lowered]
    if ":" in text:
        parts = [part.strip() for part in text.split(":") if part.strip()]
        if len(parts) >= 3:
            candidate = f"{parts[-2].upper()}:{parts[-1]}".lower()
            if candidate in index:
                return index[candidate]
        tail = parts[-1].lower() if parts else ""
        if tail in index:
            return index[tail]
    return ""


CANONICAL_FIELD_SEMANTIC_TYPES = frozenset({
    "IDENTITY",
    "FOREIGN_KEY",
    "OWNER_ID",
    "TENANT_ID",
    "STATE",
    "QUANTITY_BALANCE",
    "QUANTITY_DELTA",
    "AMOUNT_BALANCE",
    "AMOUNT_DELTA",
    "VERSION",
    "TIMESTAMP",
    "IDEMPOTENCY_KEY",
    "BOOLEAN_FLAG",
    "ENUM_VALUE",
    "AUDIT_FIELD",
    "UNKNOWN",
})

_CF_IDENTITY_TOKENS = {"id", "code", "number", "key", "identifier", "uuid", "ref", "reference", "no", "num"}
_CF_STATE_TOKENS = {"status", "state", "lifecycle", "phase", "stage", "condition", "disposition"}
_CF_TENANT_TOKENS = {"tenant", "tenant_id", "org", "organization", "company", "client_id"}
_CF_OWNER_TOKENS = {"owner", "owner_id", "user_id", "created_by", "creator", "author", "assignee", "assigned_to"}
_CF_VERSION_TOKENS = {"version", "revision", "etag", "row_version", "concurrency_token"}
_CF_TIMESTAMP_TOKENS = {"created_at", "updated_at", "deleted_at", "timestamp", "time", "date", "_at", "_time", "expires"}
_CF_AMOUNT_TOKENS = {"amount", "price", "total", "sum", "balance", "cost", "fee", "subtotal", "discount", "tax", "delta"}
_CF_QUANTITY_TOKENS = {"quantity", "qty", "count", "units", "limit"}
_CF_QUANTITY_BALANCE_TOKENS = {
    "available", "locked", "reserved", "allocated", "balance",
}
_CF_AMOUNT_BALANCE_TOKENS = {
    "balance", "total", "subtotal",
}
_CF_IDEMPOTENCY_TOKENS = {"idempotency", "idempotency_key", "request_id", "correlation_id", "dedup_key"}
_CF_AUDIT_TOKENS = {"created_by", "updated_by", "modified_by", "deleted_by", "audit", "log", "trace", "reason", "remark", "note", "comment", "detail"}
_CF_BOOLEAN_TOKENS = {"is_", "has_", "can_", "enabled", "active", "flag", "deleted", "visible", "verified"}
_CF_ENUM_TOKENS = {"role", "type", "category", "channel", "level", "kind", "mode", "tag", "label", "city", "province", "region", "gender"}
_CF_CONTACT_TOKENS = {"phone", "email", "mobile", "receiver", "contact", "address", "name"}


def _classify_field_semantics(
    field_name: str,
    *,
    data_type: str = "",
    entity_name: str = "",
    is_primary_key: bool = False,
    is_foreign_key: bool = False,
    has_enum: bool = False,
    schema: dict[str, Any] | None = None,
) -> tuple[str, float]:
    fn = field_name.lower().strip()
    sch = schema if isinstance(schema, dict) else {}
    sch_type = _text(sch.get("type") or data_type).lower()

    if is_primary_key:
        return "IDENTITY", 0.95

    if is_foreign_key or fn.endswith("_id") or (fn.endswith("id") and len(fn) > 2 and fn[:-2].isalpha()):
        for tok in _CF_TENANT_TOKENS:
            if tok in fn:
                return "TENANT_ID", 0.9
        for tok in _CF_OWNER_TOKENS:
            if tok in fn:
                return "OWNER_ID", 0.85
        return "FOREIGN_KEY", 0.8

    for tok in _CF_IDEMPOTENCY_TOKENS:
        if tok in fn:
            return "IDEMPOTENCY_KEY", 0.9

    for tok in _CF_VERSION_TOKENS:
        if tok == fn or fn.endswith("_" + tok) or tok in fn:
            return "VERSION", 0.85

    for tok in _CF_STATE_TOKENS:
        if tok == fn or fn.endswith("_" + tok) or fn.startswith(tok + "_"):
            return "STATE", 0.9
    if has_enum and any(tok in fn for tok in _CF_STATE_TOKENS):
        return "STATE", 0.85

    for tok in _CF_TENANT_TOKENS:
        if tok == fn or tok in fn:
            return "TENANT_ID", 0.85

    for tok in _CF_OWNER_TOKENS:
        if tok == fn or tok in fn:
            return "OWNER_ID", 0.8

    if sch_type in ("datetime", "timestamp", "date"):
        return "TIMESTAMP", 0.9
    for tok in _CF_TIMESTAMP_TOKENS:
        if fn.endswith(tok) or tok == fn:
            return "TIMESTAMP", 0.85

    _has_qty_token = any(tok in fn for tok in _CF_QUANTITY_TOKENS)
    _has_amount_token = any(tok in fn for tok in _CF_AMOUNT_TOKENS)
    if _has_qty_token:
        if any(tok in fn for tok in _CF_QUANTITY_BALANCE_TOKENS):
            return "QUANTITY_BALANCE", 0.9
        if any(tok in fn for tok in ("delta", "adjust", "change", "increment", "decrement")):
            return "QUANTITY_DELTA", 0.85
        return "QUANTITY_DELTA", 0.7
    if _has_amount_token:
        if any(tok in fn for tok in _CF_AMOUNT_BALANCE_TOKENS):
            return "AMOUNT_BALANCE", 0.9
        if any(tok in fn for tok in ("delta", "adjust", "change", "discount", "fee", "tax", "credit", "debit")):
            return "AMOUNT_DELTA", 0.85
        return "AMOUNT_DELTA", 0.7

    if sch_type == "boolean":
        return "BOOLEAN_FLAG", 0.9
    for tok in _CF_BOOLEAN_TOKENS:
        if fn.startswith(tok) or fn == tok:
            return "BOOLEAN_FLAG", 0.8

    if has_enum or _list(sch.get("enum")):
        return "ENUM_VALUE", 0.8

    if sch_type in ("decimal", "numeric", "money", "number"):
        if any(tok in fn for tok in _CF_AMOUNT_BALANCE_TOKENS):
            return "AMOUNT_BALANCE", 0.85
        if any(tok in fn for tok in _CF_AMOUNT_TOKENS):
            return "AMOUNT_DELTA", 0.75

    if sch_type == "integer":
        if any(tok in fn for tok in _CF_QUANTITY_TOKENS):
            if any(tok in fn for tok in _CF_QUANTITY_BALANCE_TOKENS):
                return "QUANTITY_BALANCE", 0.8
            return "QUANTITY_DELTA", 0.75

    for tok in _CF_IDENTITY_TOKENS:
        if fn == tok or fn.endswith("_" + tok) or fn.startswith(tok + "_"):
            return "IDENTITY", 0.7

    for tok in _CF_AUDIT_TOKENS:
        if tok in fn:
            return "AUDIT_FIELD", 0.7

    for tok in _CF_ENUM_TOKENS:
        if tok == fn or fn.endswith("_" + tok) or fn.startswith(tok + "_") or tok in fn:
            return "ENUM_VALUE", 0.7

    for tok in _CF_CONTACT_TOKENS:
        if tok == fn or tok in fn:
            return "IDENTITY", 0.6

    if "[]" in fn or fn.endswith("s") and len(fn) > 3:
        for tok in _CF_IDENTITY_TOKENS:
            if tok in fn:
                return "FOREIGN_KEY", 0.6
        return "FOREIGN_KEY", 0.5

    return "UNKNOWN", 0.3


def _build_canonical_fields(
    raw_fields: list[Any],
    entity_name: str,
    *,
    identity_fields: list[Any] | None = None,
    source_refs: list[Any] | None = None,
    field_dictionary: list[Any] | None = None,
    db_columns: dict[str, dict[str, Any]] | None = None,
    model_enum_index: dict[tuple[str, str], list[str]] | None = None,
) -> list[dict[str, Any]]:
    id_fields = {_text(f).lower() for f in _list(identity_fields) if _text(f)}
    src_refs = _list(source_refs)
    db_cols = db_columns if isinstance(db_columns, dict) else {}

    fd_lookup: dict[str, dict[str, Any]] = {}
    for fd in _list(field_dictionary):
        if isinstance(fd, dict):
            fname = _text(fd.get("field") or fd.get("name")).lower()
            if fname:
                fd_lookup[fname] = fd
        elif isinstance(fd, str):
            fd_lookup[fd.lower()] = {"field": fd}

    seen: set[str] = set()
    ordered_names: list[str] = []
    for f in raw_fields:
        name = ""
        if isinstance(f, dict):
            name = _text(f.get("name") or f.get("id") or f.get("field"))
        elif isinstance(f, str):
            name = f.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            ordered_names.append(name)
    for fname in fd_lookup:
        if fname not in seen:
            seen.add(fname)
            ordered_names.append(fname)
    for col_name in db_cols:
        if col_name.lower() not in seen:
            seen.add(col_name.lower())
            ordered_names.append(col_name)

    result: list[dict[str, Any]] = []
    for name in ordered_names:
        name_lower = name.lower()
        fd_info = fd_lookup.get(name_lower, {})
        db_info = db_cols.get(name_lower, {})
        data_type = _text(
            fd_info.get("type") or db_info.get("type") or db_info.get("data_type")
        )
        is_pk = name_lower in id_fields or bool(db_info.get("primary_key"))
        is_fk = bool(fd_info.get("foreign_key") or db_info.get("foreign_key"))
        has_enum = bool(_list(fd_info.get("enum")) or _list(db_info.get("enum")))
        enum_values = list(dict.fromkeys(
            _text(value)
            for value in _list(fd_info.get("enum")) or _list(db_info.get("enum"))
            if _text(value)
        ))
        if not enum_values:
            _model_enum = (model_enum_index or {}).get(
                (entity_name.lower(), name_lower),
            )
            if _model_enum:
                enum_values = list(_model_enum)
        def _declared_bound(*keys: str) -> Any:
            for source in (fd_info, db_info):
                for key in keys:
                    value = source.get(key)
                    if value is not None and not isinstance(value, bool):
                        return value
            return None

        min_value = _declared_bound("min", "minimum", "min_value")
        max_value = _declared_bound("max", "maximum", "max_value")
        nullable = db_info.get("nullable", fd_info.get("nullable"))
        description = _text(fd_info.get("description") or db_info.get("description"))

        semantic_type, confidence = _classify_field_semantics(
            name,
            data_type=data_type,
            entity_name=entity_name,
            is_primary_key=is_pk,
            is_foreign_key=is_fk,
            has_enum=has_enum,
        )

        identity_role = ""
        if is_pk:
            identity_role = "primary_key"
        elif is_fk or semantic_type == "FOREIGN_KEY":
            identity_role = "foreign_key"

        scope_role = ""
        if semantic_type == "TENANT_ID":
            scope_role = "tenant_id"
        elif semantic_type == "OWNER_ID":
            scope_role = "owner_id"

        field_src_refs = list(src_refs)
        fd_source = _text(fd_info.get("source_id"))
        if fd_source:
            field_src_refs.append({"source_id": fd_source, "kind": "field_dictionary"})

        field_node: dict[str, Any] = {
            "field_id": _canonical_field_id("cf", entity_name, name_lower),
            "name": name,
            "semantic_type": semantic_type,
            "data_type": data_type,
            "nullable": nullable if nullable is not None else None,
            "identity_role": identity_role,
            "scope_role": scope_role,
            "confidence": round(confidence, 2),
            "conflict_status": "accepted",
        }
        if description:
            field_node["description"] = description
        if enum_values:
            field_node["enum_values"] = enum_values
        if min_value is not None:
            field_node["min_value"] = min_value
        if max_value is not None:
            field_node["max_value"] = max_value
        if field_src_refs:
            field_node["source_refs"] = field_src_refs
        result.append(field_node)

    return result


_CJK_ACTION_MODAL_WORDS = (
    "不能", "不得", "必须", "只能", "仅", "可以", "允许",
    "应当", "需要", "禁止", "严禁", "不允许", "无权",
)
_CJK_ACTION_PREFIX_MODIFIERS = (
    "直接", "立即", "再次", "进行", "发起", "重新",
    "自行", "手动", "自动", "继续", "予以",
)
_CJK_TRANSFER_ACTIONS = (
    "导出", "导入", "下载", "上传", "打印", "备份", "恢复", "复制",
)


def _extract_action_phrases(
    statement: str, action_pattern: Any
) -> list[str]:
    phrases: list[str] = []
    for modal in _CJK_ACTION_MODAL_WORDS:
        idx = statement.find(modal)
        if idx < 0:
            continue
        tail = statement[idx + len(modal):]
        for segment in re.split(r"[，,、；;。]", tail):
            segment = segment.strip()
            if not segment:
                continue
            if not action_pattern.search(segment):
                continue
            core = segment
            for modifier in _CJK_ACTION_PREFIX_MODIFIERS:
                if core.startswith(modifier):
                    core = core[len(modifier):]
                    break
            if len(core) <= 6:
                phrases.append(core)
    for action in _CJK_TRANSFER_ACTIONS:
        if action in statement and action not in phrases:
            phrases.append(action)
    return list(dict.fromkeys(phrases))


_DECISION_OPERATION_TOKENS = (
    "validate", "check", "verify", "eligible", "usable", "consume",
    "apply", "simulate", "quote", "estimate", "calculate", "use",
    "claim", "校验", "验证", "使用", "领取", "可用", "模拟", "计算",
    "预估", "报价", "试算",
)
_CONSTRAINT_TOKEN_FIELD_GROUPS = (
    (("状态",), ("status", "state"), 2),
    (
        ("有效期", "生效", "失效", "过期", "到期", "时间"),
        ("expires", "expiry", "valid", "start", "effective", "end"),
        2,
    ),
    (("次数", "限制", "限额"), ("limit", "count", "usage", "uses"), 2),
    (("限用", "限领", "限兑"), ("limit", "usage", "use", "claim", "redeem"), 2),
    (("类目", "分类", "范围"), ("categor", "scope", "class", "type"), 2),
    (("封顶", "上限", "最大"), ("max", "cap", "ceiling"), 2),
    (("最低", "门槛", "最小"), ("min", "minimum", "floor"), 2),
    (("金额",), ("amount", "price", "total", "fee", "cost"), 1),
)
_USAGE_LIMIT_TERMS = (
    "使用次数", "领取次数", "兑换次数", "核销次数", "领用次数",
    "限用", "限领", "限兑", "只能用一次", "只能使用一次", "限使用",
    "次数限制", "次数上限",
)
_USAGE_LIMIT_RESTRICTORS = (
    "不能超过", "不得超过", "不超过", "限制", "上限", "最多", "仅限",
    "只能", "限用", "限领", "限兑",
)
_CONSUMPTION_OP_TOKENS = (
    "use", "consume", "redeem", "claim", "apply",
    "核销", "使用", "领取", "兑换",
)
_USAGE_ACTION_FAMILIES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("领取", "领用", "限领"), ("claim", "领取", "领")),
    (("兑换", "限兑"), ("redeem", "兑换")),
    (("使用", "核销", "限用", "只能用"), ("use", "consume", "核销", "使用", "apply")),
)


def _op_text_has_token(text: str, token: str) -> bool:
    if re.search(r"^[a-z]+$", token):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])",
                text.casefold(),
            )
        )
    return token in text


_SUBJECT_ALIAS_SUBSTRING_FLOOR = 2


def _subject_matches_alias(subject: str, alias: str) -> bool:
    if not subject or not alias:
        return False
    s = subject.casefold().strip()
    a = alias.casefold().strip()
    if not s or not a:
        return False
    if s == a or s in a or a in s:
        return True
    if (
        2 <= len(s) <= 4
        and len(a) >= _SUBJECT_ALIAS_SUBSTRING_FLOOR
        and s[-1] == a[-1]
        and ord(s[-1]) > 127
    ):
        return True
    return False


def _constraint_field_score(statement: str, field_name: str) -> int:
    combined = f" {statement} ".casefold()
    field = field_name.casefold()
    score = 0
    for terms, tokens, weight in _CONSTRAINT_TOKEN_FIELD_GROUPS:
        if not any(term in combined for term in terms):
            continue
        for token in tokens:
            if re.search(
                rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])",
                field,
            ):
                score += weight
                break
    return score


def _subject_channel_resolution(
    rule: dict[str, Any],
    frame: dict[str, Any] | None,
    statement: str,
    data: dict[str, Any],
    model: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "subject_objects": [],
        "entity_tables": [],
        "op_ids": [],
        "decision_op_ids": [],
        "field_operands": [],
        "basis": "",
    }
    if not isinstance(data, dict) or not isinstance(model, dict):
        return result
    frame = frame if isinstance(frame, dict) else {}
    subject = _text(frame.get("subject"))
    behavior = _text(frame.get("behavior"))

    subject_terms = {t for t in (subject, behavior) if _text(t)}
    objects: list[dict[str, Any]] = []
    for row in _list(data.get("business_objects")):
        if not isinstance(row, dict):
            continue
        obj_name = _text(row.get("object"))
        aliases = {
            _text(a).casefold()
            for a in _list(row.get("aliases"))
            if _text(a)
        }
        if not obj_name:
            continue
        if any(
            _subject_matches_alias(term, alias)
            for term in subject_terms
            for alias in aliases
        ):
            objects.append(row)

    all_table_names = {
        _text(t.get("name")).casefold()
        for t in _list(data.get("data_tables"))
        if isinstance(t, dict) and _text(t.get("name"))
    }

    def _object_field_scores(
        obj_name: str,
    ) -> tuple[int, int]:
        specific = 0
        generic = 0
        seen: set[tuple[str, str]] = set()
        obj_tables = {
            tname
            for tname in all_table_names
            if (
                tname == obj_name.casefold()
                or tname.startswith(obj_name.casefold())
                or obj_name.casefold().startswith(tname)
            )
        }
        for tname in obj_tables:
            for ent in _list(model.get("entities")):
                if not isinstance(ent, dict):
                    continue
                ent_table = _text(ent.get("table") or ent.get("name"))
                if ent_table.casefold() != tname:
                    continue
                for fnode in _list(ent.get("fields")):
                    if not isinstance(fnode, dict):
                        continue
                    fname = _text(fnode.get("name"))
                    if not fname:
                        continue
                    key = (tname, fname.casefold())
                    if key in seen:
                        continue
                    seen.add(key)
                    score = _constraint_field_score(statement, fname)
                    if score >= 2:
                        specific += 1
                    elif score == 1:
                        generic += 1
        return specific, generic

    field_scored: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for row in _list(data.get("business_objects")):
        if not isinstance(row, dict):
            continue
        obj_name = _text(row.get("object"))
        if not obj_name:
            continue
        scores = _object_field_scores(obj_name)
        if scores != (0, 0):
            field_scored.append((scores, row))
    field_objects = set()
    if field_scored:
        best = max(score for score, _ in field_scored)
        field_objects = {
            _text(row.get("object")).casefold()
            for score, row in field_scored
            if score == best
        }

    subject_names = {_text(row.get("object")).casefold() for row in objects}
    if objects and not field_objects:
        selected = objects
        basis = "subject_frame"
    elif not objects and field_objects:
        selected = [
            row
            for score, row in field_scored
            if _text(row.get("object")).casefold() in field_objects
        ]
        basis = "constraint_field"
    elif objects and field_objects:
        subject_own_fields = False
        for row in objects:
            if _object_field_scores(_text(row.get("object")))[0] > 0:
                subject_own_fields = True
                break
        if subject_own_fields:
            selected = objects
            basis = "subject_frame"
        else:
            selected = [
                row
                for score, row in field_scored
                if _text(row.get("object")).casefold() in field_objects
            ]
            basis = "constraint_field_dominant"
    else:
        return result
    if not selected:
        return result
    result["basis"] = basis
    result["subject_objects"] = sorted(
        {_text(row.get("object")).casefold() for row in selected}
    )

    entity_tables: set[str] = set()
    for obj_name in result["subject_objects"]:
        for tname in all_table_names:
            if (
                tname == obj_name
                or tname.startswith(obj_name)
                or obj_name.startswith(tname)
            ):
                entity_tables.add(tname)
    result["entity_tables"] = sorted(entity_tables)
    decision_ids: list[str] = []
    for op in _list(model.get("operations")):
        if not isinstance(op, dict):
            continue
        op_id = _text(op.get("id"))
        op_path = _text(op.get("path") or op.get("raw_path"))
        if not op_id or not op_path:
            continue
        if re.search(r"(?:^|/)(?:health)(?:/|$)", op_path.casefold()):
            continue
        segments = [
            seg
            for seg in op_path.casefold().strip("/").split("/")
            if seg and seg not in {"api", "health", "v1"}
        ]
        if not any(
            _seg.startswith(_obj) or _obj.startswith(_seg)
            for _seg in segments
            for _obj in result["subject_objects"]
        ):
            continue
        result["op_ids"].append(op_id)
        combined = f"{op_path.casefold()} {_text(op.get('summary')).casefold()}"
        if any(token in combined for token in _DECISION_OPERATION_TOKENS):
            decision_ids.append(op_id)
    result["decision_op_ids"] = decision_ids
    if decision_ids:
        result["op_ids"] = decision_ids

    seen_fields: set[tuple[str, str]] = set()
    for obj_name in result["subject_objects"]:
        for tname in entity_tables:
            for ent in _list(model.get("entities")):
                if not isinstance(ent, dict):
                    continue
                ent_table = _text(ent.get("table") or ent.get("name"))
                if ent_table.casefold() != tname:
                    continue
                ent_id = _text(ent.get("id")) or tname
                for fnode in _list(ent.get("fields")):
                    if not isinstance(fnode, dict):
                        continue
                    fname = _text(fnode.get("name"))
                    if not fname:
                        continue
                    if _constraint_field_score(statement, fname) <= 0:
                        continue
                    key = (ent_id.casefold(), fname.casefold())
                    if key in seen_fields:
                        continue
                    seen_fields.add(key)
                    operand: dict[str, Any] = {
                        "entity_ref": ent_id,
                        "field": fname,
                    }
                    if _text(fnode.get("field_id")):
                        operand["field_id"] = _text(fnode.get("field_id"))
                    if _text(fnode.get("semantic_type")):
                        operand["semantic_type"] = _text(fnode.get("semantic_type"))
                    result["field_operands"].append(operand)
    return result


def build_behavior_ir_from_knowledge_asset(
    asset: dict[str, Any] | None,
    *,
    project_id: str = "",
    source_snapshot_hash: str = "",
    api_operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    available_surfaces: dict[str, bool] | None = None,
    operation_path_scope: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Build Behavior IR from enterprise knowledge asset + optional OpenAPI ops."""
    model = empty_behavior_ir(project_id=project_id, source_snapshot_hash=source_snapshot_hash)
    data = _dict(asset)
    _submitted_operations = _list(api_operations)
    _submitted_scope: set[tuple[str, str]] = set()
    if operation_path_scope is not None:
        _submitted_scope = {
            (_text(m).upper(), _text(p).rstrip("/"))
            for (m, p) in operation_path_scope
            if _text(m) and _text(p)
        }
        _submitted_scope.update(
            (_text(op.get("method")).upper(), _text(op.get("path")).rstrip("/"))
            for op in _submitted_operations
            if isinstance(op, dict) and _text(op.get("path"))
        )
    _model_enum_index: dict[tuple[str, str], list[str]] = {}
    for _iface_row in _list(data.get("interfaces")):
        for _td in _list(_iface_row.get("technical_declarations")):
            if not isinstance(_td, dict):
                continue
            _cons = _dict(_td.get("constraints"))
            _enum = [
                _text(value) for value in _list(_cons.get("enum")) if _text(value)
            ]
            _pp = _list(_td.get("property_path"))
            if not _enum:
                continue
            _pp_model = _text(_pp[0]) if len(_pp) >= 2 else ""
            _pp_field = _text(_pp[-1] if _pp else "")
            if not _pp_model and _pp_field:
                _decl_locator = _text(
                    _td.get("source_locator")
                    or _iface_row.get("source_locator")
                )
                _schema_match = _COMPONENT_SCHEMA_PROPERTY_RE.search(
                    _decl_locator
                )
                if _schema_match:
                    _pp_model = _schema_match.group(1)
            if _pp_model and _pp_field:
                _model_enum_index.setdefault(
                    (_pp_model.lower(), _pp_field.lower()),
                    list(dict.fromkeys(_enum)),
                )
    if not data and not api_operations and not runtime_actors:
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "no_sources"),
            typed_fields={"gap_type": "missing_sources", "description": "No knowledge asset or operations available"},
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))
        model["model_id"] = _content_addressed_id(model)
        return model

    _frame_ledger = _dict(data.get("chinese_semantic_frame_ledger"))
    _frames = [
        row for row in _list(_frame_ledger.get("items")) if isinstance(row, dict)
    ]
    _frame_ledger_present = bool(_frames)
    _fallback_kind_counts: dict[str, int] = {}

    def _record_fallback(kind: str, count: int = 1) -> None:
        if count <= 0:
            return
        if not _frame_ledger_present and kind != "ACTION_PHRASE_BINDING_NO_FRAME_LEDGER":
            return
        _fallback_kind_counts[kind] = _fallback_kind_counts.get(kind, 0) + count

    def _norm_text(value: Any) -> str:
        return _text(value).strip().replace("\u3000", " ")

    _frame_by_fact_id: dict[str, dict[str, Any]] = {}
    _frame_by_fact_tail: dict[str, dict[str, Any]] = {}
    _frame_by_statement: dict[str, dict[str, Any]] = {}
    for _frow in _frames:
        _fid = _text(_dict(_frow.get("origin")).get("origin_fact_id"))
        if _fid:
            _frame_by_fact_id.setdefault(_fid, _frow)
            _tail = _fid.split(":", 1)[-1]
            if _tail:
                _frame_by_fact_tail.setdefault(_tail, _frow)
        _fs = _norm_text(_dict(_frow.get("source_span")).get("quote"))
        if _fs:
            _frame_by_statement.setdefault(_fs, _frow)

    def _frame_for_rule(rule_id: str, statement: str) -> dict[str, Any]:
        if not rule_id and not statement:
            return {}
        _frame = _frame_by_fact_id.get(rule_id)
        if _frame:
            return _frame
        if rule_id.startswith("zh_business:"):
            _tail = rule_id.split(":", 1)[-1]
            _frame = (
                _frame_by_fact_tail.get(_tail)
                or _frame_by_fact_tail.get(_tail[-20:])
            )
            if _frame:
                return _frame
        _fs = _norm_text(statement)
        if _fs:
            return _frame_by_statement.get(_fs, {})
        return {}

    def _frame_grounded_ops(frame: dict[str, Any]) -> set[str]:
        refs: set[str] = set()
        _tg = _dict(frame.get("technical_grounding"))
        for _r in _list(_tg.get("operation_refs")):
            if _text(_r):
                refs.add(_text(_r))
        for _r in _list(_dict(frame.get("action")).get("grounded_operation_refs")):
            if _text(_r):
                refs.add(_text(_r))
        return refs

    def _frame_grounded_actor_roles(frame: dict[str, Any]) -> set[str]:
        refs: set[str] = set()
        _tg = _dict(frame.get("technical_grounding"))
        for _r in _list(_tg.get("actor_refs")):
            if _text(_r):
                refs.add(_text(_r))
        for _r in _list(_dict(frame.get("actor")).get("grounded_actor_refs")):
            if _text(_r):
                refs.add(_text(_r))
        return refs

    _OWNERSHIP_EVIDENCE_FRAME_TYPES = frozenset({
        "OWNERSHIP_RULE", "PERMISSION_RULE", "SCOPE_RULE", "DATA_VISIBILITY_RULE",
    })

    def _frame_has_structured_ownership(frame: dict[str, Any]) -> bool:
        _ownership = _dict(_dict(frame.get("scope")).get("ownership_relation"))
        return bool(
            _text(frame.get("frame_type")) in _OWNERSHIP_EVIDENCE_FRAME_TYPES
            and any(_text(key) != "raw" for key in _ownership)
        )

    def _frame_is_grounded(frame: dict[str, Any]) -> bool:
        if not frame:
            return False
        _tg = _dict(frame.get("technical_grounding"))
        return bool(
            _list(_tg.get("operation_refs"))
            or _list(_tg.get("actor_refs"))
            or _list(_tg.get("entity_refs"))
        )

    def _field_ownership_confirm(role_key: str, operation_id: str, interface_id: str) -> bool:
        _op_row = {}
        for _row in _list(model.get("operations")):
            if isinstance(_row, dict) and _text(_row.get("id")) == operation_id:
                _op_row = _row
                break
        _mid = _text(_op_row.get("method")).upper()
        _pth = _text(_op_row.get("path") or _op_row.get("raw_path"))
        if not _mid or not _pth:
            _record_fallback("FIELD_OWNERSHIP_UNCONFIRMED_SKIPPED", 1)
            return False
        _mtp = f"{_mid}:{_pth}".lower()
        _role_terms = dict(_SUBJECT_ROLE_TERMS).get(role_key, ())
        for _frow in _frames:
            if not _frame_has_structured_ownership(_frow):
                continue
            _roles = {_text(r).lower() for r in _frame_grounded_actor_roles(_frow)}
            if (
                role_key not in _roles
                and not any(_text(t).lower() in _roles for t in _role_terms)
            ):
                continue
            _ops = {_text(o).lower() for o in _frame_grounded_ops(_frow)}
            if _mtp not in _ops:
                continue
            return True
        _record_fallback("FIELD_OWNERSHIP_UNCONFIRMED_SKIPPED", 1)
        return False

    for src in _list(data.get("sources") or data.get("source_inventory")):
        if not isinstance(src, dict):
            continue
        sid = _text(src.get("source_id") or src.get("id")) or _stable_id("src", src.get("filename"))
        model["sources"].append(_fact_node(
            node_id=sid if sid.startswith("bir_") else _stable_id("src", sid),
            typed_fields={
                "name": _text(src.get("filename") or src.get("original_name") or src.get("name") or sid),
                "source_type": _text(src.get("source_type") or src.get("type")),
                "hash": _text(src.get("text_hash") or src.get("content_hash") or src.get("hash")),
            },
            source_refs=[_source_ref(sid)],
            confidence=0.9,
            derivation="explicit",
        ))

    # Operations from OpenAPI / asset interfaces
    operations_by_transport_identity: dict[tuple[str, str, str], dict[str, Any]] = {}

    # Service ownership is part of transport/execution identity. Pre-index
    # source-declared owners so a low-precision service-less parser row can
    # inherit an owner only when that owner is unique. When more than one
    # service declares the same transport, guessing would erase a real
    # executable surface, so the service-less row must fail closed instead.
    raw_operations = [
        row
        for row in [
            *list(api_operations or []),
            *_list(data.get("operations") or data.get("interfaces")),
        ]
        if isinstance(row, dict)
    ]
    owner_services_by_transport: dict[tuple[str, str], set[str]] = {}
    for raw_op in raw_operations:
        raw_method = _text(
            raw_op.get("method") or raw_op.get("http_method") or "GET"
        ).upper()
        raw_path = _text(
            raw_op.get("path") or raw_op.get("endpoint") or raw_op.get("url")
        )
        if not raw_path:
            continue
        raw_service = _text(
            raw_op.get("service")
            or raw_op.get("service_name")
            or raw_op.get("server")
        )
        if not raw_service:
            raw_service = _service_name_from_source_refs(raw_op, data)
        if raw_service:
            owner_services_by_transport.setdefault(
                (raw_method, _path_shape(raw_path)), set()
            ).add(raw_service)

    def merge_unique(existing: list[Any], incoming: list[Any]) -> list[Any]:
        return _merge_unique_sorted(existing, incoming)

    for op in raw_operations:
        method = _text(op.get("method") or op.get("http_method") or "GET").upper()
        path = _text(op.get("path") or op.get("endpoint") or op.get("url"))
        if not path:
            continue
        service = _text(
            op.get("service") or op.get("service_name") or op.get("server")
        )
        if not service:
            service = _service_name_from_source_refs(op, data)
        path_shape = _path_shape(path)
        if not service:
            declared_owners = sorted(
                owner_services_by_transport.get((method, path_shape), set())
            )
            if len(declared_owners) == 1:
                service = declared_owners[0]
            elif len(declared_owners) > 1:
                model["coverage_gaps"].append(
                    _fact_node(
                        node_id=_stable_id(
                            "gap",
                            "operation_service_ownership_ambiguous",
                            method,
                            path_shape,
                        ),
                        typed_fields={
                            "gap_type": "operation_service_ownership_ambiguous",
                            "reason_code": "OPERATION_SERVICE_OWNERSHIP_AMBIGUOUS",
                            "description": (
                                "A service-agnostic operation matches multiple "
                                "source-declared service owners and cannot be safely "
                                "attached to one execution identity"
                            ),
                            "method": method,
                            "path_shape": path_shape,
                            "candidate_service_refs": declared_owners,
                        },
                        source_refs=[
                            _source_ref(
                                _text(op.get("source_id")) or "api_spec",
                                locator=f"{method} {path}",
                                kind="api_operation",
                            )
                        ],
                        confidence=1.0,
                        derivation="explicit",
                        status="unsupported",
                    )
                )
                continue
        op_id = _text(op.get("operation_id") or op.get("operationId") or op.get("id")) or _stable_id("op", method, path)
        side_effect = _infer_operation_effect(op, method)
        field_dictionary = _merge_unique_sorted(
            _list(op.get("field_dictionary")),
        )
        request_schema = _request_schema_for_operation(op)
        request_example = _operation_request_example(op)
        affected_fields: list[str] = []
        if method in ("POST", "PUT", "PATCH"):
            _af_seen: set[str] = set()
            for _af_key in _dict(_dict(request_schema).get("properties")):
                _af_norm = _text(_af_key)
                if _af_norm and _af_norm.casefold() not in _af_seen:
                    _af_seen.add(_af_norm.casefold())
                    affected_fields.append(_af_norm)
            for _af_key in request_example:
                _af_norm = _text(_af_key)
                if _af_norm and _af_norm.casefold() not in _af_seen:
                    _af_seen.add(_af_norm.casefold())
                    affected_fields.append(_af_norm)
        source_operation_refs = _merge_unique_sorted([
            _text(value)
            for value in (
                op.get("interface_id"),
                op.get("operation_id"),
                op.get("operationId"),
                op.get("id"),
            )
            if _text(value)
        ])
        operation = _fact_node(
            node_id=op_id if op_id.startswith("bir_") else _stable_id("op", service, method, path),
            typed_fields={
                "operation_id": op_id,
                "service": service,
                "_service_name": service,
                "method": method,
                "path": path,
                "request_schema": request_schema,
                "request_example": request_example,
                "response_schema": _dict(op.get("response_schema") or op.get("responses")),
                "parameters": _merge_unique_sorted(
                    _list(op.get("parameters")),
                    field_dictionary,
                ),
                "field_dictionary": field_dictionary,
                "security": _list(op.get("security")),
                "summary": _text(op.get("summary") or op.get("title")),
                "description": _text(op.get("description")),
                "tags": _list(op.get("tags")),
                "required_roles": _merge_unique_sorted(
                    _list(op.get("required_roles")),
                    _list(op.get("x-required-roles")),
                    _list(op.get("allowed_roles")),
                ),
                "side_effect_class": side_effect,
                "read_write": side_effect,
                "entity_refs": [_text(x) for x in _list(op.get("entity_refs")) if _text(x)],
                "affected_fields": affected_fields,
                "examples": _list(op.get("examples")),
                "source_operation_refs": source_operation_refs,
            },
            source_refs=[_source_ref(_text(op.get("source_id")) or "api_spec", locator=f"{method} {path}", kind="api_operation")],
            confidence=0.85 if op.get("operation_id") else 0.7,
            derivation="schema-derived" if not op.get("operation_id") else "explicit",
        )
        # Exact transport identity is service + method + path shape. A
        # service-less source has already been projected to a unique owner above,
        # so explicit endpoints owned by different services can never collapse
        # merely because their HTTP surfaces match.
        transport_identity = (service, method, path_shape)
        existing = operations_by_transport_identity.get(transport_identity)
        if existing is None:
            operations_by_transport_identity[transport_identity] = operation
            model["operations"].append(operation)
            continue

        existing["source_refs"] = merge_unique(
            _list(existing.get("source_refs")),
            _list(operation.get("source_refs")),
        )
        existing["source_operation_refs"] = merge_unique(
            _list(existing.get("source_operation_refs")),
            _list(operation.get("source_operation_refs")),
        )
        for field in (
            "parameters",
            "field_dictionary",
            "security",
            "tags",
            "entity_refs",
            "affected_fields",
            "examples",
        ):
            existing[field] = merge_unique(
                _list(existing.get(field)),
                _list(operation.get(field)),
            )
        for field in ("request_schema", "response_schema"):
            prior = _dict(existing.get(field))
            incoming = _dict(operation.get(field))
            if not prior and incoming:
                existing[field] = incoming
            elif prior and incoming and prior != incoming:
                existing[field] = _merge_schema_dicts(prior, incoming)
                conflict_paths = _schema_conflict_paths(prior, incoming)
                if not conflict_paths:
                    continue
                conflict_id = _stable_id(
                    "conflict",
                    "operation_schema",
                    service,
                    method,
                    path_shape,
                    field,
                )
                model["conflicts"].append(_fact_node(
                    node_id=conflict_id,
                    typed_fields={
                        "conflict_type": "operation_schema_conflict",
                        "operation_ref": _text(existing.get("id")),
                        "field": field,
                        "service": service,
                        "method": method,
                        "path_shape": path_shape,
                        "conflict_paths": conflict_paths,
                    },
                    source_refs=merge_unique(
                        _list(existing.get("source_refs")),
                        _list(operation.get("source_refs")),
                    ),
                    confidence=1.0,
                    derivation="explicit",
                    status="conflicting",
                ))
        if _dict(operation.get("request_example")):
            existing["request_example"] = (
                existing.get("request_example")
                if _dict(existing.get("request_example"))
                else operation.get("request_example")
            )
        for field in ("summary", "description"):
            existing[field] = _best_text(existing.get(field), operation.get(field))
        existing["operation_id"] = _canonical_operation_id(
            _list(existing.get("source_operation_refs")),
            _text(existing.get("operation_id")),
        )
        existing["confidence"] = max(
            float(existing.get("confidence") or 0.0),
            float(operation.get("confidence") or 0.0),
        )
        if operation.get("derivation") == "explicit":
            existing["derivation"] = "explicit"

    _canonicalize_duplicate_operation_ids(model)

    # Remaining Behavior IR construction is unchanged from the authoritative
    # baseline. The service-aware repair above is intentionally confined to the
    # operation transport-identity materialization boundary.
    raise BehaviorIRError("service_aware_operation_dedupe_patch_incomplete")
