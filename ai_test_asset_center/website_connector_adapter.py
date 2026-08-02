"""Read-only website and online help-center connector.

The adapter owns bounded URL discovery and HTTP transport only. HTML remains a source snapshot
and is handed to the existing Source Occurrence ingestion authority; this module does not create
a second knowledge store, infer business semantics, submit forms, or execute remote writes.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import posixpath
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib import robotparser
from xml.etree import ElementTree

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

WEBSITE_CONNECTOR_TYPE = "website"
WEBSITE_ADAPTER_SCHEMA = "qualibug.website-connector-adapter.v1"
WEBSITE_MATERIALIZATION_CONTRACT_VERSION = "website-materialization-v1"

_DEFAULT_MAX_DEPTH = 3
_DEFAULT_MAX_PAGES = 500
_DEFAULT_MAX_PAGE_BYTES = 4 * 1024 * 1024
_DEFAULT_MAX_TOTAL_BYTES = min(MAX_SOURCE_BYTES, 20 * 1024 * 1024)
_MAX_DEPTH = 12
_MAX_PAGES = 10_000
_MAX_PAGE_BYTES = min(MAX_SOURCE_BYTES, 20 * 1024 * 1024)
_MAX_TOTAL_BYTES = MAX_SOURCE_BYTES
_MAX_SEEDS = 64
_MAX_DOMAINS = 128
_MAX_PATH_RULES = 256
_MAX_SITEMAPS = 32
_MAX_LINKS_PER_PAGE = 2_000
_MAX_RELATIONSHIPS = 300
_MAX_TEXT = 1_000
_MAX_CURSOR_DESCRIPTORS = 100_000
_WEBSITE_USER_AGENT = "QualiBug-Website-Connector/1"
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_HTML_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
}
_ATTACHMENT_SUFFIXES = {
    ".csv",
    ".doc",
    ".docx",
    ".json",
    ".md",
    ".pdf",
    ".rst",
    ".txt",
    ".xls",
    ".xlsx",
    ".xml",
    ".yaml",
    ".yml",
}
_UNSUPPORTED_ATTACHMENT_SUFFIXES = {
    ".7z",
    ".avi",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".png",
    ".rar",
    ".svg",
    ".tar",
    ".webp",
    ".zip",
}
_SECRET_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
}


class WebsiteConnectorError(RuntimeError):
    """The website connector could not produce a trustworthy bounded snapshot."""


@dataclass(frozen=True)
class WebsiteHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str = ""


WebsiteTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float, int],
    WebsiteHttpResponse,
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


def _safe_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise WebsiteConnectorError(f"website_scope_{field}_invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise WebsiteConnectorError(f"website_scope_{field}_invalid") from exc
    if not minimum <= result <= maximum:
        raise WebsiteConnectorError(f"website_scope_{field}_out_of_range")
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
            raise WebsiteConnectorError(f"website_scope_{field}_required")
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise WebsiteConnectorError(f"website_scope_{field}_must_be_list")
    if len(values) > maximum:
        raise WebsiteConnectorError(f"website_scope_{field}_limit_exceeded")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _text(item, 2_000)
        if not text or text in seen:
            if not text:
                raise WebsiteConnectorError(f"website_scope_{field}_contains_empty_value")
            continue
        seen.add(text)
        result.append(text)
    if required and not result:
        raise WebsiteConnectorError(f"website_scope_{field}_required")
    return result


def _normalize_path_rule(value: Any, field: str) -> str:
    raw = _text(value, 1_000)
    if not raw or not raw.startswith("/"):
        raise WebsiteConnectorError(f"website_scope_{field}_invalid")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise WebsiteConnectorError(f"website_scope_{field}_invalid")
    path = posixpath.normpath(parsed.path)
    if not path.startswith("/"):
        path = "/" + path
    return path if path == "/" else path.rstrip("/")


def _domain(value: Any, field: str) -> str:
    raw = _text(value, 255).lower().rstrip(".")
    if not raw or any(ch.isspace() for ch in raw) or "/" in raw or ":" in raw:
        raise WebsiteConnectorError(f"website_scope_{field}_invalid")
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,253}[a-z0-9])?", raw):
            raise WebsiteConnectorError(f"website_scope_{field}_invalid")
    return raw


def _origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _normalize_url(value: Any, *, base: str = "") -> str:
    raw = _text(value, 4_000)
    if not raw:
        raise WebsiteConnectorError("website_url_missing")
    joined = urllib.parse.urljoin(base, raw) if base else raw
    parsed = urllib.parse.urlsplit(joined)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise WebsiteConnectorError("website_url_scheme_not_allowed")
    if parsed.username or parsed.password or not parsed.hostname:
        raise WebsiteConnectorError("website_url_userinfo_or_host_invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise WebsiteConnectorError("website_url_port_invalid") from exc
    hostname = parsed.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        netloc_host = f"[{hostname}]"
    else:
        netloc_host = hostname
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc_host += f":{port}"
    path = parsed.path or "/"
    normalized_segments: list[str] = []
    for segment in path.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if normalized_segments:
                normalized_segments.pop()
            continue
        normalized_segments.append(segment)
    normalized_path = "/" + "/".join(normalized_segments)
    if path.endswith("/") and normalized_path != "/":
        normalized_path += "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), netloc_host, normalized_path, parsed.query, "")
    )


def _path_allowed(url: str, scope: Mapping[str, Any]) -> bool:
    path = urllib.parse.urlsplit(url).path or "/"
    prefixes = list(scope.get("path_prefixes") or ["/"])
    excluded = list(scope.get("excluded_path_prefixes") or [])

    def matches(prefix: str) -> bool:
        return prefix == "/" or path == prefix or path.startswith(prefix + "/")

    return any(matches(prefix) for prefix in prefixes) and not any(
        matches(prefix) for prefix in excluded
    )


def _domain_allowed(url: str, scope: Mapping[str, Any]) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in scope["allowed_domains"])


def _target_allowed(
    url: str,
    scope: Mapping[str, Any],
    *,
    origin: str = "",
) -> bool:
    if not _domain_allowed(url, scope) or not _path_allowed(url, scope):
        return False
    if scope.get("same_origin_only") and origin and _origin(url) != origin:
        return False
    return True


def _validate_and_scope_url(
    value: Any,
    scope: Mapping[str, Any],
    *,
    base: str = "",
    origin: str = "",
    allow_out_of_scope: bool = False,
) -> str:
    url = _normalize_url(value, base=base)
    try:
        # Website adapters never inherit the process-wide internal-address escape hatch. Internal
        # access is a separate Local Runner capability and is not installed on this adapter.
        validate_url(url, allow_internal=False)
    except SsrfBlockedError as exc:
        raise WebsiteConnectorError("website_ssrf_blocked") from exc
    if not allow_out_of_scope and not _target_allowed(url, scope, origin=origin):
        raise WebsiteConnectorError("website_url_out_of_scope")
    return url


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    max_bytes: int,
) -> WebsiteHttpResponse:
    if method.upper() not in {"GET", "HEAD"} or body is not None:
        raise WebsiteConnectorError("website_write_or_body_request_forbidden")
    try:
        request = urllib.request.Request(
            url,
            headers=dict(headers),
            method=method.upper(),
        )
        with safe_urlopen(request, timeout=timeout, allow_internal=False) as response:
            content_length = _header(response.headers, "Content-Length")
            if content_length.isdigit() and int(content_length) > max_bytes:
                raise WebsiteConnectorError("website_response_size_limit_exceeded")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise WebsiteConnectorError("website_response_size_limit_exceeded")
            final_url = _text(response.geturl(), 4_000)
            return WebsiteHttpResponse(
                status=int(getattr(response, "status", response.getcode())),
                headers={str(key): str(value) for key, value in response.headers.items()},
                body=bytes(payload),
                final_url=final_url,
            )
    except urllib.error.HTTPError as exc:
        payload = exc.read(max_bytes + 1)
        if len(payload) > max_bytes:
            payload = payload[:max_bytes]
        return WebsiteHttpResponse(
            status=int(exc.code),
            headers={str(key): str(value) for key, value in exc.headers.items()},
            body=bytes(payload),
            final_url=_text(exc.geturl(), 4_000),
        )
    except (urllib.error.URLError, TimeoutError, SsrfBlockedError) as exc:
        raise WebsiteConnectorError(
            f"website_transport_failed:{type(exc).__name__}"
        ) from exc


def _request(
    context: Mapping[str, Any],
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    max_bytes: int,
) -> WebsiteHttpResponse:
    try:
        validate_url(url, allow_internal=False)
    except SsrfBlockedError as exc:
        raise WebsiteConnectorError("website_ssrf_blocked") from exc
    method = "GET"
    request_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.1",
        "User-Agent": _WEBSITE_USER_AGENT,
    }
    request_headers.update({str(key): str(value) for key, value in (headers or {}).items()})
    if any(str(key).lower() in _SECRET_HEADER_NAMES for key in request_headers if str(key).lower() != "cookie"):
        raise WebsiteConnectorError("website_secret_header_not_allowed")
    transport = context.get("transport") or _default_transport
    if not callable(transport):
        raise WebsiteConnectorError("website_transport_invalid")
    timeout = float(context.get("timeout", 15.0))
    sleeper = context.get("sleeper", time.sleep)
    attempts = 3
    last: WebsiteHttpResponse | None = None
    for attempt in range(attempts):
        response = transport(method, url, request_headers, None, timeout, max_bytes)
        if not isinstance(response, WebsiteHttpResponse):
            raise WebsiteConnectorError("website_transport_response_invalid")
        last = response
        if response.status not in _RETRYABLE_STATUSES or attempt + 1 >= attempts:
            return response
        if callable(sleeper):
            sleeper(min(0.25 * (2**attempt), 2.0))
    if last is None:
        raise WebsiteConnectorError("website_transport_returned_no_response")
    return last


class _WebsiteHTMLParser(HTMLParser):
    """Extract transport relationships without interpreting business semantics."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.canonical = ""
        self.updated_at = ""
        self.links: list[dict[str, str]] = []
        self.forms_present = False
        self._in_title = False
        self._in_nav = 0
        self._active_link: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        attributes = {str(key).lower(): _text(value, 4_000) for key, value in attrs}
        if name == "title":
            self._in_title = True
        elif name == "nav":
            self._in_nav += 1
        elif name == "form":
            self.forms_present = True
        elif name == "meta":
            meta_name = attributes.get("name", "").lower()
            http_equiv = attributes.get("http-equiv", "").lower()
            if meta_name in {"date", "last-modified", "updated", "article:modified_time"} or http_equiv == "last-modified":
                self.updated_at = attributes.get("content", "")
        elif name == "link":
            href = attributes.get("href", "")
            rel = attributes.get("rel", "").lower()
            if href and "canonical" in rel.split():
                self.canonical = href
            if href:
                self.links.append(
                    {
                        "href": href,
                        "relation": " ".join(sorted(rel.split())) or "LINK",
                        "kind": "LINK",
                        "text": "",
                    }
                )
        elif name == "a":
            href = attributes.get("href", "")
            if href:
                self._active_link = {
                    "href": href,
                    "relation": " ".join(sorted(attributes.get("rel", "").lower().split())) or "LINK",
                    "kind": "NAVIGATION" if self._in_nav else "LINK",
                    "text": "",
                }

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name == "title":
            self._in_title = False
        elif name == "nav":
            self._in_nav = max(0, self._in_nav - 1)
        elif name == "a" and self._active_link is not None:
            self.links.append(self._active_link)
            self._active_link = None

    def handle_data(self, data: str) -> None:
        bounded = _text(data, 1_000)
        if self._in_title and bounded:
            self.title_parts.append(bounded)
        if self._active_link is not None and bounded:
            self._active_link["text"] = _text(
                self._active_link.get("text", "") + " " + bounded,
                300,
            )


