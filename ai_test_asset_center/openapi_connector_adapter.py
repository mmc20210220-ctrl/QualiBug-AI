from __future__ import annotations

"""Read-only online API-document connector.

The adapter owns URL policy, conditional HTTP reads, document-shape discovery, and external
``$ref`` traversal. Raw JSON/YAML bytes still enter the existing connector snapshot and Source
Occurrence authorities; this module does not create an API semantic model or execute API calls
described by a document.
"""

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml

from .connector_materialization_capability import (
    ResourceCapability,
    classify_materialization_capability,
)
from .connector_registry import (
    ConnectorContext,
    ConnectorCredentialField,
    ConnectorManifest,
    DiscoveryResult,
    MaterializedSnapshot,
    SyncCursor,
)
from .connector_source_ingestion import ConnectorSnapshotError
from .connector_sync_authority import (
    ConnectorSyncError,
    connector_snapshot_observation_index,
    list_connector_instances,
    sync_connector_snapshot_batch,
)
from .enterprise_knowledge_center._common import MAX_SOURCE_BYTES, ROOT
from .ssrf_guard import SsrfBlockedError, safe_urlopen, validate_url

OPENAPI_CONNECTOR_TYPE = "openapi"
OPENAPI_EXPORT_CONNECTOR_TYPES = ("apifox", "yapi")
OPENAPI_ADAPTER_SCHEMA = "qualibug.openapi-connector-adapter.v1"
OPENAPI_MATERIALIZATION_CONTRACT_VERSION = "openapi-materialization-v1"

_DEFAULT_MAX_DOCUMENTS = 100
_DEFAULT_MAX_DOCUMENT_BYTES = min(MAX_SOURCE_BYTES, 8 * 1024 * 1024)
_DEFAULT_MAX_TOTAL_BYTES = min(MAX_SOURCE_BYTES, 32 * 1024 * 1024)
_MAX_DOCUMENTS = 1_000
_MAX_DOCUMENT_BYTES = MAX_SOURCE_BYTES
_MAX_TOTAL_BYTES = MAX_SOURCE_BYTES
_MAX_DOCUMENT_URLS = 64
_MAX_DOMAINS = 128
_MAX_REF_DEPTH = 12
_MAX_REFS_PER_DOCUMENT = 500
_MAX_RELATIONSHIPS = 300
_MAX_METADATA_JSON = 1_800
_MAX_CURSOR_DESCRIPTORS = 10_000
_MAX_RETRIES = 3
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_DOCUMENT_MIME_TYPES = {
    "application/json",
    "application/yaml",
    "application/x-yaml",
    "text/json",
    "text/yaml",
    "text/x-yaml",
}
_SUPPORTED_OBJECT_TYPES = {
    "openapi_document",
    "postman_collection",
    "openapi_reference",
}
_SECRET_QUERY_RE = re.compile(
    r"(?i)(?:token|secret|password|passwd|api[_-]?key|authorization|credential|signature)"
)
_SAFE_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_FORBIDDEN_HEADER_NAMES = {
    "connection",
    "content-length",
    "cookie",
    "host",
    "proxy-authorization",
    "set-cookie",
    "transfer-encoding",
}


class OpenApiConnectorError(RuntimeError):
    """The online API-document snapshot is not trustworthy or not within policy."""


def _normalize_openapi_connector_type(value: Any) -> str:
    connector_type = _text(value, 160).lower() or OPENAPI_CONNECTOR_TYPE
    if connector_type not in {
        OPENAPI_CONNECTOR_TYPE,
        *OPENAPI_EXPORT_CONNECTOR_TYPES,
    }:
        raise OpenApiConnectorError(
            f"openapi_connector_type_not_supported:{connector_type}"
        )
    return connector_type


@dataclass(frozen=True)
class OpenApiHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str = ""


OpenApiTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float, int],
    OpenApiHttpResponse,
]


def _text(value: Any, limit: int = 1_000) -> str:
    return str(value or "").strip()[:limit]


def _header(headers: Mapping[str, Any], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return _text(value, 4_000)
    return ""


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _safe_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise OpenApiConnectorError(f"openapi_scope_{field}_invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise OpenApiConnectorError(f"openapi_scope_{field}_invalid") from exc
    if not minimum <= result <= maximum:
        raise OpenApiConnectorError(f"openapi_scope_{field}_out_of_range")
    return result


def _string_list(
    value: Any,
    field: str,
    *,
    required: bool = False,
    maximum: int,
) -> list[str]:
    if value is None or value == "":
        if required:
            raise OpenApiConnectorError(f"openapi_scope_{field}_required")
        return []
    values = [value] if isinstance(value, str) else list(value) if isinstance(value, (list, tuple)) else None
    if values is None:
        raise OpenApiConnectorError(f"openapi_scope_{field}_must_be_list")
    if len(values) > maximum:
        raise OpenApiConnectorError(f"openapi_scope_{field}_limit_exceeded")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _text(item, 4_000)
        if not text:
            raise OpenApiConnectorError(f"openapi_scope_{field}_contains_empty_value")
        if text not in seen:
            seen.add(text)
            result.append(text)
    if required and not result:
        raise OpenApiConnectorError(f"openapi_scope_{field}_required")
    return result


def _domain(value: Any, field: str) -> str:
    raw = _text(value, 255).lower().rstrip(".")
    if not raw or any(ch.isspace() for ch in raw) or "/" in raw or ":" in raw:
        raise OpenApiConnectorError(f"openapi_scope_{field}_invalid")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,253}[a-z0-9])?", raw):
        raise OpenApiConnectorError(f"openapi_scope_{field}_invalid")
    return raw


def _normalize_url(value: Any, *, base: str = "", allow_fragment: bool = False) -> str:
    raw = _text(value, 4_000)
    if not raw:
        raise OpenApiConnectorError("openapi_document_url_missing")
    joined = urllib.parse.urljoin(base, raw) if base else raw
    try:
        parsed = urllib.parse.urlsplit(joined)
        port = parsed.port
    except ValueError as exc:
        raise OpenApiConnectorError("openapi_document_url_invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise OpenApiConnectorError("openapi_document_url_scheme_or_host_invalid")
    if parsed.username or parsed.password:
        raise OpenApiConnectorError("openapi_document_url_userinfo_invalid")
    if parsed.fragment and not allow_fragment:
        raise OpenApiConnectorError("openapi_document_url_fragment_invalid")
    if parsed.query:
        for key, _value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            if _SECRET_QUERY_RE.search(key):
                raise OpenApiConnectorError("openapi_document_url_query_credential_forbidden")
    hostname = parsed.hostname.lower()
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc += f":{port}"
    path = parsed.path or "/"
    normalized_parts: list[str] = []
    for segment in path.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(segment)
    normalized_path = "/" + "/".join(normalized_parts)
    if path.endswith("/") and normalized_path != "/":
        normalized_path += "/"
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            normalized_path,
            parsed.query,
            parsed.fragment if allow_fragment else "",
        )
    )


def _domain_allowed(url: str, scope: Mapping[str, Any]) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in scope["allowed_domains"])


def _validate_and_scope_url(value: Any, scope: Mapping[str, Any], *, base: str = "") -> str:
    url = _normalize_url(value, base=base)
    try:
        validate_url(url, allow_internal=False)
    except SsrfBlockedError as exc:
        raise OpenApiConnectorError("openapi_ssrf_blocked") from exc
    if not _domain_allowed(url, scope):
        raise OpenApiConnectorError("openapi_url_out_of_scope")
    return url


