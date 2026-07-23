"""Related Entity Observer Executor.

Executes HTTP GET requests for related entity collections with:
- Pagination handling (page/page_size, offset/limit, cursor)
- Record deduplication by identity fields
- Scope validation
- Collection observation receipts

This module is industry-generic and uses only bound observer plans
from related_entity_observer_binder.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Maximum pages to fetch to prevent infinite loops
MAX_PAGINATION_PAGES = 50
# Maximum records to collect
MAX_COLLECTION_RECORDS = 10000
# HTTP timeout in seconds
HTTP_TIMEOUT = 15


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_or_text(raw: str) -> Any:
    """Parse JSON or return raw text."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


# ─── Collection Extraction ───────────────────────────────────────────────────


def extract_collection_from_response(body: Any) -> list[dict[str, Any]]:
    """Extract a list of records from an API response body.

    Handles common response formats:
    - Direct array: [{...}, {...}]
    - Wrapped: {"data": [...]}, {"records": [...]}, {"items": [...]}
    - Nested: {"data": {"records": [...]}}
    """
    if isinstance(body, list):
        return [dict(item) for item in body if isinstance(item, dict)]

    if not isinstance(body, dict):
        return []

    # Check common wrapper fields
    for field in ("records", "data", "items", "results", "list", "rows", "content"):
        value = body.get(field)
        if isinstance(value, list):
            rows = [dict(item) for item in value if isinstance(item, dict)]
            if rows:
                return rows
        # Handle nested wrapper
        if isinstance(value, dict):
            nested = extract_collection_from_response(value)
            if nested:
                return nested

    # Fallback: check all values for arrays
    for value in body.values():
        if isinstance(value, list):
            rows = [dict(item) for item in value if isinstance(item, dict)]
            if len(rows) > 0:
                return rows

    return []


def detect_pagination_info(body: Any) -> dict[str, Any]:
    """Detect pagination metadata from response body.

    Returns:
    - has_more: bool indicating if more pages exist
    - next_page: page number for next request (if page-based)
    - next_offset: offset for next request (if offset-based)
    - next_cursor: cursor for next request (if cursor-based)
    - total_count: total record count (if available)
    - current_count: records in current page
    """
    info: dict[str, Any] = {
        "has_more": False,
        "next_page": None,
        "next_offset": None,
        "next_cursor": None,
        "total_count": None,
        "current_count": 0,
    }

    if not isinstance(body, dict):
        return info

    # Total count fields
    for field in ("total", "totalCount", "total_count", "count", "totalElements"):
        if field in body and isinstance(body[field], (int, float)):
            info["total_count"] = int(body[field])
            break

    # Current page records
    records = extract_collection_from_response(body)
    info["current_count"] = len(records)

    # Page-based pagination
    for field in ("page", "pageNum", "current", "currentPage"):
        if field in body and isinstance(body[field], (int, float)):
            current_page = int(body[field])
            # Check for total pages
            for tp_field in ("totalPages", "total_pages", "pages", "pageCount"):
                if tp_field in body and isinstance(body[tp_field], (int, float)):
                    total_pages = int(body[tp_field])
                    info["has_more"] = current_page < total_pages
                    if info["has_more"]:
                        info["next_page"] = current_page + 1
                    break
            # If no total pages, check hasMore/hasNext
            if info["next_page"] is None:
                for hm_field in ("hasMore", "has_more", "hasNext", "has_next"):
                    if body.get(hm_field) is True:
                        info["has_more"] = True
                        info["next_page"] = current_page + 1
                        break
            break

    # Offset-based pagination
    if info["next_page"] is None:
        for field in ("offset", "start", "skip"):
            if field in body and isinstance(body[field], (int, float)):
                current_offset = int(body[field])
                limit = 0
                for l_field in ("limit", "size", "pageSize", "page_size"):
                    if l_field in body and isinstance(body[l_field], (int, float)):
                        limit = int(body[l_field])
                        break
                if limit > 0:
                    info["next_offset"] = current_offset + limit
                    # Determine if more records exist
                    if info["total_count"] is not None:
                        info["has_more"] = info["next_offset"] < info["total_count"]
                    elif info["current_count"] >= limit:
                        info["has_more"] = True
                break

    # Cursor-based pagination
    if info["next_page"] is None and info["next_offset"] is None:
        for field in ("cursor", "nextCursor", "next_cursor", "after", "nextToken", "next_token"):
            if field in body and body[field]:
                info["next_cursor"] = str(body[field])
                info["has_more"] = True
                break

    # Fallback: if we got a full page and no explicit pagination info
    if not info["has_more"] and info["total_count"] is None:
        # Assume more if we got records and no explicit end signal
        if info["current_count"] > 0:
            # Check common page size patterns
            for size_field in ("size", "pageSize", "page_size", "limit"):
                if size_field in body and isinstance(body[size_field], (int, float)):
                    if info["current_count"] >= int(body[size_field]):
                        info["has_more"] = True
                        if info["next_page"] is None and info["next_offset"] is None:
                            info["next_page"] = 2  # Assume page-based
                    break

    return info


