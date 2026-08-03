from __future__ import annotations

"""HAR (HTTP Archive) importer — extracts real API surface and error patterns.

Parses HAR 1.2 files exported from Chrome DevTools, Charles Proxy, Firefox,
or any tool that produces standard HTTP Archive JSON.

Outputs:
- endpoints: list[dict] compatible with ApiEndpoint merge
- error_patterns: list[dict] for hypothesis generation
- request_patterns: list[dict] real auth headers, content types, etc.
"""

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class HarEndpoint:
    """A deduplicated API endpoint extracted from HAR traffic."""
    path: str                   # normalized path like /api/v1/users/{id}
    method: str                 # GET / POST / PUT / PATCH / DELETE
    summary: str = ""           # first observed URL path segment hint
    count: int = 0              # how many times this endpoint was called
    statuses: dict[int, int] = field(default_factory=dict)  # status -> count
    auth_headers: set[str] = field(default_factory=set)
    content_types: set[str] = field(default_factory=set)
    sample_body_keys: set[str] = field(default_factory=set)
    error_bodies: list[str] = field(default_factory=list)  # response bodies for 4xx/5xx
    avg_response_time: float = 0.0
    max_response_time: float = 0.0


@dataclass
class HarErrorPattern:
    """An error pattern observed in real API traffic."""
    endpoint: str               # path pattern
    method: str
    status: int
    error_message: str = ""     # extracted from response body
    count: int = 0
    sample_request_body: str = ""
    sample_response_body: str = ""


@dataclass
class HarAuthPattern:
    """Authentication pattern observed across endpoints."""
    role: str                   # admin / viewer / anonymous
    token_prefix: str = ""      # Bearer / Basic / ApiKey
    paths_accessed: list[str] = field(default_factory=list)
    avg_requests: int = 0


# ── Path normalization ────────────────────────────────────────────────────

# Regexes to collapse numeric IDs, UUIDs, and date-like segments into {param}
_ID_RE = re.compile(r'/\d{4,}(?=/|$|\.)')                           # /12345
_UUID_RE = re.compile(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
_ULID_RE = re.compile(r'/[0-9A-HJ-NP-Z]{26}')                       # ULID (26 chars, Crockford base32)
_SHORT_ID_RE = re.compile(r'/[\da-f]{24}(?=/|$)', re.I)             # MongoDB ObjectId style
_DATE_RE = re.compile(r'/\d{4}-\d{2}-\d{2}(?=/|$)')                 # /2024-01-15
_TIMESTAMP_RE = re.compile(r'/\d{10,13}(?=/|$)')                    # Unix timestamps


def _normalize_path(raw_path: str) -> str:
    """Collapse dynamic path segments into ``{param}``."""
    path = raw_path.strip()
    path = _UUID_RE.sub('/{id}', path)
    path = _ULID_RE.sub('/{id}', path)
    path = _SHORT_ID_RE.sub('/{id}', path)
    path = _ID_RE.sub('/{id}', path)
    path = _DATE_RE.sub('/{date}', path)
    path = _TIMESTAMP_RE.sub('/{timestamp}', path)
    # Collapse repeated params (e.g. /users/{id}/{id} -> /users/{id})
    path = re.sub(r'/\{([^}]+)\}/\{\1\}', r'/{\1}', path)
    return path


def _path_entity_name(path: str) -> str:
    """Extract the primary entity name from a normalized path."""
    clean = path.strip("/")
    parts = [p for p in clean.split("/") if not p.startswith("{")]
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] not in ("api", "v1", "v2", "v3", "rest", "public"):
            return parts[i]
    return parts[-1] if parts else "unknown"


# ── HAR parsing ───────────────────────────────────────────────────────────

def _parse_har_json(har_path: str | Path) -> dict[str, Any] | None:
    """Load and validate a HAR file."""
    path = Path(har_path) if not isinstance(har_path, Path) else har_path
    if not path.exists():
        print(f"  [WARN] har_importer: file not found: {path}", flush=True, file=sys.stderr)
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [WARN] har_importer: failed to parse {path}: {e}", flush=True, file=sys.stderr)
        return None
    if not isinstance(data, dict):
        return None
    # HAR 1.2 spec: top-level key is "log"
    log = data.get("log", data)
    if not isinstance(log, dict):
        return None
    return log