def _scope_from_context(context: Mapping[str, Any]) -> dict[str, Any]:
    raw = context.get("resource_scope")
    if isinstance(raw, Mapping):
        value: Any = dict(raw)
    else:
        text = _text(raw, 20_000)
        if text.startswith(("http://", "https://")):
            value = {"document_urls": [text]}
        else:
            try:
                value = json.loads(text)
            except (TypeError, ValueError) as exc:
                raise OpenApiConnectorError("openapi_resource_scope_json_required") from exc
    if not isinstance(value, dict):
        raise OpenApiConnectorError("openapi_resource_scope_must_be_object")
    allowed_keys = {
        "document_urls",
        "allowed_domains",
        "max_documents",
        "max_document_bytes",
        "max_total_bytes",
        "resolve_refs",
        "max_ref_depth",
    }
    unknown = sorted(set(value) - allowed_keys)
    if unknown:
        raise OpenApiConnectorError("openapi_scope_field_not_supported:" + str(unknown[0]))
    documents = [_normalize_url(item) for item in _string_list(
        value.get("document_urls"), "document_urls", required=True, maximum=_MAX_DOCUMENT_URLS
    )]
    seed_hosts = sorted({(urllib.parse.urlsplit(url).hostname or "").lower() for url in documents})
    domains = [_domain(item, "allowed_domains") for item in _string_list(
        value.get("allowed_domains"), "allowed_domains", maximum=_MAX_DOMAINS
    )] or seed_hosts
    if not set(seed_hosts).issubset(set(domains)):
        raise OpenApiConnectorError("openapi_scope_document_domain_not_allowed")
    resolve_refs = value.get("resolve_refs", True)
    if not isinstance(resolve_refs, bool):
        raise OpenApiConnectorError("openapi_scope_resolve_refs_invalid")
    return {
        "document_urls": documents,
        "allowed_domains": domains,
        "max_documents": _safe_int(value.get("max_documents", _DEFAULT_MAX_DOCUMENTS), "max_documents", 1, _MAX_DOCUMENTS),
        "max_document_bytes": _safe_int(
            value.get("max_document_bytes", _DEFAULT_MAX_DOCUMENT_BYTES),
            "max_document_bytes",
            1_024,
            _MAX_DOCUMENT_BYTES,
        ),
        "max_total_bytes": _safe_int(
            value.get("max_total_bytes", _DEFAULT_MAX_TOTAL_BYTES),
            "max_total_bytes",
            1_024,
            _MAX_TOTAL_BYTES,
        ),
        "resolve_refs": resolve_refs,
        "max_ref_depth": _safe_int(value.get("max_ref_depth", 5), "max_ref_depth", 0, _MAX_REF_DEPTH),
    }


def _profile_for_context(context: Mapping[str, Any]) -> dict[str, str]:
    profile = dict(context.get("connection_profile") or {})
    profile_ref = _text(context.get("connection_profile_ref"), 500)
    if profile_ref and not profile:
        resolver = context.get("resolve_connection_profile")
        if not callable(resolver):
            raise OpenApiConnectorError("openapi_connection_profile_resolver_missing")
        try:
            resolved = resolver(profile_ref)
        except Exception as exc:
            raise OpenApiConnectorError(
                f"openapi_connection_profile_resolution_failed:{type(exc).__name__}"
            ) from exc
        if not isinstance(resolved, Mapping):
            raise OpenApiConnectorError("openapi_connection_profile_invalid")
        profile = {str(key): _text(value, 8_000) for key, value in resolved.items()}
    auth_mode = _text(profile.get("auth_mode"), 64).lower() or "anonymous"
    if auth_mode not in {"anonymous", "bearer_token", "api_key", "cookie_session"}:
        raise OpenApiConnectorError("openapi_auth_mode_invalid")
    result = {"auth_mode": auth_mode}
    if auth_mode == "bearer_token":
        result["token"] = _text(profile.get("token"), 8_000)
        if not result["token"]:
            raise OpenApiConnectorError("openapi_bearer_token_required")
    elif auth_mode == "api_key":
        result["api_key"] = _text(profile.get("api_key"), 8_000)
        result["header_name"] = _text(profile.get("header_name"), 160)
        if not result["api_key"] or not result["header_name"]:
            raise OpenApiConnectorError("openapi_api_key_profile_incomplete")
    elif auth_mode == "cookie_session":
        result["session_cookie"] = _text(profile.get("session_cookie"), 8_000)
        if not result["session_cookie"]:
            raise OpenApiConnectorError("openapi_session_cookie_required")
    return result


def _safe_header_name(value: str) -> str:
    name = _text(value, 160)
    if not _SAFE_HEADER_NAME_RE.fullmatch(name) or name.lower() in _FORBIDDEN_HEADER_NAMES:
        raise OpenApiConnectorError("openapi_api_key_header_name_invalid")
    return name


def _auth_headers(context: Mapping[str, Any]) -> dict[str, str]:
    profile = _profile_for_context(context)
    mode = profile["auth_mode"]
    if mode == "bearer_token":
        return {"Authorization": "Bearer " + profile["token"]}
    if mode == "api_key":
        return {_safe_header_name(profile["header_name"]): profile["api_key"]}
    if mode == "cookie_session":
        return {"Cookie": profile["session_cookie"]}
    return {}


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    max_bytes: int,
) -> OpenApiHttpResponse:
    if method.upper() not in {"GET", "HEAD"} or body is not None:
        raise OpenApiConnectorError("openapi_write_or_body_request_forbidden")
    try:
        request = urllib.request.Request(url, headers=dict(headers), method=method.upper())
        with safe_urlopen(request, timeout=timeout, allow_internal=False) as response:
            content_length = _header(response.headers, "Content-Length")
            if content_length.isdigit() and int(content_length) > max_bytes:
                raise OpenApiConnectorError("openapi_response_size_limit_exceeded")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise OpenApiConnectorError("openapi_response_size_limit_exceeded")
            return OpenApiHttpResponse(
                status=int(getattr(response, "status", response.getcode())),
                headers={str(key): str(value) for key, value in response.headers.items()},
                body=bytes(payload),
                final_url=_text(response.geturl(), 4_000),
            )
    except urllib.error.HTTPError as exc:
        payload = exc.read(max_bytes + 1)
        return OpenApiHttpResponse(
            status=int(exc.code),
            headers={str(key): str(value) for key, value in exc.headers.items()},
            body=bytes(payload[:max_bytes]),
            final_url=_text(exc.geturl(), 4_000),
        )
    except (urllib.error.URLError, TimeoutError, SsrfBlockedError) as exc:
        raise OpenApiConnectorError(
            f"openapi_transport_failed:{type(exc).__name__}"
        ) from exc


def _request(
    context: Mapping[str, Any],
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    max_bytes: int,
) -> OpenApiHttpResponse:
    try:
        validate_url(url, allow_internal=False)
    except SsrfBlockedError as exc:
        raise OpenApiConnectorError("openapi_ssrf_blocked") from exc
    request_headers = {
        "Accept": "application/json, application/yaml, text/yaml, text/plain;q=0.8, */*;q=0.1",
        "User-Agent": "QualiBug-OpenAPI-Connector/1",
    }
    request_headers.update({str(key): str(value) for key, value in (headers or {}).items()})
    transport = context.get("transport") or _default_transport
    if not callable(transport):
        raise OpenApiConnectorError("openapi_transport_invalid")
    try:
        timeout = float(context.get("timeout", 15.0))
    except (TypeError, ValueError) as exc:
        raise OpenApiConnectorError("openapi_timeout_invalid") from exc
    sleeper = context.get("sleeper", time.sleep)
    last: OpenApiHttpResponse | None = None
    for attempt in range(_MAX_RETRIES):
        response = transport("GET", url, request_headers, None, timeout, max_bytes)
        if not isinstance(response, OpenApiHttpResponse):
            raise OpenApiConnectorError("openapi_transport_response_invalid")
        if not isinstance(response.body, (bytes, bytearray, memoryview)):
            raise OpenApiConnectorError("openapi_transport_body_invalid")
        if len(response.body) > max_bytes:
            raise OpenApiConnectorError("openapi_response_size_limit_exceeded")
        last = response
        if response.status not in _RETRYABLE_STATUSES or attempt + 1 >= _MAX_RETRIES:
            return response
        if callable(sleeper):
            sleeper(min(0.25 * (2**attempt), 2.0))
    if last is None:
        raise OpenApiConnectorError("openapi_transport_returned_no_response")
    return last


def _source_format(url: str, response: OpenApiHttpResponse, body: bytes) -> str:
    mime = _header(response.headers, "Content-Type").split(";", 1)[0].strip().lower()
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if mime in {"application/json", "text/json"} or suffix == ".json":
        return "json"
    if mime in _DOCUMENT_MIME_TYPES or suffix in {".yaml", ".yml"}:
        return "yaml"
    try:
        json.loads(body.decode("utf-8-sig"))
        return "json"
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "yaml"


