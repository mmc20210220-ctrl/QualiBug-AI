"""Read-only Git repository connector for Gitee, GitLab, and GitHub.

The adapter is a transport and snapshot authority only.  It uses the provider API to obtain
repository metadata, trees, blobs, and explicitly requested auxiliary resources, then delegates
every materialized item to the existing connector snapshot and Source Occurrence authorities.
It never executes repository code, follows build instructions, creates a second semantic model,
or persists credentials in descriptors, cursors, or synchronization receipts.
"""
from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import mimetypes
import posixpath
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
    ResourceDisposition,
    classify_materialization_capability,
)
from .connector_remote_lifecycle import reconcile_connector_remote_lifecycle
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
    connector_snapshot_observation_index,
    list_connector_instances,
    sync_connector_snapshot_batch,
)
from .enterprise_knowledge_center._common import MAX_SOURCE_BYTES, ROOT
from .ssrf_guard import SsrfBlockedError, safe_urlopen, validate_url

GIT_ADAPTER_SCHEMA = "qualibug.git-connector-adapter.v1"
GIT_MATERIALIZATION_CONTRACT_VERSION = "git-materialization-v1"
GIT_CONNECTOR_TYPES = ("gitee", "gitlab", "github", "git")

_DEFAULT_MAX_FILES = 500
_DEFAULT_MAX_FILE_BYTES = min(MAX_SOURCE_BYTES, 4 * 1024 * 1024)
_DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_AUX_RESOURCES = 200
_DEFAULT_MAX_COMMITS = 100
_MAX_FILES = 20_000
_MAX_FILE_BYTES = MAX_SOURCE_BYTES
_MAX_TOTAL_BYTES = 200 * 1024 * 1024
_MAX_AUX_RESOURCES = 2_000
_MAX_COMMITS = 1_000
_MAX_SCOPE_RULES = 256
_MAX_TREE_ENTRIES = 200_000
_MAX_API_PAGE_SIZE = 100
_MAX_API_PAGES = 100
_MAX_CURSOR_BYTES = 20_000
_MAX_RETRIES = 3
_RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_SUPPORTED_FILE_OBJECT_TYPES = {"file"}
_SUPPORTED_AUX_OBJECT_TYPES = {"commit", "issue", "release", "wiki_page"}
_SUPPORTED_OBJECT_TYPES = _SUPPORTED_FILE_OBJECT_TYPES | _SUPPORTED_AUX_OBJECT_TYPES
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key|password|passwd|secret)"
)
_SECRET_VALUE_PATTERNS = (
    re.compile(
        r"(?im)^\s*(?:aws_secret_access_key|aws_access_key_id|github_token|gitlab_token|gitee_token)\s*[:=]\s*[\"']?[^\s\"']{12,}"
    ),
    re.compile(
        r"(?i)\b(?:authorization|x-api-key|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\b\s*[:=]\s*[\"']?(?:bearer\s+)?[A-Za-z0-9._~+/=-]{12,}"
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
)
_SENSITIVE_PATH_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "*credentials*.json",
    "*service-account*.json",
    "*secret*.json",
)
_SAFE_BRANCH_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,500}$")


class GitConnectorError(RuntimeError):
    """A Git repository snapshot is unavailable or cannot be trusted."""


class GitApiError(GitConnectorError):
    """A provider API returned a non-success status."""

    def __init__(self, status: int, path: str) -> None:
        self.status = int(status)
        self.path = str(path)
        super().__init__(f"git_api_http_{self.status}")


class GitUnsupportedResource(GitConnectorError):
    """A resource is observable but intentionally blocked from materialization."""


class GitBranchNotFound(GitConnectorError):
    """The configured branch no longer exists; prior material remains authoritative."""

    def __init__(self, branch: str) -> None:
        self.branch = str(branch)
        super().__init__("git_branch_not_found")


@dataclass(frozen=True)
class GitHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str = ""


GitTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float, int],
    GitHttpResponse,
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


def _sha256(value: bytes | str) -> str:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _safe_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise GitConnectorError(f"git_scope_{field}_invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GitConnectorError(f"git_scope_{field}_invalid") from exc
    if not minimum <= result <= maximum:
        raise GitConnectorError(f"git_scope_{field}_out_of_range")
    return result


def _string_list(
    value: Any,
    field: str,
    *,
    maximum: int,
    required: bool = False,
) -> list[str]:
    if value in (None, ""):
        if required:
            raise GitConnectorError(f"git_scope_{field}_required")
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise GitConnectorError(f"git_scope_{field}_must_be_list")
    if len(values) > maximum:
        raise GitConnectorError(f"git_scope_{field}_limit_exceeded")
    result: list[str] = []
    seen: set[str] = set()
    for value_item in values:
        item = _text(value_item, 2_000)
        if not item:
            raise GitConnectorError(f"git_scope_{field}_contains_empty_value")
        if item not in seen:
            seen.add(item)
            result.append(item)
    if required and not result:
        raise GitConnectorError(f"git_scope_{field}_required")
    return result


def _normalize_http_url(value: Any, field: str) -> str:
    raw = _text(value, 4_000)
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise GitConnectorError(f"git_{field}_invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise GitConnectorError(f"git_{field}_invalid") from exc
    host = parsed.hostname.lower().rstrip(".")
    netloc = host
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc += f":{port}"
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, path.rstrip("/") or "/", "", ""))


def _validate_url_for_context(url: str, context: Mapping[str, Any]) -> str:
    approved_host = _text(context.get("approved_host"), 255).lower()
    try:
        return validate_url(url, allow_internal=False, approved_host=approved_host)
    except SsrfBlockedError as exc:
        raise GitConnectorError("git_ssrf_blocked") from exc


def _repository_parts(repository_url: str) -> tuple[str, list[str]]:
    parsed = urllib.parse.urlsplit(repository_url)
    raw_parts = [urllib.parse.unquote(item) for item in parsed.path.split("/") if item]
    if raw_parts and raw_parts[-1].lower().endswith(".git"):
        raw_parts[-1] = raw_parts[-1][:-4]
    if len(raw_parts) < 2 or any(item in {".", ".."} or not item for item in raw_parts):
        raise GitConnectorError("git_repository_path_invalid")
    if any("\x00" in item or any(ord(ch) < 32 for ch in item) for item in raw_parts):
        raise GitConnectorError("git_repository_path_invalid")
    return "/".join(raw_parts), raw_parts


def _provider_from_host(host: str) -> str:
    lowered = str(host or "").lower().rstrip(".")
    if lowered == "github.com" or lowered.endswith(".github.com"):
        return "github"
    if lowered == "gitlab.com" or lowered.endswith(".gitlab.com"):
        return "gitlab"
    if lowered == "gitee.com" or lowered.endswith(".gitee.com"):
        return "gitee"
    return ""


def _normalize_branch(value: Any) -> str:
    branch = _text(value, 500)
    if not branch or not _SAFE_BRANCH_RE.fullmatch(branch) or branch.startswith("/") or branch.endswith("/"):
        raise GitConnectorError("git_branch_invalid")
    if ".." in branch or branch.endswith(".") or "@{" in branch:
        raise GitConnectorError("git_branch_invalid")
    return branch


def _normalize_path_rule(value: Any, field: str) -> str:
    raw = _text(value, 2_000).replace("\\", "/")
    if not raw or raw.startswith("/") or "\x00" in raw:
        raise GitConnectorError(f"git_scope_{field}_invalid")
    if any(part == ".." for part in raw.split("/")):
        raise GitConnectorError(f"git_scope_{field}_invalid")
    return raw.rstrip("/") or raw


