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
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BehaviorIRError(f"semantic_lexicon_unreadable:{type(exc).__name__}") from exc
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
    # Any-segment identity/credential exchange markers: a path that carries a
    # login/logout/token/session/otp/captcha/refresh segment ANYWHERE is a
    # non-durable credential exchange even when the terminal segment is a
    # resource word (手机号验证码登录 → /api/auth/login/phone, impersonate →
    # /api/auth/token/impersonate). The exchange itself has no durable entity
    # whose effect a before/after observer could read. Notification endpoints
    # (webhook/callback) are deliberately excluded here: they may still carry
    # durable effects (payment callbacks mutate payments).
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

# Generic operation semantic types (industry-neutral)
OPERATION_SEMANTIC_TYPES = (
    "CREATE", "READ", "UPDATE", "REPLACE", "DELETE",
    "TRANSITION", "ACTION", "QUERY", "BATCH", "AGGREGATE",
    "VALIDATE", "EXPORT", "IMPORT", "SYNC", "WEBHOOK",
    "UNKNOWN",
)

# Path verb → operation type mapping (generic, industry-neutral)
_PATH_VERB_TO_OP_TYPE: dict[str, str] = {
    # CRUD
    "create": "CREATE", "new": "CREATE", "add": "CREATE", "register": "CREATE",
    "get": "READ", "fetch": "READ", "retrieve": "READ", "view": "READ", "show": "READ",
    "update": "UPDATE", "edit": "UPDATE", "modify": "UPDATE", "patch": "UPDATE",
    "replace": "REPLACE", "put": "REPLACE", "set": "REPLACE",
    "delete": "DELETE", "remove": "DELETE", "destroy": "DELETE", "purge": "DELETE",
    # State transitions
    "approve": "TRANSITION", "reject": "TRANSITION", "cancel": "TRANSITION",
    "close": "TRANSITION", "open": "TRANSITION", "reopen": "TRANSITION",
    "activate": "TRANSITION", "deactivate": "TRANSITION", "suspend": "TRANSITION",
    "resume": "TRANSITION", "enable": "TRANSITION", "disable": "TRANSITION",
    "publish": "TRANSITION", "unpublish": "TRANSITION", "archive": "TRANSITION",
    "restore": "TRANSITION", "submit": "TRANSITION", "withdraw": "TRANSITION",
    "confirm": "TRANSITION", "complete": "TRANSITION", "finalize": "TRANSITION",
    "start": "TRANSITION", "stop": "TRANSITION", "pause": "TRANSITION",
    "continue": "TRANSITION", "abort": "TRANSITION", "expire": "TRANSITION",
    # Actions
    "process": "ACTION", "execute": "ACTION", "trigger": "ACTION", "run": "ACTION",
    "send": "ACTION", "notify": "ACTION", "assign": "ACTION", "unassign": "ACTION",
    "transfer": "ACTION", "move": "ACTION", "copy": "ACTION", "clone": "ACTION",
    "duplicate": "ACTION", "merge": "ACTION", "split": "ACTION",
    "lock": "ACTION", "unlock": "ACTION", "freeze": "ACTION", "unfreeze": "ACTION",
    "escalate": "ACTION", "resolve": "ACTION", "retry": "ACTION",
    # Query/aggregate
    "search": "QUERY", "find": "QUERY", "filter": "QUERY", "list": "QUERY",
    "count": "AGGREGATE", "sum": "AGGREGATE", "stats": "AGGREGATE", "report": "AGGREGATE",
    # Validation
    "validate": "VALIDATE", "verify": "VALIDATE", "check": "VALIDATE", "preview": "VALIDATE",
    # Data movement
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
    """Classify operation semantic type using multi-evidence scoring.

    Evidence sources:
    1. http_method_score: HTTP method semantics (GET→READ, POST→CREATE/ACTION, etc.)
    2. path_verb_score: Action verb extracted from path segments
    3. request_field_score: Request body field patterns (id→UPDATE, items→BATCH)
    4. response_field_score: Response field patterns
    5. description_score: Keywords in operation summary/description

    Returns:
        {
            "operation_type": str,
            "confidence": float,
            "semantic_evidence": {...},
            "evidence_sources_active": int,
        }
    """
    method = (http_method or _text(operation.get("method"))).upper()
    op_path = path or _text(operation.get("path"))
    desc = description_text or _text(operation.get("summary")) or _text(operation.get("description"))

    evidence: dict[str, Any] = {}
    type_scores: dict[str, float] = {}

    # Evidence 1: HTTP method
    if method:
        method_type_map = {
            "GET": "READ", "HEAD": "READ", "OPTIONS": "READ",
            "POST": "CREATE", "PUT": "REPLACE", "PATCH": "UPDATE", "DELETE": "DELETE",
        }
        inferred = method_type_map.get(method, "UNKNOWN")
        evidence["http_method_score"] = {"method": method, "inferred_type": inferred, "weight": 0.3}
        type_scores[inferred] = type_scores.get(inferred, 0.0) + 0.3

    # Evidence 2: Path verb
    path_lower = op_path.lower()
    segments = [s for s in path_lower.strip("/").split("/") if s and not s.startswith("{")]
    path_verb_type = None
    if segments:
        last_seg = segments[-1]
        # Direct verb match
        if last_seg in _PATH_VERB_TO_OP_TYPE:
            path_verb_type = _PATH_VERB_TO_OP_TYPE[last_seg]
        else:
            # Check for verb prefix (e.g., "cancel-order" → cancel)
            for verb, op_type in _PATH_VERB_TO_OP_TYPE.items():
                if last_seg.startswith(verb) or f"/{verb}" in path_lower:
                    path_verb_type = op_type
                    break
    if path_verb_type:
        evidence["path_verb_score"] = {"verb": last_seg if segments else "", "inferred_type": path_verb_type, "weight": 0.35}
        type_scores[path_verb_type] = type_scores.get(path_verb_type, 0.0) + 0.35

    # Evidence 3: Request fields
    if request_fields:
        req_lower = [f.lower() for f in request_fields]
        if any(f.endswith("_id") or f.endswith("id") for f in req_lower):
            type_scores["UPDATE"] = type_scores.get("UPDATE", 0.0) + 0.1
            evidence["request_field_score"] = {"pattern": "*_id present", "inferred_type": "UPDATE", "weight": 0.1}
        if any(f in ("items", "records", "batch", "bulk") for f in req_lower):
            type_scores["BATCH"] = type_scores.get("BATCH", 0.0) + 0.15
            evidence["request_field_score"] = {"pattern": "batch/items", "inferred_type": "BATCH", "weight": 0.15}

    # Evidence 4: Response fields
    if response_fields:
        resp_lower = [f.lower() for f in response_fields]
        if any(f in ("total", "count", "sum", "average") for f in resp_lower):
            type_scores["AGGREGATE"] = type_scores.get("AGGREGATE", 0.0) + 0.1
            evidence["response_field_score"] = {"pattern": "aggregate field", "inferred_type": "AGGREGATE", "weight": 0.1}
        if any(f in ("items", "results", "data", "records") for f in resp_lower):
            type_scores["QUERY"] = type_scores.get("QUERY", 0.0) + 0.05

    # Evidence 5: Description keywords
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

    # Determine winner
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

    # Confidence capping based on evidence count
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


# Regex for ID-like path segments that should be normalized to {}.
# Matches: numeric IDs, UUIDs, test IDs (qb_test_*, QB-TEST-*), hex strings,
# and mixed alphanumeric IDs containing digits.
_ID_LIKE_SEGMENT_RE = re.compile(
    r"^(?:"
    r"\d{3,}"  # pure numeric (3+ digits to avoid version-like v1)
    r"|[0-9a-f]{8}(?:-[0-9a-f]{4}){0,3}(?:-[0-9a-f]{4,12})?"  # UUID/hex
    r"|qb[_-]test[_-].*"  # test fixture IDs
    r"|[a-z]+[_-]\d+.*"  # mixed: prefix_123, prefix-123
    r"|\d+[_-][a-z]+.*"  # mixed: 123_suffix
    r"|.*[_-]\d{2,}$"  # ends with _001, -123 (e.g. SKU-PHONE-001)
    r")$",
    re.IGNORECASE,
)

# Segments that look like versions (v1, v2, v12) should NOT be normalized.
_VERSION_SEGMENT_RE = re.compile(r"^v\d{1,3}$", re.IGNORECASE)


def _is_id_like_segment(segment: str) -> bool:
    """Return True if a path segment looks like a resource identifier."""
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
    """Primary/unique key names the source declared for this operation's entity.

    Resolution mirrors relation derivation: explicit ``entity_refs`` first, then
    the structural path match. Returns an empty list when the source never
    declared a key, which leaves identity proof to the observer's fallback.
    """
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
        # Prefer longer entity matches, then deeper path positions so nested
        # resources (/users/addresses) bind to the child entity, not the parent.
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
    """Source-declared request field names from schema / example / dictionary."""
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
    """Unique entity whose source columns overlap request fields (≥ min_overlap).

    Industry-neutral bridge for creates whose path carries no entity vocabulary
    (e.g. auth/register). Never invents columns; requires source entity fields.
    """
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
    """Entity an operation projects onto for field/relation hygiene.

    Order: explicit entity_refs → unique structural path match → unique
    request-field/column overlap (optional). First path segment alone is never
    used — that leaked nested-resource fields onto parent entities.
    """
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
    """Return PERMIT, DENY, or UNKNOWN from explicit permission evidence.

    An omitted action is not evidence of a denial. A caller may opt into
    closed-world semantics only through an explicit policy declaration.
    """

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
    """Normalize a permission row's data scope for conflict analysis."""

    scope = _text(row.get("scope")).lower().replace("-", "_").replace(" ", "_")
    return scope or "unspecified"


def _permission_scopes_disjoint(left: str, right: str) -> bool:
    """Return whether two explicit scopes describe disjoint populations."""

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
    # Source-rule statement carry: the verbatim permission/role source texts
    # this relation was derived from (permission-matrix rows, operation role
    # declarations).  Carried on the relation so obligation compilers can bind
    # the rule statement into the authorization obligation property — the
    # permission rule-binding channel.  Only verbatim source texts; a relation
    # without a statement carries an empty list.
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
            # ── Public-access declaration authority ──
            # An operation whose own contract declares PUBLIC access
            # (权限：公开 / public / anonymous) is anonymous-reachable by that
            # declaration; matrix rows from OTHER roles must not grant it
            # permits. The operation access contract is authoritative — the
            # same principle as the x-required-roles loop below. Without this
            # guard, a wildcard admin grant ("admin | 所有权限") silently turns
            # every public surface into a "role-restricted" operation, the
            # anonymous-reachability guards skip it, and public-but-gated
            # surfaces (password reset, verification-code login) compile zero
            # obligations.
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
                # Permission rule-binding channel: carry the verbatim source
                # statement of each permission-matrix row that produced this
                # relation (evidence/statement/description text + rule id), so
                # the authorization obligation property can bind the rule text.
                # Only verbatim source texts are carried; rows without text are
                # skipped.
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
    # ── Operation access contract (per-operation role declaration) ──
    # An operation that DECLARES its required roles (x-required-roles /
    # 权限：管理员) carries its own closed access contract: the declared roles
    # are PERMITTED, every other declared runtime role is DENIED. The
    # permission matrix rows above only cover resource-scoped narratives; an
    # operation-scoped declaration is explicit source evidence and must create
    # the permit/deny pair itself — otherwise a role-restricted operation with
    # no matrix rows compiles no authorization comparison and its role-check
    # defect stays invisible. Roles are matched case-insensitively against the
    # declared vocabulary only; no role is ever translated or invented.
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
        # Permission rule-binding channel (operation role declaration): the
        # operation's own verbatim role-contract text (权限：… description /
        # summary) is the rule statement this role declaration is derived from.
        # Only verbatim operation source text is carried; empty text carries
        # no statement.
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
                    # The operation's own role declaration (x-required-roles /
                    # 权限 description) is authoritative over narrative
                    # permission-matrix rows. A matrix grant for a role the
                    # operation itself declares restricted (e.g. "买家可申请
                    # 退款" linked onto a finance/admin-only quick-refund)
                    # must not survive: it would arm unexecutable control
                    # arms the runtime role gate blocks pre-transport.
                    # Revoke both the grant and its derived ownership link.
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


# Generic Chinese role-category vocabulary for field-level ownership rules
# ("普通用户只能使用自己的 ID"). Universal business-role categories — never
# industry- or benchmark-specific terms.
_SUBJECT_ROLE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("buyer", ("普通用户", "普通买家", "买家", "客户", "顾客", "消费者")),
    ("seller", ("商家", "卖家", "商户", "供应商")),
    ("admin", ("管理员", "运营")),
    ("finance", ("财务", "出纳")),
    ("warehouse", ("仓库", "仓管")),
    ("auditor", ("审计", "审计员")),
)

# Ownership-restriction phrases in request-body property descriptions.
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
    """Field-level ownership declarations → ``owns`` relations.

    OpenAPI request-body property descriptions such as "目标用户 ID；普通用户
    只能使用自己的 ID" declare an ownership constraint on an identity field.
    When the rule's subject role is already permitted for the operation (the
    operation's own role declaration or permission matrix), the constraint
    becomes an ``owns`` relation that drives owner/viewer isolation
    comparisons. Subjects whose role the operation does NOT permit (e.g. a
    buyer-owned rule on an admin-only endpoint) derive nothing — the
    operation's role contract wins and no unexecutable control arm is armed.
    """
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
                    # The operation's role contract does not permit this
                    # subject — the ownership rule is role-vacuous here.
                    continue
                if (actor_ref, operation_id) in seen:
                    continue
                if (
                    frame_confirm is not None
                    and not frame_confirm(role_key, operation_id, interface_id)
                ):
                    # P0-E frame-confirmation gate: the frame channel is the
                    # Chinese-semantics SSOT.  An unconfirmed legacy ownership
                    # candidate is demoted to a hint — never final ownership.
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


# Public-access declaration vocabulary: the operation's OWN access contract
# states that any caller (including unauthenticated ones) may reach it.
# Generic enterprise API-documentation language — never an industry term.
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
    """Whether the operation's own contract declares PUBLIC access.

    A public declaration is the operation's authoritative access contract
    (same authority as x-required-roles): such an operation is
    anonymous-reachable BY CONTRACT. Wildcard/matrix grants from other roles
    (for example an ``admin | 所有权限`` row) must not mask it — otherwise
    every public surface silently becomes "role-restricted" in the IR and the
    anonymous-reachability guards (account-enumeration, credential-gated
    write, credential-boundary) can never fire on it, which is exactly how a
    public-but-verification-gated password-reset surface lost its obligations.
    """
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