def _document_kind(payload: Mapping[str, Any], *, external_reference: bool) -> tuple[str, str, str, str]:
    info = payload.get("info") if isinstance(payload.get("info"), Mapping) else {}
    info_version = _text(info.get("version"), 240)
    if isinstance(payload.get("paths"), Mapping) and (payload.get("openapi") or payload.get("swagger")):
        version = _text(payload.get("openapi") or payload.get("swagger"), 80)
        return "openapi_document", version, info_version, _text(info.get("title"), 300)
    schema = _text(info.get("schema"), 500).lower()
    if isinstance(payload.get("item"), list) and (
        "postman" in schema or info.get("_postman_id") or payload.get("variable") is not None
    ):
        return "postman_collection", "postman", info_version, _text(info.get("name") or info.get("title"), 300)
    if external_reference and isinstance(payload, Mapping):
        return "openapi_reference", _text(payload.get("openapi") or payload.get("swagger"), 80), info_version, ""
    raise OpenApiConnectorError("openapi_document_shape_unrecognized")


def _decode_document(
    body: bytes,
    *,
    url: str,
    response: OpenApiHttpResponse,
    external_reference: bool,
) -> tuple[dict[str, Any], str, str, str, str, str]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OpenApiConnectorError("openapi_document_utf8_decode_failed") from exc
    fmt = _source_format(url, response, body)
    try:
        parsed: Any = json.loads(text) if fmt == "json" else yaml.safe_load(text)
    except Exception as exc:
        raise OpenApiConnectorError(f"openapi_document_parse_failed:{type(exc).__name__}") from exc
    if not isinstance(parsed, dict):
        raise OpenApiConnectorError("openapi_document_root_must_be_object")
    kind, version, info_version, title = _document_kind(
        parsed, external_reference=external_reference
    )
    return parsed, fmt, kind, version, info_version, title