def _default_api_base(provider: str, repository_url: str) -> str:
    parsed = urllib.parse.urlsplit(repository_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    host_provider = _provider_from_host(parsed.hostname or "")
    if provider == "github":
        return "https://api.github.com" if host_provider == "github" else origin + "/api/v3"
    if provider == "gitlab":
        return origin + "/api/v4"
    if provider == "gitee":
        return origin + "/api/v5"
    raise GitConnectorError("git_provider_unknown")


def _scope_from_context(
    context: Mapping[str, Any],
    *,
    connector_type: str,
) -> dict[str, Any]:
    raw = context.get("resource_scope")
    if isinstance(raw, Mapping):
        scope_value: Any = dict(raw)
    else:
        text = _text(raw, 20_000)
        if text.startswith("http://") or text.startswith("https://"):
            scope_value = {"repository_url": text}
        else:
            try:
                scope_value = json.loads(text)
            except (TypeError, ValueError) as exc:
                raise GitConnectorError("git_resource_scope_json_required") from exc
    if not isinstance(scope_value, dict):
        raise GitConnectorError("git_resource_scope_must_be_object")
    allowed_keys = {
        "provider",
        "repository_url",
        "api_base_url",
        "branch",
        "include_paths",
        "exclude_paths",
        "file_types",
        "max_files",
        "max_file_bytes",
        "max_total_bytes",
        "include_issues",
        "include_wiki",
        "include_releases",
        "include_commits",
        "max_aux_resources",
        "max_commits",
    }
    unknown = sorted(set(scope_value) - allowed_keys)
    if unknown:
        raise GitConnectorError("git_scope_field_not_supported:" + str(unknown[0]))
    repository_url = _normalize_http_url(scope_value.get("repository_url"), "repository_url")
    _validate_url_for_context(repository_url, context)
    repository_path, repository_parts = _repository_parts(repository_url)
    hinted = _text(connector_type, 80).lower()
    declared_provider = _text(scope_value.get("provider"), 80).lower()
    provider = declared_provider or (hinted if hinted != "git" else "")
    if not provider:
        provider = _provider_from_host(urllib.parse.urlsplit(repository_url).hostname or "")
    if provider not in {"gitee", "gitlab", "github"}:
        raise GitConnectorError("git_provider_required_or_unknown")
    if hinted != "git" and provider != hinted:
        raise GitConnectorError("git_provider_connector_type_mismatch")
    if provider in {"gitee", "github"} and len(repository_parts) != 2:
        raise GitConnectorError("git_repository_path_invalid")
    api_base = _normalize_http_url(
        scope_value.get("api_base_url") or _default_api_base(provider, repository_url),
        "api_base_url",
    )
    _validate_url_for_context(api_base, context)
    include_paths = [
        _normalize_path_rule(item, "include_paths")
        for item in _string_list(scope_value.get("include_paths"), "include_paths", maximum=_MAX_SCOPE_RULES)
    ]
    exclude_paths = [
        _normalize_path_rule(item, "exclude_paths")
        for item in _string_list(scope_value.get("exclude_paths"), "exclude_paths", maximum=_MAX_SCOPE_RULES)
    ]
    file_types = [
        _text(item, 200).lower()
        for item in _string_list(scope_value.get("file_types"), "file_types", maximum=_MAX_SCOPE_RULES)
    ]
    if any(not item for item in file_types):
        raise GitConnectorError("git_scope_file_types_contains_empty_value")
    branch = _normalize_branch(scope_value["branch"]) if scope_value.get("branch") not in (None, "") else ""
    bools: dict[str, bool] = {}
    for name in ("include_issues", "include_wiki", "include_releases", "include_commits"):
        value = scope_value.get(name, False)
        if not isinstance(value, bool):
            raise GitConnectorError(f"git_scope_{name}_invalid")
        bools[name] = value
    return {
        "provider": provider,
        "repository_url": repository_url,
        "repository_path": repository_path,
        "api_base_url": api_base,
        "branch": branch,
        "include_paths": include_paths,
        "exclude_paths": exclude_paths,
        "file_types": file_types,
        "max_files": _safe_int(scope_value.get("max_files", _DEFAULT_MAX_FILES), "max_files", 1, _MAX_FILES),
        "max_file_bytes": _safe_int(scope_value.get("max_file_bytes", _DEFAULT_MAX_FILE_BYTES), "max_file_bytes", 1_024, _MAX_FILE_BYTES),
        "max_total_bytes": _safe_int(scope_value.get("max_total_bytes", _DEFAULT_MAX_TOTAL_BYTES), "max_total_bytes", 1_024, _MAX_TOTAL_BYTES),
        **bools,
        "max_aux_resources": _safe_int(scope_value.get("max_aux_resources", _DEFAULT_MAX_AUX_RESOURCES), "max_aux_resources", 1, _MAX_AUX_RESOURCES),
        "max_commits": _safe_int(scope_value.get("max_commits", _DEFAULT_MAX_COMMITS), "max_commits", 1, _MAX_COMMITS),
    }


def _profile_for_context(context: Mapping[str, Any]) -> dict[str, str]:
    raw = context.get("_resolved_connection_profile")
    profile: Mapping[str, Any]
    if isinstance(raw, Mapping):
        profile = raw
    else:
        configured = context.get("connection_profile")
        if isinstance(configured, Mapping) and configured:
            profile = configured
        else:
            profile_ref = _text(context.get("connection_profile_ref"), 500)
            if profile_ref:
                resolver = context.get("resolve_connection_profile")
                if not callable(resolver):
                    raise GitConnectorError("git_connection_profile_resolver_missing")
                try:
                    resolved = resolver(profile_ref)
                except Exception as exc:
                    raise GitConnectorError(
                        f"git_connection_profile_resolution_failed:{type(exc).__name__}"
                    ) from exc
                if not isinstance(resolved, Mapping):
                    raise GitConnectorError("git_connection_profile_invalid")
                profile = resolved
            else:
                profile = {}
    auth_mode = _text(profile.get("auth_mode"), 80).lower() or "anonymous"
    if auth_mode not in {"anonymous", "personal_access_token"}:
        raise GitConnectorError("git_auth_mode_invalid")
    token = _text(profile.get("token"), 16_000)
    if auth_mode == "personal_access_token" and not token:
        raise GitConnectorError("git_token_required")
    if auth_mode == "anonymous" and token:
        raise GitConnectorError("git_anonymous_token_not_allowed")
    return {"auth_mode": auth_mode, "token": token}


def _auth_headers(context: Mapping[str, Any], provider: str) -> dict[str, str]:
    profile = _profile_for_context(context)
    token = profile.get("token", "")
    if not token:
        return {}
    if provider == "gitlab":
        return {"PRIVATE-TOKEN": token}
    if provider == "gitee":
        return {"Authorization": f"token {token}"}
    return {"Authorization": f"Bearer {token}"}


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    max_bytes: int,
    *,
    approved_host: str = "",
) -> GitHttpResponse:
    if method.upper() != "GET" or body is not None:
        raise GitConnectorError("git_write_or_body_request_forbidden")
    try:
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        with safe_urlopen(
            request,
            timeout=timeout,
            allow_internal=False,
            approved_host=approved_host,
        ) as response:
            length = _header(response.headers, "Content-Length")
            if length.isdigit() and int(length) > max_bytes:
                raise GitConnectorError("git_response_size_limit_exceeded")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise GitConnectorError("git_response_size_limit_exceeded")
            return GitHttpResponse(
                status=int(getattr(response, "status", response.getcode())),
                headers={str(key): str(value) for key, value in response.headers.items()},
                body=bytes(payload),
                final_url=_text(response.geturl(), 4_000),
            )
    except urllib.error.HTTPError as exc:
        payload = exc.read(max_bytes + 1)
        return GitHttpResponse(
            status=int(exc.code),
            headers={str(key): str(value) for key, value in exc.headers.items()},
            body=bytes(payload[:max_bytes]),
            final_url=_text(exc.geturl(), 4_000),
        )
    except (urllib.error.URLError, TimeoutError, SsrfBlockedError) as exc:
        raise GitConnectorError(f"git_transport_failed:{type(exc).__name__}") from exc


def _request(
    context: Mapping[str, Any],
    scope: Mapping[str, Any],
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    max_bytes: int,
) -> GitHttpResponse:
    if not path.startswith("/") or "#" in path:
        raise GitConnectorError("git_api_path_invalid")
    query = urllib.parse.urlencode(
        [(str(key), str(value)) for key, value in (params or {}).items() if value not in (None, "")],
        doseq=True,
    )
    url = str(scope["api_base_url"]).rstrip("/") + path
    if query:
        url += "?" + query
    _validate_url_for_context(url, context)
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "QualiBug-Git-Connector/1",
    }
    request_headers.update(_auth_headers(context, str(scope["provider"])))
    transport = context.get("transport")
    timeout = float(context.get("timeout", 15.0))
    sleeper = context.get("sleeper", time.sleep)
    last: GitHttpResponse | None = None
    for attempt in range(_MAX_RETRIES):
        if transport is None:
            response = _default_transport(
                "GET",
                url,
                request_headers,
                None,
                timeout,
                max_bytes,
                approved_host=_text(context.get("approved_host"), 255).lower(),
            )
        else:
            if not callable(transport):
                raise GitConnectorError("git_transport_invalid")
            response = transport("GET", url, request_headers, None, timeout, max_bytes)
        if not isinstance(response, GitHttpResponse):
            raise GitConnectorError("git_transport_response_invalid")
        if not isinstance(response.body, (bytes, bytearray, memoryview)):
            raise GitConnectorError("git_transport_response_body_invalid")
        if len(response.body) > max_bytes:
            raise GitConnectorError("git_response_size_limit_exceeded")
        last = response
        if response.status not in _RETRYABLE_STATUSES or attempt + 1 >= _MAX_RETRIES:
            return response
        if callable(sleeper):
            sleeper(min(0.25 * (2**attempt), 2.0))
    if last is None:
        raise GitConnectorError("git_transport_returned_no_response")
    return last


