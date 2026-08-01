"""Read-only Feishu enterprise knowledge connector.

This adapter owns Feishu API authentication, Wiki discovery, and official document export only.
It never parses business semantics, stores credentials, or writes source content outside the
existing source-occurrence ingestion authority. Network access is explicit and import is
side-effect free.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .connector_sync_authority import (
    ConnectorSyncError,
    connector_snapshot_observation_index,
    list_connector_instances,
    sync_connector_snapshot_batch,
)
from .connector_registry import (
    ConnectorContext,
    ConnectorCredentialField,
    ConnectorManifest,
    DiscoveryResult,
    MaterializedSnapshot,
    SyncCursor,
)
from .connector_materialization_capability import ResourceCapability
from .enterprise_knowledge_center._common import MAX_SOURCE_BYTES, ROOT
from .enterprise_knowledge_center._utils import _redact_text
from .ssrf_guard import SsrfBlockedError, safe_urlopen

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
FEISHU_API_HOST = "open.feishu.cn"
FEISHU_CONNECTOR_TYPE = "feishu"
FEISHU_ADAPTER_SCHEMA = "qualibug.feishu-connector-adapter.v1"

_JSON_RESPONSE_LIMIT = 2 * 1024 * 1024
_DEFAULT_PAGE_SIZE = 50
_DEFAULT_MAX_NODES = 5000
_DEFAULT_MAX_EXPORT_POLLS = 20
_DEFAULT_MATERIALIZATION_WORKERS = 4
_MAX_MATERIALIZATION_WORKERS = 8
_MATERIALIZATION_WORKERS_ENV = "QUALIBUG_FEISHU_MATERIALIZATION_WORKERS"
_RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
_RETRYABLE_FEISHU_CODES = {99991400, 1069923}
_TOKEN_RE = re.compile(r"^[^\s]{8,4096}$")
_SCOPE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
_EXPORT_FORMATS = {
    "doc": ("docx", "feishu_document"),
    "docx": ("docx", "feishu_document"),
    "sheet": ("xlsx", "collaboration_document"),
    "bitable": ("xlsx", "collaboration_document"),
}
_DIRECT_FILE_TYPES = {"file"}
_MATERIALIZATION_CONTRACT_VERSION = "feishu-materialization-v1"


class FeishuConnectorError(RuntimeError):
    """The Feishu connector could not produce a trustworthy complete snapshot."""


@dataclass(frozen=True)
class FeishuHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


FeishuTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float, int],
    FeishuHttpResponse,
]
ConnectionProfileResolver = Callable[[str], Mapping[str, Any]]


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _safe_identifier(value: Any, field: str) -> str:
    result = _text(value, 160)
    if not _SCOPE_ID_RE.fullmatch(result):
        raise FeishuConnectorError(f"{field}_invalid")
    return result


def _safe_filename(title: Any, suffix: str) -> str:
    raw = Path(_text(title, 300)).name
    clean = _SAFE_FILENAME_RE.sub("_", raw).strip(" ._") or "feishu_document"
    clean = clean[:180]
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    if clean.lower().endswith(normalized_suffix.lower()):
        return clean
    return clean + normalized_suffix


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return ""


def _filename_from_disposition(value: str, fallback: str) -> str:
    match = re.search(r"filename\*=UTF-8''([^;]+)", value or "", flags=re.I)
    if match:
        decoded = urllib.parse.unquote(match.group(1))
        return Path(decoded).name or fallback
    match = re.search(r'filename="?([^";]+)"?', value or "", flags=re.I)
    if match:
        return Path(match.group(1)).name or fallback
    return fallback


def _build_url(path: str, query: Mapping[str, Any] | None = None) -> str:
    clean_path = "/" + str(path or "").lstrip("/")
    url = FEISHU_API_BASE + clean_path
    if query:
        values = {
            str(key): str(value)
            for key, value in query.items()
            if value is not None and value != ""
        }
        if values:
            url += "?" + urllib.parse.urlencode(values)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != FEISHU_API_HOST:
        raise FeishuConnectorError("feishu_api_host_not_allowed")
    return url


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    max_bytes: int,
) -> FeishuHttpResponse:
    try:
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method=method,
        )
        with safe_urlopen(
            request,
            timeout=timeout,
            allow_internal=False,
            approved_host=FEISHU_API_HOST,
        ) as response:
            content_length = _text(response.headers.get("Content-Length"), 32)
            if content_length.isdigit() and int(content_length) > max_bytes:
                raise FeishuConnectorError("feishu_response_size_limit_exceeded")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise FeishuConnectorError("feishu_response_size_limit_exceeded")
            return FeishuHttpResponse(
                status=int(getattr(response, "status", response.getcode())),
                headers={str(k): str(v) for k, v in response.headers.items()},
                body=bytes(payload),
            )
    except urllib.error.HTTPError as exc:
        payload = exc.read(min(max_bytes, _JSON_RESPONSE_LIMIT))
        return FeishuHttpResponse(
            status=int(exc.code),
            headers={str(k): str(v) for k, v in exc.headers.items()},
            body=bytes(payload),
        )
    except (urllib.error.URLError, TimeoutError, SsrfBlockedError) as exc:
        raise FeishuConnectorError(
            f"feishu_transport_failed:{type(exc).__name__}"
        ) from exc


def _request(
    method: str,
    path: str,
    *,
    transport: FeishuTransport,
    access_token: str = "",
    query: Mapping[str, Any] | None = None,
    json_body: Mapping[str, Any] | None = None,
    timeout: float = 15.0,
    max_bytes: int = _JSON_RESPONSE_LIMIT,
    max_attempts: int = 4,
    sleeper: Callable[[float], None] = time.sleep,
) -> FeishuHttpResponse:
    url = _build_url(path, query)
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "QualiBug-Feishu-Connector/1",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    body: bytes | None = None
    if json_body is not None:
        body = json.dumps(
            dict(json_body),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    attempts = max(1, min(int(max_attempts), 6))
    last: FeishuHttpResponse | None = None
    for attempt in range(attempts):
        response = transport(method, url, headers, body, timeout, max_bytes)
        last = response
        if response.status not in _RETRYABLE_HTTP_STATUSES or attempt + 1 >= attempts:
            return response
        sleeper(min(0.25 * (2 ** attempt), 2.0))
    if last is None:
        raise FeishuConnectorError("feishu_transport_returned_no_response")
    return last


def _json_payload(
    method: str,
    path: str,
    *,
    transport: FeishuTransport,
    access_token: str = "",
    query: Mapping[str, Any] | None = None,
    json_body: Mapping[str, Any] | None = None,
    timeout: float = 15.0,
    max_attempts: int = 4,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    attempts = max(1, min(int(max_attempts), 6))
    for attempt in range(attempts):
        response = _request(
            method,
            path,
            transport=transport,
            access_token=access_token,
            query=query,
            json_body=json_body,
            timeout=timeout,
            max_bytes=_JSON_RESPONSE_LIMIT,
            max_attempts=1,
            sleeper=sleeper,
        )
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except Exception as exc:
            raise FeishuConnectorError(
                f"feishu_json_response_invalid:http_{response.status}"
            ) from exc
        if not isinstance(payload, dict):
            raise FeishuConnectorError("feishu_json_response_not_object")
        code = int(payload.get("code") or 0)
        if (
            (response.status in _RETRYABLE_HTTP_STATUSES or code in _RETRYABLE_FEISHU_CODES)
            and attempt + 1 < attempts
        ):
            sleeper(min(0.25 * (2 ** attempt), 2.0))
            continue
        if not 200 <= response.status < 300 or code != 0:
            message = _redact_text(payload.get("msg") or "request failed", 240)
            raise FeishuConnectorError(
                f"feishu_api_failed:http_{response.status}:code_{code}:{message}"
            )
        return payload
    raise FeishuConnectorError("feishu_api_retry_exhausted")


def _validated_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise FeishuConnectorError("feishu_connection_profile_not_object")
    result = dict(profile)
    mode = _text(result.get("auth_mode"), 64).lower()
    if not mode:
        if result.get("tenant_access_token"):
            mode = "tenant_access_token"
        elif result.get("user_access_token"):
            mode = "user_access_token"
        elif result.get("app_id") or result.get("app_secret"):
            mode = "internal_app"
    if mode not in {"tenant_access_token", "user_access_token", "internal_app"}:
        raise FeishuConnectorError("feishu_auth_mode_invalid")
    result["auth_mode"] = mode
    return result


def _token_value(value: Any, field: str) -> str:
    token = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(token):
        raise FeishuConnectorError(f"{field}_missing_or_invalid")
    return token


def _resolve_access_token(
    profile: Mapping[str, Any],
    *,
    transport: FeishuTransport,
    timeout: float,
    sleeper: Callable[[float], None],
) -> tuple[str, str]:
    resolved = _validated_profile(profile)
    mode = resolved["auth_mode"]
    if mode == "tenant_access_token":
        return _token_value(
            resolved.get("tenant_access_token"), "feishu_tenant_access_token"
        ), mode
    if mode == "user_access_token":
        return _token_value(
            resolved.get("user_access_token"), "feishu_user_access_token"
        ), mode
    app_id = _token_value(resolved.get("app_id"), "feishu_app_id")
    app_secret = _token_value(resolved.get("app_secret"), "feishu_app_secret")
    payload = _json_payload(
        "POST",
        "/auth/v3/tenant_access_token/internal",
        transport=transport,
        json_body={"app_id": app_id, "app_secret": app_secret},
        timeout=timeout,
        sleeper=sleeper,
    )
    return _token_value(
        payload.get("tenant_access_token"), "feishu_tenant_access_token"
    ), mode


def _parse_scope(resource_scope: str) -> dict[str, Any]:
    scope = _text(resource_scope, 1000)
    if scope == "wiki-all-accessible":
        return {"mode": "all", "targets": []}
    if scope.startswith("wiki-space:"):
        space_id = _safe_identifier(scope.split(":", 1)[1], "feishu_space_id")
        return {"mode": "explicit", "targets": [(space_id, "")]}
    if scope.startswith("wiki-spaces:"):
        raw = scope.split(":", 1)[1]
        ids = [
            _safe_identifier(value.strip(), "feishu_space_id")
            for value in raw.split(",")
            if value.strip()
        ]
        if not ids or len(ids) != len(set(ids)):
            raise FeishuConnectorError("feishu_resource_scope_invalid")
        return {"mode": "explicit", "targets": [(value, "") for value in ids]}
    if scope.startswith("wiki-node:"):
        parts = scope.split(":", 2)
        if len(parts) != 3:
            raise FeishuConnectorError("feishu_resource_scope_invalid")
        return {
            "mode": "explicit",
            "targets": [
                (
                    _safe_identifier(parts[1], "feishu_space_id"),
                    _safe_identifier(parts[2], "feishu_parent_node_token"),
                )
            ],
        }
    raise FeishuConnectorError("feishu_resource_scope_invalid")


def _page_data(payload: dict[str, Any], context: str) -> tuple[list[dict[str, Any]], bool, str]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise FeishuConnectorError(f"{context}_data_missing")
    rows = data.get("items") or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise FeishuConnectorError(f"{context}_items_invalid")
    has_more = bool(data.get("has_more"))
    page_token = _text(data.get("page_token"), 1000)
    if has_more and not page_token:
        raise FeishuConnectorError(f"{context}_page_token_missing")
    return [dict(row) for row in rows], has_more, page_token


def _list_spaces(
    access_token: str,
    *,
    transport: FeishuTransport,
    timeout: float,
    sleeper: Callable[[float], None],
) -> list[str]:
    result: list[str] = []
    page_token = ""
    while True:
        payload = _json_payload(
            "GET",
            "/wiki/v2/spaces",
            transport=transport,
            access_token=access_token,
            query={"page_size": _DEFAULT_PAGE_SIZE, "page_token": page_token},
            timeout=timeout,
            sleeper=sleeper,
        )
        rows, has_more, page_token = _page_data(payload, "feishu_space_list")
        for row in rows:
            space_id = _safe_identifier(row.get("space_id"), "feishu_space_id")
            if space_id not in result:
                result.append(space_id)
        if not has_more:
            return result


def _node_revision(node: Mapping[str, Any]) -> str:
    for key in (
        "obj_edit_time",
        "node_edit_time",
        "updated_at",
        "update_time",
        "edit_time",
    ):
        value = _text(node.get(key), 160)
        if value:
            return value
    basis = {
        key: node.get(key)
        for key in (
            "space_id",
            "node_token",
            "obj_token",
            "obj_type",
            "parent_node_token",
            "title",
            "has_child",
        )
    }
    return "metadata-sha256:" + hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _descriptor(space_id: str, node: Mapping[str, Any]) -> dict[str, Any]:
    node_token = _safe_identifier(node.get("node_token"), "feishu_node_token")
    obj_token = _safe_identifier(node.get("obj_token"), "feishu_object_token")
    obj_type = _text(node.get("obj_type"), 64).lower()
    if not obj_type:
        raise FeishuConnectorError("feishu_object_type_missing")
    title = _text(node.get("title") or node.get("name") or obj_token, 300)
    revision = _node_revision(node)
    return {
        "space_id": space_id,
        "node_token": node_token,
        "obj_token": obj_token,
        "obj_type": obj_type,
        "title": title,
        "parent_node_token": _text(node.get("parent_node_token"), 160),
        "has_child": bool(node.get("has_child")),
        "remote_revision": revision,
        "remote_updated_at": revision if revision.isdigit() else "",
        "remote_resource_id": f"wiki:{space_id}:{node_token}",
        "resource_kind": f"feishu-wiki-{obj_type}",
    }


def discover_feishu_wiki_resources(
    access_token: str,
    resource_scope: str,
    *,
    transport: FeishuTransport | None = None,
    timeout: float = 15.0,
    max_nodes: int = _DEFAULT_MAX_NODES,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Discover a complete scoped Wiki node inventory with explicit pagination."""
    client = transport or _default_transport
    scope = _parse_scope(resource_scope)
    targets = list(scope["targets"])
    if scope["mode"] == "all":
        targets = [(space_id, "") for space_id in _list_spaces(
            access_token,
            transport=client,
            timeout=timeout,
            sleeper=sleeper,
        )]
    if not targets:
        return []

    limit = max(1, min(int(max_nodes), 100_000))
    descriptors: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    queued: set[tuple[str, str]] = set(targets)
    queue: list[tuple[str, str]] = list(targets)

    while queue:
        space_id, parent = queue.pop(0)
        page_token = ""
        while True:
            payload = _json_payload(
                "GET",
                f"/wiki/v2/spaces/{urllib.parse.quote(space_id, safe='')}/nodes",
                transport=client,
                access_token=access_token,
                query={
                    "page_size": _DEFAULT_PAGE_SIZE,
                    "page_token": page_token,
                    "parent_node_token": parent,
                },
                timeout=timeout,
                sleeper=sleeper,
            )
            rows, has_more, page_token = _page_data(payload, "feishu_node_list")
            for row in rows:
                item = _descriptor(space_id, row)
                identity = item["remote_resource_id"]
                if identity in seen_nodes:
                    continue
                seen_nodes.add(identity)
                descriptors.append(item)
                if len(descriptors) > limit:
                    raise FeishuConnectorError("feishu_node_limit_exceeded")
                if item["has_child"]:
                    child = (space_id, item["node_token"])
                    if child not in queued:
                        queued.add(child)
                        queue.append(child)
            if not has_more:
                break
    return descriptors


