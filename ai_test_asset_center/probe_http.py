"""HTTP transport and basic utilities for probe execution.
Extracted from grounded_probe_executor.py.
"""
from __future__ import annotations

import json, re, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────

UNRESOLVED_PLACEHOLDER_RE = re.compile(r"<\s*(?:FILL|TODO|REQUIRED|SANDBOX|REPLACE)[^>]*>", re.I)
SENSITIVE_FIELD_RE = re.compile(
    r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|session|id[_-]?card|身份证|phone|mobile|手机号|email|邮箱)",
    re.I,
)

# ── Basic utilities ────────────────────────────────────────────────────

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8") or "{}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_FIELD_RE.search(str(key)):
        return "<REDACTED>"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value[:25]]
    if isinstance(value, str):
        if len(value) > 700:
            return value[:700] + "\u2026"
        if SENSITIVE_FIELD_RE.search(value) and len(value) > 24:
            return value[:8] + "\u2026<REDACTED>"
        return value
    return value


def _has_unresolved_placeholder(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_has_unresolved_placeholder(k) or _has_unresolved_placeholder(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_unresolved_placeholder(v) for v in value)
    return bool(UNRESOLVED_PLACEHOLDER_RE.search(str(value)))


def _safe_payload_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {"type": "object", "keys": sorted(map(str, payload.keys()))[:30], "size": len(payload)}
    if isinstance(payload, list):
        return {"type": "array", "size": len(payload), "first": _safe_payload_summary(payload[0]) if payload else None}
    if isinstance(payload, str):
        return {"type": "string", "length": len(payload), "sample": payload[:200]}
    return {"type": type(payload).__name__, "value": payload if isinstance(payload, (int, float, bool)) else str(payload)[:200]}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _jsonish_body(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower() or text.strip().startswith(("{", "[")):
        try:
            return json.loads(text or "null")
        except Exception:
            return text[:2000]
    return text[:2000]


# ── HTTP transport ─────────────────────────────────────────────────────

def _join_url(base_url: str, path: str) -> str:
    def quote_url_path(value: str) -> str:
        parsed = urllib.parse.urlsplit(value)
        quoted_path = urllib.parse.quote(parsed.path, safe="/%")
        quoted_query = urllib.parse.quote(parsed.query, safe="=&%:/?,+")
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, quoted_path, quoted_query, parsed.fragment))
    base = str(base_url or "").rstrip("/")
    if not base:
        return quote_url_path(str(path))
    p = str(path or "")
    if re.match(r"^https?://", p, re.I):
        return quote_url_path(p)
    return base + "/" + quote_url_path(p.lstrip("/"))


def _url_host(base_url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(base_url)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def _render_path(path: str, path_params: dict[str, Any]) -> tuple[str, list[str]]:
    missing: list[str] = []
    rendered = str(path or "")
    for name in re.findall(r"\{([^{}]+)\}", rendered):
        if name not in path_params:
            missing.append(name)
            continue
        rendered = rendered.replace("{" + name + "}", urllib.parse.quote(str(path_params[name]), safe=""))
    return rendered, missing


def _render_query(query: Any, path_params: dict[str, Any]) -> str:
    if not isinstance(query, dict) or not query:
        return ""
    rendered: dict[str, str] = {}
    for k, v in query.items():
        key = str(k)
        value = str(v)
        for name, replacement in path_params.items():
            value = value.replace("{" + str(name) + "}", str(replacement))
        if value and not _has_unresolved_placeholder(value):
            rendered[key] = value
    return urllib.parse.urlencode(rendered, doseq=True)


def _append_query(path: str, query_string: str) -> str:
    if not query_string:
        return path
    sep = "&" if "?" in str(path) else "?"
    return f"{path}{sep}{query_string}"


def _http_request(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
    data: bytes | None = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url=url, method=method.upper(), headers=req_headers, data=data)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(256_000)
            payload = _jsonish_body(raw, resp.headers.get("Content-Type", ""))
            return {"ok": True, "status_code": int(resp.status), "headers": dict(resp.headers.items()), "payload": payload, "duration_ms": int((time.time() - started) * 1000)}
    except urllib.error.HTTPError as exc:
        raw = exc.read(256_000)
        payload = _jsonish_body(raw, exc.headers.get("Content-Type", "")) if raw else None
        return {"ok": False, "status_code": int(exc.code), "headers": dict(exc.headers.items()), "payload": payload, "duration_ms": int((time.time() - started) * 1000)}
    except Exception as exc:
        return {"ok": False, "status_code": None, "error": f"{type(exc).__name__}: {exc}", "payload": None, "duration_ms": int((time.time() - started) * 1000)}


def _json_path_get(payload: Any, dotted_path: str) -> Any:
    cur = payload
    for raw_part in [p for p in str(dotted_path or "").split(".") if p]:
        part = raw_part
        while part:
            m = re.match(r"^([^\[]+)(?:\[(\d+)\])?(.*)$", part)
            if not m:
                return None
            key, idx, rest = m.group(1), m.group(2), m.group(3)
            if key:
                if not isinstance(cur, dict) or key not in cur:
                    return None
                cur = cur[key]
            if idx is not None:
                if not isinstance(cur, list) or int(idx) >= len(cur):
                    return None
                cur = cur[int(idx)]
            part = rest
    return cur


def _cookie_header_from_response(resp: dict[str, Any]) -> str:
    headers = resp.get("headers") if isinstance(resp.get("headers"), dict) else {}
    set_cookie = str(headers.get("Set-Cookie") or headers.get("set-cookie") or "")
    if set_cookie:
        parts = set_cookie.split(";")[0].strip()
        return f"Cookie={parts.split('=', 1)[1]}" if "=" in parts else set_cookie
    return ""
