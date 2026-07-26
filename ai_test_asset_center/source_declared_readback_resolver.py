"""Source-Declared Readback Surface Resolver.

V1.2.3 core module. Single responsibility: resolve a source-declared,
identity-bound, field-complete, scope-validated Readback Contract for each
write operation that requires after-state observation.

This module does NOT:
- Execute HTTP requests
- Generate findings
- Compile business rules
- Create synthetic fixtures
- Re-parse enterprise materials
- Establish a parallel binding fact source

Schema: qualibug.source-declared-readback-resolver.v1
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .real_id_resolver import (
    collection_path,
    normalize_path_placeholders,
    path_has_placeholders,
)

SCHEMA_VERSION = "qualibug.source-declared-readback-resolver.v1"
CONTRACT_SCHEMA_VERSION = "qualibug.readback-contract.v1"
RECEIPT_SCHEMA_VERSION = "qualibug.runtime-readback-receipt.v1"

# ── Readback Surface Types (§6) ──
SURFACE_WRITE_RESPONSE = "WRITE_RESPONSE_REPRESENTATION"
SURFACE_IDENTITY_GET = "IDENTITY_GET"
SURFACE_FILTERED_COLLECTION_GET = "FILTERED_COLLECTION_GET"
SURFACE_DATABASE_OBSERVER = "SOURCE_DECLARED_DATABASE_OBSERVER"
# A write observed through the entity it REFERENCES rather than the one it is named
# after. POST /api/payments/pay declares an orderId parameter and moves the order to
# PAID; the payment itself has no GET, so the only readback is GET /api/orders/{id}.
# The domain check on identity GETs rejects this by design, which left every such write
# at READBACK_SOURCE_NOT_DECLARED even though the source declares both halves.
SURFACE_RELATED_ENTITY_GET = "RELATED_ENTITY_IDENTITY_GET"

# Reserved for future expansion (§7)
SURFACE_EVENT = "EVENT_READBACK"
SURFACE_AUDIT_LOG = "AUDIT_LOG_READBACK"
SURFACE_REPORT = "REPORT_READBACK"
SURFACE_SEARCH_INDEX = "SEARCH_INDEX_READBACK"
SURFACE_UI = "UI_READBACK"

ALL_SURFACE_TYPES = frozenset({
    SURFACE_WRITE_RESPONSE,
    SURFACE_IDENTITY_GET,
    SURFACE_FILTERED_COLLECTION_GET,
    SURFACE_DATABASE_OBSERVER,
})

# ── Identity Strategies (§11) ──
IDENTITY_REQUEST_PATH = "REQUEST_PATH_ID"
IDENTITY_REQUEST_BODY = "REQUEST_BODY_ID"
IDENTITY_FIXTURE_OUTPUT = "FIXTURE_OUTPUT_ID"
IDENTITY_WRITE_RESPONSE = "WRITE_RESPONSE_ID"
IDENTITY_BUSINESS_KEY = "CANONICAL_BUSINESS_KEY"
IDENTITY_CORRELATION_KEY = "SOURCE_DECLARED_CORRELATION_KEY"

ALLOWED_IDENTITY_STRATEGIES = frozenset({
    IDENTITY_REQUEST_PATH,
    IDENTITY_REQUEST_BODY,
    IDENTITY_FIXTURE_OUTPUT,
    IDENTITY_WRITE_RESPONSE,
    IDENTITY_BUSINESS_KEY,
    IDENTITY_CORRELATION_KEY,
})

# ── Forbidden Strategies (§11) ──
FORBIDDEN_IDENTITY_STRATEGIES = frozenset({
    "FIRST_RESPONSE_ITEM",
    "LATEST_CREATED_RECORD",
    "MAX_DATABASE_ID",
    "CURRENT_TIMESTAMP_NEAREST",
    "STRING_SIMILARITY_ONLY",
})

# ── Contract Status (§8) ──
STATUS_RESOLVED = "RESOLVED"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_NOT_DECLARED = "NOT_DECLARED"
STATUS_UNSAFE = "UNSAFE"

# ── Fail-Closed Breakpoints (§21) ──
READBACK_SOURCE_NOT_DECLARED = "READBACK_SOURCE_NOT_DECLARED"
READBACK_OPERATION_NOT_BOUND = "READBACK_OPERATION_NOT_BOUND"
READBACK_ENTITY_NOT_BOUND = "READBACK_ENTITY_NOT_BOUND"
READBACK_IDENTITY_NOT_RESOLVED = "READBACK_IDENTITY_NOT_RESOLVED"
READBACK_IDENTITY_AMBIGUOUS = "READBACK_IDENTITY_AMBIGUOUS"
READBACK_FILTER_NOT_DECLARED = "READBACK_FILTER_NOT_DECLARED"
UNBOUNDED_COLLECTION_READBACK = "UNBOUNDED_COLLECTION_READBACK"
READBACK_REQUIRED_FIELD_NOT_EXPOSED = "READBACK_REQUIRED_FIELD_NOT_EXPOSED"
READBACK_SCOPE_BINDING_INCOMPLETE = "READBACK_SCOPE_BINDING_INCOMPLETE"
READBACK_AMBIGUOUS = "READBACK_AMBIGUOUS"
READBACK_UNSAFE = "READBACK_UNSAFE"
READBACK_RUNTIME_PROVENANCE_FAILED = "READBACK_RUNTIME_PROVENANCE_FAILED"
READBACK_SCOPE_MISMATCH = "READBACK_SCOPE_MISMATCH"
READBACK_RESULT_AMBIGUOUS = "READBACK_RESULT_AMBIGUOUS"
READBACK_ASYNC_TIMEOUT = "READBACK_ASYNC_TIMEOUT"

PARENT_BREAKPOINT = "SOURCE_DECLARED_READBACK_NOT_RESOLVED"

# ── Evidence Priority (§9) ──
EVIDENCE_PRIORITY = (
    "api_doc_contract",          # Explicit API documentation / OpenAPI
    "enterprise_requirement",    # Enterprise requirement / interface doc
    "behavior_ir_relation",      # Existing Behavior IR operation relation
    "database_schema_observer",  # Explicit DB schema + governed observer
    "runtime_read_receipt",      # Real runtime read receipt confirmation
    "name_structure_inference",  # Name or structure inference (CANDIDATE only)
)

# Evidence types that may produce RESOLVED status
_RESOLVABLE_EVIDENCE = frozenset({
    "api_doc_contract",
    "enterprise_requirement",
    "behavior_ir_relation",
    "database_schema_observer",
    "runtime_read_receipt",
})

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_COLON_PARAM_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})

# Response fields that indicate a success-only response (not entity state)
_SUCCESS_ONLY_TOKENS = frozenset({
    "success", "message", "msg", "code", "status_code", "ok", "result",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _fingerprint(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _normalize_path(path: str) -> str:
    """Normalize path: convert :param to {param}, strip trailing slash."""
    normalized = normalize_path_placeholders(path)
    return normalized.rstrip("/") if normalized != "/" else normalized


def _extract_path_params(path: str) -> list[str]:
    """Extract parameter names from a path template."""
    normalized = _normalize_path(path)
    return _PLACEHOLDER_RE.findall(normalized)


def _path_segments(path: str) -> list[str]:
    """Split path into non-empty segments."""
    return [s for s in _normalize_path(path).split("/") if s]


def _is_write_method(method: str) -> bool:
    return method.upper() in _WRITE_METHODS


def _is_read_method(method: str) -> bool:
    return method.upper() in _READ_METHODS


# ─────────────────────────────────────────────────────────────────────
# §6.1 Write Response Representation Analysis
# ─────────────────────────────────────────────────────────────────────

def _response_has_entity_state(operation: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check if write operation's declared response contains entity state fields.

    Returns (has_state, exposed_fields).
    A response with only success/message/code is NOT entity state.
    """
    response_schema = _dict(operation.get("response_schema"))
    response_example = _dict(operation.get("response_example"))

    # Collect declared response fields
    fields: set[str] = set()

    # From response schema properties
    properties = _dict(response_schema.get("properties"))
    if not properties:
        content = _dict(response_schema.get("content"))
        for media in content.values():
            if isinstance(media, dict):
                schema = _dict(media.get("schema"))
                properties = _dict(schema.get("properties"))
                if properties:
                    break

    for key in properties:
        fields.add(key)

    # From response example
    if response_example:
        for key in response_example:
            fields.add(key)
        # Check nested 'data' object
        data = _dict(response_example.get("data"))
        if data:
            for key in data:
                fields.add(f"data.{key}")

    # From field_dictionary
    for field_row in _list(operation.get("field_dictionary")):
        if isinstance(field_row, dict):
            fname = _text(field_row.get("name") or field_row.get("field"))
            if fname:
                fields.add(fname)

    if not fields:
        return False, []

    # Check if ONLY success-type fields
    non_success_fields = fields - _SUCCESS_ONLY_TOKENS
    if not non_success_fields:
        return False, []

    # Must have at least one identity or business field
    has_identity = any(
        _is_identity_field(f) for f in non_success_fields
    )
    has_business = len(non_success_fields) >= 2  # At least 2 non-trivial fields

    return (has_identity or has_business), sorted(non_success_fields)