def _extract_entries(har_log: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract all request/response entries from a HAR log."""
    entries = har_log.get("entries", [])
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _method_from_entry(entry: dict[str, Any]) -> str:
    request = entry.get("request", {})
    if isinstance(request, dict):
        return str(request.get("method", "GET")).upper()
    return "GET"


def _path_from_entry(entry: dict[str, Any]) -> str:
    request = entry.get("request", {})
    if isinstance(request, dict):
        url = request.get("url", "")
        if url:
            parsed = urlparse(url)
            return parsed.path or "/"
    return "/"


def _status_from_entry(entry: dict[str, Any]) -> int:
    response = entry.get("response", {})
    if isinstance(response, dict):
        return int(response.get("status", 0))
    return 0


def _response_body(entry: dict[str, Any]) -> str:
    """Extract response body text from HAR entry."""
    response = entry.get("response", {})
    if not isinstance(response, dict):
        return ""
    content = response.get("content", {})
    if not isinstance(content, dict):
        return ""
    text = content.get("text", "")
    if text:
        return text[:2000]  # limit to 2KB
    # try encoding fallback
    encoding = content.get("encoding", "")
    if encoding == "base64":
        import base64
        try:
            decoded = base64.b64decode(text or "")
            return decoded.decode("utf-8", errors="replace")[:2000]
        except Exception:
            return ""
    return ""


def _auth_headers(entry: dict[str, Any]) -> list[str]:
    """Extract authentication-related headers."""
    request = entry.get("request", {})
    if not isinstance(request, dict):
        return []
    headers = request.get("headers", [])
    if not isinstance(headers, list):
        return []
    auth = []
    for h in headers:
        if not isinstance(h, dict):
            continue
        name = str(h.get("name", "")).lower()
        if name in ("authorization", "x-api-key", "x-auth-token", "cookie"):
            value = str(h.get("value", ""))
            if len(value) > 100:
                value = value[:50] + "..." + value[-20:]
            auth.append(f"{name}: {value}")
    return auth


def _content_type_from_entry(entry: dict[str, Any]) -> str:
    response = entry.get("response", {})
    if not isinstance(response, dict):
        return ""
    content = response.get("content", {})
    if not isinstance(content, dict):
        return ""
    mime = content.get("mimeType", "")
    return mime


def _response_time(entry: dict[str, Any]) -> float:
    """Extract response time in milliseconds."""
    timings = entry.get("timings", {})
    if isinstance(timings, dict):
        # sum of send + wait + receive
        total = sum(float(timings.get(k, 0)) for k in ("send", "wait", "receive"))
        if total > 0:
            return total
    # fallback: time field
    return float(entry.get("time", 0))


def _extract_body_keys(entry: dict[str, Any]) -> set[str]:
    """Extract top-level JSON keys from response body."""
    body = _response_body(entry)
    if not body or not body.strip().startswith("{"):
        return set()
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            return set(data.keys())
    except (json.JSONDecodeError, TypeError):
        pass
    return set()


def _extract_error_message(body: str, status: int) -> str:
    """Extract a human-readable error message from response body."""
    if not body:
        return ""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            # Common error response patterns
            for key in ("message", "error", "detail", "errorMessage", "msg", "description"):
                val = data.get(key)
                if isinstance(val, str) and len(val) > 2:
                    return val[:500]
            # Django REST framework style
            for val in data.values():
                if isinstance(val, (list, str)) and val:
                    return str(val)[:500]
    except (json.JSONDecodeError, TypeError):
        pass
    return body[:200]


# ── Main import functions ─────────────────────────────────────────────────

def import_har_endpoints(
    har_path: str | Path,
    *,
    min_count: int = 1,
    include_static: bool = False,
) -> list[dict[str, Any]]:
    """Import API endpoints from a HAR file.

    Args:
        har_path: Path to .har file
        min_count: Minimum call count for an endpoint to be included
        include_static: Whether to include static file paths (.js/.css/.png etc.)

    Returns:
        List of endpoint dicts with keys: path, method, capability_code,
        capability, actors, summary, source_refs
    """

    har_log = _parse_har_json(har_path)
    if har_log is None:
        return []

    entries = _extract_entries(har_log)
    if not entries:
        print(f"  [WARN] har_importer: no entries found in HAR", flush=True, file=sys.stderr)
        return []

    # ── Aggregate by normalized (path, method) ──
    aggregated: dict[tuple[str, str], HarEndpoint] = {}

    for entry in entries:
        method = _method_from_entry(entry)
        raw_path = _path_from_entry(entry)
        status = _status_from_entry(entry)

        # Skip static assets unless requested
        if not include_static:
            path_lower = raw_path.lower()
            if any(path_lower.endswith(ext) for ext in (
                ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map",
            )):
                continue

        norm_path = _normalize_path(raw_path)
        key = (norm_path, method.lower())

        if key not in aggregated:
            aggregated[key] = HarEndpoint(
                path=norm_path,
                method=method,
                summary=_path_entity_name(norm_path),
            )

        ep = aggregated[key]
        ep.count += 1
        ep.statuses[status] = ep.statuses.get(status, 0) + 1

        # Auth headers (deduped)
        for ah in _auth_headers(entry):
            ep.auth_headers.add(ah)

        # Content types
        ct = _content_type_from_entry(entry)
        if ct:
            ep.content_types.add(ct)

        # Body keys (for entity schema inference)
        ep.sample_body_keys.update(_extract_body_keys(entry))

        # Timing
        rt = _response_time(entry)
        if rt > 0:
            if ep.avg_response_time == 0:
                ep.avg_response_time = rt
            else:
                ep.avg_response_time = (ep.avg_response_time * (ep.count - 1) + rt) / ep.count
            ep.max_response_time = max(ep.max_response_time, rt)

        # Error responses
        if status >= 400:
            body = _response_body(entry)
            msg = _extract_error_message(body, status)
            if msg:
                ep.error_bodies.append(msg)

    # ── Convert to endpoint dicts ──
    endpoints: list[dict[str, Any]] = []
    har_file_name = str(Path(har_path).name) if isinstance(har_path, (str, Path)) else "har"

    for (norm_path, method), ep in sorted(aggregated.items()):
        if ep.count < min_count:
            continue

        method_upper = method.upper()
        capability = "read"
        if method_upper in ("POST",):
            capability = "create"
        elif method_upper in ("PUT", "PATCH"):
            capability = "update"
        elif method_upper in ("DELETE",):
            capability = "delete"

        actors: list[str] = []
        for ah in ep.auth_headers:
            if "admin" in ah.lower():
                actors.append("admin")
            elif "viewer" in ah.lower() or "anonymous" in ah.lower():
                actors.append("viewer")
            elif "operator" in ah.lower():
                actors.append("operator")
        if not actors:
            actors.append("anonymous")

        entity = _path_entity_name(norm_path)
        top_status = max(ep.statuses, key=ep.statuses.get) if ep.statuses else 200
        summary = f"[HAR] {method_upper} {entity} (n={ep.count}, top_status={top_status})"

        endpoints.append({
            "path": norm_path,
            "method": method_upper,
            "capability_code": capability,
            "capability": f"{entity}_{capability}",
            "actors": list(set(actors)),
            "summary": summary,
            "source_refs": [{
                # Canonical projection contract requires kind+locator; keep the
                # legacy fields alongside for older consumers.
                "kind": "har_traffic",
                "locator": f"{har_file_name}:{method_upper} {norm_path}",
                "source": har_file_name,
                "line": 0,
                "excerpt": f"HAR traffic: {ep.count} calls, {len(ep.statuses)} status codes",
                "confidence": 0.85,
            }],
        })

    print(f"  [OK] har_importer: extracted {len(endpoints)} endpoints from {len(entries)} HAR entries", flush=True)
    return endpoints


def extract_har_error_patterns(
    har_path: str | Path,
    *,
    min_occurrences: int = 1,
) -> list[HarErrorPattern]:
    """Extract error response patterns from HAR traffic.

    These patterns map directly to bug hypotheses: if the same endpoint
    returns 4xx/5xx in real traffic, a bug likely exists.
    """
    har_log = _parse_har_json(har_path)
    if har_log is None:
        return []

    entries = _extract_entries(har_log)
    aggregated: dict[tuple[str, str, int, str], HarErrorPattern] = {}

    for entry in entries:
        status = _status_from_entry(entry)
        if status < 400:
            continue

        method = _method_from_entry(entry)
        norm_path = _normalize_path(_path_from_entry(entry))
        body = _response_body(entry)
        error_msg = _extract_error_message(body, status)
        msg_key = error_msg[:80] if error_msg else "no_message"

        key = (norm_path, method, status, msg_key)
        if key not in aggregated:
            aggregated[key] = HarErrorPattern(
                endpoint=norm_path,
                method=method,
                status=status,
                error_message=error_msg,
                sample_request_body="",
                sample_response_body=body[:1000],
            )

        ep = aggregated[key]
        ep.count += 1
        if not ep.sample_request_body:
            # capture first request body
            req = entry.get("request", {})
            if isinstance(req, dict):
                post_data = req.get("postData", {})
                if isinstance(post_data, dict):
                    ep.sample_request_body = str(post_data.get("text", ""))[:1000]

    patterns = [p for p in aggregated.values() if p.count >= min_occurrences]
    patterns.sort(key=lambda p: (-p.count, p.status))

    if patterns:
        print(f"  [OK] har_importer: found {len(patterns)} error patterns "
              f"from {sum(p.count for p in patterns)} error responses", flush=True)
    return patterns


def extract_har_auth_patterns(
    har_path: str | Path,
) -> list[HarAuthPattern]:
    """Extract authentication patterns from HAR traffic.

    Identifies which tokens/roles access which API paths, revealing:
    - Anonymous access to protected endpoints
    - Missing auth on sensitive operations
    - Role mismatch patterns
    """
    har_log = _parse_har_json(har_path)
    if har_log is None:
        return []

    entries = _extract_entries(har_log)
    role_paths: dict[str, list[str]] = defaultdict(list)
    role_counts: dict[str, int] = defaultdict(int)

    for entry in entries:
        headers = _auth_headers(entry)
        norm_path = _normalize_path(_path_from_entry(entry))

        if not headers:
            role = "anonymous"
        elif any("admin" in h.lower() or "administrator" in h.lower() for h in headers):
            role = "admin"
        elif any("viewer" in h.lower() or "guest" in h.lower() for h in headers):
            role = "viewer"
        elif any("operator" in h.lower() for h in headers):
            role = "operator"
        else:
            role = "authenticated"

        role_paths[role].append(norm_path)
        role_counts[role] += 1

    patterns: list[HarAuthPattern] = []
    for role, paths in role_paths.items():
        # Deduplicate paths per role
        unique_paths = list(dict.fromkeys(paths))
        patterns.append(HarAuthPattern(
            role=role,
            paths_accessed=unique_paths[:50],  # limit to 50 paths
            avg_requests=role_counts[role],
        ))

    patterns.sort(key=lambda p: -p.avg_requests)
    return patterns


def har_to_candidates(
    har_path: str | Path,
    *,
    project_id: str = "",
) -> list[dict[str, Any]]:
    """Convert HAR error patterns to GroundedCandidate-like structures
    ready for the discovery pipeline.

    This is the main integration point: call this from the input compiler
    to inject HAR-derived candidates into the bug discovery flow.
    """
    error_patterns = extract_har_error_patterns(har_path, min_occurrences=1)
    har_name = str(Path(har_path).name) if isinstance(har_path, (str, Path)) else "har"
    source_ref = {"source": har_name, "type": "har_traffic"}

    candidates: list[dict[str, Any]] = []
    for i, ep in enumerate(error_patterns):
        severity = "P0" if ep.status >= 500 else "P1" if ep.status >= 400 else "P2"
        risk_type = (
            "server_error" if ep.status >= 500 else
            "client_error" if ep.status == 429 else
            "auth" if ep.status in (401, 403) else
            "not_found" if ep.status == 404 else
            "validation" if ep.status == 422 else
            "business_error"
        )

        candidates.append({
            "candidate_id": f"HAR_{project_id}_{i:04d}",
            "title": f"[HAR] {ep.method} {ep.endpoint} 返回 {ep.status} (n={ep.count})",
            "status": "open",
            "risk_type": risk_type,
            "severity": severity,
            "confidence": min(0.95, 0.6 + ep.count * 0.05),
            "endpoint": {"path": ep.endpoint, "method": ep.method},
            "affected_entities": [_path_entity_name(ep.endpoint)],
            "actors": ["anonymous"],
            "expected_behavior": f"应该返回 2xx 状态码",
            "suspected_failure_pattern": f"{ep.status} 错误 (n={ep.count}): {ep.error_message[:100]}",
            "probe_plan": {
                "method": ep.method,
                "path": ep.endpoint,
                "expected_status": 200,
                "replay_request": ep.sample_request_body,
            },
            "execution_policy": "safe_read_only",
            "required_evidence": ["response_status", "error_body"],
            "source_refs": [source_ref],
            "grounding_basis": {
                "source": "har_traffic",
                "error_status": ep.status,
                "error_count": ep.count,
                "error_message": ep.error_message,
            },
            "rationale": f"真实HTTP流量中发现 {ep.count} 次 {ep.status} 错误: {ep.error_message[:150]}",
        })

    return candidates


# ── Quick CLI test ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HAR file importer")
    parser.add_argument("har_file", help="Path to .har file")
    parser.add_argument("--min-count", type=int, default=1, help="Min calls per endpoint")
    parser.add_argument("--include-static", action="store_true", help="Include static assets")
    args = parser.parse_args()

    endpoints = import_har_endpoints(args.har_file, min_count=args.min_count,
                                     include_static=args.include_static)
    print(f"\n=== Endpoints ({len(endpoints)}) ===")
    for ep in endpoints:
        print(f"  {ep.method:6s} {ep.path:40s}  calls={ep.summary}")

    errors = extract_har_error_patterns(args.har_file)
    print(f"\n=== Error Patterns ({len(errors)}) ===")
    for err in errors[:20]:
        print(f"  {err.method} {err.endpoint} → {err.status} (x{err.count}) {err.error_message[:80]}")

    auth = extract_har_auth_patterns(args.har_file)
    print(f"\n=== Auth Patterns ({len(auth)}) ===")
    for a in auth:
        print(f"  {a.role}: {len(a.paths_accessed)} unique paths, ~{a.avg_requests} requests")