# ─── Deduplication ───────────────────────────────────────────────────────────


def deduplicate_records(
    records: list[dict[str, Any]],
    identity_fields: list[str],
) -> tuple[list[dict[str, Any]], int]:
    """Deduplicate records by identity fields.

    Returns (deduplicated_records, duplicate_count).
    """
    if not identity_fields:
        identity_fields = ["id"]

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    duplicates = 0

    for record in records:
        if not isinstance(record, dict):
            continue

        # Build identity key
        identity_parts = []
        for field in identity_fields:
            value = record.get(field)
            if value is not None:
                identity_parts.append(f"{field}={value}")

        if not identity_parts:
            # No identity fields found, keep record
            result.append(record)
            continue

        identity_key = "|".join(identity_parts)
        if identity_key in seen:
            duplicates += 1
            continue

        seen.add(identity_key)
        result.append(record)

    return result, duplicates


# ─── HTTP Execution ──────────────────────────────────────────────────────────


def execute_collection_observation(
    observer: dict[str, Any],
    base_url: str,
    auth_token: str = "",
    *,
    timeout: int = HTTP_TIMEOUT,
) -> dict[str, Any]:
    """Execute a bound observer to fetch a related entity collection.

    Returns a collection observation receipt with:
    - status: OBSERVED | INDETERMINATE | FAILED
    - records: list of fetched records
    - pagination: pagination metadata
    - reason_code: blocking reason if not OBSERVED
    """
    entity_alias = _text(observer.get("entity_alias"))
    entity_name = _text(observer.get("entity_name"))
    operation_path = _text(observer.get("operation_path"))
    param_bindings = _list(observer.get("parameter_bindings"))
    identity_fields = _list(observer.get("identity_fields")) or ["id"]
    collection_reqs = _dict(observer.get("collection_requirements"))
    pagination_required = collection_reqs.get("pagination_required", True)

    if not operation_path:
        return {
            "status": "FAILED",
            "reason_code": "RELATED_ENTITY_OPERATION_NOT_FOUND",
            "entity_alias": entity_alias,
            "records": [],
            "pagination": {},
        }

    # Build query parameters from bindings
    query_params: dict[str, Any] = {}
    for binding in param_bindings:
        if not isinstance(binding, dict):
            continue
        param_name = _text(binding.get("parameter_name"))
        bound_value = binding.get("bound_value")
        if param_name and bound_value is not None:
            query_params[param_name] = bound_value

    # Execute with pagination
    all_records: list[dict[str, Any]] = []
    pages_fetched = 0
    pagination_complete = False
    last_error = ""

    # Initial request
    page_num = 1
    offset = 0
    cursor = None
    page_size = 100  # Default page size

    while pages_fetched < MAX_PAGINATION_PAGES and len(all_records) < MAX_COLLECTION_RECORDS:
        # Build URL with pagination
        current_params = dict(query_params)
        if pagination_required:
            if cursor:
                current_params["cursor"] = cursor
            elif offset > 0:
                current_params["offset"] = offset
                current_params["limit"] = page_size
            elif page_num > 1:
                current_params["page"] = page_num
                current_params["size"] = page_size

        url = _build_url(base_url, operation_path, current_params)

        # Execute HTTP GET
        try:
            request = urllib.request.Request(url, method="GET")
            request.add_header("Accept", "application/json")
            if auth_token:
                request.add_header("Authorization", f"Bearer {auth_token}")

            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(500_000).decode("utf-8", errors="replace")
                status = int(response.status)
                body = _json_or_text(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read(500_000).decode("utf-8", errors="replace") if exc.fp else ""
            status = int(exc.code)
            body = _json_or_text(raw)
            last_error = f"HTTP {status}"
        except Exception as exc:
            status = 0
            body = {"error": str(exc)}
            last_error = str(exc)

        pages_fetched += 1

        if not (200 <= status < 300):
            return {
                "status": "FAILED",
                "reason_code": "OBSERVER_QUERY_FAILED",
                "entity_alias": entity_alias,
                "entity_name": entity_name,
                "http_status": status,
                "error": last_error,
                "records": [],
                "pagination": {"pages_fetched": pages_fetched},
            }

        # Extract records
        page_records = extract_collection_from_response(body)
        all_records.extend(page_records)

        # Check pagination
        pagination_info = detect_pagination_info(body)

        if not pagination_required or not pagination_info["has_more"]:
            pagination_complete = True
            break

        # Prepare next page
        if pagination_info["next_cursor"]:
            cursor = pagination_info["next_cursor"]
        elif pagination_info["next_offset"] is not None:
            offset = pagination_info["next_offset"]
        elif pagination_info["next_page"] is not None:
            page_num = pagination_info["next_page"]
        else:
            # No pagination info, assume complete
            pagination_complete = True
            break

    # Deduplicate
    deduped_records, duplicate_count = deduplicate_records(all_records, identity_fields)

    # Build receipt
    receipt: dict[str, Any] = {
        "entity_alias": entity_alias,
        "entity_name": entity_name,
        "records": deduped_records,
        "pagination": {
            "pages_fetched": pages_fetched,
            "raw_record_count": len(all_records),
            "deduplicated_record_count": len(deduped_records),
            "duplicate_count": duplicate_count,
            "pagination_complete": pagination_complete,
        },
        "query_params": query_params,
    }

    if not pagination_complete and pagination_required:
        receipt["status"] = "INDETERMINATE"
        receipt["reason_code"] = "OBSERVER_COLLECTION_INCOMPLETE"
    elif not deduped_records and pages_fetched > 0:
        # Empty collection - distinguish from not executed
        receipt["status"] = "OBSERVED"
        receipt["reason_code"] = "QUERY_SUCCEEDED_EMPTY"
        receipt["collection_status"] = "QUERY_SUCCEEDED_EMPTY"
    else:
        receipt["status"] = "OBSERVED"
        receipt["reason_code"] = ""
        receipt["collection_status"] = "QUERY_SUCCEEDED"

    return receipt


def _build_url(base_url: str, path: str, params: dict[str, Any]) -> str:
    """Build a full URL with query parameters."""
    base = base_url.rstrip("/")
    path = path if path.startswith("/") else "/" + path

    # Substitute path parameters
    for key, value in list(params.items()):
        placeholder = "{" + key + "}"
        if placeholder in path:
            path = path.replace(placeholder, str(value))
            del params[key]

    url = base + path

    # Add query parameters
    if params:
        query_string = urllib.parse.urlencode(params, doseq=True)
        url = url + "?" + query_string

    return url


# ─── Multi-Observer Execution ────────────────────────────────────────────────


def execute_observer_plan(
    observer_plan: dict[str, Any],
    base_url: str,
    auth_token: str = "",
    *,
    root_identity_value: Any = None,
    tenant_scope_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a complete observer plan with root and related observers.

    Returns execution results with:
    - root_observation: root entity observation (if applicable)
    - related_observations: list of related entity observations
    - multi_entity_state: assembled state for assertion DSL
    - blockers: any blocking reasons
    """
    from .related_entity_observer_binder import validate_collection_scope

    result: dict[str, Any] = {
        "root_observation": None,
        "related_observations": [],
        "multi_entity_state": {},
        "blockers": [],
        "trace": [],
    }

    tenant_scope_values = tenant_scope_values or {}

    # Execute root observer if present
    root_observer = _dict(observer_plan.get("root_observer"))
    if root_observer and root_observer.get("status") == "BOUND":
        # Root is typically already observed via before/after snapshots
        # Just record the binding
        result["root_observation"] = {
            "entity_alias": root_observer.get("entity_alias"),
            "entity_name": root_observer.get("entity_name"),
            "status": "DEFERRED_TO_SNAPSHOT",
        }

    # Execute related observers
    for observer in _list(observer_plan.get("related_observers")):
        if not isinstance(observer, dict):
            continue

        if observer.get("status") != "BOUND":
            result["blockers"].append({
                "entity_alias": observer.get("entity_alias"),
                "reason": observer.get("status", "NOT_BOUND"),
            })
            continue

        # Bind root identity value if needed
        relation_key = _text(observer.get("relation_key"))
        if relation_key and root_identity_value is not None:
            for binding in _list(observer.get("parameter_bindings")):
                if isinstance(binding, dict) and binding.get("canonical_field_id") == relation_key:
                    if binding.get("bound_value") is None:
                        binding["bound_value"] = root_identity_value

        # Execute collection observation
        observation = execute_collection_observation(
            observer,
            base_url,
            auth_token,
        )

        # P7-fix: Client-side filtering when relation_key was not bound to query parameter
        if observer.get("requires_client_side_filter") and observation.get("records"):
            filter_field = _text(observer.get("client_filter_field"))
            filter_value = observer.get("client_filter_value")
            if filter_field and filter_value is not None:
                original_count = len(observation["records"])
                observation["records"] = [
                    rec for rec in observation["records"]
                    if isinstance(rec, dict) and str(rec.get(filter_field, "")) == str(filter_value)
                ]
                filtered_count = len(observation["records"])
                observation["client_side_filtered"] = True
                observation["client_filter_field"] = filter_field
                observation["client_filter_original_count"] = original_count
                observation["client_filter_result_count"] = filtered_count
                logger.debug(
                    f"Client-side filter: {observer.get('entity_alias')} "
                    f"{filter_field}={filter_value} {original_count}->{filtered_count}"
                )

        # Validate scope
        scope_fields = _list(observer.get("scope_fields"))
        if scope_fields and tenant_scope_values and observation.get("records"):
            scope_result = validate_collection_scope(
                observation["records"],
                scope_fields=scope_fields,
                expected_scope_values=tenant_scope_values,
                entity_alias=_text(observer.get("entity_alias")),
            )
            observation["scope_validation"] = scope_result
            if not scope_result["valid"]:
                observation["status"] = "INDETERMINATE"
                observation["reason_code"] = "OBSERVER_SCOPE_MISMATCH"

        result["related_observations"].append(observation)
        result["trace"].append({
            "entity_alias": observer.get("entity_alias"),
            "entity_name": observer.get("entity_name"),
            "status": observation.get("status"),
            "record_count": len(observation.get("records", [])),
            "pages_fetched": observation.get("pagination", {}).get("pages_fetched", 0),
        })

    # Assemble multi_entity_state
    for obs in result["related_observations"]:
        if obs.get("status") != "OBSERVED":
            continue
        entity_alias = _text(obs.get("entity_alias"))
        entity_name = _text(obs.get("entity_name"))
        records = obs.get("records", [])

        # Store under both alias and entity name
        state_entry = {
            "records": records,
            "record_count": len(records),
            "pagination": obs.get("pagination", {}),
            "collection_status": obs.get("collection_status", ""),
        }

        if entity_alias:
            result["multi_entity_state"][entity_alias] = state_entry
        if entity_name:
            result["multi_entity_state"][entity_name] = state_entry
        # Also store under generic "related" key for first related entity
        if "related" not in result["multi_entity_state"]:
            result["multi_entity_state"]["related"] = state_entry

    return result