def _is_identity_field(field_name: str) -> bool:
    """Check if a field name looks like an identity field."""
    lower = field_name.lower().split(".")[-1]
    return lower in {"id", "uuid", "guid", "key", "sku", "order_no", "order_id",
                     "user_id", "item_id", "refund_id", "payment_id", "cart_id",
                     "address_id", "coupon_id", "product_id"} or lower.endswith("_id")


# ─────────────────────────────────────────────────────────────────────
# §6.2/6.3 Candidate Discovery from Behavior IR
# ─────────────────────────────────────────────────────────────────────

def _build_operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build operation lookup by id."""
    return {
        _text(op.get("id")): op
        for op in _list(behavior_ir.get("operations"))
        if isinstance(op, dict) and _text(op.get("id"))
    }


def _find_identity_get_candidates(
    write_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find source-declared GET operations that read a single entity by ID.

    An Identity GET must:
    - Be declared in Behavior IR (not guessed)
    - Have exactly one path placeholder
    - Share the same resource domain as the write operation
    """
    write_path = _normalize_path(_text(write_op.get("path") or write_op.get("raw_path")))
    write_segments = _path_segments(write_path)
    if not write_segments:
        return []

    # Determine the resource domain from write path
    # e.g. /api/orders/{id}/cancel -> domain is "orders"
    # e.g. /api/users/admin/users/{id}/balance -> domain is "users"
    write_domain = _extract_resource_domain(write_segments)

    candidates: list[dict[str, Any]] = []
    for op in _list(behavior_ir.get("operations")):
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        if method not in _READ_METHODS:
            continue
        op_path = _normalize_path(_text(op.get("path") or op.get("raw_path")))
        if not op_path or not path_has_placeholders(op_path):
            continue
        params = _extract_path_params(op_path)
        if len(params) != 1:
            continue  # Multi-param GETs are not simple identity reads

        op_segments = _path_segments(op_path)
        op_domain = _extract_resource_domain(op_segments)

        # Must share the same resource domain
        if op_domain and write_domain and op_domain != write_domain:
            continue

        # Must not be the exact same operation (same path AND same method)
        # Note: GET /orders/{id} is a valid readback for PUT /orders/{id}
        write_method = _text(write_op.get("method")).upper()
        if op_path == write_path and method == write_method:
            continue

        candidates.append({
            "operation": op,
            "surface_type": SURFACE_IDENTITY_GET,
            "path": op_path,
            "param": params[0],
            "domain": op_domain,
            "evidence_type": "behavior_ir_relation",
        })

    return candidates


def _referenced_entity_params(write_op: dict[str, Any]) -> dict[str, str]:
    """Declared write parameters that name another entity, as ``{param: entity}``.

    Only DECLARED parameters count. A foreign key the source never declared would not
    have a value at execution time, so binding a readback to it would produce an
    observer that cannot run.
    """
    out: dict[str, str] = {}
    for raw in _list(write_op.get("parameters")) + _list(write_op.get("field_dictionary")):
        name = _text(raw if isinstance(raw, str) else _dict(raw).get("name"))
        if not name or "." in name or "[" in name:
            continue
        lowered = name.lower()
        # camelCase lowercases to one word, so the bare forms are needed: orderId ->
        # "orderid" ends with "id", couponCode -> "couponcode" ends with "code". A bare
        # two-character suffix is not included; "no" would strip "casino" to "casi".
        # A stem this loose is safe because the domain match below is the real gate --
        # a spurious entity name finds no identity GET and produces no candidate.
        for suffix in ("_id", "id", "_ref", "ref", "_no", "_code", "code"):
            if lowered.endswith(suffix) and len(lowered) > len(suffix):
                entity = lowered[: -len(suffix)].strip("_")
                if len(entity) >= 3:
                    out[name] = entity
                break
    return out


def _domain_matches_entity(domain: str, entity: str) -> bool:
    """Whether a path domain such as ``orders`` names the entity ``order``."""
    d = _text(domain).lower().strip("/")
    e = _text(entity).lower()
    if not d or not e:
        return False
    singular = d[:-1] if d.endswith("s") and not d.endswith("ss") else d
    return e in {d, singular}


