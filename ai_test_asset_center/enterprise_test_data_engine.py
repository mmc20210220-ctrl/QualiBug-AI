"""Enterprise test data generation engine — schema-driven, industry-agnostic.

Generates realistic, API-ready request bodies WITHOUT relying on documented
examples (which are illustrative only in real enterprise APIs). Every value
is derived from field-name semantics, schema constraints, and business context.

Schema: qualibug.enterprise-test-data-engine.v1

Key principles:
- Documented examples are format hints only — NEVER assume they are usable values
- Every generated value must be plausible for the field's semantic type
- Foreign-key fields are detected and resolved through dependency chains
- Industry-agnostic: no hardcoded paths, entity names, or business rules
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import string
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Any


SCHEMA_VERSION = "qualibug.enterprise-test-data-engine.v1"

# ── Helpers ──────────────────────────────────────────────────────────────

def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []

def _text(value: Any) -> str:
    return str(value or "").strip()


# ── Field Semantic Detection ─────────────────────────────────────────────

# Maps field-name patterns to semantic types. Used to generate realistic
# values rather than relying on documented examples.
_FIELD_SEMANTICS: dict[str, dict[str, Any]] = {
    # ── Identity fields ──
    "id": {"kind": "identity", "generator": "uuid"},
    "uuid": {"kind": "identity", "generator": "uuid"},
    "guid": {"kind": "identity", "generator": "uuid"},
    "key": {"kind": "identity", "generator": "uuid_short"},

    # ── Person / contact ──
    "email": {"kind": "contact", "generator": "email"},
    "phone": {"kind": "contact", "generator": "phone"},
    "mobile": {"kind": "contact", "generator": "phone"},
    "telephone": {"kind": "contact", "generator": "phone"},
    "name": {"kind": "name", "generator": "full_name"},
    "first_name": {"kind": "name", "generator": "first_name"},
    "last_name": {"kind": "name", "generator": "last_name"},
    "username": {"kind": "name", "generator": "username"},
    "nickname": {"kind": "name", "generator": "username"},
    "fullname": {"kind": "name", "generator": "full_name"},

    # ── Address ──
    "address": {"kind": "address", "generator": "street_address"},
    "city": {"kind": "address", "generator": "city"},
    "country": {"kind": "address", "generator": "country_code"},
    "zip": {"kind": "address", "generator": "zip_code"},
    "postcode": {"kind": "address", "generator": "zip_code"},
    "postal_code": {"kind": "address", "generator": "zip_code"},

    # ── Monetary ──
    "amount": {"kind": "money", "generator": "amount"},
    "price": {"kind": "money", "generator": "amount"},
    "total": {"kind": "money", "generator": "amount"},
    "balance": {"kind": "money", "generator": "amount"},
    "fee": {"kind": "money", "generator": "amount"},
    "tax": {"kind": "money", "generator": "amount"},
    "discount": {"kind": "money", "generator": "amount_small"},
    "cost": {"kind": "money", "generator": "amount"},

    # ── Quantity ──
    "quantity": {"kind": "quantity", "generator": "small_int"},
    "qty": {"kind": "quantity", "generator": "small_int"},
    "count": {"kind": "quantity", "generator": "small_int"},
    "stock": {"kind": "quantity", "generator": "medium_int"},
    "inventory": {"kind": "quantity", "generator": "medium_int"},

    # ── Dates ──
    "created_at": {"kind": "datetime", "generator": "past_datetime"},
    "updated_at": {"kind": "datetime", "generator": "past_datetime"},
    "date": {"kind": "date", "generator": "recent_date"},
    "start_date": {"kind": "date", "generator": "future_date"},
    "end_date": {"kind": "date", "generator": "future_date"},
    "expiry": {"kind": "date", "generator": "future_date"},
    "expiration": {"kind": "date", "generator": "future_date"},
    "timestamp": {"kind": "datetime", "generator": "recent_datetime"},

    # ── Status / type ──
    "status": {"kind": "enum_like", "generator": "status"},
    "state": {"kind": "enum_like", "generator": "status"},
    "type": {"kind": "enum_like", "generator": "type"},
    "kind": {"kind": "enum_like", "generator": "type"},
    "category": {"kind": "enum_like", "generator": "category"},
    "role": {"kind": "enum_like", "generator": "role"},
    "gender": {"kind": "enum_like", "generator": "gender"},

    # ── Codes ──
    "code": {"kind": "code", "generator": "alphanumeric_code"},
    "sku": {"kind": "code", "generator": "sku"},
    "coupon": {"kind": "code", "generator": "coupon_code"},
    "voucher": {"kind": "code", "generator": "alphanumeric_code"},
    "reference": {"kind": "code", "generator": "reference_number"},
    "ref": {"kind": "code", "generator": "reference_number"},

    # ── Descriptions ──
    "description": {"kind": "text", "generator": "sentence"},
    "note": {"kind": "text", "generator": "sentence"},
    "remark": {"kind": "text", "generator": "sentence"},
    "comment": {"kind": "text", "generator": "sentence"},
    "reason": {"kind": "text", "generator": "reason"},
    "title": {"kind": "text", "generator": "title"},
    "subject": {"kind": "text", "generator": "title"},

    # ── URLs / URIs ──
    "url": {"kind": "web", "generator": "url"},
    "website": {"kind": "web", "generator": "url"},
    "image": {"kind": "web", "generator": "image_url"},
    "avatar": {"kind": "web", "generator": "image_url"},
    "logo": {"kind": "web", "generator": "image_url"},

    # ── Boolean ──
    "active": {"kind": "boolean", "generator": "active"},
    "enabled": {"kind": "boolean", "generator": "active"},
    "verified": {"kind": "boolean", "generator": "active"},
    "deleted": {"kind": "boolean", "generator": "deleted"},
    "archived": {"kind": "boolean", "generator": "deleted"},

    # ── Password / secret ──
    "password": {"kind": "secret", "generator": "password"},
    "secret": {"kind": "secret", "generator": "token"},
    "token": {"kind": "secret", "generator": "token"},
    "api_key": {"kind": "secret", "generator": "token"},
}


def _detect_field_semantic(field_name: str) -> dict[str, Any]:
    """Detect the semantic type of a field from its name.

    Returns {"kind": ..., "generator": ..., "is_fk": bool}
    """
    normalized = re.sub(r"[_\s-]+", "_", _text(field_name).lower()).strip("_")

    # Direct match
    if normalized in _FIELD_SEMANTICS:
        result = dict(_FIELD_SEMANTICS[normalized])
        result["is_fk"] = _is_foreign_key_field(normalized)
        return result

    # Suffix match: xxx_id, xxxId → FK
    if normalized.endswith("_id") or normalized.endswith("id"):
        base = normalized[:-3] if normalized.endswith("_id") else normalized[:-2]
        if base in _FIELD_SEMANTICS:
            result = dict(_FIELD_SEMANTICS[base])
        else:
            result = {"kind": "identity", "generator": "uuid"}
        result["is_fk"] = True
        result["fk_entity"] = base
        return result

    # Contains match
    for key, value in _FIELD_SEMANTICS.items():
        if key in normalized:
            result = dict(value)
            result["is_fk"] = _is_foreign_key_field(normalized)
            return result

    # Default
    return {"kind": "unknown", "generator": "string", "is_fk": False}


def _is_foreign_key_field(normalized_name: str) -> bool:
    """Detect if a field is likely a foreign key reference."""
    fk_patterns = [
        r".+_id$", r".+id$", r".+_ref$", r".+_code$",
        r"^fk_.+", r".+_fk$",
    ]
    return any(re.match(p, normalized_name) for p in fk_patterns)


# ── Value Generators ────────────────────────────────────────────────────

_SEED_COUNTER: int = 0

def _next_seed() -> int:
    global _SEED_COUNTER
    _SEED_COUNTER += 1
    return _SEED_COUNTER


def _generate_value(generator: str, field_name: str = "", schema: dict | None = None) -> Any:
    """Generate a realistic value for the given generator type."""
    seed = _next_seed()
    schema = _dict(schema) if schema else {}

    # ── Identity generators ──
    if generator == "uuid":
        return str(_uuid.uuid4())
    if generator == "uuid_short":
        return _uuid.uuid4().hex[:12]
    if generator == "numeric_id":
        return 10000 + seed

    # ── Contact generators ──
    if generator == "email":
        return f"qb_test_{seed}@enterprise.test"
    if generator == "phone":
        prefixes = ["138", "139", "150", "186", "188"]
        return f"{random.choice(prefixes)}{seed:08d}"[:11]

    # ── Name generators ──
    if generator == "full_name":
        firsts = ["Zhang", "Wang", "Li", "Liu", "Chen", "Yang", "Zhao", "Huang"]
        lasts = ["Wei", "Fang", "Min", "Jie", "Lei", "Tao", "Na", "Peng"]
        return f"{random.choice(firsts)}{random.choice(lasts)}"
    if generator == "first_name":
        return random.choice(["Zhang", "Wang", "Li", "Liu", "Chen"])
    if generator == "last_name":
        return random.choice(["Wei", "Fang", "Min", "Jie", "Lei"])
    if generator == "username":
        return f"testuser_{seed}"

    # ── Address generators ──
    if generator == "street_address":
        return f"{seed} Test Street"
    if generator == "city":
        return random.choice(["Beijing", "Shanghai", "Shenzhen", "Hangzhou", "Chengdu"])
    if generator == "country_code":
        return random.choice(["CN", "US", "JP", "KR", "GB"])
    if generator == "zip_code":
        return f"{seed:06d}"[:6]

    # ── Money generators ──
    if generator == "amount":
        return round(random.uniform(10, 1000) + seed, 2)
    if generator == "amount_small":
        return round(random.uniform(1, 50), 2)

    # ── Quantity generators ──
    if generator == "small_int":
        return random.randint(1, 10)
    if generator == "medium_int":
        return random.randint(10, 1000)

    # ── Date generators ──
    if generator == "past_datetime":
        return (datetime.now() - timedelta(days=random.randint(1, 365))).isoformat()
    if generator == "recent_date":
        return (datetime.now() - timedelta(days=random.randint(0, 30))).strftime("%Y-%m-%d")
    if generator == "future_date":
        return (datetime.now() + timedelta(days=random.randint(1, 90))).strftime("%Y-%m-%d")
    if generator == "recent_datetime":
        return datetime.now().isoformat()

    # ── Status / enum-like generators ──
    if generator == "status":
        return random.choice(["ACTIVE", "active", "PENDING", "COMPLETED"])
    if generator == "type":
        return random.choice(["STANDARD", "PREMIUM", "BASIC"])
    if generator == "category":
        return random.choice(["ELECTRONICS", "CLOTHING", "FOOD"])
    if generator == "role":
        return random.choice(["USER", "ADMIN", "MANAGER"])
    if generator == "gender":
        return random.choice(["MALE", "FEMALE", "OTHER"])

    # ── Code generators ──
    if generator == "alphanumeric_code":
        charset = string.ascii_uppercase + string.digits
        return "".join(random.choice(charset) for _ in range(8))
    if generator == "sku":
        return f"SKU-{_uuid.uuid4().hex[:8].upper()}"
    if generator == "coupon_code":
        return f"CP{_uuid.uuid4().hex[:6].upper()}"
    if generator == "reference_number":
        return f"REF-{datetime.now().strftime('%Y%m%d')}-{seed:06d}"

    # ── Text generators ──
    if generator == "sentence":
        return f"Auto-generated test {field_name} for validation #{seed}"
    if generator == "reason":
        return f"Automated enterprise test execution #{seed}"
    if generator == "title":
        return f"Test {field_name.replace('_', ' ').title()} {seed}"

    # ── Web generators ──
    if generator == "url":
        return f"https://test-{seed}.enterprise.test"
    if generator == "image_url":
        return f"https://test-{seed}.enterprise.test/img/{_uuid.uuid4().hex[:8]}.png"

    # ── Boolean generators ──
    if generator == "active":
        return True
    if generator == "deleted":
        return False

    # ── Secret generators ──
    if generator == "password":
        return f"Test@{seed:06d}!"
    if generator == "token":
        return _uuid.uuid4().hex + _uuid.uuid4().hex

    # ── Default ──
    return f"qb_auto_{field_name}_{seed}"


# ── Schema-Driven Body Generation ───────────────────────────────────────

def _schema_property_type(prop_schema: dict[str, Any]) -> str:
    """Extract the JSON Schema type from a property definition."""
    type_val = _text(prop_schema.get("type", "")).lower()
    if type_val:
        return type_val
    # Check for anyOf/oneOf
    for key in ("anyOf", "oneOf"):
        options = prop_schema.get(key)
        if isinstance(options, list) and options:
            first = options[0]
            if isinstance(first, dict):
                return _text(first.get("type", "")).lower()
    return "string"


def _generate_field_value(
    field_name: str,
    prop_schema: dict[str, Any],
) -> Any:
    """Generate a realistic, API-ready value for a single field.

    Priority:
    1. Schema enum (pick first valid value)
    2. Schema const
    3. Schema default
    4. Semantic-based generation (field-name heuristics)
    5. Type-based generation (from JSON Schema type)
    """
    # 1. Enum
    enum_vals = prop_schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        return enum_vals[0]

    # 2. Const
    if "const" in prop_schema:
        return prop_schema["const"]

    # 3. Default
    if "default" in prop_schema:
        return prop_schema["default"]

    # 4. Semantic-based generation
    semantic = _detect_field_semantic(field_name)
    if semantic["kind"] != "unknown":
        return _generate_value(semantic["generator"], field_name, prop_schema)

    # 5. Type-based generation
    type_val = _schema_property_type(prop_schema)

    if type_val == "integer":
        minimum = prop_schema.get("minimum", 1)
        return int(minimum) if minimum is not None else _generate_value("numeric_id", field_name)
    if type_val == "number":
        return _generate_value("amount", field_name)
    if type_val == "boolean":
        return True
    if type_val == "array":
        items = prop_schema.get("items", {})
        min_items = prop_schema.get("minItems", 1)
        if isinstance(items, dict) and items.get("type") == "object":
            return [generate_request_body(items) for _ in range(min(int(min_items), 3))]
        return []
    if type_val == "object":
        return generate_request_body(prop_schema)

    # 6. String: check format
    format_val = _text(prop_schema.get("format", "")).lower()
    if format_val == "email":
        return _generate_value("email", field_name)
    if format_val in ("uri", "url"):
        return _generate_value("url", field_name)
    if format_val == "date":
        return _generate_value("recent_date", field_name)
    if format_val in ("date-time", "datetime"):
        return _generate_value("recent_datetime", field_name)
    if format_val == "uuid":
        return str(_uuid.uuid4())
    if format_val == "phone":
        return _generate_value("phone", field_name)

    # String: check pattern/minLength
    min_len = prop_schema.get("minLength", 1)
    return _generate_value("string", field_name)[:max(int(min_len), 3)]


def generate_request_body(schema: dict[str, Any], *, include_optional: bool = True) -> dict[str, Any]:
    """Generate a complete, API-ready request body from a JSON Schema.

    Args:
        schema: JSON Schema object with 'properties' and optional 'required'.
        include_optional: If True, also populate non-required fields.

    Returns a dict ready to send as an HTTP request body. NEVER uses
    documented examples as values — they are illustrative only.
    """
    if not isinstance(schema, dict):
        return {}
    properties = _dict(schema.get("properties", {}))
    if not properties:
        return {}
    required = {_text(v) for v in (schema.get("required") or []) if _text(v)}
    body: dict[str, Any] = {}
    for field_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        if field_name not in required and not include_optional:
            continue
        body[field_name] = _generate_field_value(field_name, prop_schema)
    return body


# ── Dependency-Aware Body Generation ─────────────────────────────────────

def detect_foreign_key_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect fields that are likely foreign keys requiring prerequisite resources.

    Returns list of {"field": name, "fk_entity": entity_name, "schema": prop_schema}
    """
    if not isinstance(schema, dict):
        return []
    properties = _dict(schema.get("properties", {}))
    fk_fields: list[dict[str, Any]] = []
    for field_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        semantic = _detect_field_semantic(field_name)
        if semantic.get("is_fk"):
            fk_fields.append({
                "field": field_name,
                "fk_entity": semantic.get("fk_entity", field_name.replace("_id", "").replace("Id", "")),
                "schema": prop_schema,
                "semantic": semantic,
            })
    return fk_fields