def _api_json(
    context: Mapping[str, Any],
    scope: Mapping[str, Any],
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    max_bytes: int = 8 * 1024 * 1024,
) -> tuple[Any, GitHttpResponse]:
    response = _request(context, scope, path, params=params, max_bytes=max_bytes)
    if response.status < 200 or response.status >= 300:
        raise GitApiError(response.status, path)
    try:
        payload = json.loads(bytes(response.body).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitConnectorError("git_api_invalid_json") from exc
    return payload, response


def _repo_prefix(scope: Mapping[str, Any]) -> str:
    provider = str(scope["provider"])
    path = str(scope["repository_path"])
    if provider == "gitlab":
        return "/projects/" + urllib.parse.quote(path, safe="")
    return "/repos/" + "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))


def _branch_path(scope: Mapping[str, Any], branch: str) -> str:
    if scope["provider"] == "gitlab":
        return _repo_prefix(scope) + "/repository/branches/" + urllib.parse.quote(branch, safe="")
    return _repo_prefix(scope) + "/branches/" + urllib.parse.quote(branch, safe="")


def _commit_path(scope: Mapping[str, Any], commit_sha: str) -> str:
    if scope["provider"] == "gitlab":
        return _repo_prefix(scope) + "/repository/commits/" + urllib.parse.quote(commit_sha, safe="")
    return _repo_prefix(scope) + "/git/commits/" + urllib.parse.quote(commit_sha, safe="")


def _blob_path(scope: Mapping[str, Any], descriptor: Mapping[str, Any]) -> str:
    provider = str(scope["provider"])
    if provider == "gitlab":
        path = _text(descriptor.get("path"), 4_000)
        branch = _text(descriptor.get("branch"), 500)
        return _repo_prefix(scope) + "/repository/files/" + urllib.parse.quote(path, safe="")
    return _repo_prefix(scope) + "/git/blobs/" + urllib.parse.quote(_text(descriptor.get("blob_sha"), 240), safe="")


def _issue_path(scope: Mapping[str, Any], number: str) -> str:
    return _repo_prefix(scope) + "/issues/" + urllib.parse.quote(number, safe="")


def _release_path(scope: Mapping[str, Any], tag: str) -> str:
    if scope["provider"] == "gitlab":
        return _repo_prefix(scope) + "/releases/" + urllib.parse.quote(tag, safe="")
    return _repo_prefix(scope) + "/releases/tags/" + urllib.parse.quote(tag, safe="")


def _wiki_path(scope: Mapping[str, Any], slug: str) -> str:
    if scope["provider"] == "gitlab":
        return _repo_prefix(scope) + "/wikis/" + urllib.parse.quote(slug, safe="")
    return _repo_prefix(scope) + "/wiki/pages/" + urllib.parse.quote(slug, safe="")


def _tree_path(scope: Mapping[str, Any], tree_sha: str) -> str:
    if scope["provider"] == "gitlab":
        return _repo_prefix(scope) + "/repository/tree"
    return _repo_prefix(scope) + "/git/trees/" + urllib.parse.quote(tree_sha, safe="")


def _compare_request(
    context: Mapping[str, Any],
    scope: Mapping[str, Any],
    previous_sha: str,
    current_sha: str,
) -> tuple[Any, GitHttpResponse]:
    if scope["provider"] == "gitlab":
        return _api_json(
            context,
            scope,
            _repo_prefix(scope) + "/repository/compare",
            params={"from": previous_sha, "to": current_sha},
        )
    path = _repo_prefix(scope) + "/compare/" + urllib.parse.quote(previous_sha + "..." + current_sha, safe="")
    return _api_json(context, scope, path)


def _extract_commit_state(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    commit_ref = payload.get("commit") if isinstance(payload.get("commit"), Mapping) else payload
    if not isinstance(commit_ref, Mapping):
        return "", "", ""
    commit = commit_ref.get("commit") if isinstance(commit_ref.get("commit"), Mapping) else commit_ref
    if not isinstance(commit, Mapping):
        commit = commit_ref
    sha = _text(
        payload.get("commit_id")
        or payload.get("id")
        or payload.get("sha")
        or commit_ref.get("id")
        or commit_ref.get("sha")
        or commit.get("id")
        or commit.get("sha"),
        240,
    )
    tree = commit_ref.get("tree") if isinstance(commit_ref.get("tree"), Mapping) else {}
    if not tree:
        tree = commit.get("tree") if isinstance(commit.get("tree"), Mapping) else {}
    tree_sha = _text(
        payload.get("tree_id")
        or commit_ref.get("tree_id")
        or commit.get("tree_id")
        or tree.get("sha"),
        240,
    )
    authored = commit.get("author") if isinstance(commit.get("author"), Mapping) else {}
    committed = commit.get("committer") if isinstance(commit.get("committer"), Mapping) else {}
    updated = _text(
        payload.get("committed_date")
        or payload.get("created_at")
        or committed.get("date")
        or authored.get("date"),
        160,
    )
    return sha, tree_sha, updated


def _repository_and_branch(
    context: Mapping[str, Any],
    scope: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
    repository, _ = _api_json(context, scope, _repo_prefix(scope), max_bytes=2 * 1024 * 1024)
    if not isinstance(repository, Mapping):
        raise GitConnectorError("git_repository_response_invalid")
    branch = scope.get("branch") or _text(repository.get("default_branch"), 500)
    if not branch:
        raise GitConnectorError("git_default_branch_missing")
    branch = _normalize_branch(branch)
    try:
        branch_payload, _ = _api_json(context, scope, _branch_path(scope, branch), max_bytes=2 * 1024 * 1024)
    except GitApiError as exc:
        if exc.status in {404, 410}:
            raise GitBranchNotFound(branch) from exc
        raise
    if not isinstance(branch_payload, Mapping):
        raise GitConnectorError("git_branch_response_invalid")
    commit_sha, tree_sha, updated_at = _extract_commit_state(branch_payload)
    if not commit_sha:
        raise GitConnectorError("git_commit_sha_missing")
    if not tree_sha:
        commit_payload, _ = _api_json(context, scope, _commit_path(scope, commit_sha), max_bytes=4 * 1024 * 1024)
        if isinstance(commit_payload, Mapping):
            extracted_sha, tree_sha, commit_updated = _extract_commit_state(commit_payload)
            if extracted_sha and extracted_sha != commit_sha:
                raise GitConnectorError("git_commit_identity_mismatch")
            updated_at = updated_at or commit_updated
    if not tree_sha:
        raise GitConnectorError("git_tree_hash_missing")
    return dict(repository), dict(branch_payload), branch, commit_sha, tree_sha or ""


def _scope_fingerprint(scope: Mapping[str, Any], branch: str) -> str:
    payload = {
        key: scope.get(key)
        for key in (
            "provider",
            "repository_url",
            "repository_path",
            "api_base_url",
            "include_paths",
            "exclude_paths",
            "file_types",
            "max_files",
            "max_file_bytes",
            "max_total_bytes",
            "include_issues",
            "include_wiki",
            "include_releases",
            "include_commits",
            "max_aux_resources",
            "max_commits",
        )
    }
    payload["branch"] = branch
    return _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _resource_id(scope: Mapping[str, Any], kind: str, locator: str) -> str:
    return (
        "git://"
        + urllib.parse.quote(str(scope["provider"]), safe="")
        + "/"
        + urllib.parse.quote(str(scope["repository_path"]), safe="")
        + "/"
        + urllib.parse.quote(str(scope["branch"]), safe="")
        + "/"
        + urllib.parse.quote(kind, safe="")
        + "/"
        + urllib.parse.quote(locator, safe="")
    )


def _resource_prefix(scope: Mapping[str, Any]) -> str:
    return (
        "git://"
        + urllib.parse.quote(str(scope["provider"]), safe="")
        + "/"
        + urllib.parse.quote(str(scope["repository_path"]), safe="")
        + "/"
        + urllib.parse.quote(str(scope["branch"]), safe="")
        + "/"
    )


def _web_url(scope: Mapping[str, Any], branch: str, path: str = "", *, kind: str = "file", locator: str = "") -> str:
    base = str(scope["repository_url"]).rstrip("/")
    if kind == "file":
        return base + "/blob/" + urllib.parse.quote(branch, safe="") + "/" + urllib.parse.quote(path, safe="/")
    if kind == "commit":
        return base + "/commit/" + urllib.parse.quote(locator, safe="")
    if kind == "issue":
        return base + "/issues/" + urllib.parse.quote(locator, safe="")
    if kind == "release":
        return base + "/releases/tag/" + urllib.parse.quote(locator, safe="")
    return base + "/wikis/" + urllib.parse.quote(locator, safe="")


def _entry_path(entry: Mapping[str, Any]) -> str:
    path = _text(entry.get("path") or entry.get("name"), 4_000).replace("\\", "/")
    if not path or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
        raise GitConnectorError("git_tree_path_invalid")
    return path


def _path_matches(path: str, rule: str) -> bool:
    normalized = rule.strip("/")
    return (
        fnmatch.fnmatchcase(path, normalized)
        or path == normalized
        or path.startswith(normalized + "/")
    )


def _file_in_scope(path: str, scope: Mapping[str, Any]) -> bool:
    includes = list(scope.get("include_paths") or [])
    excludes = list(scope.get("exclude_paths") or [])
    types = list(scope.get("file_types") or [])
    if includes and not any(_path_matches(path, rule) for rule in includes):
        return False
    if any(_path_matches(path, rule) for rule in excludes):
        return False
    if types:
        lowered = path.lower()
        if not any(
            fnmatch.fnmatchcase(lowered, pattern)
            or (pattern.startswith(".") and lowered.endswith(pattern))
            for pattern in types
        ):
            return False
    return True


def _sensitive_path(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(lowered, pattern) or fnmatch.fnmatchcase(name, pattern)
        for pattern in _SENSITIVE_PATH_PATTERNS
    )


def _entry_object_type(entry: Mapping[str, Any], *, file_size: int | None, scope: Mapping[str, Any]) -> str:
    entry_type = _text(entry.get("type"), 80).lower()
    mode = _text(entry.get("mode"), 32)
    if entry_type in {"commit", "submodule"} or mode == "160000":
        return "submodule"
    if mode == "120000":
        return "symlink"
    if entry_type not in {"blob", "file", ""}:
        return "unsupported_entry"
    if _sensitive_path(_text(entry.get("path") or entry.get("name"), 4_000)):
        return "sensitive_file"
    if file_size is not None and file_size > int(scope["max_file_bytes"]):
        return "large_file"
    return "file"


def _file_descriptor(
    scope: Mapping[str, Any],
    *,
    branch: str,
    commit_sha: str,
    tree_hash: str,
    updated_at: str,
    entry: Mapping[str, Any],
    status: str = "",
    previous_path: str = "",
) -> dict[str, Any]:
    path = _entry_path(entry)
    size_value = entry.get("size")
    file_size: int | None = None
    if size_value not in (None, ""):
        file_size = _safe_int(size_value, "file_size", 0, MAX_SOURCE_BYTES * 100)
    blob_sha = _text(entry.get("sha") or entry.get("id"), 240)
    object_type = _entry_object_type(entry, file_size=file_size, scope=scope)
    revision_kind = "blob_sha"
    if not blob_sha and object_type == "file" and scope["provider"] == "gitlab":
        # GitLab's compare response does not consistently expose a new blob id. The exact
        # commit/path pair is still a stable source revision, while materialization resolves
        # the file through the read-only repository-files endpoint at that commit.
        blob_sha = f"{commit_sha}:{path}"
        revision_kind = "commit_path"
    if not blob_sha and object_type == "file":
        object_type = "entry_without_revision"
    remote_id = _resource_id(scope, "file", path)
    mime = mimetypes.guess_type(path, strict=False)[0] or "application/octet-stream"
    metadata: dict[str, Any] = {
        "git_provider": str(scope["provider"]),
        "repository_path": str(scope["repository_path"]),
        "branch_ref": "refs/heads/" + branch,
        "path": path,
        "commit_sha": commit_sha,
        "tree_hash": tree_hash,
        "blob_sha": blob_sha,
        "revision_kind": revision_kind,
    }
    if file_size is not None:
        metadata["file_size"] = file_size
    if status:
        metadata["git_status"] = status
    if previous_path:
        metadata["previous_path"] = previous_path
    return {
        "remote_resource_id": remote_id,
        "resource_kind": "git-file",
        "obj_type": object_type,
        "display_title": path,
        "canonical_url": _web_url(scope, branch, path),
        "parent_remote_id": _resource_id(scope, "tree", branch),
        "remote_revision": blob_sha or commit_sha,
        "remote_updated_at": updated_at,
        "declared_mime": mime,
        "remote_materialization_fingerprint": blob_sha or _sha256(json.dumps(metadata, sort_keys=True)),
        "path": path,
        "branch": branch,
        "blob_sha": blob_sha,
        "commit_sha": commit_sha,
        "tree_hash": tree_hash,
        "file_size": file_size if file_size is not None else 0,
        "git_status": status,
        "previous_path": previous_path,
        "metadata": metadata,
    }


def _coverage(
    remote_id: str,
    reason_code: str,
    *,
    resource_kind: str = "git-file",
    display_title: str = "",
    remote_object_type: str = "file",
    metadata: Mapping[str, Any] | None = None,
    retry_trigger: str = "REMOTE_ACCESS_OR_SCOPE_CHANGE",
) -> dict[str, Any]:
    safe_metadata = {
        str(key): value
        for key, value in dict(metadata or {}).items()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,119}", str(key))
        and isinstance(value, (str, int, float, bool))
        and value not in {"", None}
    }
    return {
        "remote_resource_id": _text(remote_id, 4_000),
        "resource_kind": _text(resource_kind, 80) or "git-file",
        "state": "UNSUPPORTED",
        "reason_code": _text(reason_code, 160),
        "remote_object_type": _text(remote_object_type, 80),
        "display_title": _text(display_title, 300),
        "retry_trigger": retry_trigger,
        "capability_contract_version": GIT_MATERIALIZATION_CONTRACT_VERSION,
        "metadata": safe_metadata,
    }


def _cursor_payload(
    scope: Mapping[str, Any],
    *,
    branch: str,
    commit_sha: str,
    tree_hash: str,
    event_id: str,
    mode: str,
) -> dict[str, Any]:
    return {
        "schema": "qualibug.git-cursor.v1",
        "provider": str(scope["provider"]),
        "repository_url": str(scope["repository_url"]),
        "repository_path": str(scope["repository_path"]),
        "branch_ref": "refs/heads/" + branch,
        "branch": branch,
        "commit_sha": commit_sha,
        "tree_hash": tree_hash,
        "platform_event_id": event_id or "NOT_PROVIDED",
        "scope_fingerprint": _scope_fingerprint(scope, branch),
        "sync_mode": mode,
    }


def _encode_cursor(state: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(state), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > _MAX_CURSOR_BYTES:
        raise GitConnectorError("git_cursor_size_limit_exceeded")
    return "git-snapshot-v1:" + payload


def _decode_cursor(value: Any) -> dict[str, Any]:
    raw = _text(value, _MAX_CURSOR_BYTES + 100)
    if not raw:
        return {}
    prefix = "git-snapshot-v1:"
    if not raw.startswith(prefix):
        raise GitConnectorError("git_cursor_schema_invalid")
    try:
        payload = json.loads(raw[len(prefix) :])
    except json.JSONDecodeError as exc:
        raise GitConnectorError("git_cursor_json_invalid") from exc
    if not isinstance(payload, dict):
        raise GitConnectorError("git_cursor_object_required")
    required = {"provider", "repository_path", "branch", "commit_sha", "tree_hash", "scope_fingerprint"}
    if not required.issubset(payload):
        raise GitConnectorError("git_cursor_fields_missing")
    return payload


def _metadata_for_descriptor(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dict(descriptor.get("metadata") or {}) if isinstance(descriptor.get("metadata"), Mapping) else {}
    for key in (
        "git_provider",
        "repository_path",
        "branch_ref",
        "path",
        "commit_sha",
        "tree_hash",
        "blob_sha",
        "revision_kind",
        "file_size",
        "git_status",
        "previous_path",
        "issue_number",
        "release_tag",
        "wiki_slug",
        "resource_fingerprint",
    ):
        value = descriptor.get(key)
        if value not in (None, ""):
            metadata[key] = value
    display_title = _text(descriptor.get("display_title"), 300)
    if display_title:
        metadata["remote_display_title"] = display_title
    return metadata


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
    ):
        value = descriptor.get(key)
        if value not in (None, ""):
            result[key] = _text(value, 4_000)
    result.update(_metadata_for_descriptor(descriptor))
    return result


def _lifecycle_resource(
    descriptor: Mapping[str, Any],
    capability: ResourceCapability,
) -> dict[str, Any]:
    return {
        "remote_resource_id": _text(descriptor.get("remote_resource_id"), 4_000),
        "resource_kind": _text(descriptor.get("resource_kind"), 160),
        "display_title": _text(descriptor.get("display_title"), 300),
        "parent_remote_id": _text(descriptor.get("parent_remote_id"), 4_000),
        "remote_space_id": _text(descriptor.get("branch_ref"), 600),
        "remote_revision": _text(descriptor.get("remote_revision"), 240),
        "materialization_state": (
            "UNSUPPORTED" if capability.observable_unsupported else "MATERIALIZABLE"
        ),
    }


def _canonical_remote_fingerprint(payload: Mapping[str, Any]) -> str:
    keys = (
        "id",
        "iid",
        "number",
        "sha",
        "id",
        "tag_name",
        "name",
        "slug",
        "title",
        "state",
        "updated_at",
        "committed_date",
        "created_at",
        "web_url",
    )
    reduced = {key: payload.get(key) for key in keys if payload.get(key) not in (None, "")}
    return _sha256(json.dumps(reduced, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _aux_descriptor(
    scope: Mapping[str, Any],
    *,
    branch: str,
    commit_sha: str,
    tree_hash: str,
    updated_at: str,
    kind: str,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    provider = str(scope["provider"])
    if kind == "issue":
        locator = _text(payload.get("number") or payload.get("iid") or payload.get("id"), 240)
        if not locator:
            return None
        resource_id = _resource_id(scope, "issue", locator)
        api_path = _issue_path(scope, locator)
        title = _text(payload.get("title") or f"Issue {locator}", 300)
        revision = _text(payload.get("updated_at") or payload.get("updatedAt") or payload.get("id"), 240)
        metadata = {"git_provider": provider, "repository_path": scope["repository_path"], "branch_ref": "refs/heads/" + branch, "issue_number": locator, "resource_fingerprint": _canonical_remote_fingerprint(payload)}
        return {
            "remote_resource_id": resource_id,
            "resource_kind": "git-issue",
            "obj_type": "issue",
            "display_title": title,
            "canonical_url": _web_url(scope, branch, kind="issue", locator=locator),
            "parent_remote_id": _resource_id(scope, "repository", scope["repository_path"]),
            "remote_revision": revision or _canonical_remote_fingerprint(payload),
            "remote_updated_at": _text(payload.get("updated_at") or updated_at, 160),
            "declared_mime": "application/json",
            "remote_materialization_fingerprint": metadata["resource_fingerprint"],
            "api_path": api_path,
            "branch": branch,
            "commit_sha": commit_sha,
            "tree_hash": tree_hash,
            "issue_number": locator,
            "metadata": metadata,
        }
    if kind == "release":
        locator = _text(payload.get("tag_name") or payload.get("tag_name") or payload.get("name") or payload.get("id"), 500)
        if not locator:
            return None
        resource_id = _resource_id(scope, "release", locator)
        metadata = {"git_provider": provider, "repository_path": scope["repository_path"], "branch_ref": "refs/heads/" + branch, "release_tag": locator, "resource_fingerprint": _canonical_remote_fingerprint(payload)}
        return {
            "remote_resource_id": resource_id,
            "resource_kind": "git-release",
            "obj_type": "release",
            "display_title": _text(payload.get("name") or locator, 300),
            "canonical_url": _web_url(scope, branch, kind="release", locator=locator),
            "parent_remote_id": _resource_id(scope, "repository", scope["repository_path"]),
            "remote_revision": _text(payload.get("published_at") or payload.get("created_at") or locator, 240),
            "remote_updated_at": _text(payload.get("published_at") or payload.get("created_at") or updated_at, 160),
            "declared_mime": "application/json",
            "remote_materialization_fingerprint": metadata["resource_fingerprint"],
            "api_path": _release_path(scope, locator),
            "branch": branch,
            "commit_sha": commit_sha,
            "tree_hash": tree_hash,
            "release_tag": locator,
            "metadata": metadata,
        }
    if kind == "wiki_page":
        locator = _text(payload.get("slug") or payload.get("path") or payload.get("title"), 500)
        if not locator:
            return None
        resource_id = _resource_id(scope, "wiki", locator)
        metadata = {"git_provider": provider, "repository_path": scope["repository_path"], "branch_ref": "refs/heads/" + branch, "wiki_slug": locator, "resource_fingerprint": _canonical_remote_fingerprint(payload)}
        return {
            "remote_resource_id": resource_id,
            "resource_kind": "git-wiki",
            "obj_type": "wiki_page",
            "display_title": _text(payload.get("title") or locator, 300),
            "canonical_url": _web_url(scope, branch, kind="wiki", locator=locator),
            "parent_remote_id": _resource_id(scope, "repository", scope["repository_path"]),
            "remote_revision": _text(payload.get("updated_at") or payload.get("id") or locator, 240),
            "remote_updated_at": _text(payload.get("updated_at") or updated_at, 160),
            "declared_mime": "application/json",
            "remote_materialization_fingerprint": metadata["resource_fingerprint"],
            "api_path": _wiki_path(scope, locator),
            "branch": branch,
            "commit_sha": commit_sha,
            "tree_hash": tree_hash,
            "wiki_slug": locator,
            "metadata": metadata,
        }
    if kind == "commit":
        locator = _text(payload.get("sha") or payload.get("id"), 240)
        if not locator:
            return None
        metadata = {"git_provider": provider, "repository_path": scope["repository_path"], "branch_ref": "refs/heads/" + branch, "commit_sha": locator, "tree_hash": tree_hash, "resource_fingerprint": _canonical_remote_fingerprint(payload)}
        return {
            "remote_resource_id": _resource_id(scope, "commit", locator),
            "resource_kind": "git-commit",
            "obj_type": "commit",
            "display_title": _text((payload.get("commit") or {}).get("message") if isinstance(payload.get("commit"), Mapping) else payload.get("title") or locator, 300),
            "canonical_url": _web_url(scope, branch, kind="commit", locator=locator),
            "parent_remote_id": _resource_id(scope, "repository", scope["repository_path"]),
            "remote_revision": locator,
            "remote_updated_at": _text(payload.get("committed_date") or payload.get("created_at") or updated_at, 160),
            "declared_mime": "application/json",
            "remote_materialization_fingerprint": metadata["resource_fingerprint"],
            "api_path": _commit_path(scope, locator),
            "branch": branch,
            "commit_sha": locator,
            "tree_hash": tree_hash,
            "metadata": metadata,
        }
    return None


def _list_page_rows(
    context: Mapping[str, Any],
    scope: Mapping[str, Any],
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    max_items: int,
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    per_page = min(_MAX_API_PAGE_SIZE, max_items)
    for page in range(1, _MAX_API_PAGES + 1):
        query = dict(params or {})
        query.update({"page": page, "per_page": per_page})
        payload, _ = _api_json(context, scope, path, params=query, max_bytes=12 * 1024 * 1024)
        if isinstance(payload, list):
            page_rows = [dict(item) for item in payload if isinstance(item, Mapping)]
        elif isinstance(payload, Mapping):
            candidate = payload.get("values") or payload.get("items") or payload.get("data")
            if not isinstance(candidate, list):
                raise GitConnectorError("git_api_list_response_invalid")
            page_rows = [dict(item) for item in candidate if isinstance(item, Mapping)]
        else:
            raise GitConnectorError("git_api_list_response_invalid")
        rows.extend(page_rows)
        if len(rows) >= max_items:
            return rows[:max_items], len(page_rows) < per_page
        if len(page_rows) < per_page:
            return rows, True
    return rows[:max_items], False


def _aux_path(scope: Mapping[str, Any], kind: str) -> str:
    prefix = _repo_prefix(scope)
    if kind == "issue":
        return prefix + "/issues"
    if kind == "release":
        return prefix + "/releases"
    if kind == "wiki_page":
        if scope["provider"] == "gitlab":
            return prefix + "/wikis"
        return prefix + "/wiki/pages"
    if kind == "commit":
        if scope["provider"] == "gitlab":
            return prefix + "/repository/commits"
        return prefix + "/commits"
    raise GitConnectorError("git_aux_resource_kind_invalid")


def _discover_aux_resources(
    context: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    branch: str,
    commit_sha: str,
    tree_hash: str,
    updated_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    descriptors: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    complete = True
    flags = (
        ("issue", bool(scope.get("include_issues"))),
        ("wiki_page", bool(scope.get("include_wiki"))),
        ("release", bool(scope.get("include_releases"))),
        ("commit", bool(scope.get("include_commits"))),
    )
    for kind, enabled in flags:
        if not enabled:
            continue
        limit = int(scope["max_commits"] if kind == "commit" else scope["max_aux_resources"])
        params: dict[str, Any] = {}
        if kind == "commit":
            if scope["provider"] == "gitlab":
                params["ref_name"] = branch
            else:
                params["sha"] = branch
        elif kind == "issue" and scope["provider"] == "github":
            params["state"] = "all"
        try:
            rows, page_complete = _list_page_rows(
                context,
                scope,
                _aux_path(scope, kind),
                params=params,
                max_items=limit,
            )
        except GitApiError as exc:
            if exc.status in {404, 405}:
                coverage.append(
                    _coverage(
                        _resource_id(scope, kind, "collection"),
                        f"GIT_{kind.upper()}_API_UNAVAILABLE",
                        resource_kind="git-" + kind.replace("_page", ""),
                        remote_object_type=kind,
                        metadata={"provider": scope["provider"]},
                        retry_trigger="PROVIDER_CAPABILITY_OR_SCOPE_CHANGE",
                    )
                )
                complete = False
                continue
            raise
        if not page_complete:
            coverage.append(
                _coverage(
                    _resource_id(scope, kind, "collection"),
                    f"GIT_{kind.upper()}_PAGE_LIMIT_REACHED",
                    resource_kind="git-" + kind.replace("_page", ""),
                    remote_object_type=kind,
                    metadata={"provider": scope["provider"]},
                    retry_trigger="SCOPE_LIMIT_CHANGE",
                )
            )
            complete = False
        for row in rows:
            descriptor = _aux_descriptor(
                scope,
                branch=branch,
                commit_sha=commit_sha,
                tree_hash=tree_hash,
                updated_at=updated_at,
                kind=kind,
                payload=row,
            )
            if descriptor is not None:
                descriptors.append(descriptor)
    return descriptors, coverage, complete


def _tree_entries(
    context: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    branch: str,
    tree_hash: str,
) -> tuple[list[dict[str, Any]], bool]:
    if scope["provider"] == "gitlab":
        rows, complete = _list_page_rows(
            context,
            scope,
            _tree_path(scope, tree_hash),
            params={"ref": branch, "recursive": "true"},
            max_items=_MAX_TREE_ENTRIES,
        )
        return rows, complete
    payload, _ = _api_json(
        context,
        scope,
        _tree_path(scope, tree_hash),
        params={"recursive": "1"},
        max_bytes=16 * 1024 * 1024,
    )
    if isinstance(payload, list):
        rows = [dict(item) for item in payload if isinstance(item, Mapping)]
        truncated = False
    elif isinstance(payload, Mapping):
        raw = payload.get("tree")
        if not isinstance(raw, list):
            raise GitConnectorError("git_tree_response_invalid")
        rows = [dict(item) for item in raw if isinstance(item, Mapping)]
        truncated = bool(payload.get("truncated"))
    else:
        raise GitConnectorError("git_tree_response_invalid")
    if len(rows) > _MAX_TREE_ENTRIES:
        return rows[:_MAX_TREE_ENTRIES], False
    return rows, not truncated


def _compare_entries(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    if not isinstance(payload, Mapping):
        raise GitConnectorError("git_compare_response_invalid")
    raw_files = payload.get("files") if isinstance(payload.get("files"), list) else payload.get("diffs")
    if not isinstance(raw_files, list):
        raise GitConnectorError("git_compare_files_missing")
    entries: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            continue
        if "new_path" in raw or "old_path" in raw:
            old_path = _text(raw.get("old_path"), 4_000)
            new_path = _text(raw.get("new_path"), 4_000)
            deleted = bool(raw.get("deleted_file"))
            created = bool(raw.get("new_file"))
            renamed = bool(raw.get("renamed_file")) or (old_path and new_path and old_path != new_path)
            status = "removed" if deleted else "added" if created else "renamed" if renamed else "modified"
            path = old_path if deleted else new_path
            previous_path = old_path if renamed and not deleted else ""
            blob_sha = _text(raw.get("new_blob_id") or raw.get("blob_id"), 240)
        else:
            old_path = _text(raw.get("previous_filename"), 4_000)
            new_path = _text(raw.get("filename") or raw.get("path"), 4_000)
            status = _text(raw.get("status"), 40).lower() or "modified"
            path = new_path or old_path
            previous_path = old_path if status == "renamed" else ""
            blob_sha = _text(raw.get("sha"), 240)
        if not path:
            continue
        if status in {"removed", "deleted"}:
            lifecycle.append({"status": "removed", "path": old_path or path, "previous_path": ""})
            continue
        entries.append({
            "path": path,
            "type": "blob",
            "sha": blob_sha,
            "size": raw.get("size"),
            "status": status,
            "previous_path": previous_path,
        })
        if previous_path:
            lifecycle.append({"status": "renamed", "path": path, "previous_path": previous_path})
    complete = not bool(payload.get("compare_timeout")) and not bool(payload.get("truncated"))
    return entries, lifecycle, complete


def _lifecycle_event(
    scope: Mapping[str, Any],
    *,
    branch: str,
    kind: str,
    path: str,
    commit_sha: str,
    previous_path: str = "",
    reason: str = "",
    remote_resource_id: str = "",
) -> dict[str, Any]:
    remote_id = remote_resource_id or _resource_id(scope, "file", path)
    event = {
        "schema": "qualibug.git-lifecycle-event.v1",
        "event": kind,
        "remote_resource_id": remote_id,
        "resource_kind": "git-file",
        "branch_ref": "refs/heads/" + branch,
        "commit_sha": commit_sha,
        "path": path,
        "previous_path": previous_path,
    }
    if reason:
        event["reason_code"] = reason
    return event


def _observed_remote_id_for_path(
    scope: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, Any]],
    path: str,
) -> str:
    direct_id = _resource_id(scope, "file", path)
    if direct_id in observations:
        return direct_id
    for remote_id, observation in observations.items():
        candidate = _text(remote_id, 4_000)
        if not candidate.startswith(_resource_prefix(scope)):
            continue
        if not isinstance(observation, Mapping):
            continue
        metadata = observation.get("source_metadata")
        if not isinstance(metadata, Mapping):
            continue
        if _text(metadata.get("path"), 4_000) == path:
            return candidate
    return ""


def _preserve_renamed_remote_identities(
    scope: Mapping[str, Any],
    descriptors: list[dict[str, Any]],
    lifecycle: list[dict[str, Any]],
    observations: Mapping[str, Mapping[str, Any]],
) -> None:
    """Keep one source occurrence across a provider-reported file rename or move."""
    if not observations:
        return
    lifecycle_by_path = {
        (
            _text(change.get("path"), 4_000),
            _text(change.get("previous_path"), 4_000),
        ): change
        for change in lifecycle
        if isinstance(change, Mapping) and change.get("status") == "renamed"
    }
    for descriptor in descriptors:
        previous_path = _text(descriptor.get("previous_path"), 4_000)
        current_path = _text(descriptor.get("path"), 4_000)
        if not previous_path or not current_path:
            continue
        existing_id = _observed_remote_id_for_path(scope, observations, previous_path)
        if not existing_id:
            continue
        descriptor["remote_resource_id"] = existing_id
        change = lifecycle_by_path.get((current_path, previous_path))
        if change is not None:
            change["remote_resource_id"] = existing_id


def _discover_git_resources(
    context: Mapping[str, Any],
    *,
    connector_type: str,
    cursor: SyncCursor = "",
    previous_observations: Mapping[str, Mapping[str, Any]] | None = None,
) -> DiscoveryResult:
    scope = _scope_from_context(context, connector_type=connector_type)
    profile = _profile_for_context(context)
    working = dict(context)
    working["_resolved_connection_profile"] = profile
    previous = _decode_cursor(cursor)
    try:
        repository, branch_payload, branch, commit_sha, tree_hash = _repository_and_branch(working, scope)
    except GitBranchNotFound as exc:
        branch = exc.branch
        scope["branch"] = branch
        branch_id = _resource_id(scope, "branch", branch)
        branch_coverage = _coverage(
            branch_id,
            "GIT_BRANCH_NOT_FOUND",
            resource_kind="git-branch",
            remote_object_type="branch",
            display_title=branch,
            metadata={"branch_ref": "refs/heads/" + branch, "previous_cursor_supplied": bool(cursor)},
            retry_trigger="REMOTE_BRANCH_REAPPEARANCE_OR_SCOPE_CHANGE",
        )
        return {
            "schema": GIT_ADAPTER_SCHEMA,
            "descriptors": [],
            "complete": False,
            "coverage": {
                "discovered_count": 0,
                "file_count": 0,
                "excluded_count": 0,
                "blocked_count": 1,
                "observations": [branch_coverage],
            },
            "lifecycle": [{
                "schema": "qualibug.git-lifecycle-event.v1",
                "event": "GIT_BRANCH_DELETED_OR_UNAVAILABLE",
                "remote_resource_id": branch_id,
                "resource_kind": "git-branch",
                "branch_ref": "refs/heads/" + branch,
                "previous_cursor_present": bool(previous),
            }],
            "next_cursor": cursor if isinstance(cursor, str) else "",
            "cursor_state": {},
            "sync_mode": "INCREMENTAL",
            "snapshot_complete": False,
            "current_resource_ids": [],
            "previous_cursor_supplied": bool(cursor),
            "repository": {
                "provider": scope["provider"],
                "repository_path": scope["repository_path"],
                "repository_url": scope["repository_url"],
                "default_branch": "",
            },
            "current_commit_sha": "",
            "current_tree_hash": "",
            "branch_ref": "refs/heads/" + branch,
            "platform_event_id": _text(context.get("platform_event_id"), 500) or "NOT_PROVIDED",
            "credentials_persisted": False,
            "source_content_returned": False,
        }
    scope["branch"] = branch
    observations = previous_observations or {}
    event_id = _text(context.get("platform_event_id"), 500)
    updated_at = _text(
        branch_payload.get("commit", {}).get("committed_date")
        if isinstance(branch_payload.get("commit"), Mapping)
        else branch_payload.get("committed_date"),
        160,
    )
    if previous:
        if (
            _text(previous.get("provider"), 80) != scope["provider"]
            or _text(previous.get("repository_path"), 4_000) != scope["repository_path"]
            or _text(previous.get("branch"), 500) != branch
        ):
            raise GitConnectorError("git_cursor_scope_identity_mismatch")
    old_commit = _text(previous.get("commit_sha"), 240)
    old_tree = _text(previous.get("tree_hash"), 240)
    current_scope_fingerprint = _scope_fingerprint(scope, branch)
    scope_changed = bool(previous and _text(previous.get("scope_fingerprint"), 128) != current_scope_fingerprint)
    mode = "FULL"
    entries: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    complete = True
    if old_commit and old_commit == commit_sha and old_tree == tree_hash and not scope_changed:
        mode = "INCREMENTAL"
    elif old_commit and not scope_changed:
        try:
            compare_payload, _ = _compare_request(working, scope, old_commit, commit_sha)
            entries, compare_lifecycle, compare_complete = _compare_entries(compare_payload)
            lifecycle.extend(compare_lifecycle)
            if not compare_complete:
                mode = "FULL"
                lifecycle.append(_lifecycle_event(scope, branch=branch, kind="GIT_COMPARE_REQUIRES_FULL_RESCAN", path="", commit_sha=commit_sha, reason="GIT_COMPARE_INCOMPLETE"))
            else:
                mode = "INCREMENTAL"
        except GitApiError as exc:
            if exc.status not in {404, 409, 422}:
                raise
            mode = "FULL"
            lifecycle.append(_lifecycle_event(scope, branch=branch, kind="GIT_HISTORY_REWRITTEN", path="", commit_sha=commit_sha, reason="GIT_COMPARE_BASE_UNAVAILABLE"))
    if mode == "FULL":
        try:
            entries, tree_complete = _tree_entries(working, scope, branch=branch, tree_hash=tree_hash)
        except GitApiError as exc:
            if exc.status in {401, 403}:
                raise GitConnectorError("git_tree_permission_denied") from exc
            raise
        if not tree_complete:
            coverage.append(
                _coverage(
                    _resource_id(scope, "tree", branch),
                    "GIT_TREE_INCOMPLETE",
                    resource_kind="git-tree",
                    remote_object_type="tree",
                    metadata={"branch_ref": "refs/heads/" + branch, "tree_hash": tree_hash},
                    retry_trigger="SCOPE_LIMIT_CHANGE_OR_PROVIDER_PAGINATION",
                )
            )
            complete = False
    descriptors: list[dict[str, Any]] = []
    seen_file_count = 0
    excluded_count = 0
    for entry in entries:
        try:
            path = _entry_path(entry)
        except GitConnectorError:
            coverage.append(
                _coverage(
                    _resource_id(scope, "entry", _text(entry.get("path") or entry.get("name"), 4_000)),
                    "GIT_TREE_PATH_INVALID",
                    resource_kind="git-entry",
                    remote_object_type="tree_entry",
                )
            )
            complete = False
            continue
        if not _file_in_scope(path, scope):
            excluded_count += 1
            continue
        if seen_file_count >= int(scope["max_files"]):
            complete = False
            coverage.append(
                _coverage(
                    _resource_id(scope, "scope", "file-limit"),
                    "GIT_FILE_LIMIT_REACHED",
                    resource_kind="git-scope",
                    remote_object_type="file",
                    metadata={"max_files": int(scope["max_files"])},
                    retry_trigger="SCOPE_LIMIT_CHANGE",
                )
            )
            break
        descriptor = _file_descriptor(
            scope,
            branch=branch,
            commit_sha=commit_sha,
            tree_hash=tree_hash,
            updated_at=updated_at,
            entry=entry,
            status=_text(entry.get("status"), 40),
            previous_path=_text(entry.get("previous_path"), 4_000),
        )
        descriptors.append(descriptor)
        seen_file_count += 1
    aux_descriptors, aux_coverage, aux_complete = _discover_aux_resources(
        working,
        scope,
        branch=branch,
        commit_sha=commit_sha,
        tree_hash=tree_hash,
        updated_at=updated_at,
    )
    descriptors.extend(aux_descriptors)
    coverage.extend(aux_coverage)
    complete = complete and aux_complete

    _preserve_renamed_remote_identities(scope, descriptors, lifecycle, observations)

    descriptor_ids = {_text(row.get("remote_resource_id"), 4_000) for row in descriptors}
    if mode == "FULL" and observations:
        for remote_id, observation in observations.items():
            if not str(remote_id).startswith(_resource_prefix(scope)) or remote_id in descriptor_ids:
                continue
            metadata = dict(observation.get("source_metadata") or {}) if isinstance(observation, Mapping) else {}
            path = _text(metadata.get("path"), 4_000)
            if path:
                lifecycle.append(_lifecycle_event(scope, branch=branch, kind="GIT_RESOURCE_REMOVED", path=path, commit_sha=commit_sha, reason="GIT_FULL_TREE_ABSENCE"))
    for change in list(lifecycle):
        if change.get("status") == "removed":
            old_path = _text(change.get("path"), 4_000)
            if old_path:
                change.clear()
                change.update(_lifecycle_event(scope, branch=branch, kind="GIT_FILE_DELETED", path=old_path, commit_sha=commit_sha))
        elif change.get("status") == "renamed":
            new_path = _text(change.get("path"), 4_000)
            old_path = _text(change.get("previous_path"), 4_000)
            remote_resource_id = _text(change.get("remote_resource_id"), 4_000)
            change.clear()
            change.update(_lifecycle_event(scope, branch=branch, kind="GIT_FILE_RENAMED", path=new_path, previous_path=old_path, commit_sha=commit_sha, remote_resource_id=remote_resource_id))
    current_state = _cursor_payload(
        scope,
        branch=branch,
        commit_sha=commit_sha,
        tree_hash=tree_hash,
        event_id=event_id,
        mode=mode,
    )
    return {
        "schema": GIT_ADAPTER_SCHEMA,
        "descriptors": descriptors,
        "complete": bool(complete and not coverage),
        "coverage": {
            "discovered_count": len(descriptors),
            "file_count": seen_file_count,
            "excluded_count": excluded_count,
            "blocked_count": len(coverage),
            "observations": coverage,
        },
        "lifecycle": lifecycle,
        "next_cursor": _encode_cursor(current_state),
        "cursor_state": current_state,
        "sync_mode": mode,
        "snapshot_complete": bool(mode == "FULL" and complete and not coverage),
        "current_resource_ids": sorted(descriptor_ids),
        "previous_cursor_supplied": bool(cursor),
        "repository": {
            "provider": scope["provider"],
            "repository_path": scope["repository_path"],
            "repository_url": scope["repository_url"],
            "default_branch": _text(repository.get("default_branch"), 500),
        },
        "current_commit_sha": commit_sha,
        "current_tree_hash": tree_hash,
        "branch_ref": "refs/heads/" + branch,
        "platform_event_id": event_id or "NOT_PROVIDED",
        "credentials_persisted": False,
        "source_content_returned": False,
    }


def _adapter_capability(descriptor: Mapping[str, Any], connector_type: str) -> ResourceCapability:
    object_type = _text(descriptor.get("obj_type"), 80).lower()
    explicit_reasons = {
        "submodule": "GIT_SUBMODULE_NOT_MATERIALIZED",
        "sensitive_file": "GIT_SENSITIVE_PATH_BLOCKED",
        "large_file": "GIT_FILE_SIZE_LIMIT",
        "unsupported_entry": "GIT_TREE_ENTRY_UNSUPPORTED",
        "entry_without_revision": "GIT_FILE_REVISION_MISSING",
        "symlink": "GIT_SYMLINK_NOT_FOLLOWED",
    }
    if object_type in explicit_reasons:
        return ResourceCapability(
            disposition=ResourceDisposition.OBSERVABLE_UNSUPPORTED,
            connector_type=_text(connector_type, 80).lower(),
            remote_object_type=object_type,
            reason_code=explicit_reasons[object_type],
            contract_version=GIT_MATERIALIZATION_CONTRACT_VERSION,
            retry_trigger="REMOTE_REVISION_OR_SCOPE_CHANGE",
        )
    return classify_materialization_capability(
        descriptor,
        connector_type=connector_type,
        materializable_types=tuple(sorted(_SUPPORTED_OBJECT_TYPES)),
        contract_version=GIT_MATERIALIZATION_CONTRACT_VERSION,
    )


def _looks_like_lfs_pointer(blob: bytes) -> bool:
    preview = blob[:1_000].decode("utf-8", errors="ignore")
    return preview.startswith("version https://git-lfs.github.com/spec/v1\n") and "\noid sha256:" in preview and "\nsize " in preview


def _looks_like_secret(blob: bytes) -> bool:
    preview = blob[:4 * 1024 * 1024].decode("utf-8", errors="ignore")
    return any(pattern.search(preview) for pattern in _SECRET_VALUE_PATTERNS)


def _source_type_for_blob(filename: str, blob: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        return "other_document"
    text = blob[:2 * 1024 * 1024].decode("utf-8", errors="replace")
    try:
        payload = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError):
        payload = None
    if isinstance(payload, Mapping) and (payload.get("openapi") or payload.get("swagger")):
        return "openapi"
    if isinstance(payload, Mapping) and isinstance(payload.get("item"), list):
        return "postman"
    return "other_document"


def _safe_filename(path: str) -> str:
    name = Path(path).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip(" ._")
    return (name or "repository_material.txt")[:240]


def _materialize_git_resource(
    context: ConnectorContext,
    descriptor: Mapping[str, Any],
    *,
    connector_type: str,
) -> MaterializedSnapshot:
    capability = _adapter_capability(descriptor, connector_type)
    if not capability.materializable:
        raise GitUnsupportedResource(f"git_resource_not_materializable:{capability.reason_code}")
    scope = _scope_from_context(context, connector_type=connector_type)
    branch = _normalize_branch(descriptor.get("branch") or scope.get("branch"))
    scope["branch"] = branch
    profile = _profile_for_context(context)
    working = dict(context)
    working["_resolved_connection_profile"] = profile
    object_type = _text(descriptor.get("obj_type"), 80)
    blob: bytes
    if object_type == "file":
        expected_size = int(descriptor.get("file_size") or 0)
        if expected_size > int(scope["max_file_bytes"]):
            raise GitUnsupportedResource("git_file_size_limit")
        path = _text(descriptor.get("path"), 4_000)
        if not path or _sensitive_path(path):
            raise GitUnsupportedResource("git_sensitive_path_blocked")
        payload, _ = _api_json(
            working,
            scope,
            _blob_path(scope, descriptor),
            params={"ref": branch} if scope["provider"] == "gitlab" else None,
            max_bytes=max(int(scope["max_file_bytes"]) * 2, 1 * 1024 * 1024),
        )
        if not isinstance(payload, Mapping):
            raise GitConnectorError("git_blob_response_invalid")
        encoded = payload.get("content")
        if not isinstance(encoded, str):
            raise GitConnectorError("git_blob_content_missing")
        if _text(payload.get("encoding"), 40).lower() in {"base64", ""}:
            try:
                blob = base64.b64decode(encoded, validate=False)
            except (ValueError, base64.binascii.Error) as exc:
                raise GitConnectorError("git_blob_base64_invalid") from exc
        else:
            blob = encoded.encode("utf-8")
        if _looks_like_lfs_pointer(blob):
            raise GitUnsupportedResource("git_lfs_pointer_not_materialized")
        if _looks_like_secret(blob):
            raise GitUnsupportedResource("git_secret_suspected")
        if len(blob) > int(scope["max_file_bytes"]) or len(blob) > MAX_SOURCE_BYTES:
            raise GitUnsupportedResource("git_file_size_limit")
        filename = _safe_filename(path)
        source_type = _source_type_for_blob(filename, blob)
        export_format = Path(filename).suffix.lower().lstrip(".") or "binary"
    else:
        endpoint = _text(descriptor.get("api_path"), 4_000)
        if not endpoint:
            raise GitConnectorError("git_aux_endpoint_missing")
        payload, _ = _api_json(working, scope, endpoint, max_bytes=12 * 1024 * 1024)
        if not isinstance(payload, (Mapping, list)):
            raise GitConnectorError("git_aux_response_invalid")
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str).encode("utf-8")
        if _looks_like_secret(text):
            raise GitUnsupportedResource("git_secret_suspected")
        if len(text) > int(scope["max_file_bytes"]) or len(text) > MAX_SOURCE_BYTES:
            raise GitUnsupportedResource("git_aux_size_limit")
        blob = text
        if object_type == "issue":
            filename = "issue-" + _safe_filename(_text(descriptor.get("issue_number"), 120)) + ".json"
        elif object_type == "release":
            filename = "release-" + _safe_filename(_text(descriptor.get("release_tag"), 180)) + ".json"
        elif object_type == "wiki_page":
            filename = "wiki-" + _safe_filename(_text(descriptor.get("wiki_slug"), 180)) + ".json"
        else:
            filename = "commit-" + _safe_filename(_text(descriptor.get("commit_sha"), 120)) + ".json"
        source_type = "collaboration_document"
        export_format = "json"
    return {
        "remote_resource_id": _text(descriptor.get("remote_resource_id"), 4_000),
        "resource_kind": _text(descriptor.get("resource_kind"), 80) or "git-file",
        "display_title": _text(descriptor.get("display_title"), 300),
        "source_type": source_type,
        "filename": filename,
        "content": blob,
        "export_format": export_format,
        "declared_mime": _text(descriptor.get("declared_mime"), 160) or mimetypes.guess_type(filename, strict=False)[0] or "application/octet-stream",
        "remote_revision": _text(descriptor.get("remote_revision"), 240),
        "remote_updated_at": _text(descriptor.get("remote_updated_at"), 160),
        "retrieved_at": _utc_now(),
        "remote_materialization_fingerprint": _text(descriptor.get("remote_materialization_fingerprint"), 128) or _sha256(blob),
        "canonical_url": _text(descriptor.get("canonical_url"), 4_000),
        "parent_remote_id": _text(descriptor.get("parent_remote_id"), 4_000),
        "metadata": _metadata_for_descriptor(descriptor),
    }


def _connector_instance(project: str, connector: str, root: Path, connector_type: str) -> dict[str, Any]:
    rows = list_connector_instances(project, root=root, include_disabled=True).get("connector_instances") or []
    instance = next(
        (
            dict(row)
            for row in rows
            if isinstance(row, Mapping) and _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if instance is None:
        raise GitConnectorError("git_connector_instance_not_registered")
    actual_type = _text(instance.get("connector_type"), 80).lower()
    if actual_type != _text(connector_type, 80).lower():
        raise GitConnectorError("git_connector_instance_type_mismatch")
    if instance.get("status") != "ACTIVE":
        raise GitConnectorError("git_connector_instance_not_active")
    return instance


def git_connector_manifest(connector_type: str = "git") -> ConnectorManifest:
    kind = _text(connector_type, 80).lower() or "git"
    if kind not in GIT_CONNECTOR_TYPES:
        raise GitConnectorError("git_connector_type_invalid")
    names = {
        "gitee": "Gitee repository",
        "gitlab": "GitLab repository",
        "github": "GitHub repository",
        "git": "Generic Git repository",
    }
    return ConnectorManifest(
        connector_type=kind,
        display_name=names[kind],
        category="source_code",
        version="1",
        auth_modes=("anonymous", "personal_access_token"),
        scope_schema={
            "type": "object",
            "description": "Read-only repository API materialization with bounded path and size scope.",
            "required": ["repository_url"],
            "properties": {
                "provider": {"type": "string", "enum": ["gitee", "gitlab", "github"]},
                "repository_url": {"type": "string", "format": "uri"},
                "api_base_url": {"type": "string", "format": "uri"},
                "branch": {"type": "string"},
                "include_paths": {"type": "array", "maxItems": _MAX_SCOPE_RULES},
                "exclude_paths": {"type": "array", "maxItems": _MAX_SCOPE_RULES},
                "file_types": {"type": "array", "maxItems": _MAX_SCOPE_RULES},
                "max_files": {"type": "integer", "minimum": 1, "maximum": _MAX_FILES},
                "max_file_bytes": {"type": "integer", "minimum": 1024, "maximum": _MAX_FILE_BYTES},
                "max_total_bytes": {"type": "integer", "minimum": 1024, "maximum": _MAX_TOTAL_BYTES},
                "include_issues": {"type": "boolean", "default": False},
                "include_wiki": {"type": "boolean", "default": False},
                "include_releases": {"type": "boolean", "default": False},
                "include_commits": {"type": "boolean", "default": False},
                "max_aux_resources": {"type": "integer", "minimum": 1, "maximum": _MAX_AUX_RESOURCES},
                "max_commits": {"type": "integer", "minimum": 1, "maximum": _MAX_COMMITS},
            },
        },
        quick_connect_schema=(
            {
                "input_type": "url",
                "scope_field": "repository_url",
                "priority": 30,
            }
            if kind == "git"
            else {}
        ),
        entrypoint_evidence=(
            {"path_suffixes": [".git"]}
            if kind == "git"
            else {}
        ),
        supported_resource_types=tuple(sorted({"file", "submodule", "lfs_pointer", "issue", "wiki_page", "release", "commit"})),
        sync_modes=("FULL", "INCREMENTAL"),
        webhook_supported=True,
        local_runner_supported=True,
        local_runner_required=False,
        read_only=True,
        credential_fields=(
            ConnectorCredentialField(
                name="token",
                field_type="personal_access_token",
                required=True,
                secret=True,
                display_name="代码仓库访问令牌",
                description="Provider token used only for read-only repository API GET requests.",
                auth_modes=("personal_access_token",),
            ),
        ),
        capability_contract_version=GIT_MATERIALIZATION_CONTRACT_VERSION,
    )


def test_git_connector_connection(
    project_id: str,
    *,
    connector_instance_id: str,
    connector_type: str,
    resolve_connection_profile: Callable[[str], Mapping[str, Any]] | None = None,
    root: Path | None = None,
    transport: GitTransport | None = None,
    timeout: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    instance = _connector_instance(project_id, connector_instance_id, resolved_root, connector_type)
    context: dict[str, Any] = {
        "project_id": project_id,
        "connector_instance_id": connector_instance_id,
        "connector_type": connector_type,
        "connection_profile_ref": _text(instance.get("connection_profile_ref"), 500),
        "resolve_connection_profile": resolve_connection_profile,
        "resource_scope": _text(instance.get("resource_scope"), 20_000),
        "transport": transport,
        "timeout": timeout,
        "sleeper": sleeper,
    }
    scope = _scope_from_context(context, connector_type=connector_type)
    profile = _profile_for_context(context)
    working = dict(context)
    working["_resolved_connection_profile"] = profile
    repository, _, branch, _, _ = _repository_and_branch(working, scope)
    return {
        "schema": GIT_ADAPTER_SCHEMA,
        "status": "AVAILABLE",
        "connector_instance_id": connector_instance_id,
        "connector_type": connector_type,
        "provider": scope["provider"],
        "repository_path": scope["repository_path"],
        "repository_url": scope["repository_url"],
        "branch": branch,
        "repository_visibility": _text(repository.get("visibility"), 80),
        "auth_mode": profile["auth_mode"],
        "credentials_persisted": False,
        "source_content_returned": False,
        "network_side_effect": "READ_ONLY_GET",
    }


def sync_git_connector(
    project_id: str,
    *,
    connector_instance_id: str,
    connector_type: str,
    resolve_connection_profile: Callable[[str], Mapping[str, Any]] | None = None,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    previous_cursor: str = "",
    deletion_policy: str = "RETAIN",
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    max_nodes: int = _DEFAULT_MAX_FILES,
    transport: GitTransport | None = None,
    timeout: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
    platform_event_id: str = "",
) -> dict[str, Any]:
    resolved_root = root or ROOT
    instance = _connector_instance(project_id, connector_instance_id, resolved_root, connector_type)
    stored_hash = _text(instance.get("last_committed_cursor_fingerprint"), 128)
    if stored_hash and not previous_cursor:
        raise GitConnectorError("git_previous_cursor_required")
    context: dict[str, Any] = {
        "project_id": project_id,
        "connector_instance_id": connector_instance_id,
        "connector_type": connector_type,
        "connection_profile_ref": _text(instance.get("connection_profile_ref"), 500),
        "resolve_connection_profile": resolve_connection_profile,
        "resource_scope": _text(instance.get("resource_scope"), 20_000),
        "transport": transport,
        "timeout": timeout,
        "sleeper": sleeper,
        "platform_event_id": platform_event_id,
    }
    scope = _scope_from_context(context, connector_type=connector_type)
    profile = _profile_for_context(context)
    context["_resolved_connection_profile"] = profile
    observations = connector_snapshot_observation_index(
        project_id,
        connector_instance_id=connector_instance_id,
        root=resolved_root,
    )
    discovery = _discover_git_resources(
        context,
        connector_type=connector_type,
        cursor=previous_cursor,
        previous_observations=observations,
    )
    discovered_branch_ref = _text(discovery.get("branch_ref"), 600)
    if discovered_branch_ref.startswith("refs/heads/"):
        scope["branch"] = discovered_branch_ref[len("refs/heads/") :]
    descriptors = [dict(row) for row in discovery.get("descriptors") or []]
    coverage = [dict(row) for row in (discovery.get("coverage") or {}).get("observations") or []]
    items: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    lifecycle_resources: list[dict[str, Any]] = []
    represented: set[str] = set()
    materialized_bytes = 0
    for descriptor in descriptors:
        remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
        represented.add(remote_id)
        existing = dict(observations.get(remote_id) or {})
        existing_metadata = dict(existing.get("source_metadata") or {})
        capability = _adapter_capability(descriptor, connector_type)
        lifecycle_resources.append(_lifecycle_resource(descriptor, capability))
        if capability.observable_unsupported:
            coverage.append(
                _coverage(
                    remote_id,
                    capability.reason_code,
                    resource_kind=_text(descriptor.get("resource_kind"), 80) or "git-file",
                    display_title=_text(descriptor.get("display_title"), 300),
                    remote_object_type=capability.remote_object_type,
                    metadata=_metadata_for_descriptor(descriptor),
                    retry_trigger=capability.retry_trigger,
                )
            )
            continue
        if not capability.materializable:
            raise GitConnectorError("git_descriptor_invalid")
        fingerprint = _text(descriptor.get("remote_materialization_fingerprint"), 128)
        if existing and fingerprint and _text(existing_metadata.get("remote_materialization_fingerprint"), 128) == fingerprint:
            unchanged.append({
                "remote_resource_id": remote_id,
                "resource_kind": _text(descriptor.get("resource_kind"), 80) or "git-file",
                "metadata": _observation_metadata(descriptor),
            })
            continue
        size = int(descriptor.get("file_size") or 0)
        if size and materialized_bytes + size > int(scope["max_total_bytes"]):
            coverage.append(
                _coverage(
                    remote_id,
                    "GIT_TOTAL_SIZE_LIMIT_REACHED",
                    resource_kind=_text(descriptor.get("resource_kind"), 80) or "git-file",
                    display_title=_text(descriptor.get("display_title"), 300),
                    remote_object_type=capability.remote_object_type,
                    metadata={"max_total_bytes": int(scope["max_total_bytes"])},
                    retry_trigger="SCOPE_LIMIT_CHANGE",
                )
            )
            continue
        try:
            item = dict(_materialize_git_resource(context, descriptor, connector_type=connector_type))
        except GitUnsupportedResource as exc:
            coverage.append(
                _coverage(
                    remote_id,
                    str(exc).split(":", 1)[-1].upper().replace("-", "_"),
                    resource_kind=_text(descriptor.get("resource_kind"), 80) or "git-file",
                    display_title=_text(descriptor.get("display_title"), 300),
                    remote_object_type=capability.remote_object_type,
                    metadata=_metadata_for_descriptor(descriptor),
                    retry_trigger="REMOTE_CONTENT_OR_SCOPE_CHANGE",
                )
            )
            continue
        except (GitConnectorError, ConnectorSnapshotError) as exc:
            coverage.append(
                _coverage(
                    remote_id,
                    "GIT_MATERIALIZATION_FAILED",
                    resource_kind=_text(descriptor.get("resource_kind"), 80) or "git-file",
                    display_title=_text(descriptor.get("display_title"), 300),
                    remote_object_type=capability.remote_object_type,
                    metadata={"error_code": str(exc).split(":", 1)[0]},
                    retry_trigger="REMOTE_REVISION_OR_CREDENTIAL_CHANGE",
                )
            )
            continue
        materialized_bytes += len(item.get("content") or b"")
        items.append(item)
    lifecycle = [dict(row) for row in discovery.get("lifecycle") or [] if isinstance(row, Mapping)]
    rename_event_count = sum(
        1 for row in lifecycle if _text(row.get("event"), 80) == "GIT_FILE_RENAMED"
    )
    removed_ids = {
        _text(row.get("remote_resource_id"), 4_000)
        for row in lifecycle
        if _text(row.get("event"), 80) in {"GIT_FILE_DELETED", "GIT_RESOURCE_REMOVED"}
    }
    for remote_id, observation in observations.items():
        if remote_id in represented or remote_id in removed_ids or not remote_id.startswith(_resource_prefix(scope)):
            continue
        metadata = dict(observation.get("source_metadata") or {})
        kind = _text(metadata.get("resource_kind"), 80) or "git-file"
        unchanged.append({
            "remote_resource_id": remote_id,
            "resource_kind": kind,
            "metadata": metadata,
        })
    requested_policy = _text(deletion_policy, 32).upper() or "RETAIN"
    if requested_policy not in {"RETAIN", "RETIRE_MISSING"}:
        raise GitConnectorError("git_deletion_policy_invalid")
    mode = _text(discovery.get("sync_mode"), 32).upper() or "INCREMENTAL"
    snapshot_complete = bool(discovery.get("snapshot_complete")) and not coverage
    effective_policy = "RETAIN"
    retirement_skip_reason = ""
    if requested_policy == "RETIRE_MISSING" and (mode != "FULL" or not snapshot_complete):
        retirement_skip_reason = "INCOMPLETE_OR_INCREMENTAL_SNAPSHOT"
    run = sync_connector_snapshot_batch(
        project_id,
        connector_instance_id=connector_instance_id,
        items=items,
        unchanged_observations=unchanged,
        coverage_observations=coverage,
        root=resolved_root,
        actor=actor,
        sync_mode=mode,
        previous_cursor=previous_cursor,
        next_cursor=_text(discovery.get("next_cursor"), _MAX_CURSOR_BYTES),
        deletion_policy="RETAIN",
        snapshot_complete=snapshot_complete,
        max_retire_count=max_retire_count,
        max_retire_ratio=max_retire_ratio,
    )
    remote_lifecycle = {
        "schema": "qualibug.connector-remote-lifecycle.v1",
        "status": "SKIPPED_SYNC_INCOMPLETE",
        "requested_deletion_policy": requested_policy,
        "effective_deletion_policy": "RETAIN",
        "retired_count": 0,
        "remote_deletion_inferred": False,
        "permission_loss_inferred": False,
        "customer_material_mutation_executed": False,
    }
    if run.get("status") == "COMPLETE":
        remote_lifecycle = reconcile_connector_remote_lifecycle(
            project_id,
            connector_instance_id=connector_instance_id,
            present_resources=lifecycle_resources,
            sync_epoch_id=_text(run.get("sync_epoch_id"), 160),
            root=resolved_root,
            actor=actor,
            deletion_policy=requested_policy,
            authoritative_snapshot_complete=snapshot_complete,
            max_retire_count=max_retire_count,
            max_retire_ratio=max_retire_ratio,
        )
        effective_policy = _text(
            remote_lifecycle.get("effective_deletion_policy"), 120
        ) or "RETAIN"
    return {
        **run,
        "adapter_schema": GIT_ADAPTER_SCHEMA,
        "adapter": connector_type,
        "connector_type": connector_type,
        "provider": scope["provider"],
        "repository_path": scope["repository_path"],
        "branch_ref": discovery.get("branch_ref"),
        "current_commit_sha": discovery.get("current_commit_sha"),
        "current_tree_hash": discovery.get("current_tree_hash"),
        "platform_event_id": discovery.get("platform_event_id"),
        "sync_mode_selected": mode,
        "discovered_resource_count": len(descriptors),
        "materialized_resource_count": len(items),
        "unchanged_resource_count": len(unchanged),
        "coverage_observation_count": len(coverage),
        "lifecycle_event_count": len(lifecycle),
        "lifecycle_events": lifecycle,
        "remote_lifecycle": remote_lifecycle,
        "remote_lifecycle_status": remote_lifecycle.get("status"),
        "remote_absent_count": remote_lifecycle.get("absent_count", 0),
        "remote_lifecycle_retirement_eligible_count": remote_lifecycle.get(
            "retirement_eligible_count", 0
        ),
        "retired_count": remote_lifecycle.get("retired_count", 0),
        "renamed_resource_count": max(
            rename_event_count,
            int(remote_lifecycle.get("renamed_resource_count", 0) or 0),
        ),
        "moved_resource_count": remote_lifecycle.get("moved_resource_count", 0),
        "snapshot_complete": snapshot_complete,
        "deletion_policy_requested": requested_policy,
        "deletion_policy_effective": effective_policy,
        "retirement_skip_reason": retirement_skip_reason,
        "next_cursor": discovery.get("next_cursor"),
        "next_cursor_persisted_by_adapter": False,
        "credentials_persisted": False,
        "source_content_persisted_in_adapter_receipt": False,
        "repository_code_executed": False,
        "build_or_test_scripts_executed": False,
    }


class GitRepositoryConnectorAdapter:
    """Manifest-driven Git provider adapter over the shared connector mainline."""

    def __init__(self, connector_type: str = "git") -> None:
        kind = _text(connector_type, 80).lower() or "git"
        if kind not in GIT_CONNECTOR_TYPES:
            raise GitConnectorError("git_connector_type_invalid")
        self._connector_type = kind

    def manifest(self) -> ConnectorManifest:
        return git_connector_manifest(self._connector_type)

    def test_connection(self, context: ConnectorContext) -> dict[str, Any]:
        project = _text(context.get("project_id"), 160)
        connector = _text(context.get("connector_instance_id"), 160)
        if not project or not connector:
            raise GitConnectorError("git_connector_context_identity_missing")
        return test_git_connector_connection(
            project,
            connector_instance_id=connector,
            connector_type=self._connector_type,
            resolve_connection_profile=context.get("resolve_connection_profile"),
            root=context.get("root"),
            transport=context.get("transport"),
            timeout=float(context.get("timeout", 15.0)),
            sleeper=context.get("sleeper", time.sleep),
        )

    def discover(self, context: ConnectorContext, cursor: SyncCursor = "") -> DiscoveryResult:
        return _discover_git_resources(
            context,
            connector_type=self._connector_type,
            cursor=cursor,
            previous_observations=context.get("previous_observations"),
        )

    def classify_resource(self, descriptor: Mapping[str, Any]) -> ResourceCapability:
        return _adapter_capability(descriptor, self._connector_type)

    def materialize(self, context: ConnectorContext, descriptor: Mapping[str, Any]) -> MaterializedSnapshot:
        return _materialize_git_resource(context, descriptor, connector_type=self._connector_type)

    def build_cursor(self, discovery_result: DiscoveryResult | Sequence[Mapping[str, Any]]) -> SyncCursor:
        if isinstance(discovery_result, Mapping):
            state = discovery_result.get("cursor_state")
            if isinstance(state, Mapping):
                return _encode_cursor(state)
            value = discovery_result.get("next_cursor")
            if isinstance(value, str) and value.startswith("git-snapshot-v1:"):
                return value
        raise GitConnectorError("git_discovery_cursor_state_missing")

    def managed_remote_checkpoint(self, context: ConnectorContext) -> SyncCursor:
        result = self.discover(context)
        return self.build_cursor(result)

    def managed_sync(self, context: ConnectorContext) -> dict[str, Any]:
        return sync_git_connector(
            _text(context.get("project_id"), 160),
            connector_instance_id=_text(context.get("connector_instance_id"), 160),
            connector_type=self._connector_type,
            resolve_connection_profile=context.get("resolve_connection_profile"),
            root=context.get("root"),
            actor=dict(context.get("actor") or {}),
            previous_cursor=_text(context.get("previous_cursor"), _MAX_CURSOR_BYTES),
            deletion_policy=_text(context.get("deletion_policy"), 32) or "RETAIN",
            max_retire_count=int(context.get("max_retire_count", 100)),
            max_retire_ratio=float(context.get("max_retire_ratio", 0.25)),
            max_nodes=int(context.get("max_resources", _DEFAULT_MAX_FILES)),
            transport=context.get("transport"),
            timeout=float(context.get("timeout", 15.0)),
            sleeper=context.get("sleeper", time.sleep),
            platform_event_id=_text(context.get("platform_event_id"), 500),
        )


__all__ = [
    "GIT_ADAPTER_SCHEMA",
    "GIT_CONNECTOR_TYPES",
    "GIT_MATERIALIZATION_CONTRACT_VERSION",
    "GitApiError",
    "GitBranchNotFound",
    "GitConnectorError",
    "GitHttpResponse",
    "GitRepositoryConnectorAdapter",
    "GitUnsupportedResource",
    "git_connector_manifest",
    "sync_git_connector",
    "test_git_connector_connection",
]
