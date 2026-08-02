"""Bounded read-only entrypoint preflight for manifest-driven connector onboarding.

The preflight only helps an operator choose among already installed URL-capable connector
manifests. It does not configure a connector, persist a cursor, return source bytes, infer a
business rule, or replace the connector sync authority. Response inspection is limited to
transport metadata and structural document shape.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlsplit

import yaml

from .connector_registry import ConnectorManifest, ConnectorRegistry, build_default_connector_registry
from .ssrf_guard import SsrfBlockedError, safe_urlopen, validate_url

SOURCE_PREFLIGHT_SCHEMA = "qualibug.connector-source-preflight.v1"
_DEFAULT_TIMEOUT_SECONDS = 5.0
_MAX_TIMEOUT_SECONDS = 15.0
_DEFAULT_MAX_BYTES = 64 * 1024
_MAX_BYTES = 128 * 1024
_MIN_BYTES = 4 * 1024
_MAX_URL_LENGTH = 4_000
_MAX_CANDIDATES = 64
_SECRET_QUERY_KEY = re.compile(
    r"(?i)(?:token|secret|password|passwd|api[_-]?key|authorization|credential|signature)"
)
_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
_JSON_CONTENT_TYPES = {"application/json", "text/json"}
_YAML_CONTENT_TYPES = {
    "application/yaml",
    "application/x-yaml",
    "text/yaml",
    "text/x-yaml",
}


class SourcePreflightError(ValueError):
    """The bounded source-entry preflight could not produce trustworthy evidence."""


@dataclass(frozen=True)
class SourcePreflightHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str = ""


SourcePreflightTransport = Callable[
    [str, Mapping[str, str], float, int],
    SourcePreflightHttpResponse,
]


def _text(value: Any, limit: int = 1_000) -> str:
    return str(value or "").strip()[:limit]


def _header(headers: Mapping[str, Any], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return _text(value, 4_000)
    return ""


def _content_type(headers: Mapping[str, Any]) -> str:
    return _header(headers, "Content-Type").split(";", 1)[0].strip().lower()


def _validate_source_url(value: Any) -> str:
    url = _text(value, _MAX_URL_LENGTH)
    if not url:
        raise SourcePreflightError("source_preflight_url_required")
    if len(url) > _MAX_URL_LENGTH:
        raise SourcePreflightError("source_preflight_url_too_long")
    parsed = urlsplit(url)
    if parsed.fragment:
        raise SourcePreflightError("source_preflight_fragment_not_allowed")
    if parsed.query:
        for key, _value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
        ):
            if _SECRET_QUERY_KEY.search(key):
                raise SourcePreflightError("source_preflight_credential_query_not_allowed")
    try:
        validate_url(url, allow_internal=False)
    except SsrfBlockedError as exc:
        raise SourcePreflightError("source_preflight_ssrf_blocked") from exc
    return url


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
) -> SourcePreflightHttpResponse:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with safe_urlopen(request, timeout=timeout, allow_internal=False) as response:
            content_length = _header(response.headers, "Content-Length")
            if content_length.isdigit() and int(content_length) > max_bytes:
                raise SourcePreflightError("source_preflight_response_size_limit_exceeded")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise SourcePreflightError("source_preflight_response_size_limit_exceeded")
            return SourcePreflightHttpResponse(
                status=int(getattr(response, "status", response.getcode())),
                headers={str(key): str(value) for key, value in response.headers.items()},
                body=bytes(body),
                final_url=_text(response.geturl(), _MAX_URL_LENGTH),
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(max_bytes + 1)
        if len(body) > max_bytes:
            body = body[:max_bytes]
        return SourcePreflightHttpResponse(
            status=int(exc.code),
            headers={str(key): str(value) for key, value in exc.headers.items()},
            body=bytes(body),
            final_url=_text(exc.geturl(), _MAX_URL_LENGTH),
        )
    except SourcePreflightError:
        raise
    except (urllib.error.URLError, TimeoutError, SsrfBlockedError) as exc:
        raise SourcePreflightError(
            f"source_preflight_transport_failed:{type(exc).__name__}"
        ) from exc


def _path_suffix(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    if not path:
        return ""
    suffix = PurePosixPath(path).suffix.lower()
    return suffix if suffix else ""


def _looks_like_html(url: str, response: SourcePreflightHttpResponse, mime: str) -> bool:
    if mime in _HTML_CONTENT_TYPES:
        return True
    if _path_suffix(url) in {".html", ".htm"}:
        return True
    prefix = response.body.lstrip()[:1_024].lower()
    return prefix.startswith(b"<!doctype html") or b"<html" in prefix


def _document_shapes(response: SourcePreflightHttpResponse, mime: str) -> set[str]:
    if not response.body or mime in _HTML_CONTENT_TYPES:
        return set()
    try:
        text = response.body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return set()
    parsed: Any
    try:
        if mime in _JSON_CONTENT_TYPES or text.lstrip().startswith(("{", "[")):
            parsed = json.loads(text)
        elif mime in _YAML_CONTENT_TYPES or ":" in text[:4_096]:
            parsed = yaml.safe_load(text)
        else:
            return set()
    except (TypeError, ValueError, yaml.YAMLError):
        return set()
    if not isinstance(parsed, Mapping):
        return set()
    shapes: set[str] = set()
    if (parsed.get("openapi") or parsed.get("swagger")) and isinstance(
        parsed.get("paths"), Mapping
    ):
        shapes.add("openapi_document")
    if isinstance(parsed.get("item"), list) and (
        isinstance(parsed.get("info"), Mapping)
        or parsed.get("variable") is not None
    ):
        shapes.add("postman_collection")
    if parsed.get("openapi") or parsed.get("swagger"):
        shapes.add("openapi_reference")
    return shapes


def _manifest_candidates(
    manifests: list[ConnectorManifest],
    *,
    mime: str,
    shapes: set[str],
    suffix: str,
    authorization_required: bool,
    remote_error: bool,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for manifest in manifests:
        quick = dict(manifest.quick_connect_schema)
        if quick.get("input_type") != "url":
            continue
        evidence_declaration = dict(manifest.entrypoint_evidence)
        evidence: list[str] = []
        declared_content_types = set(evidence_declaration.get("content_types", ()))
        declared_shapes = set(evidence_declaration.get("document_shapes", ()))
        shape_evidence = [
            f"document_shape:{shape}"
            for shape in sorted(shapes & declared_shapes)
        ]
        evidence.extend(shape_evidence)
        shape_is_required_for_match = bool(declared_shapes)
        if mime and mime in declared_content_types and (
            not shape_is_required_for_match or shape_evidence
        ):
            evidence.append("content_type")
        declared_suffixes = set(evidence_declaration.get("path_suffixes", ()))
        if suffix and suffix in declared_suffixes and (
            not shape_is_required_for_match or shape_evidence
        ):
            evidence.append("path_suffix")
        score = (
            300 * len([item for item in evidence if item.startswith("document_shape:")])
            + 200 * int("content_type" in evidence)
            + 150 * int("path_suffix" in evidence)
        )
        priority = quick.get("priority", 100)
        if not isinstance(priority, int):
            priority = 100
        match_status = "REVIEW_REQUIRED" if authorization_required or remote_error else (
            "MATCHED" if evidence else "AVAILABLE"
        )
        if authorization_required:
            reason_code = "AUTHORIZATION_REQUIRED"
        elif remote_error:
            reason_code = "REMOTE_RESPONSE_NOT_SUCCESS"
        else:
            reason_code = (
                "DECLARED_RESPONSE_EVIDENCE"
                if evidence
                else "MANIFEST_URL_ENTRYPOINT"
            )
        candidates.append(
            {
                "connector_type": manifest.connector_type,
                "display_name": manifest.display_name,
                "category": manifest.category,
                "scope_field": _text(quick.get("scope_field"), 160),
                "match_status": match_status,
                "reason_code": reason_code,
                "evidence": evidence,
                "priority": priority,
                "requires_user_confirmation": True,
            }
        )
        candidates[-1]["_score"] = score
    candidates.sort(
        key=lambda row: (
            -int(row.pop("_score", 0)),
            int(row.get("priority", 100)),
            str(row.get("connector_type", "")),
        )
    )
    return candidates[:_MAX_CANDIDATES]


def preflight_source_entry(
    raw_url: str,
    *,
    registry: ConnectorRegistry | None = None,
    transport: SourcePreflightTransport | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Read one bounded anonymous GET and return manifest candidates with evidence only."""
    url = _validate_source_url(raw_url)
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise SourcePreflightError("source_preflight_timeout_invalid") from exc
    if not 1.0 <= timeout_value <= _MAX_TIMEOUT_SECONDS:
        raise SourcePreflightError("source_preflight_timeout_out_of_range")
    if isinstance(max_bytes, bool):
        raise SourcePreflightError("source_preflight_max_bytes_out_of_range")
    try:
        max_bytes_value = int(max_bytes)
    except (TypeError, ValueError) as exc:
        raise SourcePreflightError("source_preflight_max_bytes_invalid") from exc
    if not _MIN_BYTES <= max_bytes_value <= _MAX_BYTES:
        raise SourcePreflightError("source_preflight_max_bytes_out_of_range")
    request_headers = {
        "Accept": "text/html,application/xhtml+xml,application/json,application/yaml,text/yaml,*/*;q=0.1",
        "User-Agent": "QualiBug-Connector-Source-Preflight/1",
    }
    selected_transport = transport or _default_transport
    if not callable(selected_transport):
        raise SourcePreflightError("source_preflight_transport_invalid")
    response = selected_transport(url, request_headers, timeout_value, max_bytes_value)
    if not isinstance(response, SourcePreflightHttpResponse):
        raise SourcePreflightError("source_preflight_transport_response_invalid")
    if not isinstance(response.body, (bytes, bytearray, memoryview)):
        raise SourcePreflightError("source_preflight_transport_body_invalid")
    body = bytes(response.body)
    if len(body) > max_bytes_value:
        raise SourcePreflightError("source_preflight_response_size_limit_exceeded")
    if response.status < 100 or response.status > 599:
        raise SourcePreflightError("source_preflight_response_status_invalid")
    final_url = _text(response.final_url, _MAX_URL_LENGTH) or url
    if final_url != url:
        try:
            validate_url(final_url, allow_internal=False)
        except SsrfBlockedError as exc:
            raise SourcePreflightError("source_preflight_redirect_ssrf_blocked") from exc
    mime = _content_type(response.headers)
    shapes = _document_shapes(response, mime)
    if _looks_like_html(url, response, mime):
        shapes.add("html_page")
    suffix = _path_suffix(url)
    authorization_required = response.status in {401, 403}
    remote_error = response.status < 200 or response.status >= 400
    registry_value = registry or build_default_connector_registry()
    candidates = _manifest_candidates(
        registry_value.manifests(),
        mime=mime,
        shapes=shapes,
        suffix=suffix,
        authorization_required=authorization_required,
        remote_error=remote_error,
    )
    matched = [row for row in candidates if row["match_status"] == "MATCHED"]
    status = (
        "AUTHORIZATION_REQUIRED"
        if authorization_required
        else "REMOTE_ERROR"
        if remote_error
        else "READY"
        if len(matched) == 1
        else "NEEDS_USER_CONFIRMATION"
        if candidates
        else "NO_QUICK_CONNECTOR"
    )
    recommended = matched[0]["connector_type"] if len(matched) == 1 else ""
    final_host = (urlsplit(final_url).hostname or "").lower()
    observation = {
        "http_status": response.status,
        "content_type": mime,
        "response_bytes_read": len(body),
        "response_fingerprint": hashlib.sha256(body).hexdigest()[:32],
        "document_shapes": sorted(shapes),
        "path_suffix_observed": bool(suffix),
        "redirected": final_url != url,
        "final_host_fingerprint": hashlib.sha256(final_host.encode("utf-8")).hexdigest()[:32]
        if final_host
        else "",
    }
    return {
        "schema": SOURCE_PREFLIGHT_SCHEMA,
        "status": status,
        "recommended_connector_type": recommended,
        "candidates": candidates,
        "observation": observation,
        "governance": {
            "network_access_performed": True,
            "request_method": "GET",
            "request_body_sent": False,
            "write_performed": False,
            "ssrf_checked": True,
            "source_content_returned": False,
            "response_body_persisted": False,
            "credentials_returned": False,
            "raw_cursor_returned": False,
        },
    }


__all__ = [
    "SOURCE_PREFLIGHT_SCHEMA",
    "SourcePreflightError",
    "SourcePreflightHttpResponse",
    "SourcePreflightTransport",
    "preflight_source_entry",
]