def _find_related_entity_get_candidates(
    write_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Identity GETs on an entity the write declares a foreign key to.

    Requires all three of: the write declares the foreign-key parameter, the read is a
    single-placeholder identity GET, and the read's domain is the entity that key names.
    A write and a read merely sharing a word is not a relationship -- the declared
    parameter is what makes the identity available at execution time.
    """
    write_path = _normalize_path(_text(write_op.get("path") or write_op.get("raw_path")))
    write_segments = _path_segments(write_path)
    if not write_segments:
        return []
    write_domain = _extract_resource_domain(write_segments)
    referenced = _referenced_entity_params(write_op)
    if not referenced:
        return []

    candidates: list[dict[str, Any]] = []
    for op in _list(behavior_ir.get("operations")):
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() not in _READ_METHODS:
            continue
        op_path = _normalize_path(_text(op.get("path") or op.get("raw_path")))
        if not op_path or not path_has_placeholders(op_path):
            continue
        params = _extract_path_params(op_path)
        if len(params) != 1:
            continue
        op_domain = _extract_resource_domain(_path_segments(op_path))
        if not op_domain:
            continue
        # Same-domain reads are already covered by the identity GET finder; this source
        # exists only for the cross-entity case.
        if _domain_matches_entity(op_domain, write_domain.rstrip("s")):
            continue
        for param_name, entity in referenced.items():
            if not _domain_matches_entity(op_domain, entity):
                continue
            candidates.append({
                "operation": op,
                "surface_type": SURFACE_RELATED_ENTITY_GET,
                "path": op_path,
                "param": params[0],
                "domain": op_domain,
                "evidence_type": "behavior_ir_relation",
                "related_entity": entity,
                "write_parameter": param_name,
            })
            break
    return candidates


def _db_surface_available(behavior_ir: dict[str, Any]) -> bool:
    """Whether the operator declared a database this target may be read through.

    Read from the IR's own observation_surfaces so this answers the same question the
    observer gate does. A database readback is only offered when the operator declared
    the connection -- inferring one would produce an observer that cannot connect.
    """
    for node in _list(_dict(behavior_ir).get("observation_surfaces")):
        row = _dict(node)
        if _text(row.get("surface")) == "db_snapshot":
            return bool(row.get("available"))
    return False


def _find_database_readback_candidates(
    write_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Data-layer readback for a write whose entity has a source-declared table.

    Every leg is source-declared, which is what SURFACE_DATABASE_OBSERVER requires and
    why this is not inference: the table and its identity column come from the ingested
    DDL (they arrive as IR entities carrying ``fields`` and ``identity_fields``), and
    the connection comes from the operator's own declaration.

    This is the only readback available for an entity the API never exposes for reading.
    On the benchmark target the refund endpoints are exactly that case -- the source
    declares no GET for refunds, while ``refunds`` is a real table with a real primary
    key. Without this the assertion cannot be observed at all, and the alternative is
    not a weaker observation but none.
    """
    if not _db_surface_available(behavior_ir):
        return []

    write_path = _normalize_path(_text(write_op.get("path") or write_op.get("raw_path")))
    write_segments = _path_segments(write_path)
    if not write_segments:
        return []
    write_domain = _extract_resource_domain(write_segments)
    if not write_domain:
        return []

    write_path_params = _extract_path_params(write_path)
    referenced = _referenced_entity_params(write_op)

    candidates: list[dict[str, Any]] = []
    for node in _list(_dict(behavior_ir).get("entities")):
        entity = _dict(node)
        table = _text(entity.get("name"))
        if not table:
            continue
        identity_fields = [_text(f) for f in _list(entity.get("identity_fields")) if _text(f)]
        if not identity_fields:
            continue  # no declared key means no way to read one row back

        # The write's own entity, or one it declares a foreign key to.
        own = _domain_matches_entity(table, write_domain.rstrip("s"))
        referenced_param = ""
        if not own:
            for param_name, referenced_entity in referenced.items():
                if _domain_matches_entity(table, referenced_entity):
                    referenced_param = param_name
                    break
            if not referenced_param:
                continue

        if own and write_path_params:
            identity = {
                "type": IDENTITY_REQUEST_PATH,
                "source_path": write_path,
                "target_parameter": identity_fields[0],
                "canonical_field_id": write_path_params[0],
                "status": "RESOLVED",
            }
        elif referenced_param:
            identity = {
                "type": IDENTITY_REQUEST_BODY,
                "source_path": "request." + referenced_param,
                "target_parameter": identity_fields[0],
                "canonical_field_id": referenced_param,
                "status": "RESOLVED",
            }
        else:
            # A create with no identity yet. The write response would have to supply it,
            # which is the write-response surface's job, not this one.
            continue

        candidates.append({
            "operation": {
                "id": "db:" + table,
                "operation_id": "db_select_" + table,
                "method": "SELECT",
                "path": "db://" + table,
                "parameters": [_text(f) for f in _list(entity.get("fields")) if _text(f)],
            },
            "surface_type": SURFACE_DATABASE_OBSERVER,
            "path": "db://" + table,
            "param": identity_fields[0],
            "domain": table,
            # Use the name this module already declares in EVIDENCE_PRIORITY and
            # _RESOLVABLE_EVIDENCE. Inventing a second name for the same evidence class
            # is how two halves of one system start disagreeing.
            "evidence_type": "database_schema_observer",
            "table": table,
            "identity_column": identity_fields[0],
            "_identity_strategy": identity,
        })
    return candidates


def _find_collection_get_candidates(
    write_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find source-declared collection GET operations with filter parameters.

    A Filtered Collection GET must:
    - Be declared in Behavior IR
    - Have NO path placeholders (collection-level)
    - Share the same resource domain
    - Have declared query/filter parameters
    """
    write_path = _normalize_path(_text(write_op.get("path") or write_op.get("raw_path")))
    write_segments = _path_segments(write_path)
    write_domain = _extract_resource_domain(write_segments)

    candidates: list[dict[str, Any]] = []
    for op in _list(behavior_ir.get("operations")):
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        if method not in _READ_METHODS:
            continue
        op_path = _normalize_path(_text(op.get("path") or op.get("raw_path")))
        if not op_path or path_has_placeholders(op_path):
            continue  # Collection GET must not have path params

        op_segments = _path_segments(op_path)
        op_domain = _extract_resource_domain(op_segments)

        if op_domain and write_domain and op_domain != write_domain:
            continue

        # Check for declared filter parameters
        filter_params = _extract_filter_params(op)
        if not filter_params:
            # Unbounded collection read - cannot be used for targeted readback
            candidates.append({
                "operation": op,
                "surface_type": UNBOUNDED_COLLECTION_READBACK,
                "path": op_path,
                "filter_params": [],
                "domain": op_domain,
                "evidence_type": "behavior_ir_relation",
                "blocked": True,
                "block_reason": UNBOUNDED_COLLECTION_READBACK,
            })
        else:
            candidates.append({
                "operation": op,
                "surface_type": SURFACE_FILTERED_COLLECTION_GET,
                "path": op_path,
                "filter_params": filter_params,
                "domain": op_domain,
                "evidence_type": "behavior_ir_relation",
            })

    return candidates


def _extract_resource_domain(segments: list[str]) -> str:
    """Extract the primary resource domain from path segments.

    /api/orders/{id}/cancel -> orders
    /api/users/admin/users/{id}/balance -> users
    /api/inventory/{sku} -> inventory
    """
    # Skip common prefixes
    skip = {"api", "v1", "v2", "v3", "admin", "internal", "public"}
    resource_segments = [
        s for s in segments
        if s.lower() not in skip and not _PLACEHOLDER_RE.fullmatch(s)
    ]
    # The domain is typically the first resource noun
    # For /users/admin/users/{id}/balance, we want "users"
    # For /orders/{id}/cancel, we want "orders"
    if resource_segments:
        # Prefer the segment just before the first placeholder
        for seg in segments:
            if _PLACEHOLDER_RE.fullmatch(seg):
                break
            if seg.lower() not in skip:
                return seg.lower()
        return resource_segments[0].lower()
    return ""


def _extract_filter_params(operation: dict[str, Any]) -> list[str]:
    """Extract declared query/filter parameters from an operation."""
    params: list[str] = []

    # From parameters list
    for param in _list(operation.get("parameters")):
        if isinstance(param, dict):
            if _text(param.get("in")) == "query":
                name = _text(param.get("name"))
                if name:
                    params.append(name)

    # From query_params
    for qp in _list(operation.get("query_params")):
        if isinstance(qp, dict):
            name = _text(qp.get("name"))
            if name:
                params.append(name)
        elif isinstance(qp, str) and qp:
            params.append(qp)

    # From request_example (if it has query-like fields)
    # Only for GET operations where body params are actually query params
    request_example = _dict(operation.get("request_example"))
    if request_example and _text(operation.get("method")).upper() in _READ_METHODS:
        for key in request_example:
            if key not in params:
                params.append(key)

    # From documented query parameters in description
    description = _text(operation.get("description") or operation.get("summary"))
    if description:
        # Look for documented params like "category", "keyword" etc
        param_matches = re.findall(r"[`'](\w+)[`']", description)
        for pm in param_matches:
            if pm not in params and len(pm) > 2:
                params.append(pm)

    return params


# ─────────────────────────────────────────────────────────────────────
# §9 Evidence Priority and Source Refs
# ─────────────────────────────────────────────────────────────────────

def _determine_evidence_type(
    candidate_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> str:
    """Determine the evidence type for a readback candidate."""
    source_refs = _list(candidate_op.get("source_refs"))
    for ref in source_refs:
        if not isinstance(ref, dict):
            continue
        kind = _text(ref.get("kind"))
        source_id = _text(ref.get("source_id"))
        if kind in ("api_operation", "openapi_operation"):
            return "api_doc_contract"
        if kind in ("enterprise_requirement", "interface_spec"):
            return "enterprise_requirement"
        if "api_spec" in source_id or "openapi" in source_id:
            return "api_doc_contract"

    # Check if operation has explicit source documentation
    if candidate_op.get("response_schema") or candidate_op.get("parameters"):
        return "api_doc_contract"

    return "behavior_ir_relation"


def _build_source_refs(
    write_op: dict[str, Any],
    read_op: dict[str, Any],
    evidence_type: str,
) -> list[dict[str, str]]:
    """Build source reference chain for provenance."""
    refs: list[dict[str, str]] = []

    # Write operation source
    write_id = _text(write_op.get("id"))
    if write_id:
        refs.append({
            "source_id": write_id,
            "source_location": f"{_text(write_op.get('method')).upper()} {_text(write_op.get('path'))}",
            "evidence_type": "write_operation",
        })

    # Read operation source
    read_id = _text(read_op.get("id"))
    if read_id:
        refs.append({
            "source_id": read_id,
            "source_location": f"{_text(read_op.get('method')).upper()} {_text(read_op.get('path'))}",
            "evidence_type": evidence_type,
        })

    # Inherit source refs from read operation
    for ref in _list(read_op.get("source_refs"))[:3]:
        if isinstance(ref, dict):
            refs.append({
                "source_id": _text(ref.get("source_id")),
                "source_location": _text(ref.get("locator")),
                "evidence_type": _text(ref.get("kind")) or evidence_type,
            })

    return refs


# ─────────────────────────────────────────────────────────────────────
# §11 Identity Strategy Resolution
# ─────────────────────────────────────────────────────────────────────

def _resolve_identity_strategy(
    write_op: dict[str, Any],
    read_candidate: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Resolve how the target entity identity propagates from write to read.

    Returns identity strategy dict or blocked reason.
    """
    write_method = _text(write_op.get("method")).upper()
    write_path = _normalize_path(_text(write_op.get("path") or write_op.get("raw_path")))
    write_params = _extract_path_params(write_path)
    surface_type = read_candidate.get("surface_type", "")

    # Strategy 1: REQUEST_PATH_ID
    # Write path contains {id} and read path uses the same parameter
    if write_params:
        read_param = read_candidate.get("param", "")
        if surface_type == SURFACE_IDENTITY_GET and read_param:
            # Check if write path param can bind to read path param
            # e.g. PUT /orders/{id} -> GET /orders/{id}
            # e.g. POST /orders/{id}/cancel -> GET /orders/{id}
            if read_param in write_params or _params_compatible(write_params, read_param):
                return {
                    "type": IDENTITY_REQUEST_PATH,
                    "source_path": write_path,
                    "target_parameter": read_param,
                    "canonical_field_id": read_param,
                    "status": "RESOLVED",
                }

    # Strategy 1b: REQUEST_BODY_ID for a referenced entity.
    # The identity is the foreign key the write itself declares, so it is known before
    # the write runs -- which is what makes a genuine before/after read possible.
    if surface_type == SURFACE_RELATED_ENTITY_GET:
        write_parameter = _text(read_candidate.get("write_parameter"))
        if write_parameter:
            return {
                "type": IDENTITY_REQUEST_BODY,
                "source_path": "request." + write_parameter,
                "target_parameter": _text(read_candidate.get("param")),
                "canonical_field_id": write_parameter,
                "status": "RESOLVED",
            }

    # Strategy 2: WRITE_RESPONSE_ID
    # POST creates a resource and response contains the new ID
    if write_method == "POST" and surface_type == SURFACE_IDENTITY_GET:
        has_state, fields = _response_has_entity_state(write_op)
        if has_state:
            id_field = _find_identity_field_in_list(fields)
            if id_field:
                return {
                    "type": IDENTITY_WRITE_RESPONSE,
                    "source_path": f"response.{id_field}",
                    "target_parameter": read_candidate.get("param", "id"),
                    "canonical_field_id": id_field,
                    "status": "RESOLVED",
                }
        # Even without declared response schema, POST to a collection
        # that has a sibling identity GET implies response-bound identity.
        # Match by: exact collection path OR shared resource domain.
        read_path = read_candidate.get("path", "")
        read_collection = _normalize_path(collection_path(read_path))
        write_collection = _normalize_path(collection_path(write_path))
        # Domain-level match: POST /api/products/admin shares domain with
        # GET /api/products/{sku} because both are in the "products" domain.
        read_domain = read_candidate.get("domain", "")
        write_domain = _extract_resource_domain(_path_segments(write_path))
        collection_match = (
            read_collection
            and write_collection
            and (
                read_collection == write_collection
                or write_collection.startswith(read_collection + "/")
                or read_collection.startswith(write_collection + "/")
            )
        )
        domain_match = (
            read_domain and write_domain and read_domain == write_domain
        )
        if (
            (collection_match or domain_match)
            and not path_has_placeholders(write_path)
        ):
            return {
                "type": IDENTITY_WRITE_RESPONSE,
                "source_path": "response.id",
                "target_parameter": read_candidate.get("param", "id"),
                "canonical_field_id": "id",
                "status": "RESOLVED",
            }

    # Strategy 3: REQUEST_BODY_ID
    # Write body contains a field that binds to the read parameter
    request_example = _dict(write_op.get("request_example"))
    request_schema = _dict(write_op.get("request_schema"))
    body_fields = set(request_example.keys()) if request_example else set()
    if not body_fields and request_schema:
        props = _dict(_dict(request_schema.get("properties")))
        body_fields = set(props.keys())

    if surface_type == SURFACE_IDENTITY_GET:
        read_param = read_candidate.get("param", "")
        if read_param and read_param in body_fields:
            return {
                "type": IDENTITY_REQUEST_BODY,
                "source_path": f"body.{read_param}",
                "target_parameter": read_param,
                "canonical_field_id": read_param,
                "status": "RESOLVED",
            }
        # Check for semantic matches (orderId -> order_id, sku -> sku)
        for field in body_fields:
            if _fields_semantically_match(field, read_param):
                return {
                    "type": IDENTITY_REQUEST_BODY,
                    "source_path": f"body.{field}",
                    "target_parameter": read_param,
                    "canonical_field_id": field,
                    "status": "RESOLVED",
                }

    # Strategy 4: CANONICAL_BUSINESS_KEY for filtered collection
    if surface_type == SURFACE_FILTERED_COLLECTION_GET:
        filter_params = read_candidate.get("filter_params", [])
        # Check if any filter param can be bound from write context
        for fp in filter_params:
            # From path params
            if fp in write_params:
                return {
                    "type": IDENTITY_REQUEST_PATH,
                    "source_path": write_path,
                    "target_parameter": fp,
                    "canonical_field_id": fp,
                    "status": "RESOLVED",
                }
            # From body fields
            if fp in body_fields:
                return {
                    "type": IDENTITY_REQUEST_BODY,
                    "source_path": f"body.{fp}",
                    "target_parameter": fp,
                    "canonical_field_id": fp,
                    "status": "RESOLVED",
                }
            # Semantic match
            for field in body_fields:
                if _fields_semantically_match(field, fp):
                    return {
                        "type": IDENTITY_REQUEST_BODY,
                        "source_path": f"body.{field}",
                        "target_parameter": fp,
                        "canonical_field_id": field,
                        "status": "RESOLVED",
                    }
            for wp in write_params:
                if _fields_semantically_match(wp, fp):
                    return {
                        "type": IDENTITY_REQUEST_PATH,
                        "source_path": write_path,
                        "target_parameter": fp,
                        "canonical_field_id": wp,
                        "status": "RESOLVED",
                    }

    # No identity strategy resolved
    return {
        "type": "",
        "status": "BLOCKED",
        "reason": READBACK_IDENTITY_NOT_RESOLVED,
    }


def _params_compatible(write_params: list[str], read_param: str) -> bool:
    """Check if a read parameter is compatible with write path params."""
    for wp in write_params:
        if _fields_semantically_match(wp, read_param):
            return True
    return False


def _fields_semantically_match(field_a: str, field_b: str) -> bool:
    """Check if two field names refer to the same identity concept."""
    if not field_a or not field_b:
        return False
    a = field_a.lower().replace("-", "_").replace(".", "_")
    b = field_b.lower().replace("-", "_").replace(".", "_")
    if a == b:
        return True
    # orderId matches order_id, orderId matches id (in order context)
    a_parts = set(a.replace("_", " ").split())
    b_parts = set(b.replace("_", " ").split())
    # Exact containment
    if a_parts == b_parts:
        return True
    # "id" matches any "*_id" or "*Id"
    if b == "id" and a.endswith("_id"):
        return True
    if a == "id" and b.endswith("_id"):
        return True
    # "sku" matches "sku", "product_sku" etc
    if a in b or b in a:
        return True
    return False


def _find_identity_field_in_list(fields: list[str]) -> str:
    """Find the best identity field from a list of field names."""
    for f in fields:
        if f.lower() == "id":
            return f
    for f in fields:
        if f.lower().endswith("_id") or f.lower().endswith("id"):
            return f
    for f in fields:
        if _is_identity_field(f):
            return f
    return ""


# ─────────────────────────────────────────────────────────────────────
# §14 Conflict Resolution
# ─────────────────────────────────────────────────────────────────────

def _score_candidate(
    candidate: dict[str, Any],
    identity_strategy: dict[str, Any],
    write_op: dict[str, Any],
) -> float:
    """Score a readback candidate for conflict resolution.

    Higher score = better candidate. Dimensions (§14):
    - Source explicitness
    - Identity certainty
    - Field coverage
    - Scope verifiability
    - Runtime cost
    - Safety level
    """
    score = 0.0

    # Evidence type priority
    evidence = candidate.get("evidence_type", "")
    evidence_scores = {
        "api_doc_contract": 40.0,
        "enterprise_requirement": 35.0,
        "behavior_ir_relation": 30.0,
        "database_schema_observer": 25.0,
        "runtime_read_receipt": 20.0,
        "name_structure_inference": 5.0,
    }
    score += evidence_scores.get(evidence, 0.0)

    # Surface type preference
    surface = candidate.get("surface_type", "")
    surface_scores = {
        SURFACE_IDENTITY_GET: 30.0,       # Most precise
        SURFACE_WRITE_RESPONSE: 25.0,     # No extra request
        SURFACE_FILTERED_COLLECTION_GET: 20.0,  # Needs filter
        SURFACE_DATABASE_OBSERVER: 15.0,  # Requires DB access
    }
    score += surface_scores.get(surface, 0.0)

    # Identity certainty
    if identity_strategy.get("status") == "RESOLVED":
        id_type = identity_strategy.get("type", "")
        id_scores = {
            IDENTITY_REQUEST_PATH: 20.0,
            IDENTITY_WRITE_RESPONSE: 18.0,
            IDENTITY_REQUEST_BODY: 16.0,
            IDENTITY_FIXTURE_OUTPUT: 14.0,
            IDENTITY_BUSINESS_KEY: 12.0,
            IDENTITY_CORRELATION_KEY: 10.0,
        }
        score += id_scores.get(id_type, 5.0)
    else:
        score -= 50.0  # Heavily penalize unresolved identity

    # Blocked candidates get zero
    if candidate.get("blocked"):
        score = -100.0

    return score


def _resolve_conflicts(
    candidates: list[dict[str, Any]],
    write_op: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Select the best candidate from multiple options.

    Returns (selected, rejected_with_reasons).
    """
    if not candidates:
        return None, []

    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for cand in candidates:
        identity = cand.get("_identity_strategy", {})
        score = _score_candidate(cand, identity, write_op)
        scored.append((score, cand, identity))

    scored.sort(key=lambda x: -x[0])

    # If top score is negative or zero, no valid candidate
    if scored[0][0] <= 0:
        return None, [
            {"candidate": c, "reason": "score_not_positive"}
            for _, c, _ in scored
        ]

    selected = scored[0][1]
    rejected = [
        {
            "candidate": c,
            "reason": f"lower_score:{s:.1f}_vs_{scored[0][0]:.1f}",
        }
        for s, c, _ in scored[1:]
    ]

    # Check for ambiguity: if top two scores are within 5 points
    if len(scored) >= 2 and scored[0][0] - scored[1][0] < 5.0:
        # Ambiguous - but still select the best one with a warning
        selected["_ambiguity_warning"] = True
        selected["_runner_up_score"] = scored[1][0]

    return selected, rejected


# ─────────────────────────────────────────────────────────────────────
# §8 Readback Contract Construction
# ─────────────────────────────────────────────────────────────────────

def _build_contract(
    write_op: dict[str, Any],
    selected_candidate: dict[str, Any],
    identity_strategy: dict[str, Any],
    required_fields: list[str],
    scope_bindings: dict[str, str],
    source_refs: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a complete Readback Contract (§8)."""
    read_op = _dict(selected_candidate.get("operation"))
    surface_type = selected_candidate.get("surface_type", "")

    contract_id = "rbc_" + _fingerprint({
        "write_op": _text(write_op.get("id")),
        "read_op": _text(read_op.get("id")),
        "surface": surface_type,
        "identity": identity_strategy.get("type", ""),
    })

    contract = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "contract_id": contract_id,
        "version": 1,
        "write_operation_id": _text(write_op.get("id")),
        "target_entity_id": selected_candidate.get("domain", ""),
        "readback_surface_type": surface_type,
        "source_refs": source_refs,
        "read_operation_id": _text(read_op.get("id")),
        "endpoint_template": _text(read_op.get("path") or read_op.get("raw_path")),
        "method": _text(read_op.get("method")).upper(),
        "identity_strategy": identity_strategy,
        "required_fields": [
            {
                "canonical_field_id": field,
                "runtime_path": f"response.{field}",
                "required_for_oracle": True,
            }
            for field in required_fields
        ],
        "scope_bindings": scope_bindings,
        "async_policy": {
            "enabled": False,
            "expected_max_delay_ms": 0,
            "poll_interval_ms": 0,
            "max_attempts": 1,
            "terminal_condition": "immediate",
        },
        "confidence": _compute_confidence(selected_candidate, identity_strategy),
        "status": STATUS_RESOLVED,
        "provenance_fingerprint": "",
    }

    # Compute provenance fingerprint
    contract["provenance_fingerprint"] = _fingerprint({
        "contract_id": contract_id,
        "write_operation_id": contract["write_operation_id"],
        "read_operation_id": contract["read_operation_id"],
        "surface_type": surface_type,
        "identity_type": identity_strategy.get("type", ""),
        "endpoint": contract["endpoint_template"],
    })

    return contract


def _compute_confidence(
    candidate: dict[str, Any],
    identity_strategy: dict[str, Any],
) -> float:
    """Compute confidence score for a resolved contract."""
    base = 0.5
    evidence = candidate.get("evidence_type", "")
    if evidence == "api_doc_contract":
        base = 0.95
    elif evidence == "enterprise_requirement":
        base = 0.9
    elif evidence == "behavior_ir_relation":
        base = 0.8
    elif evidence == "database_schema_observer":
        base = 0.75

    # Identity bonus
    if identity_strategy.get("status") == "RESOLVED":
        id_type = identity_strategy.get("type", "")
        if id_type == IDENTITY_REQUEST_PATH:
            base = min(1.0, base + 0.05)
        elif id_type == IDENTITY_WRITE_RESPONSE:
            base = min(1.0, base + 0.03)

    return round(base, 3)


# ─────────────────────────────────────────────────────────────────────
# Main Public API
# ─────────────────────────────────────────────────────────────────────

def resolve_readback_contract(
    write_operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    required_fields: list[str] | None = None,
    scope_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a source-declared Readback Contract for a write operation.

    This is the main entry point. It:
    1. Collects readback candidates from Behavior IR
    2. Resolves identity propagation for each
    3. Validates required field coverage
    4. Resolves conflicts
    5. Returns a complete contract or a blocked reason

    Returns:
        {
            "status": "RESOLVED" | blocked_reason_code,
            "contract": {...} | None,
            "candidates_analyzed": int,
            "conflict_receipt": {...} | None,
            "block_reason": str,
        }
    """
    write_op = _dict(write_operation)
    write_method = _text(write_op.get("method")).upper()
    write_path = _normalize_path(_text(write_op.get("path") or write_op.get("raw_path")))

    if not write_path:
        return _blocked_result(READBACK_OPERATION_NOT_BOUND, 0)

    if not _is_write_method(write_method):
        return _blocked_result(READBACK_SOURCE_NOT_DECLARED, 0)

    # Step 1: Collect candidates
    all_candidates: list[dict[str, Any]] = []

    # 1a: Check write response representation (§6.1)
    has_state, exposed_fields = _response_has_entity_state(write_op)
    if has_state and exposed_fields:
        all_candidates.append({
            "operation": write_op,
            "surface_type": SURFACE_WRITE_RESPONSE,
            "path": write_path,
            "exposed_fields": exposed_fields,
            "evidence_type": _determine_evidence_type(write_op, behavior_ir),
            "domain": _extract_resource_domain(_path_segments(write_path)),
        })

    # 1b: Identity GET candidates (§6.2)
    identity_candidates = _find_identity_get_candidates(write_op, behavior_ir)
    all_candidates.extend(identity_candidates)

    # 1c: Filtered Collection GET candidates (§6.3)
    collection_candidates = _find_collection_get_candidates(write_op, behavior_ir)
    all_candidates.extend(collection_candidates)

    # 1d: Relation-declared observers from Behavior IR
    relation_candidates = _find_relation_declared_observers(write_op, behavior_ir)
    all_candidates.extend(relation_candidates)

    # 1e: Identity GETs on an entity this write declares a foreign key to. Collected
    # last so a same-domain readback always outranks a cross-entity one.
    all_candidates.extend(_find_related_entity_get_candidates(write_op, behavior_ir))

    # 1f: Data-layer readback, last of all. Its cost weight already ranks it below every
    # HTTP surface, so it is reached only when the API declares no read at all.
    all_candidates.extend(_find_database_readback_candidates(write_op, behavior_ir))

    if not all_candidates:
        return _blocked_result(READBACK_SOURCE_NOT_DECLARED, 0)

    # Step 2: Resolve identity for each candidate
    viable_candidates: list[dict[str, Any]] = []
    for cand in all_candidates:
        if cand.get("blocked"):
            continue
        pre_resolved = _dict(cand.get("_identity_strategy"))
        if pre_resolved.get("status") == "RESOLVED":
            # A schema-derived candidate carries its own identity: the table's declared
            # key column paired with the write's declared parameter. Re-deriving it
            # through the HTTP path strategies would fail and discard a viable readback.
            viable_candidates.append(cand)
            continue
        identity = _resolve_identity_strategy(write_op, cand, behavior_ir)
        cand["_identity_strategy"] = identity
        if identity.get("status") == "RESOLVED":
            viable_candidates.append(cand)
        elif cand.get("surface_type") == SURFACE_WRITE_RESPONSE:
            # Write response doesn't need separate identity resolution
            # if it contains identity fields
            if has_state:
                cand["_identity_strategy"] = {
                    "type": IDENTITY_WRITE_RESPONSE,
                    "source_path": "response",
                    "target_parameter": "",
                    "canonical_field_id": _find_identity_field_in_list(exposed_fields),
                    "status": "RESOLVED",
                }
                viable_candidates.append(cand)

    if not viable_candidates:
        # Check if there were candidates but identity failed
        if all_candidates:
            return _blocked_result(READBACK_IDENTITY_NOT_RESOLVED, len(all_candidates))
        return _blocked_result(READBACK_SOURCE_NOT_DECLARED, len(all_candidates))

    # Step 3: Resolve conflicts (§14)
    selected, rejected = _resolve_conflicts(viable_candidates, write_op)
    if not selected:
        return _blocked_result(READBACK_AMBIGUOUS, len(viable_candidates))

    # Step 4: Validate required fields (§12)
    oracle_fields = required_fields or []
    selected_identity = selected.get("_identity_strategy", {})
    source_refs = _build_source_refs(
        write_op,
        _dict(selected.get("operation")),
        selected.get("evidence_type", "behavior_ir_relation"),
    )

    # Build scope bindings (§13)
    scope_bindings = _build_scope_bindings(write_op, selected, scope_context or {})

    # Build contract
    contract = _build_contract(
        write_op,
        selected,
        selected_identity,
        oracle_fields,
        scope_bindings,
        source_refs,
    )

    # Mark ambiguity if present
    if selected.get("_ambiguity_warning"):
        contract["status"] = STATUS_AMBIGUOUS
        contract["_ambiguity_note"] = "Multiple candidates with similar scores"

    conflict_receipt = None
    if rejected:
        conflict_receipt = {
            "write_operation_id": _text(write_op.get("id")),
            "candidates_total": len(all_candidates),
            "viable_count": len(viable_candidates),
            "selected_candidate": contract["contract_id"],
            "rejected_candidates": [
                {
                    "surface_type": r["candidate"].get("surface_type", ""),
                    "path": r["candidate"].get("path", ""),
                    "reason": r["reason"],
                }
                for r in rejected
            ],
            "resolution_status": "RESOLVED" if not selected.get("_ambiguity_warning") else "AMBIGUOUS",
        }

    return {
        "status": contract["status"],
        "contract": contract,
        "candidates_analyzed": len(all_candidates),
        "viable_candidates": len(viable_candidates),
        "conflict_receipt": conflict_receipt,
        "block_reason": "",
    }


def resolve_readback_for_obligations(
    obligations: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Batch-resolve readback contracts for a list of obligations.

    Returns a ledger with per-obligation results and summary statistics.
    """
    ops_by_id = _build_operation_index(behavior_ir)
    results: list[dict[str, Any]] = []
    resolved_count = 0
    blocked_count = 0
    contracts: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        obl_id = _text(obl.get("obligation_id"))
        op_refs = _list(obl.get("operation_refs"))

        obl_result = {
            "obligation_id": obl_id,
            "operation_refs": op_refs,
            "readback_status": "",
            "contract_id": "",
            "block_reason": "",
        }

        # Resolve for primary operation
        primary_op_ref = _text(op_refs[0]) if op_refs else ""
        primary_op = ops_by_id.get(primary_op_ref, {})

        if not primary_op:
            obl_result["readback_status"] = "BLOCKED"
            obl_result["block_reason"] = READBACK_OPERATION_NOT_BOUND
            blocked_count += 1
        else:
            result = resolve_readback_contract(
                primary_op,
                behavior_ir=behavior_ir,
            )
            if result["status"] == STATUS_RESOLVED:
                obl_result["readback_status"] = "RESOLVED"
                obl_result["contract_id"] = _text(
                    _dict(result.get("contract")).get("contract_id")
                )
                resolved_count += 1
                contracts.append(result["contract"])
                if result.get("conflict_receipt"):
                    conflicts.append(result["conflict_receipt"])
            else:
                obl_result["readback_status"] = "BLOCKED"
                obl_result["block_reason"] = result.get("block_reason", PARENT_BREAKPOINT)
                blocked_count += 1

        results.append(obl_result)

    return {
        "schema_version": SCHEMA_VERSION,
        "total_obligations": len(obligations),
        "resolved_count": resolved_count,
        "blocked_count": blocked_count,
        "resolution_rate": round(resolved_count / max(1, len(obligations)), 4),
        "results": results,
        "contracts": contracts,
        "conflicts": conflicts,
    }


# ─────────────────────────────────────────────────────────────────────
# Runtime Readback Receipt (§18)
# ─────────────────────────────────────────────────────────────────────

def build_runtime_readback_receipt(
    *,
    experiment_id: str,
    write_execution_id: str,
    readback_execution_id: str,
    contract: dict[str, Any],
    identity_source: str,
    resolved_identity: str,
    request: dict[str, Any],
    response: dict[str, Any],
    scope_validation: dict[str, Any],
    field_extraction: dict[str, Any],
    async_attempts: int = 0,
) -> dict[str, Any]:
    """Build a runtime readback receipt (§18)."""
    planned_fingerprint = _text(contract.get("provenance_fingerprint"))
    runtime_fingerprint = _fingerprint({
        "contract_id": _text(contract.get("contract_id")),
        "read_operation_id": _text(contract.get("read_operation_id")),
        "endpoint": _text(contract.get("endpoint_template")),
        "identity": resolved_identity,
    })

    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "write_execution_id": write_execution_id,
        "readback_execution_id": readback_execution_id,
        "readback_contract_id": _text(contract.get("contract_id")),
        "write_operation_id": _text(contract.get("write_operation_id")),
        "read_operation_id": _text(contract.get("read_operation_id")),
        "identity_source": identity_source,
        "resolved_identity": resolved_identity,
        "request": request,
        "response": response,
        "scope_validation": scope_validation,
        "field_extraction": field_extraction,
        "async_attempts": async_attempts,
        "provenance": {
            "planned_contract_fingerprint": planned_fingerprint,
            "runtime_contract_fingerprint": runtime_fingerprint,
            "match": planned_fingerprint == runtime_fingerprint,
        },
        "final_status": "COMPLETE",
    }


# ─────────────────────────────────────────────────────────────────────
# §19 Three-Way Provenance Verification
# ─────────────────────────────────────────────────────────────────────

def verify_readback_provenance(
    source_contract: dict[str, Any],
    compiled_observer: dict[str, Any],
    runtime_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Verify three-way provenance: Source Contract = Compiled Observer = Runtime Receipt.

    Compares at minimum:
    - operation_id
    - method
    - endpoint_template
    - identity_binding
    - target_entity
    - required_fields
    - tenant/scope
    """
    mismatches: list[str] = []

    # Operation ID
    src_op = _text(source_contract.get("read_operation_id"))
    obs_op = _text(compiled_observer.get("operation_id") or compiled_observer.get("read_operation_ref"))
    rcpt_op = _text(runtime_receipt.get("read_operation_id"))
    if not (src_op == obs_op == rcpt_op):
        mismatches.append(f"operation_id: source={src_op} observer={obs_op} receipt={rcpt_op}")

    # Endpoint
    src_endpoint = _normalize_path(_text(source_contract.get("endpoint_template")))
    obs_endpoint = _normalize_path(_text(compiled_observer.get("endpoint") or compiled_observer.get("read_path")))
    if src_endpoint and obs_endpoint and src_endpoint != obs_endpoint:
        mismatches.append(f"endpoint: source={src_endpoint} observer={obs_endpoint}")

    # Identity
    src_identity = _text(_dict(source_contract.get("identity_strategy")).get("type"))
    rcpt_identity = _text(runtime_receipt.get("identity_source"))
    if src_identity and rcpt_identity and src_identity != rcpt_identity:
        mismatches.append(f"identity: source={src_identity} receipt={rcpt_identity}")

    # Contract fingerprint
    src_fp = _text(source_contract.get("provenance_fingerprint"))
    rcpt_fp = _text(_dict(runtime_receipt.get("provenance")).get("planned_contract_fingerprint"))
    if src_fp and rcpt_fp and src_fp != rcpt_fp:
        mismatches.append(f"fingerprint: source={src_fp} receipt={rcpt_fp}")

    verified = len(mismatches) == 0
    return {
        "verified": verified,
        "status": "PROVENANCE_VERIFIED" if verified else READBACK_RUNTIME_PROVENANCE_FAILED,
        "mismatches": mismatches,
        "source_contract_id": _text(source_contract.get("contract_id")),
        "runtime_receipt_id": _text(runtime_receipt.get("readback_execution_id")),
    }


# ─────────────────────────────────────────────────────────────────────
# Internal Helpers
# ─────────────────────────────────────────────────────────────────────

def _find_relation_declared_observers(
    write_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find observers declared through Behavior IR relations (observes/scopes)."""
    write_id = _text(write_op.get("id"))
    if not write_id:
        return []

    ops_by_id = _build_operation_index(behavior_ir)
    candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for relation in _list(behavior_ir.get("relations")):
        if not isinstance(relation, dict):
            continue
        rel_type = _text(relation.get("relation_type"))
        if rel_type not in ("observes", "scopes", "produces", "consumes"):
            continue

        # Check if this relation involves our write operation
        refs = {
            _text(relation.get("operation_ref")),
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
        }
        if write_id not in refs:
            continue

        # Find the observer operation
        for ref in refs:
            if not ref or ref == write_id:
                continue
            obs_op = ops_by_id.get(ref)
            if not obs_op:
                continue
            method = _text(obs_op.get("method")).upper()
            if method not in _READ_METHODS:
                continue
            obs_path = _normalize_path(_text(obs_op.get("path") or obs_op.get("raw_path")))
            if obs_path in seen_paths:
                continue
            seen_paths.add(obs_path)

            params = _extract_path_params(obs_path)
            surface = SURFACE_IDENTITY_GET if len(params) == 1 else SURFACE_FILTERED_COLLECTION_GET
            candidates.append({
                "operation": obs_op,
                "surface_type": surface,
                "path": obs_path,
                "param": params[0] if len(params) == 1 else "",
                "filter_params": _extract_filter_params(obs_op) if not params else [],
                "domain": _extract_resource_domain(_path_segments(obs_path)),
                "evidence_type": "behavior_ir_relation",
            })

    return candidates


def _build_scope_bindings(
    write_op: dict[str, Any],
    candidate: dict[str, Any],
    scope_context: dict[str, Any],
) -> dict[str, str]:
    """Build scope bindings for tenant/owner/resource validation."""
    bindings: dict[str, str] = {}

    # Extract tenant/owner from scope context
    if scope_context.get("tenant_field"):
        bindings["tenant_field"] = _text(scope_context.get("tenant_field"))
    if scope_context.get("owner_field"):
        bindings["owner_field"] = _text(scope_context.get("owner_field"))
    if scope_context.get("actor_ref"):
        bindings["actor_ref"] = _text(scope_context.get("actor_ref"))

    # Resource identity from write path
    write_path = _normalize_path(_text(write_op.get("path") or write_op.get("raw_path")))
    params = _extract_path_params(write_path)
    if params:
        bindings["resource_field"] = params[-1]  # Last param is typically the resource ID

    return bindings


def _blocked_result(reason: str, candidates_analyzed: int) -> dict[str, Any]:
    """Build a blocked result."""
    return {
        "status": reason,
        "contract": None,
        "candidates_analyzed": candidates_analyzed,
        "viable_candidates": 0,
        "conflict_receipt": None,
        "block_reason": reason,
    }