def _raw_content(
    descriptor: Mapping[str, Any],
    access_token: str,
    *,
    transport: FeishuTransport,
    timeout: float,
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    obj_token = _safe_identifier(descriptor.get("obj_token"), "feishu_object_token")
    payload = _json_payload(
        "GET",
        f"/docx/v1/documents/{urllib.parse.quote(obj_token, safe='')}/raw_content",
        transport=transport,
        access_token=access_token,
        timeout=timeout,
        sleeper=sleeper,
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise FeishuConnectorError("feishu_raw_content_data_missing")
    content = data.get("content")
    if not isinstance(content, str) or not content:
        raise FeishuConnectorError("feishu_raw_content_missing")
    return {
        "content": content,
        "filename": _safe_filename(descriptor.get("title"), "txt"),
        "export_format": "txt",
        "declared_mime": "text/plain; charset=utf-8",
        "adapter_degraded": True,
        "degradation_reason": "OFFICIAL_EXPORT_FAILED_RAW_TEXT_FALLBACK",
    }


def _download_binary(
    path: str,
    fallback_filename: str,
    access_token: str,
    *,
    transport: FeishuTransport,
    timeout: float,
    sleeper: Callable[[float], None],
) -> tuple[bytes, str, str]:
    response = _request(
        "GET",
        path,
        transport=transport,
        access_token=access_token,
        timeout=timeout,
        max_bytes=MAX_SOURCE_BYTES,
        sleeper=sleeper,
    )
    if not 200 <= response.status < 300:
        raise FeishuConnectorError(f"feishu_download_failed:http_{response.status}")
    if not response.body:
        raise FeishuConnectorError("feishu_download_empty")
    filename = _filename_from_disposition(
        _header(response.headers, "Content-Disposition"),
        fallback_filename,
    )
    mime = _header(response.headers, "Content-Type")
    return response.body, Path(filename).name or fallback_filename, mime


def _export_online_document(
    descriptor: Mapping[str, Any],
    access_token: str,
    *,
    transport: FeishuTransport,
    timeout: float,
    max_export_polls: int,
    export_poll_interval: float,
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    obj_type = _text(descriptor.get("obj_type"), 64).lower()
    if obj_type not in _EXPORT_FORMATS:
        raise FeishuConnectorError(f"feishu_export_type_unsupported:{obj_type}")
    extension, _ = _EXPORT_FORMATS[obj_type]
    obj_token = _safe_identifier(descriptor.get("obj_token"), "feishu_object_token")
    created = _json_payload(
        "POST",
        "/drive/v1/export_tasks",
        transport=transport,
        access_token=access_token,
        json_body={
            "file_extension": extension,
            "token": obj_token,
            "type": obj_type,
        },
        timeout=timeout,
        sleeper=sleeper,
    )
    data = created.get("data")
    ticket = _text(data.get("ticket") if isinstance(data, dict) else "", 160)
    if not ticket:
        raise FeishuConnectorError("feishu_export_ticket_missing")

    polls = max(1, min(int(max_export_polls), 120))
    for attempt in range(polls):
        payload = _json_payload(
            "GET",
            f"/drive/v1/export_tasks/{urllib.parse.quote(ticket, safe='')}",
            transport=transport,
            access_token=access_token,
            query={"token": obj_token},
            timeout=timeout,
            sleeper=sleeper,
        )
        data = payload.get("data")
        result = dict(data.get("result") or {}) if isinstance(data, dict) else {}
        file_token = _text(result.get("file_token"), 160)
        job_status = result.get("job_status")
        job_error = _text(result.get("job_error_msg"), 300)
        if file_token and job_status in (None, 0, "0"):
            fallback = _safe_filename(
                result.get("file_name") or descriptor.get("title"), extension
            )
            content, filename, mime = _download_binary(
                f"/drive/v1/export_tasks/file/{urllib.parse.quote(file_token, safe='')}/download",
                fallback,
                access_token,
                transport=transport,
                timeout=timeout,
                sleeper=sleeper,
            )
            return {
                "content": content,
                "filename": filename,
                "export_format": extension,
                "declared_mime": mime,
                "adapter_degraded": False,
                "export_ticket_observed": True,
            }
        if job_error and job_error.lower() not in {"success", "ok"}:
            raise FeishuConnectorError(
                "feishu_export_job_failed:" + _redact_text(job_error, 240)
            )
        if attempt + 1 < polls:
            sleeper(max(0.0, min(float(export_poll_interval), 5.0)))
    raise FeishuConnectorError("feishu_export_poll_exhausted")


def materialize_feishu_resource(
    descriptor: Mapping[str, Any],
    access_token: str,
    *,
    transport: FeishuTransport | None = None,
    timeout: float = 15.0,
    max_export_polls: int = _DEFAULT_MAX_EXPORT_POLLS,
    export_poll_interval: float = 0.5,
    allow_raw_text_fallback: bool = False,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch one Wiki node as an official export or governed explicit fallback."""
    client = transport or _default_transport
    obj_type = _text(descriptor.get("obj_type"), 64).lower()
    if obj_type in _DIRECT_FILE_TYPES:
        token = _safe_identifier(descriptor.get("obj_token"), "feishu_object_token")
        fallback = _safe_filename(descriptor.get("title"), "bin")
        content, filename, mime = _download_binary(
            f"/drive/v1/files/{urllib.parse.quote(token, safe='')}/download",
            fallback,
            access_token,
            transport=client,
            timeout=timeout,
            sleeper=sleeper,
        )
        exported = {
            "content": content,
            "filename": filename,
            "export_format": Path(filename).suffix.lower().lstrip(".") or "bin",
            "declared_mime": mime,
            "adapter_degraded": False,
        }
        source_type = "other_document"
    elif obj_type in _EXPORT_FORMATS:
        try:
            exported = _export_online_document(
                descriptor,
                access_token,
                transport=client,
                timeout=timeout,
                max_export_polls=max_export_polls,
                export_poll_interval=export_poll_interval,
                sleeper=sleeper,
            )
        except FeishuConnectorError:
            if not allow_raw_text_fallback or obj_type != "docx":
                raise
            exported = _raw_content(
                descriptor,
                access_token,
                transport=client,
                timeout=timeout,
                sleeper=sleeper,
            )
        source_type = _EXPORT_FORMATS[obj_type][1]
    else:
        raise FeishuConnectorError(f"feishu_object_type_unsupported:{obj_type}")

    return {
        "remote_resource_id": _text(descriptor.get("remote_resource_id"), 1000),
        "resource_kind": _text(descriptor.get("resource_kind"), 80),
        "source_type": source_type,
        "content": exported["content"],
        "filename": exported["filename"],
        "remote_revision": _text(descriptor.get("remote_revision"), 240),
        "remote_updated_at": _text(descriptor.get("remote_updated_at"), 80),
        "parent_remote_id": _text(descriptor.get("parent_node_token"), 160),
        "export_format": exported.get("export_format", ""),
        "declared_mime": exported.get("declared_mime", ""),
        "adapter_degraded": bool(exported.get("adapter_degraded")),
        "degradation_reason": exported.get("degradation_reason", ""),
    }


def _materialization_fingerprint(
    descriptor: Mapping[str, Any],
    *,
    allow_raw_text_fallback: bool,
) -> str:
    basis = {
        "contract_version": _MATERIALIZATION_CONTRACT_VERSION,
        "remote_resource_id": _text(
            descriptor.get("remote_resource_id"), 1000
        ),
        "obj_token": _text(descriptor.get("obj_token"), 160),
        "obj_type": _text(descriptor.get("obj_type"), 64).lower(),
        "remote_revision": _text(descriptor.get("remote_revision"), 240),
        "allow_raw_text_fallback": bool(allow_raw_text_fallback),
    }
    return hashlib.sha256(
        json.dumps(
            basis,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _unchanged_observation(
    descriptor: Mapping[str, Any],
    fingerprint: str,
) -> dict[str, Any]:
    remote_id = _text(descriptor.get("remote_resource_id"), 1000)
    resource_kind = _text(descriptor.get("resource_kind"), 80)
    return {
        "remote_resource_id": remote_id,
        "resource_kind": resource_kind,
        "metadata": {
            "source_origin": "connector_snapshot",
            "requested_source_id": remote_id,
            "remote_resource_id": remote_id,
            "resource_kind": resource_kind,
            "remote_revision": _text(descriptor.get("remote_revision"), 240),
            "remote_updated_at": _text(
                descriptor.get("remote_updated_at"), 80
            ),
            "parent_remote_id": _text(
                descriptor.get("parent_node_token"), 1000
            ),
            "remote_materialization_fingerprint": fingerprint,
        },
    }


def _materialization_worker_count(task_count: int) -> int:
    if task_count <= 0:
        return 0
    configured = _DEFAULT_MATERIALIZATION_WORKERS
    raw = os.environ.get(_MATERIALIZATION_WORKERS_ENV, "").strip()
    if raw:
        try:
            configured = int(raw)
        except ValueError as exc:
            raise FeishuConnectorError(
                "feishu_materialization_workers_invalid"
            ) from exc
        if not 1 <= configured <= _MAX_MATERIALIZATION_WORKERS:
            raise FeishuConnectorError(
                "feishu_materialization_workers_out_of_range"
            )
    return min(configured, task_count)


def _materialize_changed_resources(
    pending: list[tuple[dict[str, Any], str]],
    access_token: str,
    *,
    transport: FeishuTransport,
    timeout: float,
    max_export_polls: int,
    export_poll_interval: float,
    allow_raw_text_fallback: bool,
    sleeper: Callable[[float], None],
) -> tuple[list[dict[str, Any]], int, int]:
    """Materialize only changed resources with bounded, deterministic concurrency."""
    worker_count = _materialization_worker_count(len(pending))
    if worker_count == 0:
        return [], 0, 0

    def materialize(index: int, descriptor: dict[str, Any], fingerprint: str):
        item = materialize_feishu_resource(
            descriptor,
            access_token,
            transport=transport,
            timeout=timeout,
            max_export_polls=max_export_polls,
            export_poll_interval=export_poll_interval,
            allow_raw_text_fallback=allow_raw_text_fallback,
            sleeper=sleeper,
        )
        item["remote_materialization_fingerprint"] = fingerprint
        return index, item

    if worker_count == 1:
        ordered = [
            materialize(index, descriptor, fingerprint)[1]
            for index, (descriptor, fingerprint) in enumerate(pending)
        ]
    else:
        ordered_slots: list[dict[str, Any] | None] = [None] * len(pending)
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="qualibug-feishu-materialize",
        ) as executor:
            futures = {
                executor.submit(materialize, index, descriptor, fingerprint): index
                for index, (descriptor, fingerprint) in enumerate(pending)
            }
            try:
                for future in as_completed(futures):
                    index, item = future.result()
                    ordered_slots[index] = item
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        if any(item is None for item in ordered_slots):
            raise FeishuConnectorError(
                "feishu_materialization_result_incomplete"
            )
        ordered = [item for item in ordered_slots if item is not None]

    degraded_count = sum(
        int(bool(item.get("adapter_degraded"))) for item in ordered
    )
    return ordered, degraded_count, worker_count



def _snapshot_cursor(descriptors: list[dict[str, Any]]) -> str:
    basis = [
        {
            "remote_resource_id": row.get("remote_resource_id"),
            "obj_token": row.get("obj_token"),
            "obj_type": row.get("obj_type"),
            "remote_revision": row.get("remote_revision"),
            "parent_node_token": row.get("parent_node_token"),
        }
        for row in sorted(
            descriptors,
            key=lambda item: _text(item.get("remote_resource_id"), 1000),
        )
    ]
    digest = hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"feishu-snapshot-v1:{digest}"


def _connector_instance(
    project_id: str,
    connector_instance_id: str,
    root: Path,
) -> dict[str, Any]:
    connector = _text(connector_instance_id, 160)
    rows = list_connector_instances(
        project_id,
        root=root,
        include_disabled=True,
    ).get("connector_instances") or []
    instance = next(
        (
            dict(row)
            for row in rows
            if isinstance(row, dict)
            and _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if instance is None:
        raise FeishuConnectorError("feishu_connector_instance_not_registered")
    if _text(instance.get("connector_type"), 160).lower() != FEISHU_CONNECTOR_TYPE:
        raise FeishuConnectorError("feishu_connector_instance_type_mismatch")
    if instance.get("status") != "ACTIVE":
        raise FeishuConnectorError("feishu_connector_instance_not_active")
    if not _text(instance.get("connection_profile_ref"), 500):
        raise FeishuConnectorError("feishu_connection_profile_ref_missing")
    return instance


def test_feishu_connector_connection(
    project_id: str,
    *,
    connector_instance_id: str,
    resolve_connection_profile: ConnectionProfileResolver,
    root: Path | None = None,
    transport: FeishuTransport | None = None,
    timeout: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Validate credentials and scope without ingesting enterprise material."""
    resolved_root = root or ROOT
    instance = _connector_instance(project_id, connector_instance_id, resolved_root)
    profile_ref = _text(instance.get("connection_profile_ref"), 500)
    try:
        profile = resolve_connection_profile(profile_ref)
    except Exception as exc:
        raise FeishuConnectorError(
            f"feishu_connection_profile_resolution_failed:{type(exc).__name__}"
        ) from exc
    client = transport or _default_transport
    access_token, auth_mode = _resolve_access_token(
        profile,
        transport=client,
        timeout=timeout,
        sleeper=sleeper,
    )
    scope = _parse_scope(_text(instance.get("resource_scope"), 1000))
    if scope["mode"] == "all":
        spaces = _list_spaces(
            access_token,
            transport=client,
            timeout=timeout,
            sleeper=sleeper,
        )
    else:
        spaces = [space_id for space_id, _ in scope["targets"]]
        if spaces:
            _json_payload(
                "GET",
                f"/wiki/v2/spaces/{urllib.parse.quote(spaces[0], safe='')}/nodes",
                transport=client,
                access_token=access_token,
                query={"page_size": 1},
                timeout=timeout,
                sleeper=sleeper,
            )
    return {
        "schema": FEISHU_ADAPTER_SCHEMA,
        "status": "AVAILABLE",
        "connector_instance_id": connector_instance_id,
        "connector_type": FEISHU_CONNECTOR_TYPE,
        "auth_mode": auth_mode,
        "space_count": len(spaces),
        "connection_profile_ref": profile_ref,
        "credentials_persisted": False,
        "access_token_persisted": False,
        "network_side_effect": "READ_ONLY",
    }


def feishu_connector_manifest() -> ConnectorManifest:
    """Return the public Feishu capability declaration without resolving credentials."""
    return ConnectorManifest(
        connector_type=FEISHU_CONNECTOR_TYPE,
        display_name="飞书",
        category="knowledge_base",
        version="1",
        auth_modes=("internal_app", "tenant_access_token", "user_access_token"),
        scope_schema={
            "type": "string",
            "presets": (
                "wiki-all-accessible",
                "wiki-space",
                "wiki-spaces",
                "wiki-node",
            ),
        },
        supported_resource_types=tuple(sorted({*_EXPORT_FORMATS, *_DIRECT_FILE_TYPES})),
        sync_modes=("FULL", "INCREMENTAL"),
        webhook_supported=False,
        local_runner_supported=False,
        local_runner_required=False,
        read_only=True,
        credential_fields=(
            ConnectorCredentialField(
                name="app_id",
                field_type="text",
                required=True,
                secret=False,
                description="企业应用标识",
                auth_modes=("internal_app",),
            ),
            ConnectorCredentialField(
                name="app_secret",
                field_type="secret",
                required=True,
                secret=True,
                description="企业应用密钥",
                auth_modes=("internal_app",),
            ),
            ConnectorCredentialField(
                name="tenant_access_token",
                field_type="token",
                required=True,
                secret=True,
                description="租户访问令牌",
                auth_modes=("tenant_access_token",),
            ),
            ConnectorCredentialField(
                name="user_access_token",
                field_type="token",
                required=True,
                secret=True,
                description="用户访问令牌",
                auth_modes=("user_access_token",),
            ),
        ),
        capability_contract_version=_MATERIALIZATION_CONTRACT_VERSION,
    )


def _context_value(context: ConnectorContext, name: str) -> Any:
    value = context.get(name)
    if value is None or value == "":
        raise FeishuConnectorError(f"connector_context_{name}_missing")
    return value


class FeishuConnectorAdapter:
    """Generic ConnectorAdapter facade over the existing Feishu implementation."""

    def manifest(self) -> ConnectorManifest:
        return feishu_connector_manifest()

    def test_connection(self, context: ConnectorContext) -> dict[str, Any]:
        return test_feishu_connector_connection(
            _context_value(context, "project_id"),
            connector_instance_id=_context_value(context, "connector_instance_id"),
            resolve_connection_profile=_context_value(
                context, "resolve_connection_profile"
            ),
            root=context.get("root"),
            transport=context.get("transport"),
            timeout=float(context.get("timeout", 15.0)),
            sleeper=context.get("sleeper", time.sleep),
        )

    def discover(self, context: ConnectorContext, cursor: SyncCursor = "") -> DiscoveryResult:
        descriptors = discover_feishu_wiki_resources(
            _context_value(context, "access_token"),
            _context_value(context, "resource_scope"),
            transport=context.get("transport"),
            timeout=float(context.get("timeout", 15.0)),
            max_nodes=int(context.get("max_nodes", _DEFAULT_MAX_NODES)),
            sleeper=context.get("sleeper", time.sleep),
        )
        return {
            "schema": FEISHU_ADAPTER_SCHEMA,
            "descriptors": descriptors,
            "complete": True,
            "next_cursor": self.build_cursor(descriptors),
            "coverage": {"discovered_count": len(descriptors)},
            "previous_cursor_supplied": bool(cursor),
        }

    def classify_resource(self, descriptor: Mapping[str, Any]) -> ResourceCapability:
        from .feishu_connector_capability_sync_core import classify_feishu_resource

        return classify_feishu_resource(descriptor)

    def materialize(
        self,
        context: ConnectorContext,
        descriptor: Mapping[str, Any],
    ) -> MaterializedSnapshot:
        return materialize_feishu_resource(
            descriptor,
            _context_value(context, "access_token"),
            transport=context.get("transport"),
            timeout=float(context.get("timeout", 15.0)),
            max_export_polls=int(
                context.get("max_export_polls", _DEFAULT_MAX_EXPORT_POLLS)
            ),
            export_poll_interval=float(context.get("export_poll_interval", 0.5)),
            allow_raw_text_fallback=bool(context.get("allow_raw_text_fallback", False)),
            sleeper=context.get("sleeper", time.sleep),
        )

    def build_cursor(
        self,
        discovery_result: DiscoveryResult | list[Mapping[str, Any]],
    ) -> SyncCursor:
        descriptors = (
            discovery_result.get("descriptors")
            if isinstance(discovery_result, Mapping)
            else discovery_result
        )
        if not isinstance(descriptors, list):
            raise FeishuConnectorError("connector_discovery_descriptors_missing")
        return _snapshot_cursor([dict(item) for item in descriptors])


def sync_feishu_connector(
    project_id: str,
    *,
    connector_instance_id: str,
    resolve_connection_profile: ConnectionProfileResolver,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    previous_cursor: str = "",
    deletion_policy: str = "RETAIN",
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    max_nodes: int = _DEFAULT_MAX_NODES,
    max_export_polls: int = _DEFAULT_MAX_EXPORT_POLLS,
    export_poll_interval: float = 0.5,
    allow_raw_text_fallback: bool = False,
    transport: FeishuTransport | None = None,
    timeout: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Build one complete Feishu Wiki snapshot and hand it to the common coordinator."""
    resolved_root = root or ROOT
    instance = _connector_instance(project_id, connector_instance_id, resolved_root)
    stored_cursor_hash = _text(
        instance.get("last_committed_cursor_fingerprint"), 128
    )
    if stored_cursor_hash and not previous_cursor:
        raise FeishuConnectorError("feishu_previous_cursor_required")
    profile_ref = _text(instance.get("connection_profile_ref"), 500)
    try:
        profile = resolve_connection_profile(profile_ref)
    except Exception as exc:
        raise FeishuConnectorError(
            f"feishu_connection_profile_resolution_failed:{type(exc).__name__}"
        ) from exc

    client = transport or _default_transport
    access_token, auth_mode = _resolve_access_token(
        profile,
        transport=client,
        timeout=timeout,
        sleeper=sleeper,
    )
    descriptors = discover_feishu_wiki_resources(
        access_token,
        _text(instance.get("resource_scope"), 1000),
        transport=client,
        timeout=timeout,
        max_nodes=max_nodes,
        sleeper=sleeper,
    )
    next_cursor = _snapshot_cursor(descriptors)
    observation_index = connector_snapshot_observation_index(
        project_id,
        connector_instance_id=connector_instance_id,
        root=resolved_root,
    )
    unchanged_observations: list[dict[str, Any]] = []
    pending_materializations: list[tuple[dict[str, Any], str]] = []
    for descriptor in descriptors:
        remote_id = _text(descriptor.get("remote_resource_id"), 1000)
        fingerprint = _materialization_fingerprint(
            descriptor,
            allow_raw_text_fallback=allow_raw_text_fallback,
        )
        existing = dict(observation_index.get(remote_id) or {})
        existing_metadata = dict(existing.get("source_metadata") or {})
        if (
            existing
            and _text(
                existing_metadata.get("remote_materialization_fingerprint"),
                128,
            )
            == fingerprint
        ):
            unchanged_observations.append(
                _unchanged_observation(descriptor, fingerprint)
            )
            continue
        pending_materializations.append((dict(descriptor), fingerprint))

    items, degraded_count, materialization_worker_count = (
        _materialize_changed_resources(
            pending_materializations,
            access_token,
            transport=client,
            timeout=timeout,
            max_export_polls=max_export_polls,
            export_poll_interval=export_poll_interval,
            allow_raw_text_fallback=allow_raw_text_fallback,
            sleeper=sleeper,
        )
    )
    try:
        run = sync_connector_snapshot_batch(
            project_id,
            connector_instance_id=connector_instance_id,
            items=items,
            unchanged_observations=unchanged_observations,
            root=resolved_root,
            actor=actor,
            sync_mode="FULL",
            previous_cursor=previous_cursor,
            next_cursor=next_cursor,
            deletion_policy=deletion_policy,
            snapshot_complete=True,
            max_retire_count=max_retire_count,
            max_retire_ratio=max_retire_ratio,
        )
    except ConnectorSyncError as exc:
        raise FeishuConnectorError(f"feishu_sync_rejected:{exc}") from exc

    return {
        **run,
        "adapter_schema": FEISHU_ADAPTER_SCHEMA,
        "adapter": "feishu",
        "connector_type": FEISHU_CONNECTOR_TYPE,
        "auth_mode": auth_mode,
        "resource_scope": _text(instance.get("resource_scope"), 1000),
        "discovered_resource_count": len(descriptors),
        "materialized_resource_count": len(items),
        "unchanged_resource_count": len(unchanged_observations),
        "export_avoided_count": len(unchanged_observations),
        "materialization_worker_count": materialization_worker_count,
        "parallel_materialization_used": materialization_worker_count > 1,
        "degraded_resource_count": degraded_count,
        "snapshot_complete": True,
        "next_cursor": next_cursor,
        "next_cursor_persisted_by_adapter": False,
        "connection_profile_ref": profile_ref,
        "credentials_persisted": False,
        "access_token_persisted": False,
        "source_content_persisted_in_adapter_receipt": False,
        "connector_parser_implemented": False,
    }


__all__ = [
    "FEISHU_ADAPTER_SCHEMA",
    "FEISHU_CONNECTOR_TYPE",
    "FeishuConnectorAdapter",
    "FeishuConnectorError",
    "FeishuHttpResponse",
    "discover_feishu_wiki_resources",
    "feishu_connector_manifest",
    "materialize_feishu_resource",
    "sync_feishu_connector",
    "test_feishu_connector_connection",
]