def _parse_html(body: bytes) -> _WebsiteHTMLParser:
    parser = _WebsiteHTMLParser()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
        parser.close()
    except Exception as exc:
        raise WebsiteConnectorError("website_html_parse_failed") from exc
    return parser


def _mime(response: WebsiteHttpResponse) -> str:
    return _header(response.headers, "Content-Type").split(";", 1)[0].strip().lower()


def _is_html(url: str, response: WebsiteHttpResponse) -> bool:
    mime = _mime(response)
    if mime in _HTML_MIME_TYPES:
        return True
    return Path(urllib.parse.urlsplit(url).path).suffix.lower() in {"", ".html", ".htm"}


def _updated_at(value: Any) -> str:
    raw = _text(value, 160)
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is not None:
            return parsed.astimezone(__import__("datetime").timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
    except (TypeError, ValueError, OverflowError):
        pass
    return raw


def _content_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _revision(response: WebsiteHttpResponse, body: bytes) -> str:
    etag = _header(response.headers, "ETag")
    if etag:
        return "etag:" + etag[:240]
    modified = _header(response.headers, "Last-Modified")
    if modified:
        return "last-modified:" + modified[:240]
    return "sha256:" + _content_hash(body)


def _safe_relationships(
    links: Iterable[Mapping[str, Any]],
    *,
    page_url: str,
    scope: Mapping[str, Any],
    origin: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    relationships: list[dict[str, str]] = []
    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in list(links)[:_MAX_LINKS_PER_PAGE]:
        href = _text(raw.get("href"), 4_000)
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        try:
            target = _normalize_url(href, base=page_url)
            validate_url(target, allow_internal=False)
        except (WebsiteConnectorError, SsrfBlockedError):
            continue
        if not _target_allowed(target, scope, origin=origin):
            continue
        relation = _text(raw.get("relation"), 160) or "LINK"
        kind = _text(raw.get("kind"), 80) or "LINK"
        identity = (target, kind)
        if identity in seen:
            continue
        seen.add(identity)
        item = {
            "target_url": target,
            "relation": relation,
            "kind": kind,
            "text": _text(raw.get("text"), 300),
        }
        relationships.append(item)
        targets.append(item)
    return relationships[:_MAX_RELATIONSHIPS], targets[:_MAX_RELATIONSHIPS]


def _attachment_kind(url: str) -> str:
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if suffix in _ATTACHMENT_SUFFIXES:
        return "attachment"
    if suffix in _UNSUPPORTED_ATTACHMENT_SUFFIXES:
        return "unsupported_attachment"
    return "page"


def _filename(url: str, *, title: str = "", attachment: bool = False) -> str:
    path_name = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    raw = path_name or ("index.html" if not attachment else "attachment.bin")
    raw = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", raw).strip(" ._")
    if not raw:
        raw = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", title).strip(" ._")
    if not raw:
        raw = "website_document"
    if not attachment and Path(raw).suffix.lower() not in {".html", ".htm"}:
        raw += ".html"
    return raw[:240]


def _scope_from_context(context: Mapping[str, Any]) -> dict[str, Any]:
    raw = context.get("resource_scope")
    if isinstance(raw, Mapping):
        scope_value: Any = dict(raw)
    else:
        scope_text = _text(raw, 20_000)
        if scope_text.startswith("http://") or scope_text.startswith("https://"):
            scope_value = {"seed_urls": [scope_text]}
        else:
            try:
                scope_value = json.loads(scope_text)
            except (TypeError, ValueError) as exc:
                raise WebsiteConnectorError("website_resource_scope_json_required") from exc
    if not isinstance(scope_value, dict):
        raise WebsiteConnectorError("website_resource_scope_must_be_object")
    allowed_keys = {
        "seed_urls",
        "allowed_domains",
        "path_prefixes",
        "excluded_path_prefixes",
        "max_depth",
        "max_pages",
        "max_page_bytes",
        "max_total_bytes",
        "same_origin_only",
        "robots_policy",
        "sitemap_urls",
    }
    unknown = sorted(set(scope_value) - allowed_keys)
    if unknown:
        raise WebsiteConnectorError("website_scope_field_not_supported:" + str(unknown[0]))
    seeds_raw = _string_list(scope_value.get("seed_urls"), "seed_urls", required=True, maximum=_MAX_SEEDS)
    seeds = [_normalize_url(item) for item in seeds_raw]
    seed_hosts = sorted({(urllib.parse.urlsplit(url).hostname or "").lower() for url in seeds})
    domains_raw = _string_list(scope_value.get("allowed_domains"), "allowed_domains", maximum=_MAX_DOMAINS)
    domains = [_domain(item, "allowed_domains") for item in (domains_raw or seed_hosts)]
    if not set(seed_hosts).issubset(set(domains)):
        raise WebsiteConnectorError("website_scope_seed_domain_not_allowed")
    path_prefixes = [
        _normalize_path_rule(item, "path_prefixes")
        for item in _string_list(scope_value.get("path_prefixes"), "path_prefixes", maximum=_MAX_PATH_RULES)
    ] or ["/"]
    excluded = [
        _normalize_path_rule(item, "excluded_path_prefixes")
        for item in _string_list(
            scope_value.get("excluded_path_prefixes"),
            "excluded_path_prefixes",
            maximum=_MAX_PATH_RULES,
        )
    ]
    same_origin_only = scope_value.get("same_origin_only", True)
    if not isinstance(same_origin_only, bool):
        raise WebsiteConnectorError("website_scope_same_origin_only_invalid")
    robots_policy = _text(scope_value.get("robots_policy"), 64).upper() or "HONOR"
    if robots_policy not in {"HONOR", "OVERRIDE_AUTHORIZED"}:
        raise WebsiteConnectorError("website_scope_robots_policy_invalid")
    profile = dict(context.get("connection_profile") or {})
    auth_mode = _text(profile.get("auth_mode"), 64).lower() or "anonymous"
    if robots_policy == "OVERRIDE_AUTHORIZED" and auth_mode != "cookie_session":
        raise WebsiteConnectorError("website_robots_override_requires_private_session")
    sitemap_urls = [_normalize_url(item) for item in _string_list(scope_value.get("sitemap_urls"), "sitemap_urls", maximum=_MAX_SITEMAPS)]
    for sitemap in sitemap_urls:
        if not _domain_allowed(sitemap, {"allowed_domains": domains}):
            raise WebsiteConnectorError("website_scope_sitemap_domain_not_allowed")
    return {
        "seed_urls": seeds,
        "allowed_domains": domains,
        "path_prefixes": path_prefixes,
        "excluded_path_prefixes": excluded,
        "max_depth": _safe_int(scope_value.get("max_depth", _DEFAULT_MAX_DEPTH), "max_depth", 0, _MAX_DEPTH),
        "max_pages": _safe_int(scope_value.get("max_pages", _DEFAULT_MAX_PAGES), "max_pages", 1, _MAX_PAGES),
        "max_page_bytes": _safe_int(scope_value.get("max_page_bytes", _DEFAULT_MAX_PAGE_BYTES), "max_page_bytes", 1_024, _MAX_PAGE_BYTES),
        "max_total_bytes": _safe_int(scope_value.get("max_total_bytes", _DEFAULT_MAX_TOTAL_BYTES), "max_total_bytes", 1_024, _MAX_TOTAL_BYTES),
        "same_origin_only": same_origin_only,
        "robots_policy": robots_policy,
        "sitemap_urls": sitemap_urls,
        "auth_mode": auth_mode,
    }


def _profile_for_context(context: Mapping[str, Any]) -> dict[str, str]:
    profile = dict(context.get("connection_profile") or {})
    profile_ref = _text(context.get("connection_profile_ref"), 500)
    if profile_ref and not profile:
        resolver = context.get("resolve_connection_profile")
        if not callable(resolver):
            raise WebsiteConnectorError("website_connection_profile_resolver_missing")
        try:
            resolved = resolver(profile_ref)
        except Exception as exc:
            raise WebsiteConnectorError(
                f"website_connection_profile_resolution_failed:{type(exc).__name__}"
            ) from exc
        if not isinstance(resolved, Mapping):
            raise WebsiteConnectorError("website_connection_profile_invalid")
        profile = {str(key): _text(value, 8_000) for key, value in resolved.items()}
    auth_mode = _text(profile.get("auth_mode"), 64).lower() or "anonymous"
    if auth_mode not in {"anonymous", "cookie_session"}:
        raise WebsiteConnectorError("website_auth_mode_invalid")
    if auth_mode == "cookie_session" and not _text(profile.get("session_cookie"), 8_000):
        raise WebsiteConnectorError("website_session_cookie_required")
    return {"auth_mode": auth_mode, "session_cookie": _text(profile.get("session_cookie"), 8_000)}


def _auth_headers(context: Mapping[str, Any]) -> dict[str, str]:
    profile = _profile_for_context(context)
    cookie = profile.get("session_cookie", "")
    return {"Cookie": cookie} if cookie else {}


def _robots_url(origin: str) -> str:
    return origin.rstrip("/") + "/robots.txt"


def _robots_policy(
    context: Mapping[str, Any],
    scope: Mapping[str, Any],
    origin: str,
    *,
    total_bytes: list[int],
) -> tuple[robotparser.RobotFileParser | None, list[str], str]:
    if scope["robots_policy"] == "OVERRIDE_AUTHORIZED":
        return None, [], "OVERRIDDEN_BY_PRIVATE_SESSION"
    robots_url = _robots_url(origin)
    response = _request(
        context,
        robots_url,
        headers=_auth_headers(context),
        max_bytes=min(scope["max_page_bytes"], 512 * 1024),
    )
    total_bytes[0] += len(response.body)
    if response.status == 404:
        return None, [], "NOT_PUBLISHED"
    if response.status in {401, 403}:
        return None, [], "ACCESS_DENIED"
    if response.status < 200 or response.status >= 300:
        return None, [], "UNAVAILABLE"
    text = response.body.decode("utf-8", errors="replace")
    parser = robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(text.splitlines())
    sitemaps: list[str] = []
    for line in text.splitlines():
        if not line.lower().startswith("sitemap:"):
            continue
        value = line.split(":", 1)[1].strip()
        if not value:
            continue
        try:
            sitemap = _normalize_url(value)
        except WebsiteConnectorError:
            continue
        if not _domain_allowed(sitemap, scope) or (
            scope.get("same_origin_only") and _origin(sitemap) != origin
        ):
            continue
        sitemaps.append(sitemap)
    return parser, sitemaps[:_MAX_SITEMAPS], "HONORED"


def _sitemap_locations(body: bytes) -> tuple[list[str], list[str]]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise WebsiteConnectorError("website_sitemap_parse_failed") from exc
    pages: list[str] = []
    nested: list[str] = []
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    for node in root.iter():
        name = node.tag.rsplit("}", 1)[-1].lower()
        if name != "loc" or not _text(node.text, 4_000):
            continue
        location = _text(node.text, 4_000)
        if root_name == "sitemapindex":
            nested.append(location)
        else:
            pages.append(location)
    return pages[:_MAX_LINKS_PER_PAGE], nested[:_MAX_SITEMAPS]


def _sitemap_pages(
    context: Mapping[str, Any],
    scope: Mapping[str, Any],
    sitemap_urls: Sequence[str],
    *,
    total_bytes: list[int],
) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    queue = list(dict.fromkeys(sitemap_urls))[:_MAX_SITEMAPS]
    seen: set[str] = set()
    pages: list[tuple[str, str]] = []
    evidence: list[dict[str, str]] = []
    while queue and len(seen) < _MAX_SITEMAPS:
        sitemap = queue.pop(0)
        if sitemap in seen:
            continue
        seen.add(sitemap)
        try:
            target = _validate_and_scope_url(sitemap, scope)
        except WebsiteConnectorError:
            evidence.append({"sitemap_url": sitemap, "status": "OUT_OF_SCOPE"})
            continue
        response = _request(
            context,
            target,
            headers=_auth_headers(context),
            max_bytes=min(scope["max_page_bytes"], 4 * 1024 * 1024),
        )
        total_bytes[0] += len(response.body)
        if response.status in {401, 403}:
            evidence.append({"sitemap_url": target, "status": "ACCESS_DENIED"})
            continue
        if response.status < 200 or response.status >= 300:
            evidence.append({"sitemap_url": target, "status": f"HTTP_{response.status}"})
            continue
        locations, nested = _sitemap_locations(response.body)
        for nested_url in nested:
            try:
                normalized = _validate_and_scope_url(
                    nested_url,
                    scope,
                    base=target,
                    origin=_origin(target),
                    allow_out_of_scope=True,
                )
            except WebsiteConnectorError:
                continue
            if (
                _domain_allowed(normalized, scope)
                and (
                    not scope.get("same_origin_only")
                    or _origin(normalized) == _origin(target)
                )
                and normalized not in seen
            ):
                queue.append(normalized)
        for location in locations:
            try:
                normalized = _validate_and_scope_url(
                    location,
                    scope,
                    base=target,
                    origin=_origin(target),
                )
            except WebsiteConnectorError:
                continue
            pages.append((normalized, _updated_at(_header(response.headers, "Last-Modified"))))
            if len(pages) >= scope["max_pages"]:
                break
        evidence.append(
            {
                "sitemap_url": target,
                "status": "READ",
                "last_modified": _updated_at(_header(response.headers, "Last-Modified")),
            }
        )
    return list(dict.fromkeys(pages)), evidence


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
        aliases = metadata.get("aliases_json", "")
        if url == _text(metadata.get("canonical_url"), 4_000) or url in _json_strings(aliases):
            matches.append((remote_id, dict(row)))
    if len(matches) > 1:
        raise WebsiteConnectorError("website_previous_identity_ambiguous")
    return matches[0] if matches else ("", {})


def _json_strings(value: Any) -> list[str]:
    raw = _text(value, 20_000)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [_text(item, 4_000) for item in data] if isinstance(data, list) else []


def _descriptor_metadata(
    *,
    relationships: Sequence[Mapping[str, Any]],
    aliases: Sequence[str],
    robots_status: str,
    forms_present: bool,
    sitemap_last_modified: str = "",
) -> dict[str, Any]:
    relationship_rows: list[Mapping[str, Any]] = []
    for relationship in relationships:
        candidate = [*relationship_rows, relationship]
        encoded = json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded) > 1_800:
            break
        relationship_rows.append(relationship)
    return {
        "source_relationships_json": json.dumps(
            relationship_rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "aliases_json": _bounded_json_strings(aliases),
        "robots_status": _text(robots_status, 80),
        "forms_present": bool(forms_present),
        "sitemap_last_modified": _text(sitemap_last_modified, 160),
}


def _bounded_json_strings(values: Iterable[Any], *, limit: int = 1_800) -> str:
    selected: list[str] = []
    for value in sorted(
        set(_text(item, 4_000) for item in values if _text(item, 4_000))
    ):
        candidate = json.dumps(
            [*selected, value], ensure_ascii=False, separators=(",", ":")
        )
        if len(candidate) > limit:
            break
        selected.append(value)
    return json.dumps(selected, ensure_ascii=False, separators=(",", ":"))


def _bounded_json_objects(
    values: Iterable[Mapping[str, Any]], *, limit: int = 1_800
) -> str:
    selected: list[dict[str, Any]] = []
    for value in values:
        candidate = json.dumps(
            [*selected, dict(value)],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(candidate) > limit:
            break
        selected.append(dict(value))
    return json.dumps(
        selected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _merge_descriptors(
    descriptors: dict[str, dict[str, Any]],
    aliases: dict[str, str],
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
    if not remote_id:
        raise WebsiteConnectorError("website_descriptor_identity_missing")
    requested_url = _text(descriptor.get("requested_url"), 4_000)
    old_id = aliases.get(requested_url, "")
    if old_id and old_id != remote_id:
        previous = descriptors.pop(old_id, None)
        if previous:
            descriptor["aliases_json"] = _bounded_json_strings(
                _json_strings(previous.get("aliases_json"))
                + _json_strings(descriptor.get("aliases_json"))
            )
    current = descriptors.get(remote_id)
    if current is None:
        descriptors[remote_id] = descriptor
        current = descriptor
    else:
        current_aliases = set(_json_strings(current.get("aliases_json")))
        current_aliases.update(_json_strings(descriptor.get("aliases_json")))
        current["aliases_json"] = _bounded_json_strings(current_aliases)
        current_relationships = _json_objects(current.get("source_relationships_json"))
        current_relationships.extend(_json_objects(descriptor.get("source_relationships_json")))
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in current_relationships:
            key = (_text(item.get("target_url"), 4_000), _text(item.get("kind"), 80), _text(item.get("relation"), 160))
            if key[0]:
                unique[key] = item
        current["source_relationships_json"] = _bounded_json_objects(
            list(unique.values())[:_MAX_RELATIONSHIPS]
        )
        if descriptor.get("_body") and not current.get("_body"):
            current.update({key: value for key, value in descriptor.items() if key.startswith("_")})
    if requested_url:
        aliases[requested_url] = remote_id
    for alias in _json_strings(current.get("aliases_json")):
        aliases[alias] = remote_id
    return current


def _json_objects(value: Any) -> list[dict[str, Any]]:
    raw = _text(value, 100_000)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [dict(item) for item in data if isinstance(item, Mapping)] if isinstance(data, list) else []


def _coverage(
    remote_id: str,
    reason_code: str,
    *,
    display_title: str = "",
    remote_object_type: str = "website-page",
    metadata: Mapping[str, Any] | None = None,
    retry_trigger: str = "REMOTE_ACCESS_OR_SCOPE_CHANGE",
) -> dict[str, Any]:
    return {
        "remote_resource_id": _text(remote_id, 4_000),
        "resource_kind": "website-page",
        "state": "UNSUPPORTED",
        "reason_code": reason_code,
        "remote_object_type": remote_object_type,
        "display_title": _text(display_title, 300),
        "retry_trigger": retry_trigger,
        "capability_contract_version": WEBSITE_MATERIALIZATION_CONTRACT_VERSION,
        "metadata": {
            key: value
            for key, value in dict(metadata or {}).items()
            if isinstance(value, (str, int, float, bool)) and value not in {"", None}
        },
    }


def _discover_website_resources(
    context: Mapping[str, Any],
    *,
    cursor: SyncCursor = "",
    previous_observations: Mapping[str, Mapping[str, Any]] | None = None,
    retain_body: bool,
) -> DiscoveryResult:
    scope = _scope_from_context(context)
    observations = previous_observations or {}
    total_bytes = [0]
    coverage: list[dict[str, Any]] = []
    descriptors: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    queue: list[tuple[str, str, int, str, str, str]] = []
    queued: set[str] = set()
    fetched: set[str] = set()
    robots_by_origin: dict[str, tuple[robotparser.RobotFileParser | None, str]] = {}
    sitemap_evidence: list[dict[str, str]] = []

    def enqueue(url: str, origin: str, depth: int, parent: str, relation: str, sitemap_last_modified: str = "") -> None:
        if url in queued or url in fetched:
            return
        if len(queued) + len(fetched) >= scope["max_pages"] * 2:
            coverage.append(_coverage(url, "WEBSITE_PAGE_LIMIT_REACHED", retry_trigger="SCOPE_LIMIT_CHANGE"))
            return
        queued.add(url)
        queue.append((url, origin, depth, parent, relation, sitemap_last_modified))

    for seed in scope["seed_urls"]:
        origin = _origin(seed)
        try:
            _validate_and_scope_url(seed, scope, origin=origin)
        except WebsiteConnectorError as exc:
            coverage.append(_coverage(seed, "WEBSITE_SEED_OUT_OF_SCOPE", metadata={"error": str(exc)}))
            continue
        enqueue(seed, origin, 0, "", "SEED")

    explicit_sitemaps = list(scope["sitemap_urls"])
    origins = sorted({_origin(seed) for seed in scope["seed_urls"]})
    sitemap_candidates = list(explicit_sitemaps)
    for origin in origins:
        try:
            robots, discovered_sitemaps, robots_status = _robots_policy(
                context, scope, origin, total_bytes=total_bytes
            )
        except WebsiteConnectorError as exc:
            robots_by_origin[origin] = (None, "FETCH_FAILED")
            coverage.append(_coverage(_robots_url(origin), "WEBSITE_ROBOTS_FETCH_FAILED", metadata={"error": str(exc)}))
            continue
        robots_by_origin[origin] = (robots, robots_status)
        if robots_status == "ACCESS_DENIED":
            coverage.append(_coverage(_robots_url(origin), "WEBSITE_ROBOTS_ACCESS_DENIED", metadata={"robots_status": robots_status}))
        elif robots_status == "UNAVAILABLE":
            coverage.append(_coverage(_robots_url(origin), "WEBSITE_ROBOTS_UNAVAILABLE", metadata={"robots_status": robots_status}))
        sitemap_candidates.extend(discovered_sitemaps)
    sitemap_candidates = list(dict.fromkeys(sitemap_candidates))[:_MAX_SITEMAPS]
    if sitemap_candidates:
        try:
            sitemap_pages, sitemap_evidence = _sitemap_pages(
                context, scope, sitemap_candidates, total_bytes=total_bytes
            )
        except WebsiteConnectorError as exc:
            coverage.append(_coverage(sitemap_candidates[0], "WEBSITE_SITEMAP_PARSE_FAILED", metadata={"error": str(exc)}))
            sitemap_pages = []
        for page, last_modified in sitemap_pages:
            enqueue(page, _origin(page), 0, "", "SITEMAP", last_modified)

    while queue:
        if total_bytes[0] > scope["max_total_bytes"]:
            coverage.append(_coverage(queue[0][0], "WEBSITE_TOTAL_SIZE_LIMIT_REACHED", retry_trigger="SCOPE_LIMIT_CHANGE"))
            break
        url, origin, depth, parent_id, relation, sitemap_last_modified = queue.pop(0)
        if url in fetched:
            continue
        fetched.add(url)
        if len(fetched) > scope["max_pages"]:
            coverage.append(_coverage(url, "WEBSITE_PAGE_LIMIT_REACHED", retry_trigger="SCOPE_LIMIT_CHANGE"))
            continue
        if not _target_allowed(url, scope, origin=origin):
            coverage.append(_coverage(url, "WEBSITE_URL_OUT_OF_SCOPE"))
            continue
        robots, robots_status = robots_by_origin.get(origin, (None, "NOT_PUBLISHED"))
        if robots_status in {"ACCESS_DENIED", "UNAVAILABLE", "FETCH_FAILED"}:
            coverage.append(
                _coverage(
                    url,
                    "WEBSITE_ROBOTS_ACCESS_DENIED"
                    if robots_status == "ACCESS_DENIED"
                    else "WEBSITE_ROBOTS_UNAVAILABLE",
                    metadata={"robots_status": robots_status},
                    retry_trigger="ROBOTS_POLICY_ORIGIN_CHANGE",
                )
            )
            continue
        if robots is not None and not robots.can_fetch(_WEBSITE_USER_AGENT, url):
            coverage.append(
                _coverage(
                    url,
                    "WEBSITE_ROBOTS_DISALLOWED",
                    metadata={"robots_status": robots_status},
                )
            )
            continue
        previous_id, previous = _previous_for_url(url, observations)
        previous_metadata = dict(previous.get("source_metadata") or {})
        conditional: dict[str, str] = {}
        previous_etag = _text(previous_metadata.get("etag"), 1_000)
        previous_modified = _text(previous_metadata.get("last_modified"), 1_000)
        if previous_etag:
            conditional["If-None-Match"] = previous_etag
        if previous_modified:
            conditional["If-Modified-Since"] = previous_modified
        try:
            response = _request(
                context,
                url,
                headers={**_auth_headers(context), **conditional},
                max_bytes=scope["max_page_bytes"],
            )
        except WebsiteConnectorError as exc:
            coverage.append(_coverage(url, "WEBSITE_FETCH_FAILED", metadata={"error": str(exc)}))
            continue
        total_bytes[0] += len(response.body)
        if response.status in {401, 403}:
            coverage.append(
                _coverage(
                    url,
                    "WEBSITE_AUTH_REQUIRED" if response.status == 401 else "WEBSITE_PERMISSION_DENIED",
                    metadata={"http_status": response.status, "auth_mode": scope["auth_mode"]},
                    retry_trigger="CREDENTIAL_OR_PERMISSION_CHANGE",
                )
            )
            continue
        if response.status == 304:
            if not previous_id:
                raise WebsiteConnectorError("website_not_modified_without_previous_snapshot")
            final_url = _text(response.final_url, 4_000) or url
            try:
                final_url = _validate_and_scope_url(final_url, scope, origin=origin)
            except WebsiteConnectorError:
                coverage.append(_coverage(url, "WEBSITE_REDIRECT_OUT_OF_SCOPE", metadata={"http_status": response.status}))
                continue
            descriptor = {
                "remote_resource_id": previous_id,
                "resource_kind": "website-page",
                "obj_type": "html_page",
                "display_title": _text(previous_metadata.get("display_title"), 300),
                "canonical_url": _text(previous_metadata.get("canonical_url"), 4_000) or previous_id,
                "parent_remote_id": parent_id,
                "remote_revision": _text(previous_metadata.get("remote_revision"), 240),
                "remote_updated_at": _text(previous_metadata.get("remote_updated_at"), 160),
                "declared_mime": _text(previous_metadata.get("declared_mime"), 160) or "text/html",
                "requested_url": url,
                "unchanged": True,
                "source_relationships_json": _bounded_json_objects(
                    _json_objects(previous_metadata.get("source_relationships_json"))
                ),
                "aliases_json": _bounded_json_strings(
                    _json_strings(previous_metadata.get("aliases_json"))
                    + [url, final_url]
                ),
                "robots_status": robots_status,
                "forms_present": bool(previous_metadata.get("forms_present")),
                "sitemap_last_modified": sitemap_last_modified,
                "remote_materialization_fingerprint": _text(previous_metadata.get("remote_materialization_fingerprint"), 128),
            }
            _merge_descriptors(descriptors, aliases, descriptor)
            if depth < scope["max_depth"]:
                for relationship in _json_objects(previous_metadata.get("source_relationships_json")):
                    target_url = _text(relationship.get("target_url"), 4_000)
                    if target_url and target_url not in queued and target_url not in fetched:
                        enqueue(
                            target_url,
                            origin,
                            depth + 1,
                            previous_id,
                            _text(relationship.get("kind"), 80) or "LINK",
                        )
            continue
        if response.status == 404:
            coverage.append(_coverage(url, "WEBSITE_NOT_FOUND", metadata={"http_status": response.status}, retry_trigger="REMOTE_REAPPEARANCE"))
            continue
        if response.status < 200 or response.status >= 300:
            coverage.append(_coverage(url, f"WEBSITE_HTTP_{response.status}", metadata={"http_status": response.status}))
            continue
        final_url = _text(response.final_url, 4_000) or url
        try:
            final_url = _validate_and_scope_url(final_url, scope, origin=origin)
        except WebsiteConnectorError:
            coverage.append(_coverage(url, "WEBSITE_REDIRECT_OUT_OF_SCOPE", metadata={"http_status": response.status}))
            continue
        if len(response.body) > scope["max_page_bytes"]:
            coverage.append(_coverage(url, "WEBSITE_PAGE_SIZE_LIMIT_REACHED", retry_trigger="SCOPE_LIMIT_CHANGE"))
            continue
        kind = _attachment_kind(final_url)
        if kind == "page" and _is_html(final_url, response):
            parser = _parse_html(response.body)
            canonical_candidate = _text(parser.canonical, 4_000)
            canonical_url = final_url
            if canonical_candidate:
                try:
                    candidate = _validate_and_scope_url(canonical_candidate, scope, base=final_url, origin=origin)
                except WebsiteConnectorError:
                    candidate = final_url
                else:
                    canonical_url = candidate
            relationships, targets = _safe_relationships(
                parser.links,
                page_url=final_url,
                scope=scope,
                origin=origin,
            )
            alias_values = [url, final_url, canonical_url]
            if previous_id:
                alias_values.extend(_json_strings(previous_metadata.get("aliases_json")))
            descriptor = {
                "remote_resource_id": canonical_url,
                "resource_kind": "website-page",
                "obj_type": "html_page",
                "display_title": _text(" ".join(parser.title_parts), 300),
                "canonical_url": canonical_url,
                "parent_remote_id": parent_id,
                "remote_revision": _revision(response, response.body),
                "remote_updated_at": _updated_at(_header(response.headers, "Last-Modified") or parser.updated_at),
                "declared_mime": _mime(response) or "text/html",
                "requested_url": url,
                "etag": _header(response.headers, "ETag"),
                "last_modified": _header(response.headers, "Last-Modified"),
                "content_hash": _content_hash(response.body),
                "remote_materialization_fingerprint": _content_hash(response.body),
                **_descriptor_metadata(
                    relationships=relationships,
                    aliases=alias_values,
                    robots_status=robots_status,
                    forms_present=parser.forms_present,
                    sitemap_last_modified=sitemap_last_modified,
                ),
            }
            if retain_body:
                descriptor["_body"] = response.body
                descriptor["_headers"] = dict(response.headers)
            _merge_descriptors(descriptors, aliases, descriptor)
            if depth < scope["max_depth"]:
                for target in targets:
                    target_url = _text(target.get("target_url"), 4_000)
                    if target_url not in queued and target_url not in fetched:
                        target_kind = _attachment_kind(target_url)
                        enqueue(
                            target_url,
                            origin,
                            depth + 1,
                            canonical_url,
                            _text(target.get("kind"), 80) or "LINK",
                        )
            continue
        if kind == "page":
            kind = "unsupported_attachment"
        attachment_supported = kind == "attachment"
        descriptor = {
            "remote_resource_id": final_url,
            "resource_kind": "website-attachment",
            "obj_type": "attachment" if attachment_supported else "unsupported_attachment",
            "display_title": _filename(final_url, attachment=True),
            "canonical_url": final_url,
            "parent_remote_id": parent_id,
            "remote_revision": _revision(response, response.body),
            "remote_updated_at": _updated_at(_header(response.headers, "Last-Modified")),
            "declared_mime": _mime(response),
            "requested_url": url,
            "etag": _header(response.headers, "ETag"),
            "last_modified": _header(response.headers, "Last-Modified"),
            "content_hash": _content_hash(response.body),
            "remote_materialization_fingerprint": _content_hash(response.body),
            **_descriptor_metadata(
                relationships=[{"target_url": final_url, "relation": relation, "kind": "ATTACHMENT", "text": ""}],
                aliases=[url, final_url],
                robots_status=robots_status,
                forms_present=False,
                sitemap_last_modified=sitemap_last_modified,
            ),
        }
        if retain_body:
            descriptor["_body"] = response.body
            descriptor["_headers"] = dict(response.headers)
        _merge_descriptors(descriptors, aliases, descriptor)

    clean_descriptors = sorted(descriptors.values(), key=lambda row: _text(row.get("remote_resource_id"), 4_000))
    for row in clean_descriptors:
        row.pop("requested_url", None)
    complete = not coverage
    return {
        "schema": WEBSITE_ADAPTER_SCHEMA,
        "descriptors": clean_descriptors,
        "complete": complete,
        "coverage": {
            "discovered_count": len(clean_descriptors),
            "blocked_count": len(coverage),
            "total_bytes": total_bytes[0],
            "sitemap_evidence": sitemap_evidence,
            "observations": coverage,
        },
        "lifecycle": coverage,
        "next_cursor": _snapshot_cursor(clean_descriptors),
        "previous_cursor_supplied": bool(cursor),
    }


def _snapshot_cursor(descriptors: Sequence[Mapping[str, Any]]) -> str:
    if len(descriptors) > _MAX_CURSOR_DESCRIPTORS:
        raise WebsiteConnectorError("website_cursor_descriptor_limit_exceeded")
    payload = [
        {
            "remote_resource_id": _text(row.get("remote_resource_id"), 4_000),
            "remote_revision": _text(row.get("remote_revision"), 240),
            "remote_updated_at": _text(row.get("remote_updated_at"), 160),
            "remote_materialization_fingerprint": _text(
                row.get("remote_materialization_fingerprint"), 128
            ),
        }
        for row in descriptors
    ]
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "website-snapshot-v1:" + digest


def _adapter_capability(descriptor: Mapping[str, Any]) -> ResourceCapability:
    return classify_materialization_capability(
        descriptor,
        connector_type=WEBSITE_CONNECTOR_TYPE,
        materializable_types=("html_page", "attachment"),
        contract_version=WEBSITE_MATERIALIZATION_CONTRACT_VERSION,
    )


def _materialize_website_resource(
    context: ConnectorContext,
    descriptor: Mapping[str, Any],
) -> MaterializedSnapshot:
    capability = _adapter_capability(descriptor)
    if not capability.materializable:
        raise WebsiteConnectorError(
            f"website_resource_not_materializable:{capability.reason_code}"
        )
    scope = _scope_from_context(context)
    remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
    if not remote_id:
        raise WebsiteConnectorError("website_descriptor_identity_missing")
    body = descriptor.get("_body")
    headers = dict(descriptor.get("_headers") or {}) if isinstance(descriptor.get("_headers"), Mapping) else {}
    if not isinstance(body, (bytes, bytearray, memoryview)):
        response = _request(
            context,
            _validate_and_scope_url(remote_id, scope),
            headers=_auth_headers(context),
            max_bytes=scope["max_page_bytes"],
        )
        if response.status != 200:
            raise WebsiteConnectorError(f"website_materialization_http_{response.status}")
        body = response.body
        headers = dict(response.headers)
    blob = bytes(body)
    if not blob:
        raise WebsiteConnectorError("website_materialization_content_missing")
    if len(blob) > scope["max_page_bytes"] or len(blob) > MAX_SOURCE_BYTES:
        raise WebsiteConnectorError("website_materialization_size_limit_exceeded")
    filename = _filename(
        remote_id,
        title=_text(descriptor.get("display_title"), 300),
        attachment=_text(descriptor.get("obj_type"), 80) == "attachment",
    )
    obj_type = _text(descriptor.get("obj_type"), 80)
    return {
        "remote_resource_id": remote_id,
        "resource_kind": _text(descriptor.get("resource_kind"), 80) or "website-page",
        "display_title": _text(descriptor.get("display_title"), 300),
        "source_type": "other_document",
        "filename": filename,
        "content": blob,
        "export_format": "binary" if obj_type == "attachment" else "html",
        "declared_mime": _mime(WebsiteHttpResponse(200, headers, blob)) or _text(descriptor.get("declared_mime"), 160),
        "remote_revision": _text(descriptor.get("remote_revision"), 240),
        "remote_updated_at": _text(descriptor.get("remote_updated_at"), 160),
        "retrieved_at": _utc_now(),
        "remote_materialization_fingerprint": _text(
            descriptor.get("remote_materialization_fingerprint"), 128
        ) or _content_hash(blob),
        "canonical_url": _text(descriptor.get("canonical_url"), 4_000),
        "parent_remote_id": _text(descriptor.get("parent_remote_id"), 4_000),
        "etag": _header(headers, "ETag") or _text(descriptor.get("etag"), 1_000),
        "last_modified": _header(headers, "Last-Modified") or _text(descriptor.get("last_modified"), 1_000),
        "source_relationships_json": _text(descriptor.get("source_relationships_json"), 100_000),
        "aliases_json": _text(descriptor.get("aliases_json"), 100_000),
        "forms_present": bool(descriptor.get("forms_present")),
        "robots_status": _text(descriptor.get("robots_status"), 80),
        "sitemap_last_modified": _text(descriptor.get("sitemap_last_modified"), 160),
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
        "robots_status",
        "sitemap_last_modified",
    ):
        value = descriptor.get(key)
        if value not in {None, ""}:
            result[key] = _text(value, 100_000 if key.endswith("_json") else 4_000)
    if "forms_present" in descriptor:
        result["forms_present"] = bool(descriptor.get("forms_present"))
    return result


def _existing_remote_id(
    descriptor: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, Any]],
) -> str:
    candidate = _text(descriptor.get("remote_resource_id"), 4_000)
    if candidate in observations:
        return candidate
    descriptor_aliases = set(_json_strings(descriptor.get("aliases_json")))
    matches: list[str] = []
    for remote_id, row in observations.items():
        metadata = dict(row.get("source_metadata") or {}) if isinstance(row, Mapping) else {}
        prior_aliases = set(_json_strings(metadata.get("aliases_json")))
        if descriptor_aliases.intersection(prior_aliases):
            matches.append(remote_id)
    if len(matches) > 1:
        raise WebsiteConnectorError("website_existing_identity_ambiguous")
    return matches[0] if matches else ""


def _connector_instance(project: str, connector: str, root: Path) -> dict[str, Any]:
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
        raise WebsiteConnectorError("website_connector_instance_not_registered")
    if _text(instance.get("connector_type"), 160).lower() != WEBSITE_CONNECTOR_TYPE:
        raise WebsiteConnectorError("website_connector_instance_type_mismatch")
    if instance.get("status") != "ACTIVE":
        raise WebsiteConnectorError("website_connector_instance_not_active")
    return instance


def _context_with_profile(
    context: Mapping[str, Any],
    *,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(context)
    result["connection_profile"] = dict(profile or _profile_for_context(context))
    return result


def website_connector_manifest() -> ConnectorManifest:
    return ConnectorManifest(
        connector_type=WEBSITE_CONNECTOR_TYPE,
        display_name="网站与在线帮助中心",
        category="website",
        version="1",
        auth_modes=("anonymous", "cookie_session"),
        scope_schema={
            "type": "object",
            "description": "输入一个公开或已授权的在线资料入口，系统只执行受边界约束的只读 GET",
            "required": ["seed_urls"],
            "properties": {
                "seed_urls": {"type": "array", "maxItems": _MAX_SEEDS},
                "allowed_domains": {"type": "array", "description": "默认使用 Seed 所在域名"},
                "path_prefixes": {"type": "array", "default": ["/"]},
                "excluded_path_prefixes": {"type": "array"},
                "max_depth": {"type": "integer", "default": _DEFAULT_MAX_DEPTH, "maximum": _MAX_DEPTH},
                "max_pages": {"type": "integer", "default": _DEFAULT_MAX_PAGES, "maximum": _MAX_PAGES},
                "max_page_bytes": {"type": "integer", "default": _DEFAULT_MAX_PAGE_BYTES, "maximum": _MAX_PAGE_BYTES},
                "max_total_bytes": {"type": "integer", "default": _DEFAULT_MAX_TOTAL_BYTES, "maximum": _MAX_TOTAL_BYTES},
                "same_origin_only": {"type": "boolean", "default": True},
                "robots_policy": {"enum": ["HONOR", "OVERRIDE_AUTHORIZED"], "default": "HONOR"},
                "sitemap_urls": {"type": "array", "maxItems": _MAX_SITEMAPS},
            },
            "shorthand": "单个 HTTP(S) Seed URL 也可直接作为范围值",
        },
        quick_connect_schema={
            "input_type": "url",
            "scope_field": "seed_urls",
            "priority": 10,
        },
        entrypoint_evidence={
            "content_types": ["text/html", "application/xhtml+xml"],
            "document_shapes": ["html_page"],
        },
        supported_resource_types=("html_page", "attachment"),
        sync_modes=("FULL", "INCREMENTAL"),
        webhook_supported=False,
        local_runner_supported=False,
        local_runner_required=False,
        read_only=True,
        credential_fields=(
            ConnectorCredentialField(
                name="session_cookie",
                field_type="cookie_session_reference",
                required=True,
                secret=True,
                display_name="登录会话 Cookie",
                description="私有在线资料的会话 Cookie；仅在内存中用于只读 GET",
                auth_modes=("cookie_session",),
            ),
        ),
        capability_contract_version=WEBSITE_MATERIALIZATION_CONTRACT_VERSION,
    )


def test_website_connector_connection(
    project_id: str,
    *,
    connector_instance_id: str,
    resolve_connection_profile: Callable[[str], Mapping[str, Any]] | None = None,
    root: Path | None = None,
    transport: WebsiteTransport | None = None,
    timeout: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    instance = _connector_instance(project_id, connector_instance_id, resolved_root)
    context: dict[str, Any] = {
        "project_id": project_id,
        "connector_instance_id": connector_instance_id,
        "connection_profile_ref": _text(instance.get("connection_profile_ref"), 500),
        "resolve_connection_profile": resolve_connection_profile,
        "resource_scope": _text(instance.get("resource_scope"), 20_000),
        "transport": transport,
        "timeout": timeout,
        "sleeper": sleeper,
    }
    profile = _profile_for_context(context)
    context["connection_profile"] = profile
    scope = _scope_from_context(context)
    seed = scope["seed_urls"][0]
    _validate_and_scope_url(seed, scope, origin=_origin(seed))
    response = _request(context, seed, headers=_auth_headers(context), max_bytes=min(scope["max_page_bytes"], 128 * 1024))
    if response.status in {401, 403}:
        return {
            "schema": WEBSITE_ADAPTER_SCHEMA,
            "status": "FAILED",
            "reason_code": "WEBSITE_AUTH_REQUIRED" if response.status == 401 else "WEBSITE_PERMISSION_DENIED",
            "http_status": response.status,
            "connector_instance_id": connector_instance_id,
            "connector_type": WEBSITE_CONNECTOR_TYPE,
            "auth_mode": profile["auth_mode"],
            "network_side_effect": "READ_ONLY",
            "credentials_persisted": False,
        }
    if response.status < 200 or response.status >= 300:
        return {
            "schema": WEBSITE_ADAPTER_SCHEMA,
            "status": "FAILED",
            "reason_code": f"WEBSITE_HTTP_{response.status}",
            "http_status": response.status,
            "connector_instance_id": connector_instance_id,
            "connector_type": WEBSITE_CONNECTOR_TYPE,
            "auth_mode": profile["auth_mode"],
            "network_side_effect": "READ_ONLY",
            "credentials_persisted": False,
        }
    return {
        "schema": WEBSITE_ADAPTER_SCHEMA,
        "status": "AVAILABLE",
        "connector_instance_id": connector_instance_id,
        "connector_type": WEBSITE_CONNECTOR_TYPE,
        "auth_mode": profile["auth_mode"],
        "seed_count": len(scope["seed_urls"]),
        "network_side_effect": "READ_ONLY",
        "credentials_persisted": False,
        "session_cookie_returned": False,
    }


def sync_website_connector(
    project_id: str,
    *,
    connector_instance_id: str,
    resolve_connection_profile: Callable[[str], Mapping[str, Any]] | None = None,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    previous_cursor: str = "",
    deletion_policy: str = "RETAIN",
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    max_nodes: int = _DEFAULT_MAX_PAGES,
    transport: WebsiteTransport | None = None,
    timeout: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    instance = _connector_instance(project_id, connector_instance_id, resolved_root)
    stored_hash = _text(instance.get("last_committed_cursor_fingerprint"), 128)
    if stored_hash and not previous_cursor:
        raise WebsiteConnectorError("website_previous_cursor_required")
    context: dict[str, Any] = {
        "project_id": project_id,
        "connector_instance_id": connector_instance_id,
        "connection_profile_ref": _text(instance.get("connection_profile_ref"), 500),
        "resolve_connection_profile": resolve_connection_profile,
        "resource_scope": _text(instance.get("resource_scope"), 20_000),
        "transport": transport,
        "timeout": timeout,
        "sleeper": sleeper,
    }
    context = _context_with_profile(context)
    scope = _scope_from_context(context)
    if max_nodes > 0:
        scope["max_pages"] = min(scope["max_pages"], int(max_nodes))
        context["resource_scope"] = json.dumps(
            {key: value for key, value in scope.items() if key != "auth_mode"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    observations = connector_snapshot_observation_index(
        project_id,
        connector_instance_id=connector_instance_id,
        root=resolved_root,
    )
    discovery = _discover_website_resources(
        context,
        cursor=previous_cursor,
        previous_observations=observations,
        retain_body=True,
    )
    descriptors = [dict(row) for row in discovery.get("descriptors") or []]
    items: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    coverage = [dict(row) for row in (discovery.get("coverage") or {}).get("observations") or []]
    for descriptor in descriptors:
        existing_id = _existing_remote_id(descriptor, observations)
        if existing_id and existing_id != _text(descriptor.get("remote_resource_id"), 4_000):
            descriptor["remote_resource_id"] = existing_id
        remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
        existing = dict(observations.get(remote_id) or {})
        existing_metadata = dict(existing.get("source_metadata") or {})
        capability = _adapter_capability(descriptor)
        if capability.observable_unsupported:
            coverage.append(
                _coverage(
                    remote_id,
                    capability.reason_code,
                    display_title=_text(descriptor.get("display_title"), 300),
                    remote_object_type=capability.remote_object_type,
                    retry_trigger=capability.retry_trigger,
                )
            )
            continue
        if not capability.materializable:
            raise WebsiteConnectorError("website_descriptor_invalid")
        fingerprint = _text(descriptor.get("remote_materialization_fingerprint"), 128)
        if existing and fingerprint and _text(existing_metadata.get("remote_materialization_fingerprint"), 128) == fingerprint:
            unchanged.append(
                {
                    "remote_resource_id": remote_id,
                    "resource_kind": _text(descriptor.get("resource_kind"), 80) or "website-page",
                    "metadata": _observation_metadata(descriptor),
                }
            )
            continue
        item = _materialize_website_resource(context, descriptor)
        items.append(dict(item))
    next_cursor = _snapshot_cursor(descriptors)
    snapshot_complete = bool(discovery.get("complete")) and not coverage
    requested_deletion_policy = _text(deletion_policy, 32).upper() or "RETAIN"
    if requested_deletion_policy not in {"RETAIN", "RETIRE_MISSING"}:
        raise WebsiteConnectorError("website_deletion_policy_invalid")
    effective_deletion_policy = requested_deletion_policy
    retirement_skip_reason = ""
    if requested_deletion_policy == "RETIRE_MISSING" and not snapshot_complete:
        # The shared reconciliation authority rejects incomplete retirement by contract. Make
        # that decision explicit in the connector receipt while retaining prior snapshots.
        effective_deletion_policy = "RETAIN"
        retirement_skip_reason = "INCOMPLETE_SNAPSHOT_ACCESS_OR_SCOPE_GAP"
    else:
        effective_deletion_policy = requested_deletion_policy
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
            deletion_policy=effective_deletion_policy,
            snapshot_complete=snapshot_complete,
            max_retire_count=max_retire_count,
            max_retire_ratio=max_retire_ratio,
        )
    except (ConnectorSyncError, ConnectorSnapshotError) as exc:
        raise WebsiteConnectorError(f"website_sync_rejected:{exc}") from exc
    return {
        **run,
        "adapter_schema": WEBSITE_ADAPTER_SCHEMA,
        "adapter": WEBSITE_CONNECTOR_TYPE,
        "connector_type": WEBSITE_CONNECTOR_TYPE,
        "auth_mode": _text(context.get("connection_profile", {}).get("auth_mode"), 64) or "anonymous",
        "discovered_resource_count": len(descriptors),
        "materialized_resource_count": len(items),
        "unchanged_resource_count": len(unchanged),
        "coverage_observation_count": len(coverage),
        "snapshot_complete": snapshot_complete,
        "deletion_policy_requested": requested_deletion_policy,
        "deletion_policy_effective": effective_deletion_policy,
        "retirement_skip_reason": retirement_skip_reason,
        "next_cursor": next_cursor,
        "next_cursor_persisted_by_adapter": False,
        "credentials_persisted": False,
        "session_cookie_returned": False,
        "source_content_persisted_in_adapter_receipt": False,
        "connector_parser_implemented": False,
    }


class WebsiteConnectorAdapter:
    """Generic ConnectorAdapter facade over the website discovery authority."""

    def manifest(self) -> ConnectorManifest:
        return website_connector_manifest()

    def test_connection(self, context: ConnectorContext) -> dict[str, Any]:
        return test_website_connector_connection(
            _text(context.get("project_id"), 160),
            connector_instance_id=_text(context.get("connector_instance_id"), 160),
            resolve_connection_profile=context.get("resolve_connection_profile"),
            root=context.get("root"),
            transport=context.get("transport"),
            timeout=float(context.get("timeout", 15.0)),
            sleeper=context.get("sleeper", time.sleep),
        )

    def discover(self, context: ConnectorContext, cursor: SyncCursor = "") -> DiscoveryResult:
        prepared = _context_with_profile(context)
        result = _discover_website_resources(prepared, cursor=cursor, retain_body=False)
        for row in result.get("descriptors") or []:
            for private_key in tuple(key for key in row if str(key).startswith("_")):
                row.pop(private_key, None)
        return result

    def classify_resource(self, descriptor: Mapping[str, Any]) -> ResourceCapability:
        return _adapter_capability(descriptor)

    def materialize(
        self,
        context: ConnectorContext,
        descriptor: Mapping[str, Any],
    ) -> MaterializedSnapshot:
        return _materialize_website_resource(_context_with_profile(context), descriptor)

    def build_cursor(
        self,
        discovery_result: DiscoveryResult | Sequence[Mapping[str, Any]],
    ) -> SyncCursor:
        descriptors = (
            discovery_result.get("descriptors")
            if isinstance(discovery_result, Mapping)
            else discovery_result
        )
        if not isinstance(descriptors, Sequence) or isinstance(descriptors, (str, bytes, bytearray)):
            raise WebsiteConnectorError("website_discovery_descriptors_missing")
        return _snapshot_cursor([dict(item) for item in descriptors])

    def managed_remote_checkpoint(self, context: ConnectorContext) -> SyncCursor:
        prepared = _context_with_profile(context)
        return self.build_cursor(_discover_website_resources(prepared, retain_body=False))

    def managed_sync(self, context: ConnectorContext) -> dict[str, Any]:
        return sync_website_connector(
            _text(context.get("project_id"), 160),
            connector_instance_id=_text(context.get("connector_instance_id"), 160),
            resolve_connection_profile=context.get("resolve_connection_profile"),
            root=context.get("root"),
            actor=dict(context.get("actor") or {}),
            previous_cursor=_text(context.get("previous_cursor"), 20_000),
            deletion_policy=_text(context.get("deletion_policy"), 32) or "RETAIN",
            max_retire_count=int(context.get("max_retire_count", 100)),
            max_retire_ratio=float(context.get("max_retire_ratio", 0.25)),
            max_nodes=int(context.get("max_resources", _DEFAULT_MAX_PAGES)),
            transport=context.get("transport"),
            timeout=float(context.get("timeout", 15.0)),
            sleeper=context.get("sleeper", time.sleep),
        )


__all__ = [
    "WEBSITE_ADAPTER_SCHEMA",
    "WEBSITE_CONNECTOR_TYPE",
    "WEBSITE_MATERIALIZATION_CONTRACT_VERSION",
    "WebsiteConnectorAdapter",
    "WebsiteConnectorError",
    "WebsiteHttpResponse",
    "sync_website_connector",
    "test_website_connector_connection",
    "website_connector_manifest",
]
