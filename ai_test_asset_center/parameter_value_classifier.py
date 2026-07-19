"""Parameter value classification and generation engine.

For every API parameter (path, query, body field), classifies it into one of:

  DEPENDENCY  — needs another resource to exist first (FK, reference)
  ENUMERATED  — must be one of a fixed set of valid values
  CONSTRAINED — has format/pattern/range rules that must be followed
  COMPUTED    — calculated from other values or system state
  SEMANTIC    — meaning-driven value (email, phone, name, address)
  FREE        — free-form but with type-appropriate defaults

This drives the enterprise test data engine to generate values that are
not just type-correct but BUSINESS-CORRECT — they would pass real API
validation in any enterprise system.

Schema: qualibug.parameter-value-classifier.v1
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import string
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


SCHEMA_VERSION = "qualibug.parameter-value-classifier.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []

def _text(value: Any) -> str:
    return str(value or "").strip()


# ═══════════════════════════════════════════════════════════════════════
# Value Category Enum
# ═══════════════════════════════════════════════════════════════════════

class ValueCategory(str, Enum):
    DEPENDENCY = "DEPENDENCY"       # FK/ref — needs prerequisite resource
    ENUMERATED = "ENUMERATED"       # Fixed set of valid options
    CONSTRAINED = "CONSTRAINED"     # Format/pattern/range rules
    COMPUTED = "COMPUTED"           # Derived from other values or system state
    SEMANTIC = "SEMANTIC"           # Meaning-driven (email, phone, name...)
    FREE = "FREE"                   # Free-form, type-appropriate default


# ═══════════════════════════════════════════════════════════════════════
# Field Semantic Registry — comprehensive field-name → meaning mapping
# ═══════════════════════════════════════════════════════════════════════

# Each entry: {category, generator, format_hint, valid_values, description}
_FIELD_REGISTRY: dict[str, dict[str, Any]] = {

    # ═══ IDENTITY FIELDS (DEPENDENCY or COMPUTED) ═══
    "id": {
        "category": ValueCategory.COMPUTED,
        "generator": "uuid_v4",
        "description": "Primary key / unique identifier",
        "aliases": ["uuid", "guid", "pk", "identifier", "resource_id"],
    },
    "key": {
        "category": ValueCategory.COMPUTED,
        "generator": "uuid_short",
        "description": "Unique business key",
        "aliases": ["api_key", "secret_key", "access_key"],
    },

    # ═══ FOREIGN KEY FIELDS (DEPENDENCY) ═══
    # Pattern: *_id, *Id, *_ref, fk_*
    # These are detected by pattern matching, not direct lookup
    "_FK_SUFFIXES": {
        "category": ValueCategory.DEPENDENCY,
        "suffixes": ["_id", "id", "_ref", "_code", "_key", "_number"],
        "camel_suffixes": ["Id", "Ref", "Code", "Key", "Number"],
        "prefixes": ["fk_", "ref_", "parent_"],
        "description": "Foreign key — references another resource",
    },

    # ═══ ENUMERATED FIELDS ═══
    "status": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": ["ACTIVE", "INACTIVE", "PENDING", "COMPLETED", "CANCELLED", "SUSPENDED", "DRAFT", "ARCHIVED"],
        "default": "ACTIVE",
        "description": "Entity status / lifecycle state",
        "aliases": ["state", "lifecycle_state", "entity_status"],
    },
    "type": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": ["STANDARD", "PREMIUM", "BASIC", "CUSTOM", "DEFAULT"],
        "default": "STANDARD",
        "description": "Type / category classifier",
        "aliases": ["kind", "category", "class", "sort"],
    },
    "gender": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": ["MALE", "FEMALE", "OTHER", "UNSPECIFIED"],
        "default": "OTHER",
        "aliases": ["sex"],
    },
    "role": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": ["USER", "ADMIN", "MANAGER", "VIEWER", "EDITOR", "AUDITOR"],
        "default": "USER",
        "aliases": ["user_role", "user_type", "permission_level"],
    },
    "priority": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": ["LOW", "MEDIUM", "HIGH", "URGENT", "CRITICAL"],
        "default": "MEDIUM",
        "aliases": ["severity", "urgency", "importance"],
    },
    "channel": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": ["ONLINE", "OFFLINE", "MOBILE", "WEB", "API", "POS"],
        "default": "ONLINE",
        "aliases": ["source", "platform", "medium"],
    },
    "payment_method": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": ["CREDIT_CARD", "DEBIT_CARD", "BANK_TRANSFER", "WALLET", "CASH", "COD"],
        "default": "WALLET",
        "aliases": ["pay_method", "method"],
    },
    "currency": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": ["CNY", "USD", "EUR", "JPY", "GBP", "KRW"],
        "default": "CNY",
        "aliases": ["currency_code", "ccy"],
    },
    "language": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": ["zh-CN", "en-US", "ja-JP", "ko-KR"],
        "default": "zh-CN",
        "aliases": ["lang", "locale"],
    },
    "boolean_status": {
        "category": ValueCategory.ENUMERATED,
        "valid_values": [True, False],
        "default": True,
        "description": "Boolean state fields",
        "aliases": ["active", "enabled", "verified", "approved", "locked", "deleted", "archived", "is_active", "is_enabled", "is_verified", "is_deleted", "has_*", "can_*", "should_*"],
    },

    # ═══ CONSTRAINED FORMAT FIELDS ═══
    "email": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "email",
        "format": "email",
        "pattern": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
        "aliases": ["mail", "e_mail", "email_address", "contact_email"],
    },
    "phone": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "phone_cn",
        "format": "phone",
        "pattern": r"^1[3-9]\d{9}$",
        "aliases": ["mobile", "telephone", "tel", "cell", "contact_phone", "phone_number", "cellphone"],
    },
    "url": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "url",
        "format": "uri",
        "pattern": r"^https?://",
        "aliases": ["website", "link", "href", "uri", "redirect_url", "callback_url", "webhook"],
    },
    "date": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "date_recent",
        "format": "date",
        "pattern": r"^\d{4}-\d{2}-\d{2}$",
        "aliases": ["create_date", "update_date", "birth_date", "start_date", "end_date", "due_date"],
    },
    "datetime": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "datetime_iso",
        "format": "date-time",
        "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
        "aliases": ["timestamp", "created_at", "updated_at", "deleted_at", "occurred_at", "happened_at"],
    },
    "ip_address": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "ip_v4",
        "format": "ipv4",
        "aliases": ["ip", "client_ip", "remote_addr", "source_ip"],
    },
    "color": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "hex_color",
        "format": "hex",
        "aliases": ["colour", "bg_color", "foreground", "background"],
    },
    "idempotency_key": {
        "category": ValueCategory.COMPUTED,
        "generator": "uuid_v4",
        "format": "uuid",
        "description": "Idempotency token — must be unique per request",
        "aliases": ["idempotent_key", "request_id", "trace_id", "correlation_id"],
    },

    # ═══ SEMANTIC FIELDS ═══
    "name": {
        "category": ValueCategory.SEMANTIC,
        "generator": "person_name",
        "aliases": ["full_name", "display_name", "real_name", "nickname", "username", "login_name"],
    },
    "address": {
        "category": ValueCategory.SEMANTIC,
        "generator": "street_address",
        "aliases": ["street", "addr", "location", "shipping_address", "billing_address"],
    },
    "city": {
        "category": ValueCategory.SEMANTIC,
        "generator": "city_name",
        "aliases": ["town", "municipality"],
    },
    "country": {
        "category": ValueCategory.ENUMERATED,
        "generator": "country_code",
        "valid_values": ["CN", "US", "JP", "KR", "GB", "DE", "FR", "SG"],
        "aliases": ["nation", "country_code", "region"],
    },
    "zip": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "zip_code",
        "aliases": ["postcode", "postal_code", "zip_code"],
    },
    "description": {
        "category": ValueCategory.SEMANTIC,
        "generator": "sentence",
        "aliases": ["note", "remark", "comment", "detail", "summary", "reason", "memo"],
    },
    "title": {
        "category": ValueCategory.SEMANTIC,
        "generator": "title_text",
        "aliases": ["subject", "heading", "caption", "label"],
    },

    # ═══ MONEY / QUANTITY FIELDS ═══
    "amount": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "money_amount",
        "constraints": {"min": 0.01, "max": 999999.99, "decimals": 2},
        "aliases": ["price", "total", "subtotal", "fee", "tax", "discount", "balance", "cost", "value", "sum", "grand_total", "unit_price", "retail_price"],
    },
    "quantity": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "quantity_small",
        "constraints": {"min": 1, "max": 9999},
        "aliases": ["qty", "count", "number", "stock", "inventory", "available", "reserved"],
    },
    "rate": {
        "category": ValueCategory.CONSTRAINED,
        "generator": "percentage",
        "constraints": {"min": 0, "max": 100},
        "aliases": ["ratio", "percent", "percentage", "discount_rate", "tax_rate", "interest_rate"],
    },

    # ═══ CODE / IDENTIFIER FIELDS ═══
    "code": {
        "category": ValueCategory.COMPUTED,
        "generator": "alphanumeric_upper_8",
        "aliases": ["sku", "product_code", "item_code", "coupon_code", "voucher_code", "promo_code", "barcode", "serial_number", "batch_number"],
    },
    "reference": {
        "category": ValueCategory.COMPUTED,
        "generator": "reference_number",
        "aliases": ["ref", "ref_number", "order_ref", "transaction_ref", "payment_ref"],
    },
}


# ═══════════════════════════════════════════════════════════════════════
# Schema-Aware Value Generation
# ═══════════════════════════════════════════════════════════════════════

def _normalize_field_name(name: str) -> str:
    """Normalize field name: lowercase, underscore-separated."""
    # camelCase → snake_case
    s1 = re.sub(r"([A-Z])", r"_\1", name).lower()
    # Remove non-alphanumeric, collapse underscores
    return re.sub(r"[^a-z0-9_]+", "_", s1).strip("_")


def _match_field_registry(normalized: str) -> dict[str, Any] | None:
    """Find the best matching field registry entry."""
    # Direct match
    if normalized in _FIELD_REGISTRY:
        entry = dict(_FIELD_REGISTRY[normalized])
        if "aliases" in entry:
            del entry["aliases"]
        return entry

    # Check aliases
    for key, entry in _FIELD_REGISTRY.items():
        if key.startswith("_"):
            continue
        aliases = entry.get("aliases", [])
        if normalized in aliases or any(
            alias.replace("*", ".*") and re.match(alias.replace("*", ".*"), normalized)
            for alias in aliases if "*" in alias
        ):
            result = dict(entry)
            if "aliases" in result:
                del result["aliases"]
            return result

    # Partial match: field name contains a registry key
    for key, entry in _FIELD_REGISTRY.items():
        if key.startswith("_"):
            continue
        if key in normalized and len(key) > 2:
            result = dict(entry)
            if "aliases" in result:
                del result["aliases"]
            return result

    return None


def _is_fk_field(normalized: str) -> tuple[bool, str]:
    """Check if a field is a foreign key. Returns (is_fk, entity_name)."""
    fk_info = _FIELD_REGISTRY.get("_FK_SUFFIXES", {})

    # Check suffixes
    for suffix in fk_info.get("suffixes", []):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            base = normalized[:-len(suffix)].strip("_")
            if base and base not in ("is", "has", "can", "should", "not"):
                return True, base

    # Check CamelCase suffixes (before snake_case normalization)
    # Already handled by _normalize_field_name, but check raw too
    # Pattern: xxxId, xxxRef, xxxCode → FK
    raw_suffixes = fk_info.get("camel_suffixes", [])
    # These are handled in classify_field_value via raw name check

    # Check prefixes
    for prefix in fk_info.get("prefixes", []):
        if normalized.startswith(prefix) and len(normalized) > len(prefix):
            base = normalized[len(prefix):].strip("_")
            if base:
                return True, base

    return False, ""


# ═══════════════════════════════════════════════════════════════════════
# Core Classification Function
# ═══════════════════════════════════════════════════════════════════════

def classify_field_value(
    field_name: str,
    schema: dict[str, Any] | None = None,
    *,
    parent_entity: str = "",
) -> dict[str, Any]:
    """Classify a field and determine how to generate its value.

    Args:
        field_name: The field name (can be snake_case or camelCase).
        schema: JSON Schema property definition for this field.
        parent_entity: The entity/resource this field belongs to.

    Returns:
        {
            "field": original field name,
            "normalized": normalized field name,
            "category": ValueCategory (DEPENDENCY/ENUMERATED/CONSTRAINED/COMPUTED/SEMANTIC/FREE),
            "generator": name of value generator to use,
            "is_fk": bool,
            "fk_entity": entity name if FK,
            "valid_values": list if ENUMERATED,
            "constraints": dict if CONSTRAINED,
            "format": schema format string,
            "reason": why this classification was chosen,
        }
    """
    schema = _dict(schema) if schema else {}
    raw_name = field_name
    normalized = _normalize_field_name(field_name)
    reason_parts: list[str] = []

    result: dict[str, Any] = {
        "field": raw_name,
        "normalized": normalized,
        "category": ValueCategory.FREE,
        "generator": "string_default",
        "is_fk": False,
        "fk_entity": "",
        "valid_values": None,
        "constraints": None,
        "format": _text(schema.get("format", "")),
        "reason": "",
    }

    # ── Step 1: Schema-driven classification ──

    # Enum in schema — highest priority
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        result["category"] = ValueCategory.ENUMERATED
        result["valid_values"] = list(enum_vals)
        result["generator"] = "schema_enum"
        reason_parts.append("schema:enum")
        result["reason"] = " | ".join(reason_parts)
        return result

    # Const in schema
    if "const" in schema:
        result["category"] = ValueCategory.ENUMERATED
        result["valid_values"] = [schema["const"]]
        result["generator"] = "schema_const"
        reason_parts.append("schema:const")
        result["reason"] = " | ".join(reason_parts)
        return result

    # Format in schema
    format_val = _text(schema.get("format", "")).lower()
    if format_val:
        result["category"] = ValueCategory.CONSTRAINED
        result["format"] = format_val
        reason_parts.append(f"schema:format={format_val}")

    # Pattern in schema
    pattern = _text(schema.get("pattern", ""))
    if pattern:
        result["category"] = ValueCategory.CONSTRAINED
        result["constraints"] = result["constraints"] or {}
        result["constraints"]["pattern"] = pattern
        reason_parts.append("schema:pattern")

    # Min/Max constraints
    for constraint in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if constraint in schema:
            result["category"] = ValueCategory.CONSTRAINED
            result["constraints"] = result["constraints"] or {}
            result["constraints"][constraint] = schema[constraint]
            reason_parts.append(f"schema:{constraint}")

    # ── Step 2: FK detection ──
    is_fk, fk_entity = _is_fk_field(normalized)
    if not is_fk:
        # Also check raw camelCase: userId → is_fk
        raw_lower = raw_name.lower()
        if raw_lower.endswith("id") and len(raw_lower) > 2:
            base = raw_lower[:-2]
            if base not in ("pa", "val", "inval") and not base.endswith("uu"):
                is_fk = True
                fk_entity = base
        elif raw_lower.endswith("ref") and len(raw_lower) > 3:
            is_fk = True
            fk_entity = raw_lower[:-3]
        elif raw_lower.endswith("code") and len(raw_lower) > 4:
            is_fk = True
            fk_entity = raw_lower[:-4]

    if is_fk and fk_entity and fk_entity not in ("is", "has", "can", "should"):
        # Exclude common non-FK patterns that happen to match suffix rules
        non_fk_entities = {
            "coupon", "idempotency", "promo", "discount", "token",
            "password", "secret", "credential", "captcha", "otp",
            "verification", "confirmation", "session",
        }
        if fk_entity.lower() in non_fk_entities:
            is_fk = False
            fk_entity = ""

    if is_fk and fk_entity:
        result["category"] = ValueCategory.DEPENDENCY
        result["is_fk"] = True
        result["fk_entity"] = fk_entity
        reason_parts.append(f"fk→{fk_entity}")
        result["reason"] = " | ".join(reason_parts)
        return result

    # ── Step 3: Registry-based classification ──
    registry_entry = _match_field_registry(normalized)
    if registry_entry:
        reg_category = registry_entry.get("category")
        if isinstance(reg_category, ValueCategory):
            result["category"] = reg_category
        elif isinstance(reg_category, str):
            result["category"] = ValueCategory(reg_category)
        result["generator"] = _text(registry_entry.get("generator", result["generator"]))
        if registry_entry.get("valid_values"):
            result["valid_values"] = list(registry_entry["valid_values"])
        if registry_entry.get("constraints"):
            result["constraints"] = dict(registry_entry["constraints"])
        reason_parts.append(f"registry:{normalized}")
        result["reason"] = " | ".join(reason_parts)
        return result

    # ── Step 4: Type-based fallback ──
    type_val = _text(schema.get("type", "")).lower()
    if type_val == "integer":
        result["category"] = ValueCategory.CONSTRAINED
        result["generator"] = "integer_default"
        reason_parts.append("type:integer")
    elif type_val == "number":
        result["category"] = ValueCategory.CONSTRAINED
        result["generator"] = "number_default"
        reason_parts.append("type:number")
    elif type_val == "boolean":
        result["category"] = ValueCategory.ENUMERATED
        result["valid_values"] = [True, False]
        result["generator"] = "boolean_default"
        reason_parts.append("type:boolean")
    elif type_val == "array":
        result["category"] = ValueCategory.FREE
        result["generator"] = "empty_array"
        reason_parts.append("type:array")
    elif type_val == "object":
        result["category"] = ValueCategory.FREE
        result["generator"] = "empty_object"
        reason_parts.append("type:object")
    else:
        result["category"] = ValueCategory.SEMANTIC
        result["generator"] = "string_default"
        reason_parts.append("type:string(default)")

    result["reason"] = " | ".join(reason_parts)
    return result


# ═══════════════════════════════════════════════════════════════════════
# Value Generation by Category
# ═══════════════════════════════════════════════════════════════════════

_SEED = 0

def _seed() -> int:
    global _SEED
    _SEED += 1
    return _SEED


def generate_classified_value(classification: dict[str, Any]) -> Any:
    """Generate a value appropriate for the classified field category."""
    category = classification.get("category")
    generator = _text(classification.get("generator", ""))
    constraints = _dict(classification.get("constraints", {}))
    valid_values = classification.get("valid_values")
    field = _text(classification.get("field", ""))
    s = _seed()

    # ── DEPENDENCY: placeholder — resolved at runtime ──
    if category in (ValueCategory.DEPENDENCY, "DEPENDENCY"):
        return f"<{classification.get('fk_entity', field)}_id>"

    # ── ENUMERATED: pick from valid values ──
    if category in (ValueCategory.ENUMERATED, "ENUMERATED"):
        if valid_values and isinstance(valid_values, list):
            return valid_values[0]  # first is default
        return "ACTIVE"

    # ── CONSTRAINED: follow format/pattern/range ──
    if category in (ValueCategory.CONSTRAINED, "CONSTRAINED"):
        fmt = _text(classification.get("format", ""))

        # Email formats
        if fmt == "email" or generator == "email":
            return f"qb_test_{s}@enterprise.test"

        # Phone formats
        if fmt == "phone" or generator in ("phone_cn", "phone"):
            prefixes = ["138", "139", "150", "186", "188"]
            return f"{random.choice(prefixes)}{s:08d}"[:11]

        # URI/URL
        if fmt in ("uri", "url") or generator == "url":
            return f"https://test-{s}.enterprise.test/api"

        # Date formats
        if fmt == "date" or generator.startswith("date"):
            return (datetime.now() - timedelta(days=s % 30)).strftime("%Y-%m-%d")

        # DateTime formats
        if fmt in ("date-time", "datetime") or generator.startswith("datetime"):
            return datetime.now(timezone.utc).isoformat()

        # UUID
        if fmt == "uuid":
            return str(_uuid.uuid4())

        # IP
        if fmt in ("ipv4", "ip") or generator == "ip_v4":
            return f"10.{s % 255}.{(s*7) % 255}.{(s*13) % 253 + 2}"

        # Money
        if generator in ("money_amount", "amount"):
            min_val = float(constraints.get("min", 0.01))
            max_val = float(constraints.get("max", 9999.99))
            return round(random.uniform(min_val, min(max_val, min_val + 500)), 2)

        # Quantity
        if generator in ("quantity_small", "small_int"):
            min_val = int(constraints.get("min", 1))
            max_val = int(constraints.get("max", 99))
            return random.randint(min_val, max_val)

        # Percentage
        if generator == "percentage":
            return random.randint(0, 100)

        # Integer
        if generator == "integer_default":
            min_val = int(constraints.get("minimum", 1))
            return min_val

        # Number
        if generator == "number_default":
            return round(random.uniform(1, 100), 2)

        # Patterns
        pattern = _text(constraints.get("pattern", ""))
        if pattern:
            return _generate_from_pattern(pattern, s)

        # String with constraints
        min_len = int(constraints.get("minLength", 1))
        max_len = int(constraints.get("maxLength", 100))
        return f"qb_{field}_{s}"[:max_len].ljust(min_len, "x")

    # ── COMPUTED: generate unique/system values ──
    if category in (ValueCategory.COMPUTED, "COMPUTED"):
        if generator == "uuid_v4":
            return str(_uuid.uuid4())
        if generator == "uuid_short":
            return _uuid.uuid4().hex[:12]
        if generator == "alphanumeric_upper_8":
            chars = string.ascii_uppercase + string.digits
            return "".join(random.choice(chars) for _ in range(8))
        if generator == "reference_number":
            return f"REF-{datetime.now().strftime('%Y%m%d')}-{s:06d}"
        return _uuid.uuid4().hex[:16]

    # ── SEMANTIC: meaning-driven value ──
    if category in (ValueCategory.SEMANTIC, "SEMANTIC"):
        if generator in ("person_name", "full_name"):
            firsts = ["Zhang", "Wang", "Li", "Liu", "Chen", "Yang", "Zhao"]
            lasts = ["Wei", "Fang", "Min", "Jie", "Lei", "Tao", "Na"]
            return f"{random.choice(firsts)}{random.choice(lasts)}"
        if generator == "street_address":
            return f"No.{s} Test Road, Chaoyang District"
        if generator == "city_name":
            return random.choice(["Beijing", "Shanghai", "Shenzhen", "Hangzhou", "Chengdu", "Guangzhou"])
        if generator == "sentence":
            return f"Automated test {field} record #{s}"
        if generator == "title_text":
            return f"Test {field.replace('_', ' ').title()} #{s}"
        if generator == "country_code":
            return random.choice(["CN", "US", "JP", "KR"])
        if generator == "zip_code":
            return f"{100000 + s:06d}"[:6]
        if generator == "hex_color":
            return f"#{s:06x}"[:7]

    # ── FREE: generic fallback ──
    return f"qb_{field}_{s}"


def _generate_from_pattern(pattern: str, seed: int) -> str:
    """Generate a value matching a regex pattern (best-effort)."""
    # Common patterns
    if re.match(r"^\^?\d{4}-\d{2}-\d{2}\$?$", pattern):
        return (datetime.now() - timedelta(days=seed % 30)).strftime("%Y-%m-%d")
    if re.match(r"^\^?1[3-9]\d{9}\$?$", pattern):
        return f"138{seed:08d}"[:11]
    if "email" in pattern.lower():
        return f"test_{seed}@example.com"
    # Generic: return pattern-safe string
    return f"PTN{seed:06d}"


# ═══════════════════════════════════════════════════════════════════════
# Full Body Classification & Generation
# ═══════════════════════════════════════════════════════════════════════

def classify_request_body(
    schema: dict[str, Any],
    *,
    parent_entity: str = "",
) -> dict[str, Any]:
    """Classify every field in a request body schema.

    Returns:
        {
            "fields": {field_name: classification_dict},
            "dependency_fields": [field_names that are FKs],
            "enum_fields": [field_names that are enums],
            "constrained_fields": [field_names with format/pattern rules],
            "summary": {category: count}
        }
    """
    schema = _dict(schema)
    properties = _dict(schema.get("properties", {}))
    if not properties:
        return {"fields": {}, "dependency_fields": [], "enum_fields": [],
                "constrained_fields": [], "summary": {}}

    fields: dict[str, Any] = {}
    deps: list[str] = []
    enums: list[str] = []
    constrained: list[str] = []
    counts: dict[str, int] = {}

    for field_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        classification = classify_field_value(
            field_name, prop_schema, parent_entity=parent_entity
        )
        fields[field_name] = classification

        cat = classification["category"]
        cat_str = cat.value if isinstance(cat, ValueCategory) else str(cat)
        counts[cat_str] = counts.get(cat_str, 0) + 1

        if classification["is_fk"]:
            deps.append(field_name)
        if cat in (ValueCategory.ENUMERATED, "ENUMERATED"):
            enums.append(field_name)
        if cat in (ValueCategory.CONSTRAINED, "CONSTRAINED"):
            constrained.append(field_name)

    return {
        "fields": fields,
        "dependency_fields": deps,
        "enum_fields": enums,
        "constrained_fields": constrained,
        "summary": counts,
    }


def generate_classified_body(
    schema: dict[str, Any],
    *,
    resolved_dependencies: dict[str, Any] | None = None,
    parent_entity: str = "",
) -> dict[str, Any]:
    """Generate a complete request body with classified values.

    Args:
        schema: JSON Schema for the request body.
        resolved_dependencies: Dict of field_name → resolved FK value.
        parent_entity: The entity for context-aware generation.

    Returns a dict ready for HTTP request.
    """
    classification = classify_request_body(schema, parent_entity=parent_entity)
    resolved = dict(resolved_dependencies or {})
    body: dict[str, Any] = {}

    for field_name, cls in classification["fields"].items():
        if field_name in resolved:
            body[field_name] = resolved[field_name]
        else:
            body[field_name] = generate_classified_value(cls)

    return body