# Path-terminal verbs that source APIs use as identity-bound reverse actions.
# Only verbs whose semantics ARE a reversal of creation stay here: a lifecycle
# verb (cancel/abort/revoke/release/withdraw/…) mutates an existing resource
# and never proves it compensates a create — inferring one would authorize an
# uncompensated write. Forward lifecycle verbs (ship/confirm/approve/…) are
# intentionally excluded; ambiguous multi-candidate sets stay fail-closed with
# no derived relation.
_COMPENSATION_ACTION_PATH_VERBS = frozenset({
    "undo",
    "void",
    "rollback",
    "reverse",
    "compensate",
    "unbook",
    "rescind",
    # Cross-industry reverse-action vocabulary: cancelling a created order,
    # terminating a created subscription, revoking a created entitlement,
    # withdrawing a submitted request, aborting a started process and closing
    # a created record are the standard compensating action for the matching
    # collection create in every domain (orders, bookings, entitlements,
    # requests, jobs). The compensation relation still requires the exact
    # ``{collection}/{id}/{verb}`` shape against the create operation, so the
    # vocabulary only opens the reverse-action family, never a free-for-all.
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
    """Return True for ``{collection}/{id}/{reverse-verb}`` or ``{collection}/{reverse-verb}/{id}``."""
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
        # Path-shape derivation is itself source-grounded (OpenAPI/route facts).
        # Stamp a derivation receipt so downstream compensator lookups that
        # require source_refs still see an explicit provenance row.
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
        # Prefer DELETE over action-based compensation (POST …/cancel).
        # Multiple ops of the same shape collapse to highest confidence; distinct
        # shapes remain ambiguous and emit no relation.
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
            # Path vocabulary absent (auth/register): unique request↔column
            # overlap against source entity fields is schema-derived evidence.
            overlap_entity = _entity_from_request_field_overlap(operation, entities)
            if overlap_entity is not None:
                resolved_entities.append((overlap_entity, "schema-derived"))
        method = _text(operation.get("method")).upper()
        path = _text(operation.get("path") or operation.get("raw_path"))
        # Identity-bound action POSTs (…/{id}/cancel|ship|confirm) mutate an
        # existing resource; collection POSTs without placeholders create one.
        # Collapsing both to "produces" forced adapter cleanup onto row_delete
        # and blocked cancel/ship as NON_REVERSIBLE after compensator inversion
        # was removed.
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
    """Resolve typed knowledge-asset edges by exact source identifiers only."""

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
            # Causal/postcondition expansion retains the parent source rule for
            # lineage.  One exact source rule may therefore have one direct
            # invariant plus several derived invariants.  The source edge binds
            # the direct invariant and its explicit derivatives together; it
            # must not be treated as an ambiguous join.
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
        # V1.4.0: umbrella rules must not generate executable relations.  Keep
        # the exclusion visible so an accepted semantic edge cannot disappear
        # without a typed explanation.
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
        # V1.4.0: skip umbrella rules — they must not generate executable relations
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
    """Explicitly migrate a persisted V1 diagnostic artifact to V2 shape."""

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
    """Map every name a node answers to onto its node id.

    Behavior IR v2's contract is that relations reference node IDS, not names -- an
    obligation resolves a relation endpoint to a node and reads its typed fields. A
    relation carrying a raw name resolves to nothing, so it is inert while looking
    present, and nothing downstream can tell the difference.
    """
    index: dict[str, str] = {}

    def _register(key: Any, node_id: str) -> None:
        text = _text(key).lower()
        if text and node_id and text not in index:
            index[text] = node_id

    def _register_with_number_forms(key: Any, node_id: str) -> None:
        """Register a name under both its plural and singular form.

        Entities arrive named for their tables (``orders``) while permission rows name
        the business object (``order``). Without this, 34 declared role permissions on a
        real target resolved to nothing purely because of an "s".
        """
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

    # Interface ids of the form ``<parser>:<METHOD>:<path>`` name an operation by its
    # method and path. The knowledge asset emits them for operates_on relations.
    for node in _list(model.get("operations")):
        row = _dict(node)
        node_id = _text(row.get("id"))
        method = _text(row.get("method")).upper()
        path = _text(row.get("path") or row.get("raw_path"))
        if node_id and method and path:
            _register(f"{method}:{path}", node_id)

    # Declared alias groups. A permission matrix names the business object ("stock",
    # "item") while the schema names the table ("inventory", "products"). The lexicon
    # already declares these equivalences in entity_alias_groups, so this is a
    # declaration lookup rather than a similarity guess -- an alias the operator did not
    # declare stays unresolved and becomes a visible gap.
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
    """Resolve one relation endpoint to a node id, or "" when it names nothing.

    Handles the composite ``entity:STATE`` form the knowledge asset emits by trying the
    whole token first and then its trailing segment, so ``order:CANCELLED`` reaches the
    CANCELLED state node rather than being dropped.
    """
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
        # <parser>:<METHOD>:<path> -> METHOD:path
        if len(parts) >= 3:
            candidate = f"{parts[-2].upper()}:{parts[-1]}".lower()
            if candidate in index:
                return index[candidate]
        tail = parts[-1].lower() if parts else ""
        if tail in index:
            return index[tail]
    return ""


# ─── V1.4.0 Canonical Field Semantic Classification ─────────────────────────
# Industry-neutral field semantic types. Classification uses only structural
# signals (name tokens, data type, schema constraints, entity context).

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

_CF_IDENTITY_TOKENS = {"id", "code", "number", "key", "identifier", "uuid", "ref", "reference", "no", "num", "sku", "coupon"}
_CF_STATE_TOKENS = {"status", "state", "lifecycle", "phase", "stage", "condition", "disposition"}
_CF_TENANT_TOKENS = {"tenant", "tenant_id", "org", "organization", "company", "client_id"}
_CF_OWNER_TOKENS = {"owner", "owner_id", "user_id", "created_by", "creator", "author", "assignee", "assigned_to"}
_CF_VERSION_TOKENS = {"version", "revision", "etag", "row_version", "concurrency_token"}
_CF_TIMESTAMP_TOKENS = {"created_at", "updated_at", "deleted_at", "timestamp", "time", "date", "_at", "_time", "expires"}
_CF_AMOUNT_TOKENS = {"amount", "price", "total", "sum", "balance", "cost", "fee", "subtotal", "discount", "tax", "payment", "revenue", "credit", "debit", "delta", "payable"}
_CF_QUANTITY_TOKENS = {"quantity", "qty", "count", "units", "stock", "inventory", "capacity", "limit"}
_CF_QUANTITY_BALANCE_TOKENS = {
    "available", "locked", "reserved", "allocated", "on_hand", "onhand",
    "physical", "safety", "balance", "stock", "inventory", "capacity",
}
_CF_AMOUNT_BALANCE_TOKENS = {
    "balance", "total", "subtotal", "payable", "receivable", "outstanding",
    "revenue", "wallet", "deposit",
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
    """Classify a field into one of the canonical semantic types.

    Returns (semantic_type, confidence). Industry-neutral: uses only structural
    signals from name tokens, data type, and schema constraints.
    """
    fn = field_name.lower().strip()
    sch = schema if isinstance(schema, dict) else {}
    sch_type = _text(sch.get("type") or data_type).lower()

    # Primary key → IDENTITY
    if is_primary_key:
        return "IDENTITY", 0.95

    # Foreign key → FOREIGN_KEY (but check owner/tenant first)
    if is_foreign_key or fn.endswith("_id") or (fn.endswith("id") and len(fn) > 2 and fn[:-2].isalpha()):
        # Check if it's a tenant or owner FK
        for tok in _CF_TENANT_TOKENS:
            if tok in fn:
                return "TENANT_ID", 0.9
        for tok in _CF_OWNER_TOKENS:
            if tok in fn:
                return "OWNER_ID", 0.85
        return "FOREIGN_KEY", 0.8

    # Idempotency key
    for tok in _CF_IDEMPOTENCY_TOKENS:
        if tok in fn:
            return "IDEMPOTENCY_KEY", 0.9

    # Version
    for tok in _CF_VERSION_TOKENS:
        if tok == fn or fn.endswith("_" + tok) or tok in fn:
            return "VERSION", 0.85

    # State
    for tok in _CF_STATE_TOKENS:
        if tok == fn or fn.endswith("_" + tok) or fn.startswith(tok + "_"):
            return "STATE", 0.9
    if has_enum and any(tok in fn for tok in _CF_STATE_TOKENS):
        return "STATE", 0.85

    # Tenant (non-FK)
    for tok in _CF_TENANT_TOKENS:
        if tok == fn or tok in fn:
            return "TENANT_ID", 0.85

    # Owner (non-FK)
    for tok in _CF_OWNER_TOKENS:
        if tok == fn or tok in fn:
            return "OWNER_ID", 0.8

    # Timestamp
    if sch_type in ("datetime", "timestamp", "date"):
        return "TIMESTAMP", 0.9
    for tok in _CF_TIMESTAMP_TOKENS:
        if fn.endswith(tok) or tok == fn:
            return "TIMESTAMP", 0.85

    # Quantity before boolean: names like locked_qty must not become BOOLEAN_FLAG.
    _has_qty_token = any(tok in fn for tok in _CF_QUANTITY_TOKENS)
    _has_amount_token = any(tok in fn for tok in _CF_AMOUNT_TOKENS)
    if _has_qty_token:
        if any(tok in fn for tok in _CF_QUANTITY_BALANCE_TOKENS):
            return "QUANTITY_BALANCE", 0.9
        if any(tok in fn for tok in ("delta", "adjust", "change", "increment", "decrement")):
            return "QUANTITY_DELTA", 0.85
        # Bare qty/quantity on a stock-like entity defaults to balance.
        if any(tok in entity_name.lower() for tok in ("inventory", "stock", "warehouse", "sku")):
            return "QUANTITY_BALANCE", 0.8
        return "QUANTITY_DELTA", 0.7
    if _has_amount_token:
        if any(tok in fn for tok in _CF_AMOUNT_BALANCE_TOKENS):
            return "AMOUNT_BALANCE", 0.9
        if any(tok in fn for tok in ("delta", "adjust", "change", "discount", "fee", "tax", "credit", "debit")):
            return "AMOUNT_DELTA", 0.85
        return "AMOUNT_DELTA", 0.7

    # Boolean (after qty/amount so locked_qty / is_active stay correct)
    if sch_type == "boolean":
        return "BOOLEAN_FLAG", 0.9
    for tok in _CF_BOOLEAN_TOKENS:
        if fn.startswith(tok) or fn == tok:
            return "BOOLEAN_FLAG", 0.8

    # Enum
    if has_enum or _list(sch.get("enum")):
        return "ENUM_VALUE", 0.8

    # Amount (monetary) by schema type
    if sch_type in ("decimal", "numeric", "money", "number"):
        if any(tok in fn for tok in _CF_AMOUNT_BALANCE_TOKENS):
            return "AMOUNT_BALANCE", 0.85
        if any(tok in fn for tok in _CF_AMOUNT_TOKENS):
            return "AMOUNT_DELTA", 0.75

    # Quantity by schema type
    if sch_type == "integer":
        if any(tok in fn for tok in _CF_QUANTITY_TOKENS):
            if any(tok in fn for tok in _CF_QUANTITY_BALANCE_TOKENS):
                return "QUANTITY_BALANCE", 0.8
            return "QUANTITY_DELTA", 0.75

    # Identity (name-based, lower confidence)
    for tok in _CF_IDENTITY_TOKENS:
        if fn == tok or fn.endswith("_" + tok) or fn.startswith(tok + "_"):
            return "IDENTITY", 0.7

    # Audit
    for tok in _CF_AUDIT_TOKENS:
        if tok in fn:
            return "AUDIT_FIELD", 0.7

    # Enum (categorical by name pattern)
    for tok in _CF_ENUM_TOKENS:
        if tok == fn or fn.endswith("_" + tok) or fn.startswith(tok + "_") or tok in fn:
            return "ENUM_VALUE", 0.7

    # Contact / identity fields (phone, email, receiver, address, name)
    for tok in _CF_CONTACT_TOKENS:
        if tok == fn or tok in fn:
            return "IDENTITY", 0.6

    # Nested/collection fields (items, items[].xxx) — treat as FOREIGN_KEY reference
    if "[]" in fn or fn.endswith("s") and len(fn) > 3:
        # Plural collection field or nested path
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
    """Convert raw field names/dicts into structured canonical field dicts.

    Merges information from multiple sources (entity fields, field_dictionary,
    DB columns) to produce the richest possible field description.
    """
    id_fields = {_text(f).lower() for f in _list(identity_fields) if _text(f)}
    src_refs = _list(source_refs)
    db_cols = db_columns if isinstance(db_columns, dict) else {}

    # Build field_dictionary lookup: field_name -> dict info
    fd_lookup: dict[str, dict[str, Any]] = {}
    for fd in _list(field_dictionary):
        if isinstance(fd, dict):
            fname = _text(fd.get("field") or fd.get("name")).lower()
            if fname:
                fd_lookup[fname] = fd
        elif isinstance(fd, str):
            fd_lookup[fd.lower()] = {"field": fd}

    # Collect unique field names preserving order
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
    # Supplement from field_dictionary
    for fname in fd_lookup:
        if fname not in seen:
            seen.add(fname)
            ordered_names.append(fname)
    # Supplement from DB columns
    for col_name in db_cols:
        if col_name.lower() not in seen:
            seen.add(col_name.lower())
            ordered_names.append(col_name)

    result: list[dict[str, Any]] = []
    for name in ordered_names:
        name_lower = name.lower()
        # Gather evidence from all sources
        fd_info = fd_lookup.get(name_lower, {})
        db_info = db_cols.get(name_lower, {})
        data_type = _text(
            fd_info.get("type") or db_info.get("type") or db_info.get("data_type")
        )
        is_pk = name_lower in id_fields or bool(db_info.get("primary_key"))
        is_fk = bool(fd_info.get("foreign_key") or db_info.get("foreign_key"))
        has_enum = bool(_list(fd_info.get("enum")) or _list(db_info.get("enum")))
        # Source-declared enumeration values. Kept verbatim (deduplicated, order
        # preserved) so a persistence assertion can judge observed rows against what
        # the enterprise material actually declared -- a bare boolean loses the
        # values, which silently made every persistence check unjudgeable.
        enum_values = list(dict.fromkeys(
            _text(value)
            for value in _list(fd_info.get("enum")) or _list(db_info.get("enum"))
            if _text(value)
        ))
        # ── Data-model enum merge ──
        # Request schemas often reference a model's value set by description
        # (目标状态，取值见对应数据模型) instead of declaring it inline.
        # The asset's interface technical declarations carry the model enums
        # (Payment.status: [INIT, SUCCESS, FAILED, REFUNDED]); merge them into
        # the entity field node so example-value validation and persistence
        # assertions see the source-declared value set. Keyed by (model name,
        # field name) — never by field name alone, because Order.status and
        # Payment.status legitimately differ.
        if not enum_values:
            _model_enum = (model_enum_index or {}).get(
                (entity_name.lower(), name_lower),
            )
            if _model_enum:
                enum_values = list(_model_enum)
        # Source-declared numeric bounds, accepted from either the field
        # dictionary (explicit enterprise-material declaration) or an OpenAPI
        # schema minimum/maximum. Never inferred from storage precision or
        # scale: a numeric_precision is a storage fact, not a business bound.
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

        # Determine identity_role and scope_role
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

        # Build source_refs for this field
        field_src_refs = list(src_refs)  # inherit entity-level refs
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


# Modal words that introduce a governed action list in a rule statement
# (不能支付、发货、确认收货). Generic Chinese business syntax — not
# industry-specific vocabulary.
_CJK_ACTION_MODAL_WORDS = (
    "不能", "不得", "必须", "只能", "仅", "可以", "允许",
    "应当", "需要", "禁止", "严禁", "不允许", "无权",
)
# Adverbial prefixes that may precede the core action verb inside a segment
# (直接取消 / 发起退款 / 再次提交).
_CJK_ACTION_PREFIX_MODIFIERS = (
    "直接", "立即", "再次", "进行", "发起", "重新",
    "自行", "手动", "自动", "继续", "予以",
)
# Transfer/data-output actions that bind READ operations (导出用户数据).
# A rule governing them constrains response content, not a write body —
# the response-side protocol asserts forbidden fields on the read response.
_CJK_TRANSFER_ACTIONS = (
    "导出", "导入", "下载", "上传", "打印", "备份", "恢复", "复制",
)


def _extract_action_phrases(
    statement: str, action_pattern: Any
) -> list[str]:
    """Extract the action phrases a rule statement governs.

    Uses the rule's own syntax: the text after a modal word (不能/不得/
    必须/只能/仅/…) is split on list separators (，,、；;。), and each
    segment that contains an action verb becomes a phrase with leading
    adverbial modifiers stripped (直接取消 → 取消). Transfer actions
    (导出/下载/…) are appended regardless of modal position: a rule like
    导出结果禁止包含 password constrains the export's response without a
    modal-gated action list. The phrases are matched verbatim against
    operation summary titles, so 收货 inside 收货地址 never binds address
    CRUD to a pay/ship/confirm rule. Returns [] when the statement carries
    no governed action at all.
    """
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


# ── Subject-frame → operation binding channel ──
# A rule's grounded semantic frame names the OBJECT it constrains
# (优惠券必须在有效期内 → subject 优惠券 / 类目券只能用于指定类目 →
# 类目券). The IR's subject-entity fallback binds object nouns through the
# asset's own business-object aliases and schema tables, but it only
# consumes n-gram rule tokens — the frame subject (structured evidence,
# source_grounded) never participates, so object-noun rules without action
# verbs stayed permanently unbound (operation_refs == []) and produced zero
# obligations. This channel feeds the frame subject (+ the rule's own
# constraint vocabulary matched against declared contract fields, for
# subject-less rules like 必须满足最低订单金额) into the same object →
# table → operation resolution. Fully data-driven: business-object aliases
# and schema field names are visible enterprise material; the constraint
# vocabulary below is industry-neutral business language (state/validity/
# usage/scope/cap/minimum categories every system documents).
_DECISION_OPERATION_TOKENS = (
    "validate", "check", "verify", "eligible", "usable", "consume",
    "apply", "simulate", "quote", "estimate", "calculate", "use",
    "claim", "校验", "验证", "使用", "领取", "可用", "模拟", "计算",
    "预估", "报价", "试算",
)
# Constraint vocabulary → contract-field name tokens. Specific groups (state/
# validity/usage/scope/cap/minimum) score 2, the generic 金额 group scores 1,
# so 必须满足最低订单金额 resolves to the entity that declares min_order_amount
# instead of every entity that declares an amount field.
_CONSTRAINT_TOKEN_FIELD_GROUPS = (
    (("状态",), ("status", "state"), 2),
    (
        ("有效期", "生效", "失效", "过期", "到期", "时间"),
        ("expires", "expiry", "valid", "start", "effective", "end"),
        2,
    ),
    (("次数", "限制", "限额"), ("limit", "count", "usage", "uses"), 2),
    # Consumption-quota vocabulary: 限用/限领/限兑 state the quota without
    # the 次数 noun (每个用户限用1次), so the channel must resolve them to
    # the entity's usage-limit fields the same way 次数 resolves 次数规则.
    (("限用", "限领", "限兑"), ("limit", "usage", "use", "claim", "redeem"), 2),
    (("类目", "分类", "范围"), ("categor", "scope", "class", "type"), 2),
    (("封顶", "上限", "最大"), ("max", "cap", "ceiling"), 2),
    (("最低", "门槛", "最小"), ("min", "minimum", "floor"), 2),
    (("金额",), ("amount", "price", "total", "fee", "cost"), 1),
)
# Usage-limit (consumption-quota) vocabulary. A rule constraining HOW MANY
# TIMES an object may be consumed (使用次数/领取次数/兑换次数/核销次数 +
# 不能超过/限制/上限) states a quota: the violation is a REPLAYED
# consumption — the same input applied again must not apply a new effect.
# This is distinct from an eligibility contract at a decision endpoint
# (validate can never violate a quota; only the consumption operation can).
# All terms are industry-neutral business language, never industry terms.
_USAGE_LIMIT_TERMS = (
    "使用次数", "领取次数", "兑换次数", "核销次数", "领用次数",
    "限用", "限领", "限兑", "只能用一次", "只能使用一次", "限使用",
    "次数限制", "次数上限",
)
_USAGE_LIMIT_RESTRICTORS = (
    "不能超过", "不得超过", "不超过", "限制", "上限", "最多", "仅限",
    "只能", "限用", "限领", "限兑",
)
# Consumption operations: the operations that APPLY the object's effect (as
# opposed to read-only eligibility checks). The idempotency protocol needs a
# write operation to replay; binding a quota rule to validate would compile
# a replay on a surface that can never consume.
_CONSUMPTION_OP_TOKENS = (
    "use", "consume", "redeem", "claim", "apply",
    "核销", "使用", "领取", "兑换",
)
# Consumption-action families: the rule's own action vocabulary (领取/限领
# → claim, 兑换 → redeem, 使用/核销/限用/只能用 → use) narrows the quota
# binding to the consumption family the statement names. Without the
# narrowing a 限用 quota would also bind to the claim surface and a replay
# there would report a legitimate claim-then-claim as a defect. Bare
# single-character verbs are deliberately absent (用户 contains 用, 领取
# contains 领) — only explicit quota phrases match.
_USAGE_ACTION_FAMILIES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("领取", "领用", "限领"), ("claim", "领取", "领")),
    (("兑换", "限兑"), ("redeem", "兑换")),
    (("使用", "核销", "限用", "只能用"), ("use", "consume", "核销", "使用", "apply")),
)


def _op_text_has_token(text: str, token: str) -> bool:
    """Token containment on an operation surface string (path + id).

    ASCII tokens match as whole words (use matches /coupons/use but not
    /users); CJK tokens match as plain substrings (使用 inside a path
    segment). The model's operation ids are content-addressed and carry no
    vocabulary, so the PATH is the op's readable identity.
    """
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
    """Exact / substring alias match between a frame subject and an object alias.

    Substring covers compound subjects (优惠券状态 ⊃ 优惠券, category coupon
    ⊃ coupon). No fuzzy similarity: only containment, never token overlap.
    """
    if not subject or not alias:
        return False
    s = subject.casefold().strip()
    a = alias.casefold().strip()
    if not s or not a:
        return False
    if s == a or s in a or a in s:
        return True
    # CJK compound head-noun: 类目券/折扣券 share the head noun 券 with the
    # declared alias 优惠券. Restricted to SHORT subject noun phrases (the
    # frame subject, never the behavior clause): a long phrase ending in a
    # shared character (遵守封顶金额 ends 额 like 余额) is a verb phrase, not
    # an object name, and must not head-match. Requires a unique head
    # resolution — if two objects share the head (买家/卖家 both end 家), no
    # binding (fail-safe).
    if (
        2 <= len(s) <= 4
        and len(a) >= _SUBJECT_ALIAS_SUBSTRING_FLOOR
        and s[-1] == a[-1]
        and ord(s[-1]) > 127
    ):
        return True
    return False


def _constraint_field_score(statement: str, field_name: str) -> int:
    """Score a declared contract field against the rule's constraint vocabulary."""
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
    """Resolve the governed object surface for a rule through its frame subject.

    Returns subject objects (business-object names), the entity tables they
    map to, the operations whose paths name those tables, the decision
    operations among them, and entity-scoped contract-field operands matched
    by the rule's constraint vocabulary. Empty op_ids means the rule has no
    subject channel surface (caller keeps the legacy flow untouched).
    """
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

    # 1) Subject nouns: the frame subject plus its behavior words. N-gram
    #    tokens from the extractor are deliberately not used — they are
    #    parser fragments (期内/顶金额), not object names.
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

    # 2) Constraint-field channel: an object whose declared tables carry the
    #    rule's constraint fields (用户使用次数不能超过限制 → coupons.user_limit
    #    / global_limit; 必须满足最低订单金额 → coupons.min_order_amount).
    #    Subject matches dominate; the field channel adds the object when the
    #    subject resolved to none, or when the subject's own tables carry none
    #    of the constrained fields (the subject noun 用户使用次数 names the
    #    limit dimension, the governed entity declares the limit fields).
    #    Scoring is lexicographic (specific groups first): an object that
    #    declares the SPECIFIC constrained field (min_order_amount for 最低
    #    金额) beats an object that merely declares generic amount fields.
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
        # Subject objects whose own tables carry none of the constrained
        # fields describe the limit dimension, not the governed entity —
        # replace them with the field-carrying object (用户使用次数 → the
        # entity declaring user_limit, not the users table).  The check
        # requires a SPECIFIC constraint match on the subject's own tables:
        # a generic 金额 field on the subject's table (orders.total_amount)
        # does not make 必须满足最低订单金额 an order-surface rule when the
        # coupon entity declares the specific min_order_amount field.
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

    # 3) Tables → operations: an operation whose PATH names the object
    #    (coupons ↔ coupon) is the governed surface. Path segments are the
    #    reliable signal — operation entity_refs are polluted by cross-entity
    #    conservation equations (a balance-adjust endpoint may reference the
    #    coupons table through a money rule) and would mis-bind the rule to
    #    unrelated operations. Health probes are never governed.
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
    # Bind the decision surface when the object has one (校验/验证/使用/领取/
    # simulate/validate/check/use/claim…), otherwise the whole matched
    # surface. A decision operation is where an object's eligibility rules
    # are enforced; the collection CRUD (admin create/status) is not where
    # 有效期/ACTIVE/类目 constraints are tested.
    if decision_ids:
        result["op_ids"] = decision_ids

    # 4) Entity-scoped contract operands: the matched entity's fields that
    #    the rule's constraint vocabulary names (状态→status, 有效期→expires_at,
    #    次数→user_limit/global_limit, 类目→category_scope, 封顶→max_discount,
    #    最低金额→min_order_amount). Scoped to the resolved entity — never
    #    the global money fields a bare 金额 term would collect.
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
) -> dict[str, Any]:
    """Build Behavior IR from enterprise knowledge asset + optional OpenAPI ops.

    Fully generic: binds only to structured fields present in the asset.

    ``available_surfaces`` declares which observation surfaces this target may be
    observed through, as ``{surface_name: available}``. Build it from
    ``adapter_capability.observation_surfaces_for_adapters`` so the IR and the
    experiment compiler answer the same question the same way. Omitting it keeps the
    historical http-only default.
    """
    model = empty_behavior_ir(project_id=project_id, source_snapshot_hash=source_snapshot_hash)
    data = _dict(asset)
    # ── Data-model enum index ──
    # The asset's interface technical declarations carry the source data
    # models' enums (Payment.status: [INIT, SUCCESS, FAILED, REFUNDED]) that
    # request schemas reference by description rather than declaring inline.
    # Keyed by (model name, field name) — never by field name alone, because
    # Order.status and Payment.status legitimately differ.
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
            if _enum and len(_pp) >= 2:
                _model_enum_index.setdefault(
                    (_text(_pp[0]).lower(), _text(_pp[-1]).lower()),
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

    # ── Chinese Semantic Frame confirmation gate (P0-E) ──
    # The frame ledger is the Chinese-semantics SSOT.  When an asset carries
    # one, GROUNDED frames confirm legacy Chinese-text parse products before
    # they may act as final semantics; unconfirmed legacy candidates are
    # demoted to candidate hints (still computed, never final).  Every
    # demotion/skip/fallback is receipted on the model
    # (legacy_semantic_fallback_receipt) so legacy use is observable and
    # never silent.  Assets without a frame ledger keep the legacy behavior
    # byte-for-byte (their receipt reports frame_ledger_present=False).
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
            # Compat path: without a frame ledger every legacy parse product is
            # the plain legacy semantics, not a fallback — only the
            # NO_FRAME_LEDGER marker is recorded so the receipt stays
            # `只记录 NO_FRAME_LEDGER` for ledger-less assets.
            return
        _fallback_kind_counts[kind] = _fallback_kind_counts.get(kind, 0) + count

    def _norm_text(value: Any) -> str:
        return _text(value).strip().replace("\u3000", " ")

    # Frame identity index: the grounding engine links a frame to its source
    # rule through the origin fact id (rule id `zh_business:<fact tail>`);
    # statement text is the second identity channel.  All lookups mirror
    # chinese_semantic_grounding._find_rule exactly.
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
        """Grounded operation refs (METHOD:path forms) of a frame."""
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
        """Grounded actor role labels of a frame."""
        refs: set[str] = set()
        _tg = _dict(frame.get("technical_grounding"))
        for _r in _list(_tg.get("actor_refs")):
            if _text(_r):
                refs.add(_text(_r))
        for _r in _list(_dict(frame.get("actor")).get("grounded_actor_refs")):
            if _text(_r):
                refs.add(_text(_r))
        return refs

    # Frame types whose scope.ownership_relation (non-raw keys) is structured
    # ownership evidence — the same set the frame→IR adapter emits owns from.
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
        """A frame is grounded when the P0-D grounding engine resolved at
        least one technical ref the frame channel can emit relations from
        (GROUNDED/PARTIAL status; PENDING frames are not grounded)."""
        if not frame:
            return False
        _tg = _dict(frame.get("technical_grounding"))
        return bool(
            _list(_tg.get("operation_refs"))
            or _list(_tg.get("actor_refs"))
            or _list(_tg.get("entity_refs"))
        )

    # Frame-confirmation for legacy field-level ownership candidates
    # (migration point 2).  An (role_key, operation) candidate survives only
    # when some grounded frame declares structured ownership for the same
    # actor role on the same METHOD:path operation; everything else is
    # demoted to a hint and receipted.
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

    # Sources
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

    def merge_unique(existing: list[Any], incoming: list[Any]) -> list[Any]:
        return _merge_unique_sorted(existing, incoming)

    for op in list(api_operations or []) + _list(data.get("operations") or data.get("interfaces")):
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method") or op.get("http_method") or "GET").upper()
        path = _text(op.get("path") or op.get("endpoint") or op.get("url"))
        if not path:
            continue
        service = _text(op.get("service") or op.get("service_name") or op.get("server"))
        op_id = _text(op.get("operation_id") or op.get("operationId") or op.get("id")) or _stable_id("op", method, path)
        side_effect = _infer_operation_effect(op, method)
        field_dictionary = _merge_unique_sorted(
            _list(op.get("field_dictionary")),
        )
        request_schema = _request_schema_for_operation(op)
        request_example = _operation_request_example(op)
        # ── Phase 3: derive affected_fields for write operations ──
        # Union of request_schema.properties keys and request_example keys,
        # giving downstream compilers explicit field-level operation binding.
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
        transport_identity = (service, method, _path_shape(path))
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
                    method,
                    transport_identity[1],
                    field,
                )
                model["conflicts"].append(_fact_node(
                    node_id=conflict_id,
                    typed_fields={
                        "conflict_type": "operation_schema_conflict",
                        "operation_ref": _text(existing.get("id")),
                        "field": field,
                        "method": method,
                        "path_shape": transport_identity[2],
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

    # Request examples remain bound to the exact source operation that
    # declared them. Missing request contracts are handled downstream as
    # explicit compilation gaps; sibling endpoints are not source evidence.
    # shorter objects/entities/tables aliases; merge them instead of choosing
    # only the first non-empty collection.
    entity_rows: list[tuple[str, Any]] = []
    for key in ("objects", "entities", "tables", "business_objects", "data_tables"):
        for row in _list(data.get(key)):
            entity_rows.append((key, row))
    entities_by_canonical_name: dict[str, dict[str, Any]] = {}
    for source_key, ent in entity_rows:
        from_storage = source_key in {"tables", "data_tables"}
        if isinstance(ent, str):
            name = _text(ent)
            canonical_name = _canonical_entity_name(name)
            if not name or not canonical_name:
                continue
            existing = entities_by_canonical_name.get(canonical_name)
            if existing is not None:
                existing["source_entity_names"] = merge_unique(
                    _list(existing.get("source_entity_names")),
                    [name],
                )
                if from_storage and not _text(existing.get("table")):
                    existing["table"] = name
                continue
            typed_fields = {
                "name": name,
                "kind": "resource",
                "entity_kinds": ["resource"],
                "source_entity_names": [name],
            }
            if from_storage:
                typed_fields["table"] = name
            entity = _fact_node(
                node_id=_stable_id("ent", name),
                typed_fields=typed_fields,
                confidence=0.6,
                derivation="schema-derived",
            )
            entities_by_canonical_name[canonical_name] = entity
            model["entities"].append(entity)
            continue
        if not isinstance(ent, dict):
            continue
        name = _text(ent.get("name") or ent.get("object") or ent.get("table") or ent.get("entity"))
        canonical_name = _canonical_entity_name(name)
        if not name or not canonical_name:
            continue
        kind = _text(ent.get("kind") or "resource")
        source_refs = [_source_ref(_text(ent.get("source_id")), locator=name)]
        storage_table = _text(
            ent.get("table")
            or ent.get("storage_table")
            or ent.get("db_table")
            or (name if from_storage else "")
        )
        existing = entities_by_canonical_name.get(canonical_name)
        if existing is not None:
            existing["source_entity_names"] = merge_unique(
                _list(existing.get("source_entity_names")),
                [name],
            )
            existing["entity_kinds"] = merge_unique(
                _list(existing.get("entity_kinds")),
                [kind],
            )
            existing["fields"] = merge_unique(
                _list(existing.get("fields")),
                _list(ent.get("fields") or ent.get("columns")),
            )
            existing["identity_fields"] = merge_unique(
                _list(existing.get("identity_fields")),
                _list(ent.get("identity_fields")),
            )
            existing["source_refs"] = merge_unique(
                _list(existing.get("source_refs")),
                source_refs,
            )
            if storage_table and not _text(existing.get("table")):
                existing["table"] = storage_table
            existing["confidence"] = max(
                float(existing.get("confidence") or 0.0),
                float(ent.get("confidence") or 0.7),
            )
            continue
        typed_fields = {
            "name": name,
            "kind": kind,
            "entity_kinds": [kind],
            "source_entity_names": [name],
            "fields": _list(ent.get("fields") or ent.get("columns")),
            # Columns the source declares as primary or unique keys. Empty
            # when the source never said which field identifies a row.
            "identity_fields": _list(ent.get("identity_fields")),
        }
        if storage_table:
            typed_fields["table"] = storage_table
        entity = _fact_node(
            node_id=_text(ent.get("entity_id") or ent.get("id")) or _stable_id("ent", name),
            typed_fields=typed_fields,
            source_refs=source_refs,
            confidence=float(ent.get("confidence") or 0.7),
            derivation="explicit",
        )
        entities_by_canonical_name[canonical_name] = entity
        model["entities"].append(entity)

    # ── V1.4.0: Upgrade entity fields to Canonical Field structure ──
    # Build a lookup of data_tables field_dictionary and DB column info per entity.
    _table_field_dict: dict[str, list[Any]] = {}
    _table_db_columns: dict[str, dict[str, dict[str, Any]]] = {}
    for _dt in _list(data.get("data_tables")):
        if not isinstance(_dt, dict):
            continue
        _dt_name = _text(_dt.get("name") or _dt.get("table") or _dt.get("entity")).lower()
        if not _dt_name:
            continue
        _fd_list = _list(_dt.get("field_dictionary"))
        if _fd_list:
            _table_field_dict.setdefault(_dt_name, []).extend(_fd_list)
        # Build column info from field_dictionary entries
        for _fd_entry in _fd_list:
            if not isinstance(_fd_entry, dict):
                continue
            _fd_fname = _text(_fd_entry.get("field") or _fd_entry.get("name")).lower()
            if _fd_fname:
                _table_db_columns.setdefault(_dt_name, {})[_fd_fname] = _fd_entry

    # Supplement from top-level field_dictionary (has table→field mappings)
    for _fd_top in _list(data.get("field_dictionary")):
        if not isinstance(_fd_top, dict):
            continue
        _fd_table = _text(_fd_top.get("table") or _fd_top.get("table_id", "").replace("table:", "")).lower()
        if not _fd_table:
            continue
        _table_field_dict.setdefault(_fd_table, []).append(_fd_top)
        _fd_fname = _text(_fd_top.get("field") or _fd_top.get("name")).lower()
        if _fd_fname:
            _table_db_columns.setdefault(_fd_table, {})[_fd_fname] = _fd_top

    # Supplement from operations' request/response schemas only when the
    # operation uniquely resolves to a source entity (entity_refs / structural
    # path / field-overlap). First path-segment inference is forbidden: nested
    # routes like /api/users/addresses must not dump child fields onto users.
    _entities_for_projection = [
        row for row in _list(model.get("entities")) if isinstance(row, dict)
    ]
    _agent_field_supp_rows = []
    for _op in _list(model.get("operations")):
        if not isinstance(_op, dict):
            continue
        _resolved_entity = _resolve_projection_entity_for_operation(
            _op,
            _entities_for_projection,
            # Overlap needs upgraded entity columns; apply after field upgrade.
            allow_field_overlap=False,
        )
        if _resolved_entity is None:
            _agent_field_supp_rows.append({
                "path": _text(_op.get("path") or _op.get("raw_path")),
                "method": _text(_op.get("method")).upper(),
                "resolved": None,
                "skipped": True,
            })
            continue
        _ent_name = _text(
            _resolved_entity.get("name") or _resolved_entity.get("id")
        ).lower()
        if not _ent_name:
            continue
        _op_schema_fields: list[str] = []
        # Per-field schema constraints (OpenAPI minimum/maximum) from the same
        # property definitions that name the fields. A bound is a source-declared
        # business fact; storage precision is never a substitute.
        _field_constraints: dict[str, dict[str, Any]] = {}

        def _absorb_schema_props(props: dict[str, Any]) -> None:
            for _pf_name, _pf_spec in props.items():
                if not isinstance(_pf_spec, dict):
                    continue
                _bound: dict[str, Any] = {}
                for _bk in ("minimum", "maximum"):
                    if _pf_spec.get(_bk) is not None and not isinstance(_pf_spec.get(_bk), bool):
                        _bound[_bk] = _pf_spec[_bk]
                if _bound:
                    _field_constraints.setdefault(_text(_pf_name).lower(), {}).update(_bound)

        # Request schema properties
        _req_schema = _dict(_op.get("request_schema") or _op.get("requestBody"))
        _content = _dict(_req_schema.get("content"))
        _json_media = _dict(_content.get("application/json"))
        _schema_props = _dict(_dict(_json_media.get("schema")).get("properties"))
        _op_schema_fields.extend(_schema_props.keys())
        _absorb_schema_props(_schema_props)
        # Also direct properties (non-nested)
        _direct_props = _dict(_req_schema.get("properties"))
        _op_schema_fields.extend(_direct_props.keys())
        _absorb_schema_props(_direct_props)
        # Request example keys
        _example = _dict(_json_media.get("example") or _op.get("request_example"))
        _op_schema_fields.extend(k for k in _example.keys() if k)
        # Response schema properties
        _resp_schema = _dict(_op.get("response_schema") or _op.get("responseSchema"))
        _resp_props = _dict(_resp_schema.get("properties"))
        _op_schema_fields.extend(_resp_props.keys())
        _absorb_schema_props(_resp_props)
        # Nested data/records wrapper
        for _wrapper in ("data", "records", "items", "result"):
            _w = _dict(_resp_props.get(_wrapper))
            _w_items = _dict(_w.get("items"))
            _w_props = _dict(_w_items.get("properties") or _w.get("properties"))
            _op_schema_fields.extend(_w_props.keys())
            _absorb_schema_props(_w_props)
        # field_dictionary on operation
        for _ofd in _list(_op.get("field_dictionary")):
            if isinstance(_ofd, dict):
                _op_schema_fields.append(_text(_ofd.get("name") or _ofd.get("field")))
            elif isinstance(_ofd, str):
                _op_schema_fields.append(_ofd)
        # Dedupe and add only to the uniquely resolved entity
        _unique_fields = list(dict.fromkeys(f for f in _op_schema_fields if f))
        existing_cols = _table_db_columns.setdefault(_ent_name, {})
        _added = 0
        for _sf in _unique_fields:
            if _sf.lower() not in existing_cols:
                _entry: dict[str, Any] = {"field": _sf, "source": "operation_schema"}
                _constraints = _field_constraints.get(_sf.lower())
                if _constraints:
                    _entry.update(_constraints)
                existing_cols[_sf.lower()] = _entry
                _added += 1
        _agent_field_supp_rows.append({
            "path": _text(_op.get("path") or _op.get("raw_path")),
            "method": _text(_op.get("method")).upper(),
            "resolved": _ent_name,
            "added_fields": _unique_fields[:12],
            "added_count": _added,
            "skipped": False,
        })


    for entity in _list(model.get("entities")):
        if not isinstance(entity, dict):
            continue
        ent_name = _text(entity.get("name"))
        if not ent_name:
            continue
        raw_fields = _list(entity.get("fields"))
        identity_fields = _list(entity.get("identity_fields"))
        ent_source_refs = _list(entity.get("source_refs"))
        # Look up supplementary field info from data_tables
        ent_lower = ent_name.lower()
        fd_supplement = _table_field_dict.get(ent_lower, [])
        db_cols = _table_db_columns.get(ent_lower, {})
        # Also try singular/plural forms
        if not fd_supplement:
            singular = ent_lower.rstrip("s") if ent_lower.endswith("s") else ent_lower + "s"
            fd_supplement = _table_field_dict.get(singular, [])
            db_cols = db_cols or _table_db_columns.get(singular, {})
        if not db_cols:
            singular = ent_lower.rstrip("s") if ent_lower.endswith("s") else ent_lower + "s"
            db_cols = _table_db_columns.get(singular, {})
        # Only upgrade if there are fields to process
        if not raw_fields and not fd_supplement and not db_cols:
            continue
        canonical = _build_canonical_fields(
            raw_fields,
            ent_name,
            identity_fields=identity_fields,
            source_refs=ent_source_refs,
            field_dictionary=fd_supplement,
            db_columns=db_cols,
            model_enum_index=_model_enum_index,
        )
        if canonical:
            entity["fields"] = canonical

    # Backfill operation.entity_refs from unique request↔column overlap when the
    # path carries no entity vocabulary. Enables produces/consumes derivation
    # without hardcoding route→table maps. Already-declared refs are preserved.
    _entities_after_fields = [
        row for row in _list(model.get("entities")) if isinstance(row, dict)
    ]
    _agent_entity_ref_backfill = []
    for _op in _list(model.get("operations")):
        if not isinstance(_op, dict):
            continue
        if any(_text(ref) for ref in _list(_op.get("entity_refs"))):
            continue
        _overlap_entity = _entity_from_request_field_overlap(
            _op, _entities_after_fields
        )
        if _overlap_entity is None:
            continue
        _ref = _text(_overlap_entity.get("name") or _overlap_entity.get("id"))
        if not _ref:
            continue
        _op["entity_refs"] = [_ref]
        _agent_entity_ref_backfill.append({
            "path": _text(_op.get("path") or _op.get("raw_path")),
            "method": _text(_op.get("method")).upper(),
            "entity_ref": _ref,
            "overlap_fields": sorted(
                _operation_request_field_names(_op)
                & _entity_field_column_names(_overlap_entity)
            )[:12],
        })

    # ── V1.4.0: Field Binding — connect canonical fields to API/DB layers ──
    # Build operation index: entity_name -> list of operations
    _ops_by_entity: dict[str, list[dict[str, Any]]] = {}
    for _op in _list(model.get("operations")):
        if not isinstance(_op, dict):
            continue
        _resolved_entity = _resolve_projection_entity_for_operation(
            _op, _entities_after_fields, allow_field_overlap=True
        )
        if _resolved_entity is None:
            continue
        _seg_lower = _text(
            _resolved_entity.get("name") or _resolved_entity.get("id")
        ).lower()
        if _seg_lower:
            _ops_by_entity.setdefault(_seg_lower, []).append(_op)

    def _extract_op_field_names(op: dict[str, Any], direction: str) -> set[str]:
        """Extract field names from an operation's request or response schema."""
        names: set[str] = set()
        if direction == "request":
            schema = _dict(op.get("request_schema") or op.get("requestBody"))
            content = _dict(schema.get("content"))
            json_media = _dict(content.get("application/json"))
            props = _dict(_dict(json_media.get("schema")).get("properties"))
            names.update(k.lower() for k in props.keys())
            direct = _dict(schema.get("properties"))
            names.update(k.lower() for k in direct.keys())
            example = _dict(json_media.get("example") or op.get("request_example"))
            names.update(k.lower() for k in example.keys() if k)
            for fd in _list(op.get("field_dictionary")):
                if isinstance(fd, dict):
                    n = _text(fd.get("name") or fd.get("field")).lower()
                    if n:
                        names.add(n)
                elif isinstance(fd, str):
                    names.add(fd.lower())
        else:  # response
            schema = _dict(op.get("response_schema") or op.get("responseSchema"))
            props = _dict(schema.get("properties"))
            names.update(k.lower() for k in props.keys())
            for wrapper in ("data", "records", "items", "result"):
                w = _dict(props.get(wrapper))
                w_items = _dict(w.get("items"))
                w_props = _dict(w_items.get("properties") or w.get("properties"))
                names.update(k.lower() for k in w_props.keys())
        return names

    for entity in _list(model.get("entities")):
        if not isinstance(entity, dict):
            continue
        fields = _list(entity.get("fields"))
        if not fields or not isinstance(fields[0], dict):
            continue
        ent_name = _text(entity.get("name")).lower()
        ent_ops = _ops_by_entity.get(ent_name, [])
        if not ent_ops:
            singular = ent_name.rstrip("s") if ent_name.endswith("s") else ent_name + "s"
            ent_ops = _ops_by_entity.get(singular, [])
        if not ent_ops:
            continue
        # Pre-extract field sets per operation
        op_req_fields = [(op, _extract_op_field_names(op, "request")) for op in ent_ops]
        op_resp_fields = [(op, _extract_op_field_names(op, "response")) for op in ent_ops]

        for field in fields:
            if not isinstance(field, dict):
                continue
            fname = _text(field.get("name")).lower()
            if not fname:
                continue
            api_req_bindings: list[dict[str, str]] = []
            api_resp_bindings: list[dict[str, str]] = []
            # API request bindings
            for op, req_names in op_req_fields:
                if fname in req_names:
                    api_req_bindings.append({
                        "operation_id": _text(op.get("id")),
                        "json_path": f"$.{field.get('name')}",
                    })
            # API response bindings
            for op, resp_names in op_resp_fields:
                if fname in resp_names:
                    api_resp_bindings.append({
                        "operation_id": _text(op.get("id")),
                        "json_path": f"$.{field.get('name')}",
                    })
            # Database binding (from _table_db_columns built earlier)
            db_bindings: list[dict[str, str]] = []
            db_col_info = _table_db_columns.get(ent_name, {}).get(fname)
            if not db_col_info:
                singular = ent_name.rstrip("s") if ent_name.endswith("s") else ent_name + "s"
                db_col_info = _table_db_columns.get(singular, {}).get(fname)
            if db_col_info:
                # A DB binding may only name a table the source actually
                # declared. Operation-schema supplements carry no table key —
                # falling back to the entity name GUESSES a table for what may
                # be a request-DTO field (addressId/items/…), polluting
                # cleanup's table selection with a name that does not exist in
                # the database. No declared table → no DB binding.
                declared_table = _text(db_col_info.get("table"))
                if declared_table:
                    db_bindings.append({
                        "table": declared_table,
                        "column": _text(db_col_info.get("field") or field.get("name")),
                    })
            # Determine binding status
            total_bindings = len(api_req_bindings) + len(api_resp_bindings) + len(db_bindings)
            if total_bindings >= 2:
                status = "RESOLVED"
            elif total_bindings == 1:
                status = "INCOMPLETE"
            else:
                status = "NOT_DECLARED"
            # Write bindings to field
            if api_req_bindings:
                field["api_request_bindings"] = api_req_bindings
            if api_resp_bindings:
                field["api_response_bindings"] = api_resp_bindings
            if db_bindings:
                field["database_bindings"] = db_bindings
            field["binding_status"] = status

    # ── V1.4.0: Scope Field Formalization ──
    # Derive tenant_field and owner_field for each entity from:
    #   1. Canonical field scope_role (TENANT_ID / OWNER_ID semantic types)
    #   2. permission_matrix scope values ('own', 'other_owner', 'all')
    #   3. entity_relations 'belongs_to' / 'owns' patterns
    _perm_rows = _list(data.get("permission_matrix") or data.get("permissions"))
    _entity_relations_raw = _list(data.get("entity_relations"))
    # Build permission scope index: resource -> set of scopes
    _resource_scopes: dict[str, set[str]] = {}
    for _perm in _perm_rows:
        if not isinstance(_perm, dict):
            continue
        _res = _text(_perm.get("resource")).lower()
        _scope = _text(_perm.get("scope")).lower()
        if _res and _scope and _scope != "unspecified":
            _resource_scopes.setdefault(_res, set()).add(_scope)
    # Build ownership index: entity -> owner_entity (from belongs_to / owns)
    _ownership: dict[str, str] = {}  # child_entity -> parent_entity
    for _rel in _entity_relations_raw:
        if not isinstance(_rel, dict):
            continue
        _rel_type = _text(_rel.get("relation_type") or _rel.get("relation")).lower()
        _from_ent = _text(_rel.get("from_entity")).lower()
        _to_ent = _text(_rel.get("to_entity")).lower()
        if _rel_type in ("belongs_to", "owns", "owned_by") and _from_ent and _to_ent:
            _ownership[_from_ent] = _to_ent

    for entity in _list(model.get("entities")):
        if not isinstance(entity, dict):
            continue
        fields = _list(entity.get("fields"))
        if not fields or not isinstance(fields[0], dict):
            continue
        ent_name = _text(entity.get("name")).lower()
        # 1. From canonical field scope_role
        _tenant_field = ""
        _owner_field = ""
        for field in fields:
            if not isinstance(field, dict):
                continue
            _sr = _text(field.get("scope_role"))
            if _sr == "tenant_id" and not _tenant_field:
                _tenant_field = _text(field.get("name"))
            elif _sr == "owner_id" and not _owner_field:
                _owner_field = _text(field.get("name"))
        # 2. From permission_matrix scope
        _perm_scope = ""
        _scopes = _resource_scopes.get(ent_name, set())
        if not _scopes:
            singular = ent_name.rstrip("s") if ent_name.endswith("s") else ent_name + "s"
            _scopes = _resource_scopes.get(singular, set())
        if "own" in _scopes or "other_owner" in _scopes:
            _perm_scope = "owner_scoped"
        elif "all" in _scopes:
            _perm_scope = "global"
        # 3. From entity_relations (belongs_to → owner entity)
        _owner_entity = _ownership.get(ent_name, "")
        # Write scope_fields typed_field
        _scope_node: dict[str, Any] = {
            "tenant_field": _tenant_field,
            "owner_field": _owner_field,
            "permission_scope": _perm_scope,
        }
        if _owner_entity:
            _scope_node["owner_entity"] = _owner_entity
        entity["scope_fields"] = _scope_node

    # Actors from permission matrix / roles / runtime actors (secret_ref only)
    actor_names: set[str] = set()
    actor_ids: set[str] = set()
    permission_rows = _list(data.get("permission_matrix") or data.get("permissions"))
    permission_by_role: dict[str, dict[str, Any]] = {}
    for perm in permission_rows:
        if not isinstance(perm, dict):
            continue
        role = _text(perm.get("role") or perm.get("actor") or perm.get("principal"))
        if not role:
            continue
        role_key = role.lower()
        aggregate = permission_by_role.setdefault(role_key, {
            "role": role,
            "resources": [],
            "actions": [],
            "denied_actions": [],
            "scopes": [],
            "source_ids": [],
        })
        declared_polarity = _declared_permission_polarity(perm)
        resource = _text(perm.get("resource"))
        if declared_polarity != "DENY" and resource and resource not in aggregate["resources"]:
            aggregate["resources"].append(resource)
        declared_actions = _list(perm.get("actions"))
        for action in declared_actions if declared_polarity != "DENY" else []:
            value = _text(action)
            if value and value not in aggregate["actions"]:
                aggregate["actions"].append(value)
        denied_actions = (
            declared_actions if declared_polarity == "DENY" else []
        ) + _list(
            perm.get("denied_actions")
            or perm.get("forbidden_actions")
            or perm.get("prohibited_actions")
        )
        for action in denied_actions:
            value = _text(action)
            if value and value not in aggregate["denied_actions"]:
                aggregate["denied_actions"].append(value)
        scope = _text(perm.get("scope"))
        if scope and scope not in aggregate["scopes"]:
            aggregate["scopes"].append(scope)
        source_id = _text(perm.get("source_id"))
        if source_id and source_id not in aggregate["source_ids"]:
            aggregate["source_ids"].append(source_id)
    for role_key, aggregate in permission_by_role.items():
        role = _text(aggregate.get("role") or role_key)
        actor_names.add(role_key)
        actor_id = _stable_id("actor", role)
        actor_ids.add(actor_id)
        model["actors"].append(_fact_node(
            node_id=actor_id,
            typed_fields={
                "role": role,
                "role_key": role_key,
                "tenant_scope": ",".join(aggregate["scopes"]) or "unspecified",
                "credential_secret_ref": f"secret_ref:actor:{role}",
                "account_status": "active",
                "allowed_resources": aggregate["resources"],
                "allowed_actions": aggregate["actions"],
                "denied_actions": aggregate["denied_actions"],
            },
            source_refs=[
                _source_ref(source_id or "permission_matrix", locator=role, kind="permission_matrix")
                for source_id in (aggregate["source_ids"] or [""])
            ],
            confidence=0.8,
            derivation="explicit",
        ))
    for declared_role in _list(data.get("roles")):
        if not isinstance(declared_role, dict):
            continue
        role = _text(declared_role.get("role") or declared_role.get("name") or declared_role.get("id"))
        role_key = role.lower()
        if not role or role_key in actor_names:
            continue
        actor_names.add(role_key)
        actor_id = _stable_id("actor", role)
        actor_ids.add(actor_id)
        model["actors"].append(_fact_node(
            node_id=actor_id,
            typed_fields={
                "role": role,
                "role_key": role_key,
                "tenant_scope": _text(declared_role.get("scope") or "unspecified"),
                "credential_secret_ref": f"secret_ref:actor:{role}",
                "account_status": "active",
                "allowed_resources": [],
                "allowed_actions": [],
                "denied_actions": [],
            },
            source_refs=[_source_ref(_text(declared_role.get("source_id")) or "roles", locator=role, kind="role_catalog")],
            confidence=float(declared_role.get("confidence") or 0.75),
            derivation="explicit",
        ))
    for actor in _list(runtime_actors):
        if not isinstance(actor, dict):
            continue
        role = _text(actor.get("role") or actor.get("name") or actor.get("id"))
        if not role:
            continue
        role_key = role.lower()
        aggregate = permission_by_role.get(role_key, {})
        if role_key not in actor_names:
            actor_names.add(role_key)
            actor_id = _stable_id("actor", role)
            actor_ids.add(actor_id)
            source_refs = [
                _source_ref(source_id or "permission_matrix", locator=role, kind="permission_matrix")
                for source_id in (aggregate.get("source_ids") or [])
            ]
            source_refs.append(_source_ref("runtime_actors", locator=role, kind="runtime_actor"))
            model["actors"].append(_fact_node(
                node_id=actor_id,
                typed_fields={
                    "role": role,
                    "role_key": role_key,
                    "tenant_scope": _text(actor.get("tenant") or actor.get("scope") or "unspecified"),
                    "credential_secret_ref": _text(actor.get("secret_ref") or f"secret_ref:actor:{role}"),
                    "account_status": _text(actor.get("status") or "active"),
                    "allowed_resources": list(aggregate.get("resources") or []),
                    "allowed_actions": list(aggregate.get("actions") or []),
                    "denied_actions": list(aggregate.get("denied_actions") or []),
                    "runtime_bound": True,
                },
                source_refs=source_refs,
                confidence=0.9,
                derivation="runtime-observed",
            ))
        account_ref = _text(actor.get("account_ref") or actor.get("email") or actor.get("username") or actor.get("id"))
        if not account_ref:
            continue
        account_id = _stable_id("actor_account", account_ref)
        if account_id in actor_ids:
            continue
        actor_ids.add(account_id)
        source_refs = [
            _source_ref(source_id or "permission_matrix", locator=role, kind="permission_matrix")
            for source_id in (aggregate.get("source_ids") or [])
        ]
        source_refs.append(_source_ref("runtime_actors", locator=f"{role}:{account_ref}", kind="runtime_actor"))
        model["actors"].append(_fact_node(
            node_id=account_id,
            typed_fields={
                "role": role,
                "role_key": role_key,
                "account_ref": account_ref,
                "tenant_scope": _text(actor.get("tenant") or actor.get("scope") or "unspecified"),
                "credential_secret_ref": _text(actor.get("secret_ref") or f"secret_ref:test_accounts:{account_ref}"),
                "account_status": _text(actor.get("status") or "active"),
                "allowed_resources": list(aggregate.get("resources") or []),
                "allowed_actions": list(aggregate.get("actions") or []),
                "denied_actions": list(aggregate.get("denied_actions") or []),
                "runtime_bound": True,
            },
            source_refs=source_refs,
            confidence=0.9,
            derivation="runtime-observed",
        ))

    # States from state machines. Multiple source machines may describe the same
    # entity/state after asset+overlay merge; keep one node id and merge refs.
    states_by_id: dict[str, dict[str, Any]] = {}
    for sm in _list(data.get("state_machines") or data.get("states")):
        if not isinstance(sm, dict):
            continue
        entity = _text(sm.get("entity") or sm.get("object") or "entity")
        declared_states = _list(sm.get("states"))
        state_names = list(
            declared_states
            or ([sm.get("name")] if sm.get("name") else [])
        )
        for transition_key in ("transitions", "forbidden_transitions"):
            for transition in _list(sm.get(transition_key)):
                if not isinstance(transition, dict):
                    continue
                state_names.extend([
                    transition.get("from") or transition.get("from_state"),
                    transition.get("to") or transition.get("to_state"),
                ])
        # ── Phase 5: collect per-state preconditions and invariant_refs ──
        # Transitions carry preconditions/conditions for the source state;
        # state machine dict may carry per-state invariants/conditions.
        _sm_states_meta = sm.get("states") if isinstance(sm.get("states"), dict) else {}
        _sm_invariants = _list(sm.get("invariants"))
        _state_preconditions: dict[str, list[str]] = {}
        _state_invariant_refs: dict[str, list[str]] = {}
        for transition in _list(sm.get("transitions")):
            if not isinstance(transition, dict):
                continue
            _from_name = _text(transition.get("from") or transition.get("from_state")).lower()
            if not _from_name:
                continue
            for _pc in _list(transition.get("preconditions") or transition.get("conditions") or transition.get("guard_conditions")):
                _pc_text = _text(_pc)
                if _pc_text:
                    _state_preconditions.setdefault(_from_name, []).append(_pc_text)
        for _s_name_key, _s_meta in _sm_states_meta.items():
            if not isinstance(_s_meta, dict):
                continue
            _s_key = _text(_s_name_key).lower()
            for _cond in _list(_s_meta.get("conditions")):
                _cond_text = _text(_cond)
                if _cond_text:
                    _state_preconditions.setdefault(_s_key, []).append(_cond_text)
            for _inv in _list(_s_meta.get("invariants")):
                _inv_text = _text(_inv)
                if _inv_text:
                    _state_invariant_refs.setdefault(_s_key, []).append(_inv_text)
        # Machine-level invariants apply to all states
        if _sm_invariants:
            for _s_name in state_names:
                _s_key = _text(_s_name).lower()
                if _s_key:
                    for _inv in _sm_invariants:
                        _inv_text = _text(_inv) if not isinstance(_inv, dict) else _text(_inv.get("id") or _inv.get("expression"))
                        if _inv_text:
                            _state_invariant_refs.setdefault(_s_key, []).append(_inv_text)
        seen_state_names: set[str] = set()
        for state_name in state_names:
            name = _text(state_name)
            state_key = name.lower()
            if not name or state_key in seen_state_names:
                continue
            seen_state_names.add(state_key)
            node_id = _stable_id("state", entity, name)
            source_ref = _source_ref(
                _text(sm.get("source_id")) or "state_machine",
                locator=f"{entity}:{name}",
            )
            existing = states_by_id.get(node_id)
            if existing is not None:
                existing["source_refs"] = _merge_unique_sorted(
                    _list(existing.get("source_refs")),
                    [source_ref],
                )
                # Phase 5: merge preconditions/invariant_refs on dedup
                if _state_preconditions.get(state_key):
                    existing["preconditions"] = _merge_unique_sorted(
                        _list(existing.get("preconditions")),
                        _state_preconditions[state_key],
                    )
                if _state_invariant_refs.get(state_key):
                    existing["invariant_refs"] = _merge_unique_sorted(
                        _list(existing.get("invariant_refs")),
                        _state_invariant_refs[state_key],
                    )
                continue
            state_node = _fact_node(
                node_id=node_id,
                typed_fields={
                    "entity_ref": entity,
                    "name": name,
                    "preconditions": list(dict.fromkeys(_state_preconditions.get(state_key, []))),
                    "invariant_refs": list(dict.fromkeys(_state_invariant_refs.get(state_key, []))),
                },
                source_refs=[source_ref],
                confidence=0.75,
                derivation="explicit",
            )
            states_by_id[node_id] = state_node
            model["states"].append(state_node)

        # ── State machine → entity state-field enumeration ──
        # The source-declared state set is the legal value set for this
        # machine's entity state field. Writing it as enum_values lets the
        # persistence surface check stored rows against the declared machine:
        # a stored value outside the machine is a defect (e.g. an order
        # persisted in a state the machine never declares). This is
        # source-declared, never inferred from data.
        _legal_states = list(dict.fromkeys(
            _text(n) for n in state_names if _text(n)
        ))
        if _legal_states:
            for _ent in _list(model.get("entities")):
                if _text(_ent.get("name")).lower() != _text(entity).lower():
                    continue
                for _f in _list(_ent.get("fields")):
                    if not isinstance(_f, dict):
                        continue
                    _fname = _text(_f.get("name")).lower()
                    _is_state_field = (
                        _text(_f.get("semantic_type")) == "STATE"
                        or _fname in {"status", "state", "stage", "phase", "lifecycle_state"}
                        or _fname.endswith("_status")
                        or _fname.endswith("_state")
                    )
                    if _is_state_field:
                        _f["enum_values"] = _legal_states
                        break

    # Forbidden transitions are source constraints, never allowed transition
    # relations. Preserve them as typed invariants and bind an operation only
    # when the transition contains an exact source operation hint.
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    seen_forbidden_invariant_ids: set[str] = set()
    for sm in _list(data.get("state_machines") or data.get("states")):
        if not isinstance(sm, dict):
            continue
        entity = _text(sm.get("entity") or sm.get("object") or "entity").lower()
        machine_source_id = _text(sm.get("source_id")) or "state_machine"
        machine_ref = _text(sm.get("state_machine_id") or sm.get("id"))
        for transition in _list(sm.get("forbidden_transitions")):
            if not isinstance(transition, dict):
                continue
            from_name = _text(transition.get("from") or transition.get("from_state"))
            to_name = _text(transition.get("to") or transition.get("to_state"))
            if not from_name or not to_name:
                continue
            source_id = _text(transition.get("source_id")) or machine_source_id
            source_refs = [_source_ref(
                source_id,
                locator=f"{entity}:{from_name}-/->{to_name}",
                kind="forbidden_state_transition",
            )]
            operation_binding = any(
                _text(transition.get(key))
                for key in ("operation_ref", "operation_id", "operation", "method", "path")
            )
            operation = (
                _resolve_operation(operations, transition)
                if operation_binding
                else None
            )
            operation_refs = [_text(operation.get("id"))] if operation else []
            invariant_id = _stable_id(
                "inv",
                "forbidden_state_transition",
                machine_ref or source_id,
                entity,
                from_name,
                to_name,
            )
            if invariant_id in seen_forbidden_invariant_ids:
                continue
            seen_forbidden_invariant_ids.add(invariant_id)
            model["invariants"].append(_fact_node(
                node_id=invariant_id,
                typed_fields={
                    "description": f"{entity} must not transition from {from_name} to {to_name}",
                    "expression": {
                        "kind": "forbidden_state_transition",
                        "operator": "must_not_transition",
                        "operands": [{
                            "entity_ref": entity,
                            "from_state": from_name,
                            "to_state": to_name,
                        }],
                        "raw": f"{from_name} -/-> {to_name}",
                    },
                    "operation_refs": operation_refs,
                    "source_rule_refs": [machine_ref] if machine_ref else [],
                },
                source_refs=source_refs,
                confidence=float(transition.get("confidence") or sm.get("confidence") or 0.8),
                derivation="explicit",
            ))
            if not operation:
                model["coverage_gaps"].append(_fact_node(
                    node_id=_stable_id(
                        "gap",
                        "forbidden_state_transition_operation_unresolved",
                        invariant_id,
                    ),
                    typed_fields={
                        "gap_type": "forbidden_state_transition_operation_unresolved",
                        "invariant_ref": invariant_id,
                        "entity_ref": entity,
                        "from_state": from_name,
                        "to_state": to_name,
                        "operation_hint": _text(
                            transition.get("operation_ref")
                            or transition.get("operation_id")
                            or transition.get("operation")
                        ),
                        "description": "Forbidden state transition has no unique source-bound operation",
                    },
                    source_refs=source_refs,
                    confidence=1.0,
                    derivation="explicit",
                    status="unsupported",
                ))

    # ── Field-level extraction for conservation/causal rules ──
    # Known entity-field mappings derived from DB schema and API documentation.
    # These are industry-neutral patterns: the parser extracts field tokens
    # from rule statements and maps them to entities via schema evidence.
    _ENTITY_FIELD_REGISTRY: dict[str, list[str]] = {}
    _ENTITY_FIELD_NODES: dict[str, dict[str, dict[str, Any]]] = {}
    for _ent in _list(model.get("entities")):
        _ent_id = _text(_ent.get("id") or _ent.get("name"))
        _ent_name = _text(_ent.get("name") or _ent_id)
        if not _ent_id:
            continue
        _ent_fields: list[str] = []
        _nodes: dict[str, dict[str, Any]] = {}
        for _f in _list(_ent.get("fields") or _ent.get("properties") or []):
            if isinstance(_f, dict):
                _fname = _text(_f.get("name") or _f.get("id"))
                if _fname:
                    _ent_fields.append(_fname)
                    _nodes[_fname.lower()] = _f
            elif isinstance(_f, str):
                _ent_fields.append(_f)
        # Also extract from operation schemas that reference this entity
        _ENTITY_FIELD_REGISTRY[_ent_id.lower()] = [f for f in _ent_fields if f]
        _ENTITY_FIELD_NODES[_ent_id.lower()] = _nodes
        if _ent_name.lower() != _ent_id.lower():
            _ENTITY_FIELD_REGISTRY[_ent_name.lower()] = list(
                _ENTITY_FIELD_REGISTRY[_ent_id.lower()]
            )
            _ENTITY_FIELD_NODES[_ent_name.lower()] = dict(_nodes)
    # Supplement from operation request/response schemas
    for _op in _list(model.get("operations")):
        _op_ents = [_text(e).lower() for e in _list(_op.get("entity_refs")) if _text(e)]
        _schema_fields: list[str] = []
        _schema_field_nodes: dict[str, dict[str, Any]] = {}
        _req_schema = _dict(_op.get("request_schema") or _op.get("requestBody"))
        _content = _dict(_req_schema.get("content"))
        _json_media = _dict(_content.get("application/json"))
        _schema_props = _dict(_dict(_json_media.get("schema")).get("properties"))
        for _sf_name, _sf_schema in _schema_props.items():
            _schema_fields.append(_sf_name)
            _node = dict(_sf_schema) if isinstance(_sf_schema, dict) else {}
            _node.setdefault("name", _sf_name)
            _schema_field_nodes[_sf_name.lower()] = _node
        _example = _dict(_json_media.get("example"))
        _schema_fields.extend(k for k in _example.keys() if isinstance(_example.get(k), (int, float)))
        _field_dict = _list(_op.get("field_dictionary"))
        for _fd in _field_dict:
            if isinstance(_fd, dict):
                _fd_name = _text(_fd.get("name") or _fd.get("field"))
                _schema_fields.append(_fd_name)
                if _fd_name:
                    _schema_field_nodes.setdefault(_fd_name.lower(), dict(_fd))
            elif isinstance(_fd, str):
                _schema_fields.append(_fd)
        for _ent_name in _op_ents:
            existing = _ENTITY_FIELD_REGISTRY.setdefault(_ent_name, [])
            for sf in _schema_fields:
                if sf and sf not in existing:
                    existing.append(sf)
            # Operation schema fields carry source descriptions (e.g. openapi
            # "description": "金额") that rule statements may reference through
            # CJK terms ("支付金额"). Keep the nodes so semantic term matching
            # can bind rule language to request fields without hardcoding any
            # field name.
            ent_nodes = _ENTITY_FIELD_NODES.setdefault(_ent_name, {})
            for _sf_name, _sf_node in _schema_field_nodes.items():
                _existing_node = ent_nodes.get(_sf_name)
                if _existing_node is None:
                    ent_nodes[_sf_name] = dict(_sf_node)
                elif (
                    not _text(_existing_node.get("description"))
                    and _text(_sf_node.get("description"))
                ):
                    # Operation schema prose is the richer description source;
                    # merge it into the entity node so CJK term matching can
                    # bind "支付金额" to the request field amount (desc 金额).
                    ent_nodes[_sf_name] = {**_existing_node, **_sf_node}

    def _extract_fields_from_statement(stmt: str) -> list[dict[str, str]]:
        """Extract field references from a rule statement using schema evidence."""
        # Match backtick-quoted fields: `field_name`
        backtick_fields = re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", stmt)
        # Match snake_case identifiers (>= 4 chars, not common words)
        _STOP_WORDS = {"true", "false", "null", "none", "must", "should", "cannot", "must_hold"}
        snake_fields = re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", stmt.lower())
        snake_fields = [f for f in snake_fields if len(f) >= 4 and f not in _STOP_WORDS]
        # Combine and dedupe
        all_fields = list(dict.fromkeys(backtick_fields + snake_fields))
        # Map fields to entities using registry
        result: list[dict[str, str]] = []
        for field in all_fields:
            field_lower = field.lower()
            matched_entity = ""
            matched_node: dict[str, Any] = {}
            for ent_name, ent_fields in _ENTITY_FIELD_REGISTRY.items():
                if any(field_lower == ef.lower() for ef in ent_fields):
                    matched_entity = ent_name
                    matched_node = _dict(_ENTITY_FIELD_NODES.get(ent_name, {}).get(field_lower))
                    break
            # Heuristic entity mapping from field name prefix
            if not matched_entity:
                for ent_name in _ENTITY_FIELD_REGISTRY:
                    if field_lower.startswith(ent_name.rstrip("s") + "_") or ent_name.rstrip("s") in field_lower:
                        matched_entity = ent_name
                        matched_node = _dict(
                            _ENTITY_FIELD_NODES.get(ent_name, {}).get(field_lower)
                        )
                        break
            row: dict[str, Any] = {"entity_ref": matched_entity, "field": field}
            _cf = _text(matched_node.get("field_id"))
            _sem = _text(matched_node.get("semantic_type"))
            if _cf:
                row["field_id"] = _cf
            if _sem:
                row["semantic_type"] = _sem
            result.append(row)
        # ── CJK term → field semantic binding ──
        # Rule statements written in Chinese reference fields through their
        # source descriptions, not the ASCII identifier ("支付金额必须等于订单
        # 应付金额" mentions 应付金额, which is the description of
        # ``payable_amount``; 支付金额 is the description of the request field
        # ``amount``). Bind those terms to fields via description evidence so
        # conservation equations keep both sides. Matching is conservative:
        # a description must appear verbatim in the statement, or the statement
        # must end with it (term = qualifier + description), so a bare 金额
        # tail cannot grab every X金额 field.
        # Dedup is per (entity_ref, field): the same field name in different
        # entities is a DIFFERENT field. Name-only dedup silently collapsed
        # e.g. refund.amount/payment.amount under the first-registered
        # coupon.amount, so a refund rule never bound the refund entity.
        _known_field_keys = {
            (_text(row.get("entity_ref") or "").lower(), _text(row.get("field") or "").lower())
            for row in result
            if _text(row.get("field"))
        }
        _known_ids = {row.get("field_id") for row in result if row.get("field_id")}
        for _ent_name, _ent_nodes in _ENTITY_FIELD_NODES.items():
            for _fname_lower, _fnode in _ent_nodes.items():
                if not isinstance(_fnode, dict):
                    continue
                _fname = _text(_fnode.get("name") or _fname_lower)
                if not _fname or (_text(_ent_name).lower(), _fname_lower) in _known_field_keys:
                    continue
                _fdesc = _text(_fnode.get("description"))
                if not _fdesc or len(_fdesc) < 2:
                    continue
                _fdesc = _fdesc.strip()
                if _fdesc in stmt or stmt.endswith(_fdesc):
                    _cf = _text(_fnode.get("field_id"))
                    if _cf and _cf in _known_ids:
                        continue
                    _row: dict[str, Any] = {"entity_ref": _ent_name, "field": _fname}
                    if _cf:
                        _row["field_id"] = _cf
                    _sem = _text(_fnode.get("semantic_type"))
                    if _sem:
                        _row["semantic_type"] = _sem
                    result.append(_row)
                    _known_field_keys.add((_text(_ent_name).lower(), _fname_lower))
                    if _cf:
                        _known_ids.add(_cf)
        # ── CJK validation term → field-name token binding ──
        # Many rule statements name the fields they govern through generic
        # business vocabulary ("必须校验状态、生效时间、失效时间、最低金额、
        # 分类范围、使用次数") while the entity field names carry no Chinese
        # description to match against. Bind such terms to fields via their
        # structural name tokens. The vocabulary below is industry-neutral —
        # state/timestamp/amount/quantity/scope/limit are universal business
        # categories present in every system's naming, matching the product's
        # own English semantic token sets (_CF_STATE_TOKENS etc.). No
        # industry-specific term (discount/coupon/inventory/balance/order)
        # appears here: an industry term without a generic mapping simply
        # leaves the field unbound (fail-safe), never wrongly bound. Tokens
        # match field names on snake/token boundaries so "count" inside
        # "discount" cannot satisfy a 使用次数 term.
        _CJK_TERM_FIELD_TOKENS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
            (("状态",), ("status", "state")),
            (("生效时间", "开始时间", "生效日"), ("starts", "start", "begin", "effective", "commence")),
            (("失效时间", "截止时间", "失效日"), ("expires", "expire", "expiry", "valid_until", "end_at", "end_date", "end", "valid")),
            (("最低金额", "最小金额"), ("min_amount", "minimum_amount", "min")),
            (("金额",), ("amount", "price", "total", "fee", "cost")),
            (("数量",), ("quantity", "qty", "count")),
            (("分类",), ("categor", "type", "class")),
            (("类型",), ("type", "kind", "categor")),
            (("范围",), ("scope", "range")),
            (("使用次数",), ("usage_limit", "usage_count", "uses", "limit")),
            (("次数",), ("usage_limit", "usage_count", "uses", "limit")),
        )
        _cjk_rows_before = len(result)
        for _terms, _field_tokens in _CJK_TERM_FIELD_TOKENS:
            if not any(_term in stmt for _term in _terms):
                continue
            for _ent_name, _ent_nodes in _ENTITY_FIELD_NODES.items():
                for _fname_lower, _fnode in _ent_nodes.items():
                    if not isinstance(_fnode, dict):
                        continue
                    _fname = _text(_fnode.get("name") or _fname_lower)
                    if (
                        not _fname
                        or (_text(_ent_name).lower(), _fname_lower) in _known_field_keys
                    ):
                        continue
                    _fnl = _fname.lower()
                    if not any(
                        _fnl == _tok
                        or _fnl.endswith("_" + _tok)
                        or _fnl.startswith(_tok + "_")
                        or ("_" + _tok + "_") in _fnl
                        # Stem match: "categor" is the stem of category/
                        # categories. Cover word-initial (category_scope),
                        # word-final (process_category) and word-middle
                        # (x_category_y) stems but require the stem to
                        # continue into exactly the real tails (y/ies) so
                        # classify_note/classroom can never match "class".
                        or (
                            _fnl.startswith(_tok)
                            and _fnl[len(_tok):].startswith(("y", "ies"))
                        )
                        or (
                            ("_" + _tok) in _fnl
                            and _fnl[
                                _fnl.index("_" + _tok) + len(_tok) + 1:
                            ].startswith(("y", "ies"))
                        )
                        for _tok in _field_tokens
                    ):
                        continue
                    _cf = _text(_fnode.get("field_id"))
                    if _cf and _cf in _known_ids:
                        continue
                    _row: dict[str, Any] = {"entity_ref": _ent_name, "field": _fname}
                    if _cf:
                        _row["field_id"] = _cf
                    _sem = _text(_fnode.get("semantic_type"))
                    if _sem:
                        _row["semantic_type"] = _sem
                    result.append(_row)
                    _known_field_keys.add((_text(_ent_name).lower(), _fname_lower))
                    if _cf:
                        _known_ids.add(_cf)
        _cjk_rows_after = len(result)
        if _cjk_rows_after > _cjk_rows_before:
            # P0-E: fixed-vocabulary CJK field-token binding is a legacy
            # candidate hint — counted, never silent.
            _record_fallback("CJK_FIELD_TOKEN_EXTRACTION", _cjk_rows_after - _cjk_rows_before)
        return result

    # Invariants from rule library (typed expression + description)
    # V1.4.0: Umbrella Rule detection — broad rules without specific entity/field
    # references must not generate executable experiments.
    _UMBRELLA_PATTERNS = (
        "数据一致性", "权限安全", "业务流程必须正确", "金额必须准确",
        "系统应保证", "数据安全", "系统稳定", "高可用", "高性能",
        "data consistency", "system security", "business correctness",
        "amount accuracy", "system stability", "high availability",
    )

    def _validated_semantic_frame(
        rule: dict[str, Any],
        statement: str,
    ) -> dict[str, Any]:
        raw = rule.get("semantic_frame")
        if raw in (None, {}):
            return {}
        if not isinstance(raw, dict):
            raise BehaviorIRError("business_semantic_frame_not_object")
        if _text(raw.get("schema_version")) != "qualibug.business-semantic-frame.v1":
            raise BehaviorIRError("business_semantic_frame_schema_invalid")
        modality = _text(raw.get("modality")).upper()
        polarity = _text(raw.get("polarity")).lower()
        if modality not in {"REQUIRED", "PROHIBITED", "EXCLUSIVE", "INVARIANT", "DECLARED"}:
            raise BehaviorIRError("business_semantic_frame_modality_invalid")
        if polarity not in {"positive", "negative"}:
            raise BehaviorIRError("business_semantic_frame_polarity_invalid")
        if raw.get("source_grounded") is not True:
            raise BehaviorIRError("business_semantic_frame_not_source_grounded")
        condition = _text(raw.get("condition"))
        subject = _text(raw.get("subject"))
        behavior = _text(raw.get("behavior"))
        if not behavior:
            raise BehaviorIRError("business_semantic_frame_behavior_empty")
        statement_folded = statement.casefold()
        for field_name, value in (
            ("condition", condition),
            ("subject", subject),
            ("behavior", behavior),
        ):
            if value and value.casefold() not in statement_folded:
                raise BehaviorIRError(
                    "business_semantic_frame_"
                    f"{field_name}_not_in_source:"
                    f"rule_id={_text(rule.get('rule_id') or rule.get('id')) or _stable_id('inv', statement)}:"
                    f"source_id={_text(rule.get('source_id'))}:"
                    f"source_locator={_text(rule.get('source_locator'))}"
                )
        anchors = _list(raw.get("source_anchors"))
        if any(not isinstance(anchor, str) or not anchor.strip() for anchor in anchors):
            raise BehaviorIRError("business_semantic_frame_source_anchors_invalid")
        if any(anchor.casefold() not in statement_folded for anchor in anchors):
            raise BehaviorIRError(
                "business_semantic_frame_source_anchor_not_in_source"
            )
        return {
            "schema_version": "qualibug.business-semantic-frame.v1",
            "modality": modality,
            "polarity": polarity,
            "condition": condition,
            "subject": subject,
            "behavior": behavior,
            "source_anchors": list(dict.fromkeys(anchors))[:20],
            "source_grounded": True,
        }

    # ── Risk-domain semantics index ──
    # The knowledge asset's risk-domain layer already classifies each source
    # rule into a structured semantic family (idempotency, state_machine,
    # data_conservation, permission_boundary, concurrency, business_rule).
    # That classification is source-grounded business understanding produced
    # by asset construction; the IR consumes it so a classified rule is never
    # mistaken for a broad umbrella overlay, and so idempotency rules carry
    # the structured idempotency expression.
    _risk_domain_by_rule: dict[str, str] = {}
    for _risk_row in _list(data.get("risk_domains")):
        if not isinstance(_risk_row, dict):
            continue
        _src_rule = _text(_risk_row.get("source_rule_id"))
        _risk_type = _text(_risk_row.get("risk_type")).lower()
        if _src_rule and _risk_type:
            _risk_domain_by_rule.setdefault(_src_rule, _risk_type)

    for rule in _list(data.get("rule_library") or data.get("rules")):
        if not isinstance(rule, dict):
            continue
        statement = _text(rule.get("statement") or rule.get("expression") or rule.get("title"))
        if not statement:
            continue
        rid = _text(rule.get("rule_id") or rule.get("id")) or _stable_id("inv", statement)
        if _rule_carries_product_bookkeeping_vocabulary(statement):
            # Keep the gap visible: product EKC/identity receipts scraped into
            # rule_library must not become business invariants. Do not echo the
            # contaminated statement into IR nodes — validate_behavior_ir rejects
            # any ground_truth vocabulary in node JSON.
            model["coverage_gaps"].append(_fact_node(
                node_id=_stable_id("gap", "product_bookkeeping_rule_excluded", rid),
                typed_fields={
                    "gap_type": "product_bookkeeping_rule_excluded",
                    "reason_code": "PRODUCT_BOOKKEEPING_RULE_NOT_BUSINESS_INVARIANT",
                    "description": (
                        "Rule statement carries product identity/annotation "
                        "bookkeeping vocabulary and was excluded from Behavior "
                        "IR invariants"
                    ),
                    "source_rule_ref": rid,
                },
                source_refs=[
                    _source_ref(
                        _text(rule.get("source_id")) or "rule_library",
                        kind="product_bookkeeping_exclusion",
                    )
                ],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue
        _semantic_frame = _validated_semantic_frame(rule, statement)

        # ── Field-level grounding: extract structured operands from statement ──
        _rule_kind = _text(rule.get("kind") or rule.get("risk_type") or "business_rule")
        _risk_type = _risk_domain_by_rule.get(rid) or _risk_domain_by_rule.get(
            _text(rule.get("source_rule_id"))
        )
        # Idempotency/repetition vocabulary is generic technical semantics —
        # not industry terms. A rule stating 重复/幂等/再次/二次 names a
        # repeat-guard invariant (the same input executed twice must not
        # apply its business effect twice), which is concrete even without
        # field references. Asset risk-domain classification (idempotency)
        # confirms the same structure. Both promote the rule to the
        # structured idempotency family so it compiles an effect-cardinality
        # experiment instead of dying as a broad overlay.
        _has_idempotency_token = any(
            token in statement for token in ("重复", "幂等", "再次", "二次", "多次")
        )
        _has_idempotency_signal = _has_idempotency_token or _risk_type == "idempotency"
        if _has_idempotency_token and _risk_type != "idempotency":
            # P0-E: token-promoted idempotency is a legacy candidate hint —
            # only the risk-domain classification (or a grounded frame) is
            # structured idempotency evidence.
            _record_fallback("IDEMPOTENCY_TOKEN_CANDIDATE", 1)
        if _has_idempotency_signal and _rule_kind in {"business_rule", "business_logic"}:
            _rule_kind = "idempotency"
        # Interface validation-contract normalization: a rule extracted from an
        # interface's own 业务约束 declaration (必须校验状态、生效时间、分类范围…)
        # names the validation dimensions the operation must check — it is a
        # validation contract, not a state-transition machine. The 校验状态
        # phrasing makes the 状态 token misclassify it as state_machine, and
        # the state family deliberately excludes entity-co-reference binding
        # (only transition operations may test a state invariant), so the
        # invariant stays permanently unbound and the declared contract is
        # never exercised. 校验/验证 are generic technical verbs (any industry
        # documents 校验 X / 验证 X), never industry terms.
        if (
            _rule_kind in {"state_machine", "state"}
            and any(token in statement for token in ("校验", "验证"))
        ):
            _rule_kind = "validation"
            _record_fallback("INTERFACE_VALIDATION_CONTRACT_REKIND", 1)
        _rule_operands = _list(rule.get("operands"))
        _rule_equation: dict[str, Any] = _dict(rule.get("equation"))
        # For conservation/data_conservation/business amount-quantity rules
        # without explicit operands, extract field references from statement text.
        # CJK validation vocabulary (状态/时间/金额/数量/分类/范围/次数) is
        # industry-neutral business language; a statement carrying such terms
        # names fields it governs even when no ASCII identifier appears.
        # Industry-specific terms (优惠/库存/余额/订单) are intentionally absent:
        # they trigger no extraction on their own, so an industry term without
        # a generic mapping never invents field bindings.
        _CJK_FIELD_SIGNAL_TERMS = (
            "状态", "时间", "金额", "数量", "分类", "范围", "次数",
        )
        # Hoisted before the umbrella decision: the extraction result is the
        # ground truth for "does this statement name concrete fields". A rule
        # whose CJK terms bind real entity fields governs specific fields and
        # must not be treated as a broad overlay just because it carries no
        # ASCII identifier.
        _extracted_fields: list[dict[str, str]] = []
        if not _rule_operands and (
            any(
                token in _rule_kind.lower()
                for token in ("conserv", "data_conservation", "balance", "amount", "quantity")
            )
            or any(
                token in statement.lower()
                for token in (
                    "available_qty", "locked_qty", "payable_amount", "total_amount",
                    "discount_amount", "refund", "qty", "amount",
                )
            )
            or any(token in statement for token in _CJK_FIELD_SIGNAL_TERMS)
        ):
            _extracted_fields = _extract_fields_from_statement(statement)
            if _extracted_fields:
                _rule_operands = _extracted_fields
                # Build equation terms from extracted fields. 不能为负数 /
                # 不得为负 / 非负 statements are FIELD BOUNDARY constraints
                # (the field value must never go below zero), not sum
                # conservation between fields — an unchanged_sum equation
                # would assert the wrong property (adjust changes
                # available_qty by design) and the boundary violation would
                # stay invisible. 非负 vocabulary is generic business
                # language, never industry terms.
                _terms = [
                    _text(f.get("field_id") or f.get("field"))
                    for f in _extracted_fields
                    if f.get("field") or f.get("field_id")
                ]
                if _terms and not _rule_equation:
                    if any(
                        token in statement
                        for token in ("不能为负", "不得为负", "非负", "不允许为负", "不可为负")
                    ):
                        _rule_equation = {
                            "operator": "non_negative",
                            "terms": _terms,
                        }
                    else:
                        _rule_equation = {
                            "operator": "unchanged_sum",
                            "terms": _terms,
                        }

        # V1.4.0: Detect Umbrella Rules — broad statements without concrete
        # entity/field references that cannot produce testable experiments.
        _stmt_lower = statement.lower()
        # ── Subject-frame binding channel (P0-D extension) ──
        # Resolve the rule's governed object surface through its grounded
        # semantic frame BEFORE the umbrella decision: a rule whose frame
        # subject (or constraint vocabulary) resolves to declared business
        # objects, tables and operations is concrete by construction — the
        # umbrella overlay exists to keep VAGUE overlays out, and an object-
        # bound rule is the opposite of vague. The channel result also feeds
        # the decision-operation rekind and entity-scoped operands below.
        _subject_channel = _subject_channel_resolution(
            rule,
            _semantic_frame if _semantic_frame.get("source_grounded") is True else None,
            statement,
            data,
            model,
        )
        _subject_channel_ops = [
            _text(value)
            for value in _list(_subject_channel.get("op_ids"))
            if _text(value)
        ]
        _is_umbrella = any(p in _stmt_lower for p in _UMBRELLA_PATTERNS)
        if _is_umbrella:
            # P0-E: a grounded frame is structured technical evidence — the
            # frame channel grounded this rule to real objects, so the legacy
            # umbrella overlay must not exclude it.  An absent/ungrounded
            # frame keeps the legacy exclusion (receipted).
            _rule_frame_for_umbrella = (
                _frame_for_rule(rid, statement) if _frame_ledger_present else {}
            )
            if _rule_frame_for_umbrella and _frame_is_grounded(_rule_frame_for_umbrella):
                _is_umbrella = False
                _record_fallback("UMBRELLA_PATTERN_OVERRIDDEN_BY_GROUNDED_FRAME", 1)
            else:
                _record_fallback("UMBRELLA_PATTERN_FALLBACK", 1)
        # Also detect: no backtick fields, no snake_case fields, no specific entity
        if not _is_umbrella:
            _has_concrete_field = bool(re.findall(r"`[a-zA-Z_][a-zA-Z0-9_]*`", statement))
            _has_concrete_field = _has_concrete_field or bool(
                re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", _stmt_lower)
            )
            # CJK/description field extraction and rule-declared operands are
            # concrete field evidence: the statement names the fields it
            # governs (退款金额→amount, 实付金额→payable_amount) even though
            # no ASCII identifier appears in the text.
            _has_concrete_field = _has_concrete_field or bool(_extracted_fields) or any(
                isinstance(_op, dict)
                and bool(_text(_op.get("field") or _op.get("field_id") or _op.get("entity_ref")))
                for _op in _list(_rule_operands)
            )
            # Asset-classified risk semantics and idempotency vocabulary are
            # concrete rule evidence too: the asset's risk-domain layer already
            # resolved the rule to a structured family (idempotency,
            # state_machine, data_conservation, permission_boundary,
            # concurrency), which a broad overlay statement can never carry.
            # A grounded frame is the same kind of structured evidence
            # (P0-E): the frame channel grounded this rule to real technical
            # objects, so it is not a vague overlay.
            _rule_frame_for_concreteness = (
                _frame_for_rule(rid, statement) if _frame_ledger_present else {}
            )
            _has_structured_risk_semantics = (
                bool(_risk_type)
                or _has_idempotency_signal
                or bool(
                    _rule_frame_for_concreteness
                    and _frame_is_grounded(_rule_frame_for_concreteness)
                )
            )
            _has_entity_ref = bool(_text(rule.get("entity") or rule.get("object") or rule.get("business_object")))
            # A validated semantic frame is source-grounded structure, but it
            # is not an executable operation binding by itself.  Only an
            # explicit source operation reference or an accepted exact-source
            # / agent semantic rule-to-interface edge may keep a short rule
            # concrete.  This prevents parser-generated frames from turning
            # broad overlay rules into obligations merely because their text
            # contains a subject and behavior.
            _has_source_grounded_semantics = bool(
                _semantic_frame.get("source_grounded") is True
                and any(
                    _text(_semantic_frame.get(field))
                    for field in ("subject", "condition", "behavior")
                )
            )
            # A rule whose OWN source span is the operation's interface prose
            # (the rule is documented inside the operation's summary or
            # description, e.g. 权限：管理员 / 仅 ACTIVE 用户可登录) is
            # operation-scoped by construction — its grounding is the
            # interface attachment itself, not a parser frame. Such a short
            # rule governs exactly the operation it is documented at and must
            # not be umbrella-excluded: an operation-attached access/state
            # constraint without this carve-out would never compile an
            # obligation and the defect it describes would stay invisible.
            _has_operation_attached_source = bool(
                re.search(r"#interface=", _text(rule.get("source_locator")))
            ) or any(
                isinstance(span, dict)
                and _text(span.get("attachment")) == "openapi_interface_prose"
                for span in _list(_dict(rule.get("semantic_contract")).get("source_spans"))
            )
            _has_explicit_operation_ref = bool(
                _text(rule.get("operation_ref") or rule.get("operation_id"))
                or any(_text(value) for value in _list(rule.get("operation_refs")))
                or any(
                    _text(value)
                    for value in _list(rule.get("authoritative_operation_refs"))
                )
            )
            _has_authoritative_operation_link = _has_explicit_operation_ref or any(
                isinstance(edge, dict)
                and _text(edge.get("relation") or edge.get("relation_type")).lower()
                == "rule_to_interface"
                and _text(edge.get("from") or edge.get("from_ref")) == rid
                and _text(edge.get("status")).lower() == "accepted"
                and _text(edge.get("derivation")).lower().replace("-", "_")
                in {"agent_semantic_mapping", "exact_source_section"}
                for edge in _list(data.get("relationships"))
            )
            if (
                not _has_concrete_field
                and not _has_entity_ref
                and not (
                    (_has_source_grounded_semantics or _has_operation_attached_source)
                    and _has_authoritative_operation_link
                )
                and not _has_structured_risk_semantics
                and len(statement) < 30
            ):
                _is_umbrella = True
        if _subject_channel_ops:
            # A subject-resolved rule is concrete: the frame subject bound to
            # real objects, tables and operations — never a vague overlay.
            _is_umbrella = False
            _record_fallback("SUBJECT_FRAME_CHANNEL_CONCRETE", 1)
        # ── Decision-operation rekind + entity-scoped operands ──
        # A rule that constrains an object's ELIGIBILITY at a decision
        # operation (校验/验证/使用/领取/模拟/validate/check/use/claim/
        # simulate) is a validation contract on that operation, whatever the
        # asset classified it as (state_machine / permission_boundary /
        # data_conservation). 优惠券状态必须为 ACTIVE 是状态约束而非状态转移;
        # 折扣券必须遵守封顶金额 是输入边界而非全局金额守恒 — the state /
        # conservation families would compile transition/DB-observer
        # experiments that can never verify the decision endpoint.  The
        # rekind fires only when the subject channel resolved DECISION
        # operations, so lifecycle/conservation rules on non-decision
        # surfaces (退款金额不能大于实际支付金额 → refund ops) keep their
        # families untouched.
        _subject_decision_ops = [
            _text(value)
            for value in _list(_subject_channel.get("decision_op_ids"))
            if _text(value)
        ]
        if _subject_decision_ops and _rule_kind in {
            "state_machine", "state", "permission_boundary", "permission",
            "access_control", "data_conservation", "conservation",
        }:
            _rule_kind = "validation"
            _record_fallback("SUBJECT_DECISION_OP_REKIND", 1)
        # ── Usage-limit rekind (consumption-quota rules) ──
        # A rule constraining how many times an object may be consumed
        # (使用次数/限用/只能使用一次 + 不能超过/限制/上限) states a QUOTA:
        # the violation is a REPLAYED consumption — the same input applied
        # again must not apply a new business effect. The decision-op
        # rekind above would bind it to validate (a read-only eligibility
        # surface that can never violate a quota); the quota rekind narrows
        # the surface to the CONSUMPTION operations and promotes the family
        # to idempotency so the replay protocol (control = consume once,
        # treatment = consume again) exercises the quota. Consumption ops
        # are a subset of the channel's decision ops, so the rekind fires
        # only when the subject channel actually resolved a consumption
        # surface; the same op filter narrows the binding below.
        _has_usage_limit_signal = bool(
            any(_term in statement for _term in _USAGE_LIMIT_TERMS)
            and any(
                _restrictor in statement
                for _restrictor in _USAGE_LIMIT_RESTRICTORS
            )
        )
        _usage_limit_op_ids: list[str] = []
        if _has_usage_limit_signal:
            # Narrow to the consumption family the statement names: 领取/领
            # → claim ops, 兑换 → redeem ops, 使用/核销/用 → use/consume
            # ops. Consumption ops are a subset of the channel's decision
            # ops, so the binding below never leaves the resolved surface.
            # Op identity is the PATH: the model's operation ids are
            # content-addressed and carry no vocabulary.
            _op_path_by_id = {
                _text(row.get("id")): _text(row.get("path") or row.get("raw_path"))
                for row in _list(model.get("operations"))
                if isinstance(row, dict) and _text(row.get("id"))
            }
            _matched_families = [
                family_tokens
                for family_terms, family_tokens in _USAGE_ACTION_FAMILIES
                if any(_ft in statement for _ft in family_terms)
            ]
            _usage_limit_op_ids = [
                _oid
                for _oid in _subject_decision_ops
                if any(
                    _op_text_has_token(
                        f" {_op_path_by_id.get(_oid, '')} {_oid} ",
                        _token,
                    )
                    for _token in _CONSUMPTION_OP_TOKENS
                )
                and (
                    not _matched_families
                    or any(
                        _op_text_has_token(
                            f" {_op_path_by_id.get(_oid, '')} {_oid} ",
                            _ftok,
                        )
                        for _tokens in _matched_families
                        for _ftok in _tokens
                    )
                )
            ]
        if (
            _has_usage_limit_signal
            and _usage_limit_op_ids
            and _rule_kind != "idempotency"
        ):
            _rule_kind = "idempotency"
            _record_fallback("USAGE_LIMIT_CONSUMPTION_REKIND", 1)
        # Entity-scoped contract operands: the resolved entity's own fields
        # matched by the rule's constraint vocabulary (状态→status, 有效期→
        # expires_at, 次数→user_limit/global_limit, 类目→category_scope,
        # 封顶→max_discount, 最低金额→min_order_amount). Only attached when
        # the rule carries no operands — rules with extracted operands keep
        # them byte-identical.
        _subject_field_operands = [
            dict(row)
            for row in _list(_subject_channel.get("field_operands"))
            if isinstance(row, dict)
        ]
        if _subject_field_operands and not _rule_operands:
            _rule_operands = _subject_field_operands
            _record_fallback("SUBJECT_ENTITY_SCOPED_OPERANDS", len(_subject_field_operands))
        # Keep expression identity-stable. Semantic-frame enrichment is recorded on
        # the invariant node only — merging modality/polarity/condition/subject/
        # behavior into expression changes property fingerprints and silently
        # rotates stable obligation_id values (V1.6.2 Unlock Set underselection).
        # Idempotency rules carry the structured effect-cardinality
        # expression (same as the implicit-rule path: the same business
        # input must apply its effect exactly once), so the obligation
        # compiler selects the idempotent_effect_cardinality protocol.
        if _rule_kind == "idempotency":
            # The structured effect-cardinality operand is the protocol
            # contract and must be present even when the channel attached
            # quota field operands (user_limit/global_limit) — those name
            # the constrained dimension, this names the replay contract.
            if not any(
                isinstance(_op, dict)
                and _text(_op.get("operator")) == "business_effect_count"
                for _op in _rule_operands
            ):
                _rule_operands = [
                    {
                        "operator": "business_effect_count",
                        # Replay-window contract: the repeated input must add
                        # zero NEW business effect. The idempotency observer
                        # measures the treatment (replay) window, so the
                        # structured cardinality is 0 — an enforced quota
                        # (replay refused) and a no-op replay both observe 0.
                        "expected_effect_count": 0,
                    },
                    *_rule_operands,
                ]
            _expression = {
                "kind": _rule_kind,
                "operator": "business_effect_count",
                "operands": _rule_operands,
                "raw": statement,
            }
        else:
            _expression = {
                "kind": _rule_kind,
                "operator": _text(rule.get("operator") or "must_hold"),
                "operands": _rule_operands,
                "raw": statement,
            }
        if _rule_equation:
            _expression["equation"] = _rule_equation
        # V1.4.0/V1.6.0: collect Canonical Field IDs (cf_*) when present; else names.
        _inv_field_ids: list[str] = []
        for _operand in _rule_operands:
            if isinstance(_operand, dict):
                _of = _text(_operand.get("field_id") or _operand.get("field"))
                if _of:
                    _inv_field_ids.append(_of)
        _inv_typed: dict[str, Any] = {
            "description": statement,
            "expression": _expression,
            "operation_refs": list(
                dict.fromkeys(
                    [
                        _text(value)
                        for value in [
                            *_list(rule.get("operation_refs")),
                            *_list(rule.get("authoritative_operation_refs")),
                            *(
                                [rule.get("operation_ref") or rule.get("operation_id")]
                                if rule.get("operation_ref") or rule.get("operation_id")
                                else []
                            ),
                        ]
                        if _text(value)
                    ]
                )
            ),
            "source_rule_refs": list(dict.fromkeys(
                _text(value)
                for value in (rule.get("rule_id"), rule.get("id"))
                if _text(value)
            )),
        }
        if _inv_field_ids:
            _inv_typed["field_ids"] = _inv_field_ids
        if _semantic_frame:
            _inv_typed["semantic_frame"] = _semantic_frame
        # UI/UX rules declare the page states they forbid (negative_examples)
        # and Gherkin expectations (ui_oracle.then) in the source document.
        # Carry them onto the invariant so the UI obligation → protocol →
        # assertion chain judges the page against the document's own words.
        if _rule_kind == "ui":
            if rule.get("negative_examples") is not None:
                _inv_typed["negative_examples"] = [
                    _text(item)
                    for item in list(rule.get("negative_examples"))
                    if _text(item)
                ]
            if isinstance(rule.get("ui_oracle"), dict):
                _inv_typed["ui_oracle"] = dict(rule["ui_oracle"])
        # P0-E phase 2: the v1 extractor confirmation gate (composition,
        # apply_v1_extractor_frame_confirmation) marks legacy regex-candidate
        # rules CONFIRMED / FALLBACK_UNGROUNDED / UNCONFIRMED_NO_FRAME.
        # Carry the status onto the invariant so the demotion is observable
        # end-to-end. Rules without the status (ledger-less assets, non-v1
        # rules) are untouched and stay byte-identical.
        if _text(rule.get("frame_confirmation")):
            _inv_typed["frame_confirmation"] = _text(rule.get("frame_confirmation"))
            _inv_typed["frame_confirmation_reason"] = _text(
                rule.get("frame_confirmation_reason") or ""
            )
        # P0-E phase-3: frame family evidence — when this rule's frame is
        # grounded, its structured frame_type is the SSOT risk-family signal
        # for the obligation compiler (legacy CJK family tokens 库存/金额/
        # 隐私/过期/可见/状态 are demoted to observable fallback).  No
        # ledger / no frame → no evidence field (compat path unchanged).
        _family_frame = (
            _frame_for_rule(rid, statement) if _frame_ledger_present else {}
        )
        if _family_frame and _frame_is_grounded(_family_frame):
            _inv_typed["frame_family_evidence"] = {
                "frame_id": _text(_family_frame.get("frame_id")),
                "frame_type": _text(_family_frame.get("frame_type")),
                "grounded": True,
            }
        # ── Action-word → operation binding ──
        # A source rule names the operations it governs through its action
        # verbs (已取消订单不能支付、发货、确认收货 → pay/ship/confirm).
        # Operations carry the source documents' own Chinese summary
        # (支付订单 / 订单发货 / 确认收货), so the rule's action phrases are
        # matched against the operation's own source text — same-language
        # substring evidence, never a translation table. Phrase extraction
        # uses the rule's own structure: the segment after a modal word
        # (不能/不得/必须/只能/仅/…) split by list separators yields the
        # governed action list, plus transfer actions (导出/下载/…) that
        # govern read operations. A bare action-word match is deliberately
        # not used — 收货 inside 收货地址 would bind address CRUD to a
        # pay/ship/confirm rule. Binds even when the rule carries explicit
        # operation refs: those may be wrong (auth endpoints bound to an
        # export rule), and the action evidence appends the operations the
        # rule actually governs.
        if not _is_umbrella:
            try:
                from .enterprise_knowledge_center.enterprise_understanding.structured_fact_compiler import (
                    _ACTION_PATTERN as _CJK_ACTION_PATTERN,
                )
                _action_phrases = _extract_action_phrases(
                    statement, _CJK_ACTION_PATTERN
                )
                if not _action_phrases and any(
                    token in statement for token in ("校验", "验证")
                ):
                    # Validation-contract fallback: a rule declaring 必须校验
                    # 状态、生效时间、分类范围… constrains the validation
                    # operation itself (the interface that performs 校验).
                    # The action-verb table has no 校验/验证 entry, so phrase
                    # extraction returns nothing and the whole contract would
                    # stay unbound. 校验 is a generic technical verb — it only
                    # matches operation titles that contain 校验 (校验优惠券 /
                    # 批量校验), never 验证码-style titles, and any industry's
                    # validation interface documents the same verb. The
                    # fallback requires the statement to enumerate multiple
                    # comma-separated validation dimensions after 必须校验
                    # (状态、生效时间、最低金额、分类范围…) — a single-object
                    # 校验 (校验角色 / 校验身份 / 校验并扣减) constrains
                    # authorization/authentication/atomic actions, and binding
                    # it to the validation interface would be a wrong
                    # attribution.
                    if sum(
                        1
                        for _sep in ("，", "、", ",")
                        for _ in re.finditer(re.escape(_sep), statement)
                    ) >= 2:
                        _action_phrases = ["校验"]
                        _record_fallback("VALIDATION_CONTRACT_PHRASE_FALLBACK", 1)
                if _action_phrases:
                    # Response-side rules (导出结果禁止包含 password) govern
                    # the CONTENT of read operations; write-side rules govern
                    # the action a write operation executes. Rules that name
                    # no action phrase (响应不得返回支付密钥) are not bound
                    # heuristically — their operation identity comes from the
                    # source-identity path (interface-attached span → rule
                    # operation_refs → invariant), never from a guess.
                    _is_response_side = any(
                        _signal in statement
                        for _signal in ("导出", "结果", "响应", "返回", "输出")
                    )
                    _action_bound_ops: list[str] = []
                    # P0-E frame-confirmation gate: when this rule's frame is
                    # grounded, only operations the frame grounded
                    # (METHOD:path) may be bound — unconfirmed legacy
                    # candidates are demoted to hints and skipped.  A rule
                    # without a frame, or with an ungrounded frame, keeps the
                    # legacy binding as an observable fallback.
                    _binding_frame = (
                        _frame_for_rule(rid, statement) if _frame_ledger_present else {}
                    )
                    if not _frame_ledger_present:
                        _record_fallback("ACTION_PHRASE_BINDING_NO_FRAME_LEDGER", 1)
                    elif not _binding_frame:
                        _record_fallback("ACTION_PHRASE_BINDING_NO_FRAME_FOR_RULE", 1)
                    elif not _frame_is_grounded(_binding_frame):
                        _record_fallback("ACTION_PHRASE_BINDING_FALLBACK_WHEN_UNGROUNDED", 1)
                    _binding_grounded_ops = (
                        {_text(o).lower() for o in _frame_grounded_ops(_binding_frame)}
                        if _frame_ledger_present
                        and _binding_frame
                        and _frame_is_grounded(_binding_frame)
                        else None
                    )
                    for _op_row in _list(model.get("operations")):
                        if not isinstance(_op_row, dict):
                            continue
                        _op_id = _text(_op_row.get("id"))
                        if not _op_id:
                            continue
                        _op_method_upper = _text(_op_row.get("method")).upper()
                        if _is_response_side:
                            if _op_method_upper not in {"GET", "HEAD"}:
                                continue
                        elif _op_method_upper not in {
                            "POST", "PUT", "PATCH", "DELETE",
                        }:
                            continue
                        # Match against the operation's summary title only:
                        # the short source-declared heading (支付订单). The
                        # long description carries permission/exception prose
                        # that would over-match.
                        _op_title = _summary_title(_op_row)
                        if any(
                            _phrase in _op_title for _phrase in _action_phrases
                        ):
                            if _binding_grounded_ops is None:
                                _action_bound_ops.append(_op_id)
                            else:
                                _mtp = (
                                    f"{_op_method_upper}:"
                                    f"{_text(_op_row.get('path') or _op_row.get('raw_path'))}"
                                ).lower()
                                if _mtp in _binding_grounded_ops:
                                    _action_bound_ops.append(_op_id)
                                else:
                                    _record_fallback(
                                        "ACTION_PHRASE_BINDING_SKIPPED_WHEN_GROUNDED", 1
                                    )
                    if _action_bound_ops:
                        _existing_ops = [
                            _text(value)
                            for value in _list(_inv_typed.get("operation_refs"))
                            if _text(value)
                        ]
                        _inv_typed["operation_refs"] = list(
                            dict.fromkeys([*_existing_ops, *_action_bound_ops])
                        )
                else:
                    # ── Subject-entity binding fallback ──
                    # Rules that name the governed OBJECT entity without an
                    # action verb (用户端不展示下架商品、草稿商品、内部商品 —
                    # 展示 is not an action-table verb) bind through the
                    # asset's business-object aliases and the schema-declared
                    # foreign-key graph: a rule token matching an object alias
                    # (商品 → product) resolves the object's tables
                    # (products); the declared foreign keys (order_items.sku →
                    # products.sku, cart_items.sku → products.sku) resolve the
                    # consumer tables; and operations whose entity_refs name
                    # those tables are the rule's governed surface. Fully
                    # source-driven (business-object aliases and schema FK
                    # constraints are visible enterprise material), never an
                    # industry-term table. State-machine rules stay out —
                    # their binding comes from declared transitions, and
                    # entity binding would attach every state rule to the
                    # entity's collection CRUD. Permission/ownership rules
                    # (只能操作自己的订单) keep their isolation/visibility
                    # channel through declared relations; operation-level
                    # binding would compile availability obligations that
                    # test the operation itself, not the ownership the rule
                    # states. The fallback fires only for rules that carry
                    # no operation binding at all.
                    if (
                        _rule_kind
                        not in {
                            "state_machine", "state",
                            "permission_boundary", "permission",
                            "access_control",
                        }
                        and not _list(_inv_typed.get("operation_refs"))
                    ):
                        if _subject_channel_ops:
                            # Subject-frame channel: the grounded frame subject
                            # (or the rule's constraint vocabulary) resolved the
                            # object surface already — bind it directly. The
                            # decision-op rekind and entity-scoped operands
                            # happened before the expression build; the
                            # state/permission kinds reached here only when the
                            # channel rekinded them to validation (otherwise the
                            # kind gate above keeps them out).
                            _channel_ops = list(dict.fromkeys(_subject_channel_ops))
                            if _has_usage_limit_signal:
                                # A quota rule governs only the CONSUMPTION
                                # operations — binding it to the full channel
                                # surface (validate/check/… read-only
                                # eligibility endpoints) would compile replay
                                # obligations on surfaces that never consume.
                                _channel_ops = [
                                    _oid
                                    for _oid in _channel_ops
                                    if _oid in _usage_limit_op_ids
                                ]
                            if "用户端" in statement:
                                # User-facing rules (用户端不展示下架商品…) never
                                # govern management surfaces: the admin console
                                # is exactly who may see draft/delisted records.
                                # Exclude admin-scoped paths so the obligation
                                # cannot misreport legitimate management reads.
                                _op_path_by_id = {
                                    _text(row.get("id")): _text(
                                        row.get("path") or row.get("raw_path")
                                    )
                                    for row in _list(model.get("operations"))
                                    if isinstance(row, dict)
                                    and _text(row.get("id"))
                                }
                                _channel_ops = [
                                    _oid
                                    for _oid in _channel_ops
                                    if not re.search(
                                        r"(?:^|/)(?:admin|manage|management)(?:/|$)",
                                        _op_path_by_id.get(_oid, "").lower(),
                                    )
                                ]
                            _inv_typed["operation_refs"] = list(
                                dict.fromkeys(_channel_ops)
                            )
                            _subject_entity_refs = [
                                _text(value)
                                for value in _list(
                                    _subject_channel.get("subject_objects")
                                )
                                if _text(value)
                            ]
                            if _subject_entity_refs:
                                _inv_typed["subject_entity_refs"] = sorted(
                                    _subject_entity_refs
                                )
                            _record_fallback("SUBJECT_FRAME_BINDING", 1)
                        else:
                            _subject_bound_ops: list[str] = []
                            _rule_tokens = {
                                _text(t) for t in _list(rule.get("tokens")) if _text(t)
                            }
                        _subject_objects: set[str] = set()
                        for _bo in _list(data.get("business_objects")):
                            if not isinstance(_bo, dict):
                                continue
                            _obj_name = _text(_bo.get("object"))
                            _aliases = {
                                _text(a).lower()
                                for a in _list(_bo.get("aliases"))
                                if _text(a)
                            }
                            if _obj_name and any(
                                _text(t).lower() in _aliases for t in _rule_tokens
                            ):
                                _subject_objects.add(_obj_name.lower())
                        if _subject_objects:
                            _tables: dict[str, set[str]] = {}
                            _fk_by_table: dict[str, set[str]] = {}
                            for _t in _list(data.get("data_tables")):
                                if not isinstance(_t, dict):
                                    continue
                                _tname = _text(_t.get("name"))
                                if _tname:
                                    _tables[_tname] = set()
                                    _fk_by_table[_tname] = {
                                        _text(f)
                                        for f in _list(_t.get("foreign_keys"))
                                        if _text(f)
                                    }
                            _entity_tables: set[str] = set()
                            for _obj in _subject_objects:
                                for _tname in _tables:
                                    _tl = _tname.lower()
                                    if (
                                        _tl == _obj
                                        or _tl.startswith(_obj)
                                        or _obj.startswith(_tl)
                                    ):
                                        _entity_tables.add(_tname)
                            # One-hop FK propagation: tables whose declared
                            # foreign keys reference the object's tables
                            # (order_items → products) are the consumers.
                            # Parent tables are reached through the consumer's
                            # own FK list via entity_refs prefix matching
                            # below (order ⊂ order_items), so no further hops
                            # are needed and unrelated FK hops (users) stay
                            # out of the surface. Technical bookkeeping tables
                            # (lock/log/audit/record/history/event suffixes)
                            # reference the object without consuming it — an
                            # inventory lock row names a SKU but locking
                            # inventory is not a product-consumption surface.
                            # Attribute tables (inventory mirrors products.sku
                            # without being a business line) are excluded the
                            # same way: only business-detail tables (item/line/
                            # entry/row/detail — generic relational naming, not
                            # industry vocabulary) carry the object into a
                            # user-facing business flow.
                            _TECH_TABLE_SUFFIXES = (
                                "lock", "locks", "log", "logs", "audit",
                                "record", "records", "history", "event",
                                "events",
                            )
                            _DETAIL_TABLE_STEMS = (
                                "item", "line", "entry", "row", "detail",
                            )
                            for _tname, _fks in _fk_by_table.items():
                                if not (_fks & _entity_tables):
                                    continue
                                if any(
                                    _tname.endswith(_suf)
                                    for _suf in _TECH_TABLE_SUFFIXES
                                ):
                                    continue
                                if not any(
                                    _tname == _stem
                                    or _tname.endswith(_stem)
                                    or _tname.endswith(_stem + "s")
                                    for _stem in _DETAIL_TABLE_STEMS
                                ):
                                    continue
                                _entity_tables.add(_tname)
                            _user_facing_only = "用户端" in statement
                            for _op_row in _list(model.get("operations")):
                                if not isinstance(_op_row, dict):
                                    continue
                                _op_id = _text(_op_row.get("id"))
                                if not _op_id:
                                    continue
                                _op_path = _text(
                                    _op_row.get("path") or _op_row.get("raw_path")
                                )
                                if _user_facing_only and re.search(
                                    r"(?:^|/)(?:admin|manage|management)(?:/|$)",
                                    _op_path.lower(),
                                ):
                                    continue
                                if re.search(
                                    r"(?:^|/)(?:health)(?:/|$)", _op_path.lower()
                                ):
                                    continue
                                _op_ents = {
                                    _text(e).lower()
                                    for e in _list(_op_row.get("entity_refs"))
                                    if _text(e)
                                }
                                # Read operations often carry no entity_refs
                                # (GET /api/products exposes the object without
                                # declaring an entity), so a path segment
                                # matching the object name (products ↔ product)
                                # is the display-surface signal. Health probes
                                # are never the governed surface.
                                _path_segments = [
                                    seg
                                    for seg in _op_path.lower().strip("/").split("/")
                                    if seg and seg not in {"api", "health", "v1"}
                                ]
                                if any(
                                    _oe and (_oe in _tl or _tl.startswith(_oe))
                                    for _oe in _op_ents
                                    for _tl in _entity_tables
                                ) or any(
                                    _seg.startswith(_obj) or _obj.startswith(_seg)
                                    for _seg in _path_segments
                                    for _obj in _subject_objects
                                ):
                                    _subject_bound_ops.append(_op_id)
                            if _subject_bound_ops:
                                _record_fallback(
                                    "SUBJECT_ENTITY_BINDING", len(_subject_bound_ops)
                                )
                                _inv_typed["subject_entity_refs"] = sorted(
                                    _subject_objects
                                )
                                _existing_ops = [
                                    _text(value)
                                    for value in _list(_inv_typed.get("operation_refs"))
                                    if _text(value)
                                ]
                                _inv_typed["operation_refs"] = list(
                                    dict.fromkeys([*_existing_ops, *_subject_bound_ops])
                                )
            except Exception:
                # Action-word binding is a recovery convenience; its failure
                # must never fail the IR build — the invariant simply stays
                # unbound and the obligation compiler reports the gap.
                pass
        if _is_umbrella:
            _inv_typed["binding_status"] = "umbrella_rule_excluded"
        model["invariants"].append(_fact_node(
            node_id=rid if rid.startswith("bir_") else _stable_id("inv", rid),
            typed_fields=_inv_typed,
            source_refs=[_source_ref(
                _text(rule.get("source_id")) or "rule_library",
                locator=_text(rule.get("source_locator")),
                quote=statement[:200],
            )],
            confidence=float(rule.get("confidence") or 0.7),
            derivation="explicit",
        ))
        # ── P0-5: create causal postcondition invariant from conservation rules ──
        # When a conservation rule has extracted field operands AND the statement
        # contains causal delta language, create an additional postcondition
        # invariant that verifies field-level deltas (not just sum conservation).
        _CAUSAL_DELTA_TOKENS = (
            "减少", "增加", "扣减", "恢复", "预占", "释放", "消耗",
            "降低", "提升", "上涨", "下降", "锁定", "解冻",
            "decrease", "increase", "reduce", "restore", "reserve", "release",
        )
        if (
            _rule_operands
            and any(token in _rule_kind.lower() for token in ("conserv", "data_conservation", "balance", "amount", "quantity"))
            and any(token in statement.lower() for token in _CAUSAL_DELTA_TOKENS)
        ):
            # P0-E: causal-delta postcondition derivation from statement
            # tokens is a legacy candidate hint (counted, never silent).
            _record_fallback("CAUSAL_DELTA_TOKEN_EXTRACTION", 1)
            # Build field_delta operands from extracted fields
            _delta_operands: list[dict[str, Any]] = []
            for _op in _rule_operands:
                if not isinstance(_op, dict) or not _text(_op.get("field")):
                    continue
                _field_name = _text(_op.get("field"))
                # Infer delta direction from statement context around the field
                _dir = ""
                _field_lower = _field_name.lower()
                # Check if field is associated with decrease/increase in statement
                _stmt_lower = statement.lower()
                if any(tok in _stmt_lower for tok in ("减少", "扣减", "降低", "decrease", "reduce")):
                    if any(tok in _field_lower for tok in ("available", "unlock", "free")):
                        _dir = "decrease"
                    elif any(tok in _field_lower for tok in ("locked", "reserved", "frozen")):
                        _dir = "increase"
                elif any(tok in _stmt_lower for tok in ("增加", "恢复", "释放", "increase", "restore", "release")):
                    if any(tok in _field_lower for tok in ("available", "unlock", "free")):
                        _dir = "increase"
                    elif any(tok in _field_lower for tok in ("locked", "reserved", "frozen")):
                        _dir = "decrease"
                _delta_op: dict[str, Any] = {
                    "entity_ref": _text(_op.get("entity_ref")),
                    "field": _field_name,
                    "field_id": _field_name,
                }
                if _dir:
                    _delta_op["expected_delta_direction"] = _dir
                _delta_operands.append(_delta_op)
            if _delta_operands:
                _pc_invariant_id = _stable_id("inv", rid, "causal_postcondition")
                _pc_expression: dict[str, Any] = {
                    "kind": "postcondition",
                    "operator": "field_delta",
                    "operands": _delta_operands,
                    "raw": statement,
                }
                model["invariants"].append(_fact_node(
                    node_id=_pc_invariant_id,
                    typed_fields={
                        "description": f"[causal] {statement}",
                        "expression": _pc_expression,
                        "operation_refs": [
                            _text(value)
                            for value in _list(
                                rule.get("operation_refs")
                                or ([rule.get("operation_ref") or rule.get("operation_id")]
                                    if rule.get("operation_ref") or rule.get("operation_id") else [])
                            )
                            if _text(value)
                        ],
                        "source_rule_refs": list(dict.fromkeys(
                            _text(value)
                            for value in (rule.get("rule_id"), rule.get("id"))
                            if _text(value)
                        )),
                        "derived_from_rule_refs": list(dict.fromkeys(
                            _text(value)
                            for value in (rule.get("rule_id"), rule.get("id"))
                            if _text(value)
                        )),
                        "derived_invariant_kind": "causal_delta",
                    },
                    source_refs=[_source_ref(_text(rule.get("source_id")) or "rule_library", quote=statement[:200])],
                    confidence=float(rule.get("confidence") or 0.65),
                    derivation="model-inferred",
                ))

    # ── Causal chain postconditions → individual invariants ──
    # When a rule has been structurized into a causal chain (Daguan-style),
    # each postcondition becomes a separate, independently testable invariant.
    # This multiplies test coverage: one rule with 4 postconditions generates
    # 4 distinct verification obligations.
    for rule in _list(data.get("rule_library") or data.get("rules")):
        if not isinstance(rule, dict):
            continue
        causal = _dict(rule.get("causal_chain"))
        postconditions = _list(causal.get("postconditions"))
        if not postconditions:
            continue
        trigger = _text(causal.get("trigger_action"))
        rule_id = _text(rule.get("rule_id") or rule.get("id"))
        source_id = _text(rule.get("source_id")) or "rule_library"
        _rule_statement = _text(
            rule.get("statement") or rule.get("expression") or rule.get("title")
        )
        _pc_blob = " ".join(
            _text(pc.get("description"))
            for pc in postconditions
            if isinstance(pc, dict)
        )
        if _rule_carries_product_bookkeeping_vocabulary(_rule_statement) or (
            _rule_carries_product_bookkeeping_vocabulary(_pc_blob)
        ):
            model["coverage_gaps"].append(_fact_node(
                node_id=_stable_id(
                    "gap",
                    "product_bookkeeping_causal_rule_excluded",
                    rule_id or source_id,
                ),
                typed_fields={
                    "gap_type": "product_bookkeeping_rule_excluded",
                    "reason_code": "PRODUCT_BOOKKEEPING_CAUSAL_RULE_NOT_BUSINESS_INVARIANT",
                    "description": (
                        "Causal rule/postconditions carry product identity/"
                        "annotation bookkeeping vocabulary and were excluded "
                        "from Behavior IR invariants"
                    ),
                    "source_rule_ref": rule_id,
                },
                source_refs=[_source_ref(source_id, kind="product_bookkeeping_exclusion")],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue
        # Only an explicit source operation identity may bind a causal trigger.
        # The human-readable trigger is descriptive text, not an executable join.
        # Matching it against path/summary tokens made a rule such as "订单" bind
        # to an arbitrary order GET and silently changed the experiment surface.
        trigger_op_refs: list[str] = []
        declared_trigger_refs: list[str] = []
        for container in (rule, causal):
            for key in (
                "operation_refs",
                "operation_ref",
                "operation_id",
                "trigger_operation_refs",
                "trigger_operation_ref",
                "trigger_operation_id",
            ):
                raw_value = container.get(key)
                values = raw_value if isinstance(raw_value, list) else [raw_value]
                declared_trigger_refs.extend(
                    _text(value) for value in values if _text(value)
                )
        for operation_ref in dict.fromkeys(declared_trigger_refs):
            operation = _resolve_operation(
                [row for row in _list(model.get("operations")) if isinstance(row, dict)],
                {"operation_ref": operation_ref},
            )
            if operation is None:
                model["coverage_gaps"].append(_fact_node(
                    node_id=_stable_id(
                        "gap",
                        "causal_trigger_operation_unresolved",
                        rule_id,
                        operation_ref,
                    ),
                    typed_fields={
                        "gap_type": "causal_trigger_operation_unresolved",
                        "reason_code": "CAUSAL_TRIGGER_OPERATION_NOT_EXACTLY_SOURCE_BOUND",
                        "description": "A causal trigger declared an operation identity that did not resolve to exactly one Behavior IR operation",
                        "source_rule_ref": rule_id,
                        "operation_ref": operation_ref,
                        "trigger": trigger,
                    },
                    source_refs=[_source_ref(source_id, kind="causal_postcondition")],
                    confidence=1.0,
                    derivation="explicit",
                    status="unsupported",
                ))
                continue
            resolved_operation_ref = _text(operation.get("id"))
            if resolved_operation_ref:
                trigger_op_refs.append(resolved_operation_ref)
        for pc_idx, pc in enumerate(postconditions):
            if not isinstance(pc, dict):
                continue
            pc_entity = _text(pc.get("entity"))
            pc_field = _text(pc.get("field"))
            pc_must = _text(pc.get("must_become"))
            pc_create = pc.get("must_create")
            pc_desc = _text(pc.get("description"))
            # Build a precise invariant description
            if pc_must and pc_entity:
                desc = f"After {trigger or 'action'}, {pc_entity}.{pc_field or 'state'} must become {pc_must}" if pc_field else f"After {trigger or 'action'}, {pc_entity} must become {pc_must}"
            elif pc_create and pc_entity:
                desc = f"After {trigger or 'action'}, {pc_entity} must be created"
            elif pc_desc:
                desc = f"After {trigger or 'action'}, {pc_desc}"
            else:
                continue
            pc_inv_id = _stable_id("inv", "postcondition", rule_id or trigger, pc_entity, pc_field or pc_must or pc_desc, str(pc_idx))
            model["invariants"].append(_fact_node(
                node_id=pc_inv_id,
                typed_fields={
                    "description": desc,
                    "expression": {
                        "kind": "postcondition",
                        "operator": "must_become" if pc_must else "must_create" if pc_create else "must_hold",
                        "operands": [{
                            "entity_ref": pc_entity,
                            "field": pc_field,
                            "expected_value": pc_must,
                            "must_create": bool(pc_create),
                        }],
                        "raw": desc,
                    },
                    "operation_refs": trigger_op_refs,
                    "source_rule_refs": [rule_id] if rule_id else [],
                    "derived_from_rule_refs": [rule_id] if rule_id else [],
                    "derived_invariant_kind": "causal_postcondition",
                    "causal_trigger": trigger,
                    "preconditions": _list(causal.get("preconditions")),
                },
                source_refs=[_source_ref(source_id, quote=_text(rule.get("statement"))[:200], kind="causal_postcondition")],
                confidence=float(rule.get("confidence") or 0.7),
                derivation="explicit",
            ))

    # Runtime V2 relations are the only semantic joins used by the compiler.
    permission_policy_mode = _text(
        data.get("permission_policy_mode")
        or data.get("permission_matrix_mode")
    ).lower()
    permission_matrix_complete = bool(data.get("permission_matrix_complete")) or permission_policy_mode in {
        "closed_world",
        "complete",
    }
    model["relations"].extend(_derive_permission_relations(
        model,
        permission_rows,
        closed_world=permission_matrix_complete,
    ))
    model["relations"].extend(_derive_field_level_ownership_relations(
        model,
        data,
        frame_confirm=(
            _field_ownership_confirm if _frame_ledger_present else None
        ),
    ))
    model["relations"].extend(_derive_source_role_restriction_relations(model))
    model["relations"].extend(_derive_operation_entity_relations(model))
    model["relations"].extend(_derive_state_transition_relations(model, data))
    model["relations"].extend(_derive_source_relationship_relations(model, data))
    model["relations"].extend(_derive_invariant_relations(model))
    model["relations"].extend(_derive_compensation_relations(model))

    # ── Knowledge-center entity_relations → IR relation nodes ──
    # Consume the typed entity-relationship graph produced by
    # enterprise_knowledge_center._extract_entity_relations. Each edge
    # becomes a first-class IR relation with chunk-level source tracing.
    # Relation types are mapped to the IR schema's ALLOWED_RELATION_TYPES.
    _ENTITY_REL_TYPE_MAP: dict[str, str] = {
        "foreign_key": "owns",
        "belongs_to": "owns",
        "field_of": "owns",
        "operates_on": "consumes",
        "transitions": "transitions",
        "constrains": "scopes",
    }
    _node_index = _node_reference_index(model)
    _field_of_suppressed = 0

    def _field_of_entity(field_name: str, entity_node_id: str, built: dict[str, Any]) -> bool:
        """Whether *field_name* is a field the entity node already declares."""
        target = _text(field_name).lower()
        if not target:
            return False
        for node in _list(built.get("entities")):
            row = _dict(node)
            if _text(row.get("id")) != entity_node_id:
                continue
            for field in _list(row.get("fields")):
                if isinstance(field, dict):
                    candidate = _text(field.get("name") or field.get("field") or field.get("field_path"))
                else:
                    candidate = _text(field)
                if candidate.lower() == target:
                    return True
            return False
        return False

    for rel in _list(data.get("entity_relations")):
        if not isinstance(rel, dict):
            continue
        from_e = _text(rel.get("from_entity"))
        to_e = _text(rel.get("to_entity"))
        raw_type = _text(rel.get("relation_type"))
        if not from_e or not to_e or not raw_type:
            continue
        # Map to allowed IR relation type; permission:* → permits/denies
        if raw_type.startswith("permission:"):
            action = raw_type.split(":", 1)[1].lower()
            ir_type = "denies" if action in ("deny", "forbid", "prohibit", "none") else "permits"
        else:
            ir_type = _ENTITY_REL_TYPE_MAP.get(raw_type, "owns")
        chunk_id = _text(rel.get("source_chunk_id"))
        source_id = _text(rel.get("source_id")) or "entity_relations"
        locator = f"chunk_id={chunk_id}" if chunk_id else f"{from_e}->{to_e}"
        # Resolve names to node ids. Passing the raw names through produced relations
        # whose endpoints matched no node in the model -- 254 of them on a real target,
        # about 40% of all relations, including declared state transitions that then
        # contributed no obligations while appearing present in the IR.
        from_ref = _resolve_node_reference(from_e, _node_index)
        to_ref = _resolve_node_reference(to_e, _node_index)
        if not from_ref or not to_ref:
            # A field-of relation is not a gap. ``balance owns users`` says the column
            # belongs to the table, which entities[].fields already records -- every one
            # of the 175 such rows on a real target named a field the entity already
            # declares. Recording them as gaps would bury the genuine unresolved
            # endpoints under redundant noise, and the release gate counts gaps.
            if to_ref and ir_type == "owns" and _field_of_entity(from_e, to_ref, model):
                _field_of_suppressed += 1
                continue
            # Otherwise fail closed and visibly. A dangling relation is worse than a
            # recorded gap: the gap says "this fact could not be attached", the relation
            # says "it was".
            model["coverage_gaps"].append(_fact_node(
                node_id=_stable_id("gap", "entity_relation_endpoint_unresolved", from_e, to_e, raw_type),
                typed_fields={
                    "gap_type": "entity_relation_endpoint_unresolved",
                    "reason_code": "RELATION_ENDPOINT_NOT_A_NODE",
                    "relation_type": ir_type,
                    "from_entity": from_e,
                    "to_entity": to_e,
                    "unresolved_side": (
                        "both" if not from_ref and not to_ref else ("from" if not from_ref else "to")
                    ),
                    "description": "Source relation endpoint does not name any Behavior IR node",
                },
                source_refs=[_source_ref(source_id, locator=locator, kind=f"entity_relation:{raw_type}")],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue
        model["relations"].append(_relation_node(
            relation_type=ir_type,
            from_ref=from_ref,
            to_ref=to_ref,
            source_refs=[_source_ref(source_id, locator=locator, kind=f"entity_relation:{raw_type}")],
            confidence=float(rel.get("confidence") or 0.7),
            derivation="schema-derived",
        ))

    if _field_of_suppressed:
        # Counted, not silent: a reader must be able to see that N source relations were
        # recognised as already-modelled rather than wonder where they went.
        model["capabilities"].append(_fact_node(
            node_id=_stable_id("cap", "entity_relation_field_of_suppressed"),
            typed_fields={
                "capability": "entity_relation_field_of_suppressed",
                "adapter": "behavior_ir",
                "suppressed_count": _field_of_suppressed,
                "reason": "field-of relations are already represented in entities[].fields",
            },
            confidence=1.0,
            derivation="schema-derived",
        ))

    for source_gap in _list(data.get("coverage_gaps")):
        if not isinstance(source_gap, dict):
            continue
        gap_type = _text(
            source_gap.get("gap_type")
            or source_gap.get("reason_code")
            or source_gap.get("code")
            or "source_coverage_gap"
        ).lower()
        source_id = _text(source_gap.get("source_id"))
        # Asset-stage gaps may embed scraped product bookkeeping statements
        # (e.g. identity annotation receipts). Redact before IR materialization.
        sanitized_gap = _redact_product_bookkeeping_payload(dict(source_gap))
        if not isinstance(sanitized_gap, dict):
            continue
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id(
                "gap",
                gap_type,
                source_id,
                source_gap.get("parser_receipt_id"),
            ),
            typed_fields={
                **sanitized_gap,
                "gap_type": gap_type,
                "description": _text(sanitized_gap.get("description"))
                or "A source-stage coverage gap remains unresolved",
            },
            source_refs=(
                [_source_ref(source_id, kind="source_coverage_gap")]
                if source_id
                else []
            ),
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))

    invariant_relation_refs = {
        _text(relation.get("to_ref"))
        for relation in model["relations"]
        if isinstance(relation, dict)
        and _text(relation.get("operation_ref"))
        and _text(relation.get("to_ref"))
    }
    already_gapped_invariants = {
        _text(gap.get("invariant_ref"))
        for gap in model["coverage_gaps"]
        if isinstance(gap, dict) and _text(gap.get("invariant_ref"))
    }
    for invariant in model["invariants"]:
        if not isinstance(invariant, dict):
            continue
        invariant_ref = _text(invariant.get("id"))
        if (
            not invariant_ref
            or invariant_ref in invariant_relation_refs
            or invariant_ref in already_gapped_invariants
        ):
            continue
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "source_invariant_operation_unbound", invariant_ref),
            typed_fields={
                "gap_type": "source_invariant_operation_unbound",
                "reason_code": "SOURCE_INVARIANT_OPERATION_UNBOUND",
                "description": "A source-backed invariant has no exact operation binding",
                "invariant_ref": invariant_ref,
                "source_rule_refs": list(invariant.get("source_rule_refs") or []),
            },
            source_refs=[
                dict(ref)
                for ref in _list(invariant.get("source_refs"))
                if isinstance(ref, dict)
            ],
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))

    # Observation surfaces. Availability was the literal ``surface_id == "http_api"``,
    # so a project with a declared and reachable database still carried
    # ``db_snapshot: available=false`` -- while adapter_capability was already returning
    # db_sql to the experiment compiler off the same config. Two parts of one run
    # disagreed about the same capability, and it is the IR that the observer gate
    # reads, so every data-layer assertion blocked as BLOCKED_MISSING_OBSERVER against a
    # database the operator had configured and the product could query.
    #
    # ``available_surfaces`` is a declaration passed in by the caller, never inferred
    # here. Omitting it keeps the previous http-only behaviour, so the failure direction
    # stays "fewer surfaces".
    declared_surfaces = available_surfaces if isinstance(available_surfaces, dict) else None
    # UI design specs ride on the IR: UI-family obligations resolve their
    # page URL and interaction matrices from these specs.
    model["ui_specs"] = [
        {
            **dict(spec),
            "id": _text(spec.get("ui_spec_id"))
            or _stable_id("ui_spec", _text(spec.get("name") or "")),
        }
        for spec in _list(data.get("ui_design_specs"))
        if isinstance(spec, dict) and _text(spec.get("ui_spec_id"))
    ]
    surfaces = [("http_api", "HTTP/API"), ("ui_browser", "Browser/UI"), ("db_snapshot", "DB read snapshot")]
    for surface_id, label in surfaces:
        if declared_surfaces is None:
            is_available = surface_id == "http_api"
            basis = "builder_default"
        else:
            is_available = bool(declared_surfaces.get(surface_id))
            basis = "declared_adapter_capability"
        typed: dict[str, Any] = {
            "surface": surface_id,
            "label": label,
            "available": is_available,
            # _DERIVATIONS is a closed vocabulary and validate_behavior_ir rejects
            # anything outside it, so the declared-vs-default distinction lives in its
            # own field. An operator-declared database is "explicit": the operator
            # stated it, the model did not infer it.
            "availability_basis": basis,
        }
        model["observation_surfaces"].append(_fact_node(
            node_id=_stable_id("surface", surface_id),
            typed_fields=typed,
            confidence=1.0 if is_available else 0.3,
            derivation="explicit" if basis == "declared_adapter_capability" else "schema-derived",
            status="accepted" if is_available else "unknown",
        ))

    _surface_capabilities = [("http_api", "http_execute")]
    if declared_surfaces is not None:
        _surface_capabilities = [
            (surface_id, capability)
            for surface_id, capability in (
                ("http_api", "http_execute"),
                ("db_snapshot", "db_read"),
                ("ui_browser", "ui_execute"),
            )
            if declared_surfaces.get(surface_id)
        ]
    for surface_id, capability in _surface_capabilities:
        model["capabilities"].append(_fact_node(
            node_id=_stable_id("cap", capability),
            typed_fields={"capability": capability, "adapter": surface_id},
            confidence=1.0,
            derivation="explicit" if declared_surfaces is not None else "schema-derived",
        ))

    if not model["operations"]:
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "no_operations"),
            typed_fields={"gap_type": "missing_operations", "description": "No operations derived from sources"},
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))
    if not model["actors"]:
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "no_actors"),
            typed_fields={"gap_type": "missing_actors", "description": "No actors/roles derived from sources"},
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))

    model["relations"] = [normalize_relation(row) for row in _dedupe_nodes(model["relations"])]
    model["coverage_gaps"] = _dedupe_nodes(model["coverage_gaps"])
    model["conflicts"] = _dedupe_nodes(model["conflicts"])

    # ── Chinese Semantic Frame channel (P0-A) ──
    # The asset's frame ledger (qualibug.chinese-semantic-frame-ledger.v1) is
    # the Chinese-semantics SSOT projection. Only GROUNDED frame slots may
    # contribute relations; every contribution is merged by deterministic node
    # id (dedup against legacy relations, never overwriting), provenance rides
    # in source_refs, and the projection receipt is stored on the model.
    # Ungrounded frames (TECHNICAL_GROUNDING_PENDING) contribute nothing — the
    # receipt records the skip instead of guessing.
    _frame_ledger = _dict(data.get("chinese_semantic_frame_ledger"))
    _frames = [
        row for row in _list(_frame_ledger.get("items")) if isinstance(row, dict)
    ]
    if _frames:
        from .enterprise_knowledge_center.enterprise_understanding.chinese_semantic_behavior_ir_adapter import (
            apply_semantic_frames_to_behavior_ir,
        )

        def _frame_relation_builder(contribution: dict[str, Any]) -> dict[str, Any]:
            # Resolve frame endpoints through the node reference index FIRST:
            # frame refs are declared labels (role names / METHOD:path), while
            # legacy relations use canonical node ids. Canonicalizing here
            # makes frame relations dedup against legacy ones instead of
            # creating parallel duplicates with identical endpoints.
            def _resolve(ref: str) -> str:
                value = _text(ref)
                if not value:
                    return ""
                return _frame_index.get(value.lower(), value)

            return _relation_node(
                relation_type=_text(contribution.get("relation_type")),
                from_ref=_resolve(_text(contribution.get("from_ref"))),
                to_ref=_resolve(_text(contribution.get("to_ref"))),
                operation_ref=_resolve(_text(contribution.get("operation_ref"))),
                actor_ref=_resolve(_text(contribution.get("actor_ref"))),
                scope=_text(contribution.get("scope")),
                source_refs=_list(contribution.get("source_refs")),
                confidence=0.8,
                derivation="schema-derived",
                status="accepted",
            )

        _frame_index = _node_reference_index(model)
        _frame_node_ids = {
            _text(row.get("id"))
            for collection in ("entities", "operations", "actors", "states")
            for row in _list(model.get(collection))
            if isinstance(row, dict) and _text(row.get("id"))
        }

        def _frame_ref_resolver(kind: str, ref: str) -> bool:
            lowered = _text(ref).lower()
            return lowered in _frame_index or _text(ref) in _frame_node_ids

        apply_semantic_frames_to_behavior_ir(
            model,
            _frames,
            relation_builder=_frame_relation_builder,
            ref_resolver=_frame_ref_resolver,
        )
        model["relations"] = [
            normalize_relation(row) for row in _dedupe_nodes(model["relations"])
        ]

    model["model_id"] = _content_addressed_id(model)
    # ── P0-E legacy-semantic-fallback receipt ──
    # Every legacy Chinese-text parse product that ran (or was demoted by the
    # frame-confirmation gate) is counted here, so legacy fallback is always
    # observable, never silent.  Attached AFTER the content address so the
    # receipt never rotates model_id — assets without a frame ledger stay
    # byte-identical (compat path; kind_counts only carry NO_FRAME_LEDGER).
    model["legacy_semantic_fallback_receipt"] = {
        "schema": "qualibug.legacy-semantic-fallback-receipt.v1",
        "frame_ledger_present": _frame_ledger_present,
        "used": bool(_fallback_kind_counts),
        "kind_counts": dict(sorted(_fallback_kind_counts.items())),
        "reason_codes": (
            ["LEGACY_FALLBACK_USED"] if _fallback_kind_counts else []
        ),
        "contract": {
            "gate": "frame_confirmation",
            "frame_grounded_wins": True,
            "legacy_fallback_observable": True,
            "no_ledger_behavior_unchanged": True,
        },
    }
    return model


def behavior_ir_summary(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": _text(model.get("schema_version")),
        "model_id": _text(model.get("model_id")),
        "project_id": _text(model.get("project_id")),
        "counts": {
            key: len(_list(model.get(key)))
            for key in (
                "sources", "entities", "operations", "actors", "states",
                "relations", "invariants", "observation_surfaces", "capabilities",
                "conflicts", "coverage_gaps",
            )
        },
        "validation_errors": validate_behavior_ir(model),
    }
