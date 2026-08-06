from __future__ import annotations

import re
from typing import Any, Callable, Iterable

QUALIBUG_UNRESOLVED_ID = "QUALIBUG_UNRESOLVED_ID"

_LIST_FIELDS = ("records", "data", "items", "results", "list", "rows", "content")
# Extended envelope keys for broader REST API surface coverage.
# Priority-ordered: common keys first, rarer keys as fallback.
_EXTENDED_LIST_FIELDS = (
    "records", "data", "items", "results", "list", "rows", "content",
    "orders", "products", "users", "accounts", "entities", "objects",
    "payload", "body", "response", "resources", "members", "elements",
    "collection", "documents", "nodes", "entries", "assets",
)
# Pagination wrappers that contain entity lists one level deeper.
_PAGINATION_WRAPPERS = (
    "data", "result", "payload", "body", "response",
)
# Second-level list keys commonly found inside pagination wrappers.
_INNER_LIST_KEYS = (
    "content", "items", "records", "list", "rows", "results", "data",
)
_PAGINATION_SUFFIXES = (
    "?page=1&size=1",
    "?pageNum=1&pageSize=1",
    "?offset=0&limit=1",
    "?current=1&pageSize=1",
)
_NORMALIZED_PARAM_RE = re.compile(r"\{([A-Za-z_]\w*)\}")
_PLACEHOLDER_PATTERNS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (
        re.compile(r"\$\{([A-Za-z_]\w*)\}"),
        lambda match: "{" + str(match.group(1) or "").strip() + "}",
    ),
    (
        re.compile(r"<([A-Za-z_]\w*)>"),
        lambda match: "{" + str(match.group(1) or "").strip() + "}",
    ),
    (
        re.compile(r"\{([A-Za-z_]\w*)(?::[^{}]+)?\}"),
        lambda match: "{" + str(match.group(1) or "").strip() + "}",
    ),
    (
        re.compile(r"(?<=/):([A-Za-z_]\w*)\b"),
        lambda match: "{" + str(match.group(1) or "").strip() + "}",
    ),
)

# Generic field aliases for path params — no industry hardcoding of values,
# only common REST naming conventions across systems.
_PARAM_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "uuid", "ID", "pk"),
    "sku": ("sku", "product_sku", "productSku", "code", "product_code", "id"),
    "orderid": ("orderId", "order_id", "id", "uuid", "order_no", "orderNo"),
    "order_id": ("order_id", "orderId", "id", "uuid", "order_no", "orderNo"),
    "userid": ("userId", "user_id", "uid", "id"),
    "user_id": ("user_id", "userId", "uid", "id"),
    "addressid": ("addressId", "address_id", "id"),
    "address_id": ("address_id", "addressId", "id"),
    "paymentid": ("paymentId", "payment_id", "id"),
    "refundid": ("refundId", "refund_id", "id"),
    "couponid": ("couponId", "coupon_id", "code", "id"),
    "code": ("code", "coupon_code", "couponCode", "sku", "id"),
    # Cross-industry identity params (healthcare / booking / settlement / claims).
    "patientid": ("patientId", "patient_id", "id", "uuid"),
    "patient_id": ("patient_id", "patientId", "id", "uuid"),
    "appointmentid": ("appointmentId", "appointment_id", "id", "uuid"),
    "appointment_id": ("appointment_id", "appointmentId", "id", "uuid"),
    "encounterid": ("encounterId", "encounter_id", "id", "uuid"),
    "encounter_id": ("encounter_id", "encounterId", "id", "uuid"),
    "claimid": ("claimId", "claim_id", "id", "uuid", "claim_no", "claimNo"),
    "claim_id": ("claim_id", "claimId", "id", "uuid", "claim_no", "claimNo"),
    "bookingid": ("bookingId", "booking_id", "id", "uuid", "reservation_id"),
    "booking_id": ("booking_id", "bookingId", "id", "uuid", "reservation_id"),
    "settlementid": ("settlementId", "settlement_id", "id", "uuid", "settlement_no"),
    "settlement_id": ("settlement_id", "settlementId", "id", "uuid", "settlement_no"),
    "prescriptionid": ("prescriptionId", "prescription_id", "id", "uuid"),
    "prescription_id": ("prescription_id", "prescriptionId", "id", "uuid"),
}