def generate_body_with_fk_resolution(
    schema: dict[str, Any],
    fk_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a request body, substituting resolved FK values where available.

    Args:
        schema: JSON Schema for the request body.
        fk_values: Dict mapping field_name → resolved value for FK fields.

    Returns a body with FK fields populated from fk_values, other fields
    auto-generated.
    """
    body = generate_request_body(schema)
    if fk_values:
        for field_name, value in fk_values.items():
            if field_name in body and value is not None:
                body[field_name] = value
    return body


# ── Response ID Extraction ──────────────────────────────────────────────

def extract_resource_id(response_body: Any, entity_name: str = "") -> str | None:
    """Extract a resource identifier from an API creation response.

    Handles common enterprise API patterns:
    - {"id": "xxx"}
    - {"data": {"id": "xxx"}}
    - {"result": {"orderId": "xxx"}}
    - {"order": {"id": "xxx"}}
    - List wrapper responses

    Returns the ID string or None if not found.
    """
    if not isinstance(response_body, dict):
        return None

    # Direct ID fields
    for id_field in ("id", "uuid", "key", "code", "number", "ref"):
        val = response_body.get(id_field)
        if val and isinstance(val, (str, int)):
            return str(val)

    # Nested in common wrappers
    for wrapper in ("data", "result", "response", "payload", "content"):
        inner = response_body.get(wrapper)
        if isinstance(inner, dict):
            extracted = extract_resource_id(inner, entity_name)
            if extracted:
                return extracted

    # Entity-named wrapper: {"order": {"id": "xxx"}}
    if entity_name:
        entity_wrapper = response_body.get(entity_name)
        if isinstance(entity_wrapper, dict):
            extracted = extract_resource_id(entity_wrapper, "")
            if extracted:
                return extracted
        # camelCase variant
        camel = entity_name[0].upper() + entity_name[1:] if entity_name else ""
        if camel:
            camel_wrapper = response_body.get(camel) or response_body.get(entity_name.lower())
            if isinstance(camel_wrapper, dict):
                extracted = extract_resource_id(camel_wrapper, "")
                if extracted:
                    return extracted

    # List response: {"items": [{"id": "xxx"}]}
    for list_key in ("items", "records", "data", "results", "list", "rows"):
        items = response_body.get(list_key)
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                extracted = extract_resource_id(first, entity_name)
                if extracted:
                    return extracted

    return None


# ── Response Field Binding ──────────────────────────────────────────────

def bind_response_field(
    response_body: Any,
    field_name: str,
    entity_name: str = "",
) -> Any:
    """Extract a specific field value from an API response.

    Handles the same wrapper patterns as extract_resource_id but
    returns any field, not just identity fields.
    """
    if not isinstance(response_body, dict):
        return None

    # Direct field
    if field_name in response_body:
        return response_body[field_name]

    # camelCase / snake_case variants
    camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), field_name)
    snake = re.sub(r"([A-Z])", r"_\1", field_name).lower().strip("_")
    for variant in (camel, snake, field_name.lower(), field_name.upper()):
        if variant in response_body:
            return response_body[variant]

    # Nested wrappers
    for wrapper in ("data", "result", "response", "payload"):
        inner = response_body.get(wrapper)
        if isinstance(inner, dict):
            val = bind_response_field(inner, field_name, entity_name)
            if val is not None:
                return val

    # Entity wrapper
    if entity_name:
        entity = response_body.get(entity_name) or response_body.get(entity_name.lower())
        if isinstance(entity, dict):
            val = bind_response_field(entity, field_name, "")
            if val is not None:
                return val

    return None


# ── Schema-to-Operation Mapping ─────────────────────────────────────────

def find_create_operation(
    collection_path: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the POST operation for creating a resource at the given collection path.

    Matches by normalized collection path prefix (e.g., '/api/orders' matches
    POST '/api/orders' or POST '/api/orders/admin').
    """
    from .real_id_resolver_base import normalize_path_placeholders

    normalized_target = normalize_path_placeholders(collection_path).rstrip("/")
    if not normalized_target.startswith("/"):
        return None

    best: dict[str, Any] | None = None
    best_score = -1

    for op in operations:
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() != "POST":
            continue
        op_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        ).rstrip("/")

        # Exact match
        if op_path == normalized_target:
            return op

        # Prefix match (e.g., /api/orders matches /api/orders/admin)
        if op_path.startswith(normalized_target + "/") or normalized_target.startswith(op_path + "/"):
            score = len(set(op_path.split("/")) & set(normalized_target.split("/")))
            if score > best_score:
                best_score = score
                best = op

        # Collection match (e.g., /api/orders matches POST /api/orders with same prefix)
        if op_path.startswith(normalized_target) or normalized_target.startswith(op_path):
            score = len(op_path)  # Prefer longer (more specific) matches
            if score > best_score:
                best_score = score
                best = op

    return best


def find_read_operations(
    resource_path: str,
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find GET operations that can read/list resources at the given path."""
    from .real_id_resolver_base import normalize_path_placeholders, collection_path

    normalized = normalize_path_placeholders(resource_path).rstrip("/")
    collection = normalize_path_placeholders(collection_path(normalized)).rstrip("/")
    results: list[dict[str, Any]] = []

    for op in operations:
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() not in ("GET", "HEAD"):
            continue
        op_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        ).rstrip("/")
        if op_path == collection or op_path == normalized or op_path.startswith(collection):
            results.append(op)

    return results