def _pointer_token(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _redacted_ref_identity(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() in {"http", "https"} and parsed.hostname and not (
        parsed.username or parsed.password
    ):
        try:
            port = parsed.port
        except ValueError:
            port = None
        hostname = parsed.hostname.lower()
        netloc = hostname if ":" not in hostname else f"[{hostname}]"
        if port is not None and not (
            (parsed.scheme.lower() == "http" and port == 80)
            or (parsed.scheme.lower() == "https" and port == 443)
        ):
            netloc += f":{port}"
        return urllib.parse.urlunsplit(
            (parsed.scheme.lower(), netloc, parsed.path or "/", "", "")
        )
    return "blocked-ref:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _external_references(
    payload: Mapping[str, Any],
    *,
    base_url: str,
    scope: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    relationships: list[dict[str, str]] = []
    coverage: list[dict[str, Any]] = []
    active_containers: set[int] = set()
    ref_limit_reached = False

    def visit(value: Any, pointer: str) -> None:
        nonlocal ref_limit_reached
        if len(relationships) >= _MAX_REFS_PER_DOCUMENT:
            ref_limit_reached = True
            return
        if isinstance(value, Mapping):
            marker = id(value)
            if marker in active_containers:
                raise OpenApiConnectorError("openapi_document_alias_cycle")
            active_containers.add(marker)
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.strip() and not ref.lstrip().startswith("#"):
                raw_target = urllib.parse.urljoin(base_url, ref.strip())
                target = raw_target
                ref_pointer = pointer + "/$ref"
                fragment = ""
                try:
                    parsed = urllib.parse.urlsplit(raw_target)
                    fragment = _text(parsed.fragment, 2_000)
                    target = _normalize_url(
                        urllib.parse.urlunsplit(
                            (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
                        )
                    )
                    _validate_and_scope_url(target, scope)
                except OpenApiConnectorError as exc:
                    message = str(exc)
                    if "ssrf" in message:
                        reason = "openapi_ref_ssrf_blocked"
                    elif "out_of_scope" in message:
                        reason = "openapi_ref_out_of_scope"
                    else:
                        reason = "openapi_ref_invalid_url"
                    safe_target = _redacted_ref_identity(raw_target)
                    relationships.append(
                        {
                            "target_url": safe_target,
                            "target_fragment": fragment,
                            "relation": "EXTERNAL_REF_BLOCKED",
                            "kind": "OPENAPI_REF",
                            "source_pointer": ref_pointer,
                        }
                    )
                    coverage.append(
                        _coverage(
                            safe_target,
                            reason,
                            remote_object_type="openapi_reference",
                            metadata={"source_pointer": ref_pointer},
                        )
                    )
                else:
                    relationships.append(
                        {
                            "target_url": target,
                            "target_fragment": _text(parsed.fragment, 2_000),
                            "relation": "EXTERNAL_REF",
                            "kind": "OPENAPI_REF",
                            "source_pointer": ref_pointer,
                        }
                    )
            for key, child in value.items():
                visit(child, pointer + "/" + _pointer_token(key))
            active_containers.remove(marker)
        elif isinstance(value, list):
            marker = id(value)
            if marker in active_containers:
                raise OpenApiConnectorError("openapi_document_alias_cycle")
            active_containers.add(marker)
            for index, child in enumerate(value):
                visit(child, pointer + "/" + str(index))
            active_containers.remove(marker)

    visit(payload, "")
    if ref_limit_reached:
        coverage.append(
            _coverage(
                base_url,
                "OPENAPI_REF_LIMIT_REACHED",
                remote_object_type="openapi_reference",
                metadata={"max_refs_per_document": _MAX_REFS_PER_DOCUMENT},
            )
        )
    return relationships, coverage


def _json_strings(value: Any) -> list[str]:
    raw = _text(value, 20_000)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [_text(item, 4_000) for item in parsed if _text(item, 4_000)] if isinstance(parsed, list) else []


def _json_objects(value: Any) -> list[dict[str, Any]]:
    raw = _text(value, 100_000)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [dict(item) for item in parsed if isinstance(item, Mapping)] if isinstance(parsed, list) else []


def _bounded_json(values: Iterable[Any], *, objects: bool = False, limit: int = _MAX_METADATA_JSON) -> str:
    selected: list[Any] = []
    for value in values:
        item = dict(value) if objects and isinstance(value, Mapping) else _text(value, 4_000)
        if not item:
            continue
        candidate = json.dumps([*selected, item], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(candidate) > limit:
            break
        selected.append(item)
    return json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _merge_relationships(*values: Any) -> str:
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for value in values:
        for row in _json_objects(value):
            key = (
                _text(row.get("target_url"), 4_000),
                _text(row.get("target_fragment"), 2_000),
                _text(row.get("source_pointer"), 4_000),
                _text(row.get("relation"), 100),
            )
            if key[0]:
                rows[key] = row
    return _bounded_json(list(rows.values())[:_MAX_RELATIONSHIPS], objects=True)


def _filename(url: str, *, kind: str, fmt: str) -> str:
    name = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    name = re.sub(r"[^A-Za-z0-9._\-]+", "_", name).strip(" ._")
    suffix = ".json" if fmt == "json" else ".yaml"
    if not name or Path(name).suffix.lower() not in {".json", ".yaml", ".yml"}:
        name = "postman_collection" if kind == "postman_collection" else "openapi_document"
        name += suffix
    return name[:240]


def _metadata_for_descriptor(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in (
        "document_kind",
        "document_format",
        "openapi_version",
        "openapi_info_version",
        "content_hash",
        "external_ref_count",
        "dependency_fingerprint",
        "resolved_content_hash",
        "resolved_external_ref_count",
        "external_ref_resolution_status",
    ):
        value = descriptor.get(key)
        if value not in {None, ""}:
            metadata[key] = _text(value, 2_000)
    return metadata


def _descriptor_metadata(
    *,
    relationships: Sequence[Mapping[str, Any]],
    aliases: Iterable[str],
) -> dict[str, str]:
    return {
        "source_relationships_json": _bounded_json(relationships, objects=True),
        "aliases_json": _bounded_json(sorted(set(_text(item, 4_000) for item in aliases if _text(item, 4_000)))),
    }


def _coverage(
    remote_id: str,
    reason_code: str,
    *,
    display_title: str = "",
    remote_object_type: str = "openapi_document",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "remote_resource_id": _text(remote_id, 4_000),
        "resource_kind": "openapi-document",
        "state": "UNSUPPORTED",
        "reason_code": reason_code,
        "remote_object_type": remote_object_type,
        "display_title": _text(display_title, 300),
        "retry_trigger": "REMOTE_ACCESS_OR_SCOPE_CHANGE",
        "capability_contract_version": OPENAPI_MATERIALIZATION_CONTRACT_VERSION,
        "metadata": {
            key: value
            for key, value in dict(metadata or {}).items()
            if isinstance(value, (str, int, float, bool)) and value not in {"", None}
        },
    }


def _previous_for_url(
    url: str,
    observations: Mapping[str, Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    direct = dict(observations.get(url) or {})
    if direct:
        return url, direct
    matches: list[tuple[str, dict[str, Any]]] = []
    for remote_id, row in observations.items():
        metadata = dict(row.get("source_metadata") or {}) if isinstance(row, Mapping) else {}
        if url == _text(metadata.get("canonical_url"), 4_000) or url in _json_strings(metadata.get("aliases_json")):
            matches.append((remote_id, dict(row)))
    if len(matches) > 1:
        raise OpenApiConnectorError("openapi_previous_identity_ambiguous")
    return matches[0] if matches else ("", {})


def _merge_descriptor(
    descriptors: dict[str, dict[str, Any]],
    aliases: dict[str, str],
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
    requested_url = _text(descriptor.get("requested_url"), 4_000)
    if not remote_id:
        raise OpenApiConnectorError("openapi_descriptor_identity_missing")
    old_id = aliases.get(requested_url, "")
    if old_id and old_id != remote_id:
        prior = descriptors.pop(old_id, None)
        if prior:
            descriptor["aliases_json"] = _bounded_json(
                [*_json_strings(prior.get("aliases_json")), *_json_strings(descriptor.get("aliases_json"))]
            )
    current = descriptors.get(remote_id)
    if current is None:
        descriptors[remote_id] = descriptor
        current = descriptor
    else:
        current["aliases_json"] = _bounded_json(
            [*_json_strings(current.get("aliases_json")), *_json_strings(descriptor.get("aliases_json"))]
        )
        current["source_relationships_json"] = _merge_relationships(
            current.get("source_relationships_json"), descriptor.get("source_relationships_json")
        )
        for key in (
            "document_kind",
            "document_format",
            "openapi_version",
            "openapi_info_version",
            "content_hash",
            "dependency_fingerprint",
            "resolved_content_hash",
            "resolved_external_ref_count",
            "external_ref_resolution_status",
            "remote_materialization_fingerprint",
            "remote_revision",
            "remote_updated_at",
            "declared_mime",
            "etag",
            "last_modified",
        ):
            if descriptor.get(key) not in {None, ""}:
                current[key] = descriptor[key]
        if descriptor.get("_body") is not None:
            current.update({key: value for key, value in descriptor.items() if key.startswith("_")})
    for alias in [requested_url, *_json_strings(current.get("aliases_json"))]:
        if alias:
            aliases[alias] = remote_id
    return current


def _descriptor_from_previous(
    requested_url: str,
    previous_id: str,
    previous: Mapping[str, Any],
    *,
    parent_remote_id: str,
) -> dict[str, Any]:
    metadata = dict(previous.get("source_metadata") or {})
    aliases = [requested_url, *_json_strings(metadata.get("aliases_json"))]
    return {
        "remote_resource_id": previous_id or requested_url,
        "resource_kind": _text(metadata.get("resource_kind"), 80) or "openapi-document",
        "obj_type": _text(metadata.get("document_kind"), 80) or "openapi_document",
        "display_title": _text(metadata.get("display_title"), 300),
        "canonical_url": _text(metadata.get("canonical_url"), 4_000) or requested_url,
        "parent_remote_id": parent_remote_id or _text(metadata.get("parent_remote_id"), 4_000),
        "remote_revision": _text(metadata.get("remote_revision"), 240),
        "remote_updated_at": _text(metadata.get("remote_updated_at"), 160),
        "declared_mime": _text(metadata.get("declared_mime"), 160),
        "etag": _text(metadata.get("etag"), 1_000),
        "last_modified": _text(metadata.get("last_modified"), 1_000),
        "content_hash": _text(metadata.get("content_hash"), 128),
        "remote_materialization_fingerprint": _text(
            metadata.get("remote_materialization_fingerprint"), 128
        ),
        "document_kind": _text(metadata.get("document_kind"), 80) or "openapi_document",
        "document_format": _text(metadata.get("document_format"), 40) or "json",
        "openapi_version": _text(metadata.get("openapi_version"), 80),
        "openapi_info_version": _text(metadata.get("openapi_info_version"), 240),
        "resolved_content_hash": _text(metadata.get("resolved_content_hash"), 128),
        "resolved_external_ref_count": _text(metadata.get("resolved_external_ref_count"), 80),
        "external_ref_resolution_status": _text(
            metadata.get("external_ref_resolution_status"), 80
        ),
        "source_relationships_json": _text(metadata.get("source_relationships_json"), 100_000),
        "aliases_json": _bounded_json(aliases),
        "not_modified": True,
        "requested_url": requested_url,
    }


def _descriptor_from_response(
    *,
    requested_url: str,
    final_url: str,
    response: OpenApiHttpResponse,
    body: bytes,
    scope: Mapping[str, Any],
    parent_remote_id: str,
    external_reference: bool,
    retain_body: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload, fmt, kind, version, info_version, title = _decode_document(
        body,
        url=final_url,
        response=response,
        external_reference=external_reference,
    )
    relationships, coverage = _external_references(payload, base_url=final_url, scope=scope)
    aliases = [requested_url, final_url]
    descriptor: dict[str, Any] = {
        "remote_resource_id": final_url,
        "resource_kind": "openapi-document",
        "obj_type": kind,
        "display_title": title or Path(urllib.parse.urlsplit(final_url).path).name or final_url,
        "canonical_url": final_url,
        "parent_remote_id": parent_remote_id,
        "remote_revision": (
            "etag:" + _header(response.headers, "ETag")[:240]
            if _header(response.headers, "ETag")
            else "last-modified:" + _header(response.headers, "Last-Modified")[:240]
            if _header(response.headers, "Last-Modified")
            else "sha256:" + _content_hash(body)
        ),
        "remote_updated_at": _header(response.headers, "Last-Modified"),
        "declared_mime": _header(response.headers, "Content-Type").split(";", 1)[0].strip().lower(),
        "etag": _header(response.headers, "ETag"),
        "last_modified": _header(response.headers, "Last-Modified"),
        "content_hash": _content_hash(body),
        "remote_materialization_fingerprint": _content_hash(body),
        "document_kind": kind,
        "document_format": fmt,
        "openapi_version": version,
        "openapi_info_version": info_version,
        "external_ref_count": len(relationships),
        "external_ref_resolution_status": "NOT_ATTEMPTED",
        "requested_url": requested_url,
        "not_modified": False,
        **_descriptor_metadata(relationships=relationships, aliases=aliases),
    }
    if retain_body:
        descriptor["_body"] = body
        descriptor["_parsed_payload"] = payload
    return descriptor, coverage


def _json_pointer_value(payload: Any, fragment: str) -> Any:
    if not fragment:
        return payload
    if not fragment.startswith("#/"):
        raise OpenApiConnectorError("openapi_external_ref_fragment_invalid")
    value = payload
    for raw_token in fragment[2:].split("/"):
        token = urllib.parse.unquote(raw_token).replace("~1", "/").replace("~0", "~")
        if isinstance(value, Mapping):
            if token not in value:
                raise OpenApiConnectorError("openapi_external_ref_fragment_missing")
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            raise OpenApiConnectorError("openapi_external_ref_fragment_missing")
    return value


def _resolve_external_refs(
    value: Any,
    *,
    base_url: str,
    payloads: Mapping[str, Any],
    scope: Mapping[str, Any],
    stack: tuple[str, ...] = (),
    unresolved: list[str] | None = None,
) -> Any:
    unresolved_refs = unresolved if unresolved is not None else []
    if isinstance(value, Mapping):
        raw_ref = value.get("$ref")
        if isinstance(raw_ref, str) and raw_ref.strip() and not raw_ref.lstrip().startswith("#"):
            parsed = urllib.parse.urlsplit(urllib.parse.urljoin(base_url, raw_ref.strip()))
            target = _normalize_url(
                urllib.parse.urlunsplit(
                    (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
                )
            )
            _validate_and_scope_url(target, scope)
            identity = target + "#" + parsed.fragment
            if target not in payloads or identity in stack:
                unresolved_refs.append(identity)
                return dict(value)
            resolved = _resolve_external_refs(
                _json_pointer_value(payloads[target], "#" + parsed.fragment if parsed.fragment else ""),
                base_url=target,
                payloads=payloads,
                scope=scope,
                stack=(*stack, identity),
                unresolved=unresolved_refs,
            )
            if isinstance(resolved, Mapping):
                merged = dict(resolved)
                merged.update(
                    {
                        key: _resolve_external_refs(
                            child,
                            base_url=base_url,
                            payloads=payloads,
                            scope=scope,
                            stack=stack,
                            unresolved=unresolved_refs,
                        )
                        for key, child in value.items()
                        if key != "$ref"
                    }
                )
                merged.setdefault(
                    "x-qualibug-source-ref",
                    {"url": target, "fragment": parsed.fragment},
                )
                return merged
            return resolved
        return {
            str(key): _resolve_external_refs(
                child,
                base_url=base_url,
                payloads=payloads,
                scope=scope,
                stack=stack,
                unresolved=unresolved_refs,
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_external_refs(
                child,
                base_url=base_url,
                payloads=payloads,
                scope=scope,
                stack=stack,
                unresolved=unresolved_refs,
            )
            for child in value
        ]
    return value


def _prepare_external_ref_snapshots(
    descriptors: Mapping[str, dict[str, Any]],
    *,
    scope: Mapping[str, Any],
    coverage: list[dict[str, Any]],
    retain_body: bool,
) -> None:
    payloads = {
        _text(row.get("remote_resource_id"), 4_000): row.get("_parsed_payload")
        for row in descriptors.values()
        if isinstance(row.get("_parsed_payload"), Mapping)
    }
    for descriptor in descriptors.values():
        relationships = _json_objects(descriptor.get("source_relationships_json"))
        if not relationships:
            descriptor["external_ref_resolution_status"] = "NOT_REQUIRED"
            continue
        if _text(descriptor.get("external_ref_resolution_status"), 80) == "BLOCKED":
            continue
        if not scope["resolve_refs"]:
            descriptor["external_ref_resolution_status"] = "BLOCKED"
            coverage.append(_coverage(
                _text(descriptor.get("remote_resource_id"), 4_000),
                "OPENAPI_EXTERNAL_REF_RESOLUTION_DISABLED",
                metadata={"external_ref_count": len(relationships)},
            ))
            continue
        payload = descriptor.get("_parsed_payload")
        if not isinstance(payload, Mapping):
            # A 304 descriptor can remain unchanged without source bytes. It is only a
            # materialization blocker when its dependency fingerprint requires a refresh.
            descriptor["external_ref_resolution_status"] = "BASELINE_UNCHANGED"
            continue
        unresolved: list[str] = []
        try:
            resolved = _resolve_external_refs(
                payload,
                base_url=_text(descriptor.get("remote_resource_id"), 4_000),
                payloads=payloads,
                scope=scope,
                unresolved=unresolved,
            )
        except OpenApiConnectorError as exc:
            unresolved.append(str(exc).split(":", 1)[0])
            resolved = payload
        if unresolved:
            descriptor["external_ref_resolution_status"] = "BLOCKED"
            coverage.append(_coverage(
                _text(descriptor.get("remote_resource_id"), 4_000),
                "OPENAPI_EXTERNAL_REF_UNRESOLVED",
                metadata={"unresolved_ref_count": len(unresolved)},
            ))
            continue
        resolved_body = json.dumps(
            resolved,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor["resolved_content_hash"] = _content_hash(resolved_body)
        descriptor["resolved_external_ref_count"] = len(relationships)
        descriptor["external_ref_resolution_status"] = "RESOLVED"
        if retain_body:
            descriptor["_body"] = resolved_body


def _dependency_fingerprint(
    descriptor: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    observations: Mapping[str, Mapping[str, Any]],
) -> str:
    dependencies: list[dict[str, str]] = []
    for relation in _json_objects(descriptor.get("source_relationships_json")):
        target = _text(relation.get("target_url"), 4_000)
        if not target:
            continue
        child = descriptors.get(target)
        child_hash = _text((child or {}).get("remote_materialization_fingerprint"), 128)
        if not child_hash:
            metadata = dict((observations.get(target) or {}).get("source_metadata") or {})
            child_hash = _text(metadata.get("remote_materialization_fingerprint"), 128)
        dependencies.append({"target_url": target, "fingerprint": child_hash})
    return hashlib.sha256(
        json.dumps(sorted(dependencies, key=lambda row: row["target_url"]), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest() if dependencies else ""


def _finalize_fingerprints(
    descriptors: Mapping[str, dict[str, Any]],
    observations: Mapping[str, Mapping[str, Any]],
) -> None:
    for _ in range(_MAX_REF_DEPTH + 1):
        previous = tuple(
            (
                key,
                _text(row.get("dependency_fingerprint"), 128),
                _text(row.get("remote_materialization_fingerprint"), 128),
            )
            for key, row in sorted(descriptors.items())
        )
        for descriptor in descriptors.values():
            dependency = _dependency_fingerprint(descriptor, descriptors, observations)
            descriptor["dependency_fingerprint"] = dependency
            content_hash = _text(
                descriptor.get("resolved_content_hash")
                or descriptor.get("content_hash"),
                128,
            )
            if content_hash and dependency:
                descriptor["remote_materialization_fingerprint"] = hashlib.sha256(
                    (content_hash + ":" + dependency).encode("utf-8")
                ).hexdigest()
            elif content_hash:
                descriptor["remote_materialization_fingerprint"] = content_hash
        current = tuple(
            (
                key,
                _text(row.get("dependency_fingerprint"), 128),
                _text(row.get("remote_materialization_fingerprint"), 128),
            )
            for key, row in sorted(descriptors.items())
        )
        if current == previous:
            return


def _discover_openapi_resources(
    context: Mapping[str, Any],
    *,
    cursor: SyncCursor = "",
    previous_observations: Mapping[str, Mapping[str, Any]] | None = None,
    retain_body: bool = False,
) -> dict[str, Any]:
    scope = _scope_from_context(context)
    observations = previous_observations or {}
    descriptors: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    coverage: list[dict[str, Any]] = []
    queue: list[tuple[str, str, int, bool]] = [
        (url, "", 0, False) for url in scope["document_urls"]
    ]
    queued = set(scope["document_urls"])
    fetched: set[str] = set()
    total_bytes = 0
    while queue:
        if len(fetched) >= scope["max_documents"]:
            coverage.append(_coverage(
                queue[0][0], "OPENAPI_DOCUMENT_LIMIT_REACHED", remote_object_type="openapi_reference"
            ))
            break
        requested_url, parent_remote_id, depth, external_reference = queue.pop(0)
        if requested_url in fetched:
            continue
        fetched.add(requested_url)
        previous_id, previous = _previous_for_url(requested_url, observations)
        previous_metadata = dict(previous.get("source_metadata") or {})
        request_headers = _auth_headers(context)
        previous_etag = _text(previous_metadata.get("etag"), 1_000)
        previous_modified = _text(previous_metadata.get("last_modified"), 1_000)
        if previous_etag:
            request_headers["If-None-Match"] = previous_etag
        elif previous_modified:
            request_headers["If-Modified-Since"] = previous_modified
        try:
            response = _request(
                context,
                _validate_and_scope_url(requested_url, scope),
                headers=request_headers,
                max_bytes=scope["max_document_bytes"],
            )
        except OpenApiConnectorError as exc:
            coverage.append(_coverage(
                requested_url,
                "OPENAPI_FETCH_BLOCKED",
                remote_object_type="openapi_reference" if external_reference else "openapi_document",
                metadata={"error_code": str(exc).split(":", 1)[0]},
            ))
            continue
        total_bytes += len(response.body)
        if total_bytes > scope["max_total_bytes"]:
            coverage.append(_coverage(requested_url, "OPENAPI_TOTAL_SIZE_LIMIT_REACHED"))
            break
        if response.status == 304:
            if not previous:
                coverage.append(_coverage(requested_url, "OPENAPI_NOT_MODIFIED_WITHOUT_BASELINE"))
                continue
            descriptor = _descriptor_from_previous(
                requested_url,
                previous_id or requested_url,
                previous,
                parent_remote_id=parent_remote_id,
            )
            current = _merge_descriptor(descriptors, aliases, descriptor)
            if scope["resolve_refs"]:
                for relation in _json_objects(current.get("source_relationships_json")):
                    target = _text(relation.get("target_url"), 4_000)
                    if target and target not in queued and depth < scope["max_ref_depth"]:
                        queued.add(target)
                        queue.append((target, _text(current.get("remote_resource_id"), 4_000), depth + 1, True))
            continue
        if response.status != 200:
            coverage.append(_coverage(
                requested_url,
                f"OPENAPI_FETCH_HTTP_{response.status}",
                remote_object_type="openapi_reference" if external_reference else "openapi_document",
            ))
            continue
        final_url = _text(response.final_url, 4_000) or requested_url
        try:
            final_url = _validate_and_scope_url(final_url, scope)
            descriptor, ref_coverage = _descriptor_from_response(
                requested_url=requested_url,
                final_url=final_url,
                response=response,
                body=response.body,
                scope=scope,
                parent_remote_id=parent_remote_id,
                external_reference=external_reference,
                retain_body=retain_body,
            )
        except OpenApiConnectorError as exc:
            reason = "OPENAPI_DOCUMENT_INVALID"
            if "shape_unrecognized" in str(exc):
                reason = "OPENAPI_DOCUMENT_SHAPE_UNRECOGNIZED"
            elif "parse_failed" in str(exc) or "decode" in str(exc):
                reason = "OPENAPI_DOCUMENT_PARSE_FAILED"
            coverage.append(_coverage(
                requested_url,
                reason,
                remote_object_type="openapi_reference" if external_reference else "openapi_document",
                metadata={"error_code": str(exc).split(":", 1)[0]},
            ))
            continue
        coverage.extend(ref_coverage)
        current = _merge_descriptor(descriptors, aliases, descriptor)
        for relation in _json_objects(current.get("source_relationships_json")):
            target = _text(relation.get("target_url"), 4_000)
            if not target:
                continue
            if not scope["resolve_refs"]:
                continue
            if depth >= scope["max_ref_depth"]:
                coverage.append(_coverage(
                    target,
                    "OPENAPI_REF_DEPTH_LIMIT",
                    remote_object_type="openapi_reference",
                    metadata={"source_pointer": _text(relation.get("source_pointer"), 4_000)},
                ))
                continue
            if target not in queued:
                queued.add(target)
                queue.append((target, _text(current.get("remote_resource_id"), 4_000), depth + 1, True))
    _prepare_external_ref_snapshots(
        descriptors,
        scope=scope,
        coverage=coverage,
        retain_body=retain_body,
    )
    _finalize_fingerprints(descriptors, observations)

    # A conditional 304 has no source bytes. If a referenced document changed, refresh the
    # parent once so the existing Source Occurrence receives a real snapshot and its dependency
    # fingerprint is observable. No retry loop is allowed inside one sync round.
    for descriptor in list(descriptors.values()):
        if not descriptor.get("not_modified"):
            continue
        prior_fp = _text(
            (observations.get(_text(descriptor.get("remote_resource_id"), 4_000)) or {})
            .get("source_metadata", {})
            .get("remote_materialization_fingerprint"),
            128,
        )
        if prior_fp and prior_fp == _text(descriptor.get("remote_materialization_fingerprint"), 128):
            continue
        remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
        try:
            response = _request(
                context,
                _validate_and_scope_url(remote_id, scope),
                headers=_auth_headers(context),
                max_bytes=scope["max_document_bytes"],
            )
            if response.status != 200:
                raise OpenApiConnectorError(f"openapi_parent_refresh_http_{response.status}")
            refreshed, refresh_coverage = _descriptor_from_response(
                requested_url=remote_id,
                final_url=_text(response.final_url, 4_000) or remote_id,
                response=response,
                body=response.body,
                scope=scope,
                parent_remote_id=_text(descriptor.get("parent_remote_id"), 4_000),
                external_reference=_text(descriptor.get("obj_type"), 80) == "openapi_reference",
                retain_body=retain_body,
            )
            coverage.extend(refresh_coverage)
            descriptor.update(refreshed)
        except OpenApiConnectorError as exc:
            coverage.append(_coverage(
                remote_id,
                "OPENAPI_DEPENDENCY_REFRESH_FAILED",
                metadata={"error_code": str(exc).split(":", 1)[0]},
            ))
    _prepare_external_ref_snapshots(
        descriptors,
        scope=scope,
        coverage=coverage,
        retain_body=retain_body,
    )
    _finalize_fingerprints(descriptors, observations)
    clean = sorted(descriptors.values(), key=lambda row: _text(row.get("remote_resource_id"), 4_000))
    return {
        "schema": OPENAPI_ADAPTER_SCHEMA,
        "descriptors": clean,
        "complete": not coverage and not queue,
        "coverage": {
            "discovered_count": len(clean),
            "blocked_count": len(coverage),
            "total_bytes": total_bytes,
            "observations": coverage,
        },
        "lifecycle": coverage,
        "next_cursor": _snapshot_cursor(clean),
        "previous_cursor_supplied": bool(cursor),
    }


def _snapshot_cursor(descriptors: Sequence[Mapping[str, Any]]) -> str:
    if len(descriptors) > _MAX_CURSOR_DESCRIPTORS:
        raise OpenApiConnectorError("openapi_cursor_descriptor_limit_exceeded")
    payload = [
        {
            "remote_resource_id": _text(row.get("remote_resource_id"), 4_000),
            "remote_revision": _text(row.get("remote_revision"), 240),
            "content_hash": _text(row.get("content_hash"), 128),
            "remote_materialization_fingerprint": _text(row.get("remote_materialization_fingerprint"), 128),
            "openapi_info_version": _text(row.get("openapi_info_version"), 240),
            "dependency_fingerprint": _text(row.get("dependency_fingerprint"), 128),
        }
        for row in descriptors
    ]
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "openapi-snapshot-v1:" + digest


def _adapter_capability(
    descriptor: Mapping[str, Any],
    *,
    connector_type: str = OPENAPI_CONNECTOR_TYPE,
) -> ResourceCapability:
    return classify_materialization_capability(
        descriptor,
        connector_type=_normalize_openapi_connector_type(connector_type),
        materializable_types=tuple(_SUPPORTED_OBJECT_TYPES),
        contract_version=OPENAPI_MATERIALIZATION_CONTRACT_VERSION,
    )


def _materialize_openapi_resource(
    context: ConnectorContext,
    descriptor: Mapping[str, Any],
) -> MaterializedSnapshot:
    connector_type = _normalize_openapi_connector_type(
        context.get("connector_type")
    )
    capability = _adapter_capability(descriptor, connector_type=connector_type)
    if not capability.materializable:
        raise OpenApiConnectorError(f"openapi_resource_not_materializable:{capability.reason_code}")
    body = descriptor.get("_body")
    response_headers: Mapping[str, str] = {}
    if not isinstance(body, (bytes, bytearray, memoryview)):
        remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
        if not remote_id:
            raise OpenApiConnectorError("openapi_materialization_body_missing")
        scope = _scope_from_context(context)
        response = _request(
            context,
            _validate_and_scope_url(remote_id, scope),
            headers=_auth_headers(context),
            max_bytes=scope["max_document_bytes"],
        )
        if response.status != 200:
            raise OpenApiConnectorError(f"openapi_materialization_http_{response.status}")
        _decode_document(
            response.body,
            url=_text(response.final_url, 4_000) or remote_id,
            response=response,
            external_reference=_text(descriptor.get("obj_type"), 80) == "openapi_reference",
        )
        body = response.body
        response_headers = dict(response.headers)
    blob = bytes(body)
    if not blob:
        raise OpenApiConnectorError("openapi_materialization_content_missing")
    if len(blob) > MAX_SOURCE_BYTES:
        raise OpenApiConnectorError("openapi_materialization_size_limit_exceeded")
    kind = _text(descriptor.get("obj_type"), 80)
    fmt = _text(descriptor.get("document_format"), 40) or "json"
    source_type = (
        "postman"
        if kind == "postman_collection"
        else "other_document"
        if kind == "openapi_reference"
        else "openapi"
    )
    return {
        "remote_resource_id": _text(descriptor.get("remote_resource_id"), 4_000),
        "resource_kind": _text(descriptor.get("resource_kind"), 80) or "openapi-document",
        "display_title": _text(descriptor.get("display_title"), 300),
        "source_type": source_type,
        "filename": _filename(_text(descriptor.get("remote_resource_id"), 4_000), kind=kind, fmt=fmt),
        "content": blob,
        "export_format": fmt,
        "declared_mime": _text(descriptor.get("declared_mime"), 160)
        or _header(response_headers, "Content-Type").split(";", 1)[0].strip().lower(),
        "remote_revision": _text(descriptor.get("remote_revision"), 240),
        "remote_updated_at": _text(descriptor.get("remote_updated_at"), 160),
        "retrieved_at": _utc_now(),
        "remote_materialization_fingerprint": _text(
            descriptor.get("remote_materialization_fingerprint"), 128
        ) or _content_hash(blob),
        "canonical_url": _text(descriptor.get("canonical_url"), 4_000),
        "parent_remote_id": _text(descriptor.get("parent_remote_id"), 4_000),
        "etag": _text(descriptor.get("etag"), 1_000),
        "last_modified": _text(descriptor.get("last_modified"), 1_000),
        "source_relationships_json": _text(descriptor.get("source_relationships_json"), 100_000),
        "aliases_json": _text(descriptor.get("aliases_json"), 100_000),
        "metadata": _metadata_for_descriptor(descriptor),
    }


def _observation_metadata(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "display_title",
        "canonical_url",
        "parent_remote_id",
        "remote_revision",
        "remote_updated_at",
        "declared_mime",
        "remote_materialization_fingerprint",
        "etag",
        "last_modified",
        "source_relationships_json",
        "aliases_json",
    ):
        value = descriptor.get(key)
        if value not in {None, ""}:
            result[key] = _text(value, 100_000 if key.endswith("_json") else 4_000)
    result.update(_metadata_for_descriptor(descriptor))
    return result


def _connector_instance(
    project: str,
    connector: str,
    root: Path,
    *,
    connector_type: str = OPENAPI_CONNECTOR_TYPE,
) -> dict[str, Any]:
    expected_type = _normalize_openapi_connector_type(connector_type)
    rows = list_connector_instances(project, root=root, include_disabled=True).get("connector_instances") or []
    instance = next(
        (
            dict(row)
            for row in rows
            if isinstance(row, dict) and _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if instance is None:
        raise OpenApiConnectorError("openapi_connector_instance_not_registered")
    if _text(instance.get("connector_type"), 160).lower() != expected_type:
        raise OpenApiConnectorError("openapi_connector_instance_type_mismatch")
    if instance.get("status") != "ACTIVE":
        raise OpenApiConnectorError("openapi_connector_instance_not_active")
    return instance


def openapi_connector_manifest(
    connector_type: str = OPENAPI_CONNECTOR_TYPE,
    *,
    display_name: str = "",
) -> ConnectorManifest:
    normalized_type = _normalize_openapi_connector_type(connector_type)
    names = {
        OPENAPI_CONNECTOR_TYPE: "Online OpenAPI and API documents",
        "apifox": "Apifox OpenAPI export",
        "yapi": "YApi OpenAPI export",
    }
    return ConnectorManifest(
        connector_type=normalized_type,
        display_name=_text(display_name, 240) or names[normalized_type],
        category="api_contract",
        version="1",
        auth_modes=("anonymous", "bearer_token", "api_key", "cookie_session"),
        scope_schema={
            "type": "object",
            "description": "Read-only JSON/YAML API-document URLs with bounded external $ref traversal.",
            "required": ["document_urls"],
            "properties": {
                "document_urls": {"type": "array", "maxItems": _MAX_DOCUMENT_URLS},
                "allowed_domains": {"type": "array", "maxItems": _MAX_DOMAINS},
                "max_documents": {"type": "integer", "minimum": 1, "maximum": _MAX_DOCUMENTS},
                "max_document_bytes": {"type": "integer", "minimum": 1_024, "maximum": _MAX_DOCUMENT_BYTES},
                "max_total_bytes": {"type": "integer", "minimum": 1_024, "maximum": _MAX_TOTAL_BYTES},
                "resolve_refs": {"type": "boolean", "default": True},
                "max_ref_depth": {"type": "integer", "minimum": 0, "maximum": _MAX_REF_DEPTH},
            },
        },
        quick_connect_schema=(
            {
                "input_type": "url",
                "scope_field": "document_urls",
                "priority": 20,
            }
            if normalized_type == OPENAPI_CONNECTOR_TYPE
            else {}
        ),
        supported_resource_types=tuple(sorted(_SUPPORTED_OBJECT_TYPES)),
        sync_modes=("FULL", "INCREMENTAL"),
        read_only=True,
        credential_fields=(
            ConnectorCredentialField(
                name="token",
                field_type="token",
                required=True,
                secret=True,
                display_name="只读 Bearer Token",
                description="Bearer token used only for read-only document GET requests.",
                auth_modes=("bearer_token",),
            ),
            ConnectorCredentialField(
                name="header_name",
                field_type="text",
                required=True,
                display_name="API Key 请求头",
                description="Explicit API-key request header name.",
                auth_modes=("api_key",),
            ),
            ConnectorCredentialField(
                name="api_key",
                field_type="token",
                required=True,
                secret=True,
                display_name="API Key",
                description="API-key value used only for read-only document GET requests.",
                auth_modes=("api_key",),
            ),
            ConnectorCredentialField(
                name="session_cookie",
                field_type="cookie_session_reference",
                required=True,
                secret=True,
                display_name="登录会话 Cookie",
                description="Cookie session reference used only for read-only document GET requests.",
                auth_modes=("cookie_session",),
            ),
        ),
        capability_contract_version=OPENAPI_MATERIALIZATION_CONTRACT_VERSION,
    )


def test_openapi_connector_connection(
    project_id: str,
    *,
    connector_instance_id: str,
    connector_type: str = OPENAPI_CONNECTOR_TYPE,
    resolve_connection_profile: Callable[[str], Mapping[str, Any]] | None = None,
    root: Path | None = None,
    transport: OpenApiTransport | None = None,
    timeout: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    normalized_type = _normalize_openapi_connector_type(connector_type)
    instance = _connector_instance(
        project_id,
        connector_instance_id,
        resolved_root,
        connector_type=normalized_type,
    )
    context: dict[str, Any] = {
        "project_id": project_id,
        "connector_instance_id": connector_instance_id,
        "connector_type": normalized_type,
        "connection_profile_ref": _text(instance.get("connection_profile_ref"), 500),
        "resolve_connection_profile": resolve_connection_profile,
        "resource_scope": _text(instance.get("resource_scope"), 20_000),
        "transport": transport,
        "timeout": timeout,
        "sleeper": sleeper,
    }
    scope = _scope_from_context(context)
    _profile_for_context(context)
    url = _validate_and_scope_url(scope["document_urls"][0], scope)
    response = _request(context, url, headers=_auth_headers(context), max_bytes=scope["max_document_bytes"])
    if response.status != 200:
        raise OpenApiConnectorError(f"openapi_connection_http_{response.status}")
    _decode_document(
        response.body,
        url=_text(response.final_url, 4_000) or url,
        response=response,
        external_reference=False,
    )
    return {
        "schema": OPENAPI_ADAPTER_SCHEMA,
        "status": "AVAILABLE",
        "connector_instance_id": connector_instance_id,
        "connector_type": normalized_type,
        "auth_mode": _profile_for_context(context)["auth_mode"],
        "document_url_count": len(scope["document_urls"]),
        "credentials_persisted": False,
        "source_content_returned": False,
        "network_side_effect": "READ_ONLY_GET",
    }


def sync_openapi_connector(
    project_id: str,
    *,
    connector_instance_id: str,
    connector_type: str = OPENAPI_CONNECTOR_TYPE,
    resolve_connection_profile: Callable[[str], Mapping[str, Any]] | None = None,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    previous_cursor: str = "",
    deletion_policy: str = "RETAIN",
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    max_nodes: int = _DEFAULT_MAX_DOCUMENTS,
    transport: OpenApiTransport | None = None,
    timeout: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    normalized_type = _normalize_openapi_connector_type(connector_type)
    instance = _connector_instance(
        project_id,
        connector_instance_id,
        resolved_root,
        connector_type=normalized_type,
    )
    stored_hash = _text(instance.get("last_committed_cursor_fingerprint"), 128)
    if stored_hash and not previous_cursor:
        raise OpenApiConnectorError("openapi_previous_cursor_required")
    context: dict[str, Any] = {
        "project_id": project_id,
        "connector_instance_id": connector_instance_id,
        "connector_type": normalized_type,
        "connection_profile_ref": _text(instance.get("connection_profile_ref"), 500),
        "resolve_connection_profile": resolve_connection_profile,
        "resource_scope": _text(instance.get("resource_scope"), 20_000),
        "transport": transport,
        "timeout": timeout,
        "sleeper": sleeper,
    }
    scope = _scope_from_context(context)
    if max_nodes > 0:
        scope["max_documents"] = min(scope["max_documents"], int(max_nodes))
        context["resource_scope"] = json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
    observations = connector_snapshot_observation_index(
        project_id,
        connector_instance_id=connector_instance_id,
        root=resolved_root,
    )
    discovery = _discover_openapi_resources(
        context,
        cursor=previous_cursor,
        previous_observations=observations,
        retain_body=True,
    )
    descriptors = [dict(row) for row in discovery.get("descriptors") or []]
    coverage = [dict(row) for row in (discovery.get("coverage") or {}).get("observations") or []]
    items: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for descriptor in descriptors:
        remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
        existing = dict(observations.get(remote_id) or {})
        existing_metadata = dict(existing.get("source_metadata") or {})
        capability = _adapter_capability(
            descriptor,
            connector_type=normalized_type,
        )
        if capability.observable_unsupported:
            coverage.append(_coverage(
                remote_id,
                capability.reason_code,
                display_title=_text(descriptor.get("display_title"), 300),
                remote_object_type=capability.remote_object_type,
            ))
            continue
        if not capability.materializable:
            raise OpenApiConnectorError("openapi_descriptor_invalid")
        if _text(descriptor.get("external_ref_resolution_status"), 80) == "BLOCKED":
            continue
        fingerprint = _text(descriptor.get("remote_materialization_fingerprint"), 128)
        if existing and fingerprint and _text(existing_metadata.get("remote_materialization_fingerprint"), 128) == fingerprint:
            unchanged.append({
                "remote_resource_id": remote_id,
                "resource_kind": _text(descriptor.get("resource_kind"), 80) or "openapi-document",
                "metadata": _observation_metadata(descriptor),
            })
            continue
        try:
            items.append(dict(_materialize_openapi_resource(context, descriptor)))
        except OpenApiConnectorError as exc:
            coverage.append(_coverage(
                remote_id,
                "OPENAPI_MATERIALIZATION_FAILED",
                metadata={"error_code": str(exc).split(":", 1)[0]},
            ))
    next_cursor = _snapshot_cursor(descriptors)
    snapshot_complete = bool(discovery.get("complete")) and not coverage
    requested_policy = _text(deletion_policy, 32).upper() or "RETAIN"
    if requested_policy not in {"RETAIN", "RETIRE_MISSING"}:
        raise OpenApiConnectorError("openapi_deletion_policy_invalid")
    effective_policy = requested_policy
    retirement_skip_reason = ""
    if requested_policy == "RETIRE_MISSING" and not snapshot_complete:
        effective_policy = "RETAIN"
        retirement_skip_reason = "INCOMPLETE_SNAPSHOT_ACCESS_OR_SCOPE_GAP"
    try:
        run = sync_connector_snapshot_batch(
            project_id,
            connector_instance_id=connector_instance_id,
            items=items,
            unchanged_observations=unchanged,
            coverage_observations=coverage,
            root=resolved_root,
            actor=actor,
            sync_mode="FULL",
            previous_cursor=previous_cursor,
            next_cursor=next_cursor,
            deletion_policy=effective_policy,
            snapshot_complete=snapshot_complete,
            max_retire_count=max_retire_count,
            max_retire_ratio=max_retire_ratio,
        )
    except (ConnectorSyncError, ConnectorSnapshotError) as exc:
        raise OpenApiConnectorError(f"openapi_sync_rejected:{exc}") from exc
    return {
        **run,
        "adapter_schema": OPENAPI_ADAPTER_SCHEMA,
        "adapter": normalized_type,
        "connector_type": normalized_type,
        "auth_mode": _profile_for_context(context)["auth_mode"],
        "discovered_resource_count": len(descriptors),
        "materialized_resource_count": len(items),
        "unchanged_resource_count": len(unchanged),
        "coverage_observation_count": len(coverage),
        "snapshot_complete": snapshot_complete,
        "deletion_policy_requested": requested_policy,
        "deletion_policy_effective": effective_policy,
        "retirement_skip_reason": retirement_skip_reason,
        "next_cursor": next_cursor,
        "next_cursor_persisted_by_adapter": False,
        "credentials_persisted": False,
        "source_content_persisted_in_adapter_receipt": False,
        "connector_parser_implemented": False,
    }


class OpenApiConnectorAdapter:
    """ConnectorAdapter facade over the online API-document authority."""

    def __init__(self, connector_type: str = OPENAPI_CONNECTOR_TYPE) -> None:
        self._connector_type = _normalize_openapi_connector_type(connector_type)

    def manifest(self) -> ConnectorManifest:
        return openapi_connector_manifest(self._connector_type)

    def test_connection(self, context: ConnectorContext) -> dict[str, Any]:
        project_id = _text(context.get("project_id"), 160)
        connector_id = _text(context.get("connector_instance_id"), 160)
        if not project_id or not connector_id:
            raise OpenApiConnectorError("openapi_connector_context_identity_missing")
        return test_openapi_connector_connection(
            project_id,
            connector_instance_id=connector_id,
            connector_type=self._connector_type,
            resolve_connection_profile=context.get("resolve_connection_profile"),
            root=context.get("root"),
            transport=context.get("transport"),
            timeout=float(context.get("timeout", 15.0)),
            sleeper=context.get("sleeper", time.sleep),
        )

    def discover(self, context: ConnectorContext, cursor: SyncCursor = "") -> DiscoveryResult:
        working_context = dict(context)
        working_context["connector_type"] = self._connector_type
        result = _discover_openapi_resources(
            working_context,
            cursor=cursor,
            retain_body=True,
        )
        result["next_cursor"] = self.build_cursor(result)
        return result

    def classify_resource(self, descriptor: Mapping[str, Any]) -> ResourceCapability:
        return _adapter_capability(
            descriptor,
            connector_type=self._connector_type,
        )

    def materialize(self, context: ConnectorContext, descriptor: Mapping[str, Any]) -> MaterializedSnapshot:
        working_context = dict(context)
        working_context["connector_type"] = self._connector_type
        return _materialize_openapi_resource(working_context, descriptor)

    def build_cursor(self, discovery_result: DiscoveryResult | Sequence[Mapping[str, Any]]) -> SyncCursor:
        descriptors = (
            discovery_result.get("descriptors")
            if isinstance(discovery_result, Mapping)
            else discovery_result
        )
        if not isinstance(descriptors, Sequence) or isinstance(descriptors, (str, bytes, bytearray)):
            raise OpenApiConnectorError("openapi_discovery_descriptors_missing")
        return _snapshot_cursor([dict(item) for item in descriptors if isinstance(item, Mapping)])

    def managed_remote_checkpoint(self, context: ConnectorContext) -> SyncCursor:
        working_context = dict(context)
        working_context["connector_type"] = self._connector_type
        result = _discover_openapi_resources(working_context, retain_body=False)
        return self.build_cursor(result)

    def managed_sync(self, context: ConnectorContext) -> dict[str, Any]:
        return sync_openapi_connector(
            _text(context.get("project_id"), 160),
            connector_instance_id=_text(context.get("connector_instance_id"), 160),
            connector_type=self._connector_type,
            resolve_connection_profile=context.get("resolve_connection_profile"),
            root=context.get("root"),
            actor=dict(context.get("actor") or {}),
            previous_cursor=_text(context.get("previous_cursor"), 20_000),
            deletion_policy=_text(context.get("deletion_policy"), 32) or "RETAIN",
            max_retire_count=int(context.get("max_retire_count", 100)),
            max_retire_ratio=float(context.get("max_retire_ratio", 0.25)),
            max_nodes=int(context.get("max_resources", _DEFAULT_MAX_DOCUMENTS)),
            transport=context.get("transport"),
            timeout=float(context.get("timeout", 15.0)),
            sleeper=context.get("sleeper", time.sleep),
        )


__all__ = [
    "OPENAPI_ADAPTER_SCHEMA",
    "OPENAPI_CONNECTOR_TYPE",
    "OPENAPI_EXPORT_CONNECTOR_TYPES",
    "OPENAPI_MATERIALIZATION_CONTRACT_VERSION",
    "OpenApiConnectorAdapter",
    "OpenApiConnectorError",
    "OpenApiHttpResponse",
    "sync_openapi_connector",
    "test_openapi_connector_connection",
    "openapi_connector_manifest",
]