def normalize_path_placeholders(path: str) -> str:
    normalized = str(path or "")
    for pattern, replacer in _PLACEHOLDER_PATTERNS:
        normalized = pattern.sub(replacer, normalized)
    return normalized


def infer_path_params(path: str, declared: Iterable[str] | None = None) -> list[str]:
    values = [str(item) for item in (declared or []) if str(item)]
    if values:
        return values
    inferred = _NORMALIZED_PARAM_RE.findall(normalize_path_placeholders(path))
    return [str(item) for item in inferred if str(item)]


def path_has_placeholders(path: str) -> bool:
    return bool(_NORMALIZED_PARAM_RE.search(normalize_path_placeholders(path)))


def collection_path(path: str) -> str:
    normalized = normalize_path_placeholders(path).split("?", 1)[0]
    if not normalized.startswith("/"):
        return ""
    placeholder_match = re.search(r"/\{[A-Za-z_]\w*\}", normalized)
    if placeholder_match:
        return normalized[:placeholder_match.start()] or ""
    return normalized


def param_field_candidates(param_name: str) -> list[str]:
    """Return response-field names that can satisfy a path parameter."""
    name = str(param_name or "").strip()
    if not name:
        return ["id"]
    key = re.sub(r"[^a-z0-9_]+", "", name.lower())
    aliases = list(_PARAM_FIELD_ALIASES.get(key, ()))
    # Always include the literal param name and common id fallbacks.
    ordered = [name, *aliases, "id", "uuid", "code", "sku", "business_no", "order_id"]
    seen: set[str] = set()
    result: list[str] = []
    for item in ordered:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def bind_path_params_from_documented_body(
    path: str,
    body: Any,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Bind path parameters from scalar fields in the same documented body.

    This is intentionally structural and industry-neutral. Exact field-name
    matches win. A bare ``{id}`` may use a generic object/entity identity field,
    but scope, actor and event identifiers are penalized so they cannot silently
    masquerade as the resource identity. The caller remains responsible for
    enforcing the StrategyBundle source policy before using these candidates.
    """

    scalar_fields: list[tuple[str, str, Any]] = []

    def walk(value: Any, dotted: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                name = str(key or "").strip()
                walk(child, f"{dotted}.{name}" if dotted else name)
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{dotted}[{index}]")
            return
        if value in (None, "") or isinstance(value, bool) or not dotted:
            return
        leaf = re.sub(r"\[\d+\]$", "", dotted.rsplit(".", 1)[-1])
        scalar_fields.append((dotted, leaf, value))

    walk(body)
    bindings: dict[str, str] = {}
    evidence: list[dict[str, Any]] = []
    context_identity_stems = {
        "actor", "account", "event", "external", "request", "trace", "tenant", "user",
    }

    for param in infer_path_params(path):
        param_key = re.sub(r"[^a-z0-9]+", "", str(param or "").lower())
        ranked: list[tuple[int, int, str, str, Any]] = []
        for index, (dotted, leaf, value) in enumerate(scalar_fields):
            field_key = re.sub(r"[^a-z0-9]+", "", leaf.lower())
            if not field_key:
                continue
            score = 0
            if field_key == param_key:
                score = 100
            elif field_key in {
                re.sub(r"[^a-z0-9]+", "", item.lower())
                for item in param_field_candidates(param)
            }:
                score = 90
            elif param_key == "id" and (
                field_key == "id"
                or field_key.endswith("_id")
                or (len(field_key) > 2 and field_key[-2:] == "Id" and field_key[-3].isalpha())):
                stem = field_key[:-2]
                score = 70 if stem in {"entity", "object", "resource"} else 45
                if any(token in stem for token in context_identity_stems):
                    score -= 30
            elif param_key and (field_key.endswith(param_key) or param_key.endswith(field_key)):
                score = 55
            if score > 0:
                ranked.append((-score, index, dotted, leaf, value))
        if not ranked:
            continue
        ranked.sort()
        negative_score, _index, dotted, leaf, value = ranked[0]
        text = str(value)
        bindings[param] = text
        bindings.setdefault(leaf, text)
        evidence.append({
            "path_param": param,
            "body_field": dotted,
            "compatibility_score": -negative_score,
        })
    return bindings, evidence


def _pluralize_resource(stem: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "", str(stem or "").strip().lower())
    if not token:
        return ""
    # Already-plural collection tokens (orders, addresses) must not become
    # orderses / addresseses when deriving siblings from the path resource.
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token
    if token.endswith("y") and len(token) > 1 and token[-2] not in "aeiou":
        return token[:-1] + "ies"
    if token.endswith(("s", "x", "z", "ch", "sh")):
        return token + "es"
    return token + "s"


def _resource_siblings_from_token(token: str) -> list[str]:
    """Derive list-endpoint siblings from a path/body token (industry-agnostic)."""
    key = re.sub(r"[^a-z0-9]+", "", str(token or "").strip().lower())
    if not key:
        return []
    stem = key
    for suffix in ("uuid", "guid", "code", "number", "no", "key", "id"):
        if key.endswith(suffix) and len(key) > len(suffix) + 1:
            stem = key[: -len(suffix)]
            break
    if not stem:
        return []
    # Bare identity tokens are not collection resources (sku/code/id → use catalog hints).
    if stem in {"sku", "code", "id", "uuid", "guid", "no", "key", "pk", "num"}:
        return []
    plural = _pluralize_resource(stem)
    siblings = [stem, plural]
    # Common REST nesting for nested write surfaces (balance/status under users).
    if stem in {"user", "account", "customer", "member", "patient", "employee"}:
        siblings.extend([plural, f"{plural}/search", f"{stem}s/search"])
    return list(dict.fromkeys(item for item in siblings if item))


def alternate_collection_paths(path: str) -> list[str]:
    """Fallback list endpoints when the structural collection path is not listable.

    Derives siblings from path params and resource tokens instead of hardcoding
    ecommerce catalogs. Nested admin write paths still walk parents / search /
    identity siblings.
    """
    normalized = normalize_path_placeholders(path).split("?", 1)[0]
    params = infer_path_params(normalized)
    if not params:
        return []
    primary = collection_path(normalized)
    parts = [p for p in primary.strip("/").split("/") if p]
    if len(parts) < 2:
        return []
    prefix = "/" + parts[0]  # usually "api"
    resource = parts[-1].lower()
    param_keys = {re.sub(r"[^a-z0-9]+", "", p.lower()) for p in params}
    path_tokens = {p.lower() for p in parts}
    alternates: list[str] = []

    # Nested admin/collection trees: prefer searchable siblings and parent walks
    # before inventing unrelated catalogs. E.g. /api/users/admin/users →
    # /api/users/admin/search, /api/users, /api/auth/me.
    if "admin" in path_tokens or len(parts) >= 3:
        for i in range(len(parts), 1, -1):
            parent_parts = parts[:i]
            parent = "/" + "/".join(parent_parts)
            last = parent_parts[-1].lower()
            if last in {"admin", "api"}:
                # /api/users/admin → try /api/users/admin/search
                search = f"{parent}/search"
                if search != primary:
                    alternates.append(search)
                continue
            if parent != primary:
                alternates.append(parent)
            search = f"{parent}/search"
            if search != primary:
                alternates.append(search)
            # Replace trailing resource with search under the same admin parent.
            if "admin" in {p.lower() for p in parent_parts}:
                admin_idx = next(
                    (idx for idx, p in enumerate(parent_parts) if p.lower() == "admin"),
                    None,
                )
                if admin_idx is not None:
                    admin_search = "/" + "/".join(parent_parts[: admin_idx + 1] + ["search"])
                    if admin_search != primary:
                        alternates.append(admin_search)

    # Param-derived siblings: patient_id → /api/patients, material_code → /api/materials
    for param in params:
        for sibling in _resource_siblings_from_token(param):
            candidate = f"{prefix}/{sibling}"
            if candidate != primary:
                alternates.append(candidate)

    # Catalog-like identity tokens often resolve from a sibling list endpoint
    # (inventory/{sku} → products/materials; not ecommerce-only).
    if any(k in param_keys for k in ("sku", "productsku", "materialcode", "itemcode", "partnumber", "partno")):
        for sibling in ("products", "product", "materials", "material", "items", "goods", "catalog", "catalog_items", "skus"):
            candidate = f"{prefix}/{sibling}"
            if candidate != primary:
                alternates.append(candidate)

    # Resource-token siblings for sub-resources (inventory/{sku}, payments/{order_id})
    for sibling in _resource_siblings_from_token(resource):
        candidate = f"{prefix}/{sibling}"
        if candidate != primary:
            alternates.append(candidate)

    # Structural parent walk: /api/work-orders/{id}/release → /api/work-orders
    if len(parts) >= 3:
        parent = "/" + "/".join(parts[:-1])
        if parent != primary:
            alternates.append(parent)

    user_like = (
        any(k in param_keys for k in ("userid", "user_id", "patientid", "employeeid", "accountid"))
        or resource in {"users", "user", "accounts", "customers", "patients", "employees"}
        or bool({"users", "user", "patients", "employees"} & path_tokens)
    )
    if user_like:
        for sibling in ("users", "accounts", "customers", "patients", "employees"):
            candidate = f"{prefix}/{sibling}"
            if candidate != primary:
                alternates.append(candidate)
            alternates.append(f"{candidate}/admin/search")
            alternates.append(f"{candidate}/search")
        # Current-actor identity — prefer namespaced /auth/me over bare /me so
        # invented shallow paths do not crowd out real list endpoints.
        for identity in (
            f"{prefix}/auth/me",
            f"{prefix}/users/me",
            f"{prefix}/accounts/me",
        ):
            if identity != primary:
                alternates.append(identity)
    return list(dict.fromkeys(item for item in alternates if item.startswith("/")))


# Generic REST collection hints for body-field binding (not domain-specific values).
# Prefer structural derivation via body_field_collection_paths(); this map only
# covers common REST naming aliases that pluralization alone would miss.
_BODY_FIELD_COLLECTION_SEGMENTS: dict[str, tuple[str, ...]] = {
    "orderid": ("orders", "order"),
    "order_id": ("orders", "order"),
    "orderno": ("orders", "order"),
    "order_no": ("orders", "order"),
    "sku": ("products", "product", "skus", "items", "goods", "materials", "material"),
    "productid": ("products", "product", "items"),
    "product_id": ("products", "product", "items"),
    "materialid": ("materials", "material", "items"),
    "material_id": ("materials", "material", "items"),
    "materialcode": ("materials", "material", "items"),
    "material_code": ("materials", "material", "items"),
    "patientid": ("patients", "patient"),
    "patient_id": ("patients", "patient"),
    "appointmentid": ("appointments", "appointment"),
    "appointment_id": ("appointments", "appointment"),
    "encounterid": ("encounters", "encounter"),
    "encounter_id": ("encounters", "encounter"),
    "claimid": ("claims", "claim"),
    "claim_id": ("claims", "claim"),
    "bookingid": ("bookings", "booking", "reservations", "reservation"),
    "booking_id": ("bookings", "booking", "reservations", "reservation"),
    "prescriptionid": ("prescriptions", "prescription"),
    "prescription_id": ("prescriptions", "prescription"),
    "caseid": ("cases", "case"),
    "case_id": ("cases", "case"),
    "permitid": ("permits", "permit"),
    "permit_id": ("permits", "permit"),
    "workorderid": ("work-orders", "work_orders", "workorders"),
    "work_order_id": ("work-orders", "work_orders", "workorders"),
    "settlementid": ("settlements", "settlement"),
    "settlement_id": ("settlements", "settlement"),
    "userid": ("users", "accounts", "customers"),
    "user_id": ("users", "accounts", "customers"),
    "addressid": ("users/addresses", "user/addresses", "addresses", "address"),
    "address_id": ("users/addresses", "user/addresses", "addresses", "address"),
    "paymentid": ("payments", "payment"),
    "payment_id": ("payments", "payment"),
    "refundid": ("refunds", "refund"),
    "refund_id": ("refunds", "refund"),
    "couponid": ("coupons", "coupon", "promotions", "vouchers"),
    "coupon_id": ("coupons", "coupon", "promotions", "vouchers"),
    "promocode": ("promo_codes", "promotions", "coupons"),
    "promo_code": ("promo_codes", "promotions", "coupons"),
    "cartitemid": ("cart", "cart/items", "cartitems"),
    "cart_item_id": ("cart", "cart/items", "cartitems"),
    "lineid": ("cart/items", "order/items", "lines"),
    "line_id": ("cart/items", "order/items", "lines"),
}


def body_field_collection_paths(field: str, *, api_prefix: str = "/api") -> list[str]:
    """List endpoints that may supply a runtime value for a body placeholder."""
    name = str(field or "").strip()
    if not name:
        return []
    key = re.sub(r"[^a-z0-9_]+", "", name.lower())
    segments = _BODY_FIELD_COLLECTION_SEGMENTS.get(key, ())
    prefix = str(api_prefix or "/api").rstrip("/") or "/api"
    paths = [f"{prefix}/{segment}" for segment in segments if segment]
    if not paths and key.endswith("id"):
        stem = key[:-2].strip("_")
        if stem:
            paths.append(f"{prefix}/{stem}s")
            paths.append(f"{prefix}/{stem}")
    return list(dict.fromkeys(paths))


def extract_body_binding_fields(body: Any) -> list[str]:
    """Return ``{placeholder}`` field names referenced in a request body template."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
            return
        if isinstance(value, list):
            for child in value:
                walk(child)
            return
        if isinstance(value, str):
            for match in re.finditer(r"\{([A-Za-z_]\w*)\}", value):
                token = str(match.group(1) or "").strip()
                if token and token not in found:
                    found.append(token)

    walk(body)
    return found


def extract_fields_for_path(path: str) -> list[str]:
    """Fields a resolve step should extract to satisfy path placeholders."""
    fields: list[str] = []
    for param in infer_path_params(path):
        fields.extend(param_field_candidates(param))
    if not fields:
        fields = ["id", "sku", "code", "uuid"]
    return list(dict.fromkeys(fields))


def extract_first_entity_id(body: Any, param_name: str) -> str | None:
    entities = _extract_entity_candidates(body)
    if entities:
        first = entities[0]
        if isinstance(first, dict):
            for field_name in param_field_candidates(param_name):
                value = first.get(field_name)
                if value not in {None, ""}:
                    return str(value)
    if isinstance(body, dict):
        for field_name in param_field_candidates(param_name):
            value = body.get(field_name)
            if value not in {None, ""}:
                return str(value)
        # Only fall back to numeric values in identity-like fields
        for field_name, value in body.items():
            if not isinstance(value, (int, float)) or value <= 0:
                continue
            name = str(field_name).lower()
            if name.endswith("id") or name in ("pk", "uuid", "code", "sku", "key", "no", "number"):
                return str(int(value))
    return None


def bind_entity_fields(body: Any, path: str = "") -> dict[str, str]:
    """Extract a binding map from a list/detail response for path placeholders.

    Scans ALL entity candidates (not just the first) to find matching fields.
    When multiple entities match different params, bindings are merged.
    """
    entities = _extract_entity_candidates(body)
    if not entities and isinstance(body, dict):
        entities = [body]
    if not entities:
        return {}
    bindings: dict[str, str] = {}
    params = infer_path_params(path) or ["id"]
    has_multiple_path_identities = len(params) > 1
    generic_identity_fallbacks = {"id", "uuid", "pk", "code", "sku"}
    unmatched_params = set(params)

    # Scan all entities for each unmatched param
    for param in list(unmatched_params):
        for source in entities:
            if not isinstance(source, dict):
                continue
            for field_name in param_field_candidates(param):
                if (
                    has_multiple_path_identities
                    and field_name.lower() in generic_identity_fallbacks
                    and param.lower() not in generic_identity_fallbacks
                ):
                    continue
                value = source.get(field_name)
                if isinstance(value, (dict, list, bool)) or value in {None, ""}:
                    continue
                text = str(value)
                bindings[param] = text
                bindings[field_name] = text
                if param.lower() in {"id", "sku", "code"} or field_name.lower() in {"id", "sku"}:
                    bindings.setdefault("id", text)
                unmatched_params.discard(param)
                break
            if param not in unmatched_params:
                break

    # Gather common identity fields from all entities (broader scan).
    _identity_field_patterns = (
        "id", "_id", "uuid", "uid", "key", "code", "sku", "no", "number",
        "order_id", "orderId", "orderNo", "order_no",
        "user_id", "userId", "product_id", "productId", "productSku", "product_sku",
        "payment_id", "paymentId", "refund_id", "refundId",
        "address_id", "addressId", "coupon_id", "couponId",
        "tenant_id", "tenantId", "account_id", "accountId",
    )
    for source in entities:
        if not isinstance(source, dict):
            continue
        for field_name, field_value in source.items():
            if isinstance(field_value, (dict, list, bool)) or field_value in {None, ""}:
                continue
            key = re.sub(r"[^a-z0-9]", "", str(field_name).lower())
            for pattern in _identity_field_patterns:
                if re.sub(r"[^a-z0-9]", "", pattern) == key:
                    bindings.setdefault(field_name, str(field_value))
                    break
            # Also capture any field ending with "id" or "_id" or "Id"
            if key.endswith("id") and key not in {"paid", "said", "avoid", "laid", "maid", "raid"}:
                bindings.setdefault(field_name, str(field_value))
    # Final-fallback: for any still-unmatched params, scan every entity
    # field that looks like a valid non-empty identity value.
    _still_unmatched = [p for p in params if bindings.get(p) in {None, ""}]
    for param in _still_unmatched:
        for source in entities:
            if not isinstance(source, dict):
                continue
            for field_name, field_value in source.items():
                if isinstance(field_value, (dict, list, bool)) or field_value in {None, ""}:
                    continue
                text = str(field_value)
                key = re.sub(r"[^a-z0-9]", "", str(field_name).lower())
                # Accept if the field key matches the param or is a known identity pattern
                if key == re.sub(r"[^a-z0-9]", "", param.lower()):
                    bindings[param] = text
                    bindings[field_name] = text
                    break
            if bindings.get(param) not in {None, ""}:
                break
    # Mirror ``id`` onto unresolved sibling identity params (e.g., orderId)
    # when ``id`` is an accepted candidate for them.
    primary = bindings.get("id")
    if primary:
        for param in params:
            if bindings.get(param) not in {None, ""}:
                continue
            candidates = {c.lower() for c in param_field_candidates(param)}
            if "id" in candidates or "uuid" in candidates:
                bindings[param] = primary
                for alias in param_field_candidates(param):
                    bindings.setdefault(alias, primary)
    return bindings


def resolve_real_id_from_documented_list(
    path_pattern: str,
    param_name: str,
    try_extract_id: Callable[[str, str], str | None],
) -> str:
    list_path = collection_path(path_pattern)
    candidates = [list_path] if list_path and list_path != path_pattern else []
    candidates.extend(alternate_collection_paths(path_pattern))
    candidates = [item for item in dict.fromkeys(candidates) if item]

    for candidate in candidates:
        result = try_extract_id(candidate, param_name)
        if result is not None:
            return result
        for page_suffix in _PAGINATION_SUFFIXES:
            result = try_extract_id(candidate + page_suffix, param_name)
            if result is not None:
                return result

    if list_path:
        parent_path = re.sub(r"/[^/]+$", "", list_path)
        if parent_path and parent_path != list_path:
            parent_id = try_extract_id(parent_path, "id")
            if parent_id is not None:
                result = try_extract_id(list_path, param_name)
                if result is not None:
                    return result

    return QUALIBUG_UNRESOLVED_ID


def _extract_entity_candidates(body: Any) -> list[dict[str, Any]]:
    """Extract entity objects from a response body.

    Handles top-level arrays, envelope-wrapped lists, pagination wrappers
    (``{"data": {"content": [...]}}``), and deeply nested structures.
    Never returns pagination metadata, error objects, or statistics nodes.
    """
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    # Phase 1: Try all extended envelope keys (common + domain-specific).
    for field_name in _EXTENDED_LIST_FIELDS:
        value = body.get(field_name)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            # Check for pagination wrapper: {"data": {"content": [...]}}
            if field_name in _PAGINATION_WRAPPERS:
                for inner_key in _INNER_LIST_KEYS:
                    inner = value.get(inner_key)
                    if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                        return [item for item in inner if isinstance(item, dict)]
            nested = _extract_entity_candidates(value)
            if nested:
                return nested
    # Phase 2: Walk any list-of-dicts value not matched by Phase 1.
    for value in body.values():
        if isinstance(value, dict):
            nested = _extract_entity_candidates(value)
            if nested:
                return nested
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            return [item for item in value if isinstance(item, dict)]
    return []
