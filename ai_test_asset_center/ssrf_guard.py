"""SSRF guard for server-initiated HTTP requests.

Arbitrary URLs are restricted to public HTTP(S) targets. A caller may grant one
explicitly approved private target host, but that grant is host-bound and is
revalidated on every redirect; it is never a process-wide allow-internal
escape hatch.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import urllib.request
from typing import Any
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOW_INTERNAL_ENV = "QUALIBUG_SSRF_ALLOW_INTERNAL"


class SsrfBlockedError(ValueError):
    """Raised when a URL targets an unauthorized address."""


def _is_internal_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_host(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
        return list({info[4][0] for info in infos})
    except Exception:
        return []


def _check_host(host: str, *, allow_internal: bool) -> None:
    if allow_internal:
        return
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_internal_ip(literal):
            raise SsrfBlockedError(f"URL host '{host}' is an internal/loopback address")
        return
    addresses = _resolve_host(host)
    if not addresses:
        raise SsrfBlockedError(f"URL host '{host}' could not be resolved")
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError:
            raise SsrfBlockedError(f"URL host '{host}' resolved to an invalid address")
        if _is_internal_ip(resolved):
            raise SsrfBlockedError(
                f"URL host '{host}' resolves to internal address {address}"
            )


def _allow_internal_default() -> bool:
    return os.environ.get(_ALLOW_INTERNAL_ENV, "0") == "1"


def _normalized_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def validate_url(
    url: str,
    *,
    allow_internal: bool | None = None,
    approved_host: str = "",
) -> str:
    """Validate an HTTP(S) URL against the arbitrary or approved-target policy."""

    if allow_internal is None:
        allow_internal = _allow_internal_default()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SsrfBlockedError(
            f"URL scheme '{parsed.scheme}' is not allowed (http/https only)"
        )
    if parsed.username or parsed.password:
        raise SsrfBlockedError("URL userinfo is not allowed")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SsrfBlockedError("URL has no hostname")
    grant_host = str(approved_host or "").strip().lower()
    host_is_explicitly_approved = bool(grant_host and host == grant_host)
    if grant_host and not host_is_explicitly_approved:
        raise SsrfBlockedError(
            f"URL host '{host}' is outside approved target host '{grant_host}'"
        )
    _check_host(
        host,
        allow_internal=bool(allow_internal or host_is_explicitly_approved),
    )
    return url


class _SsrfSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, allow_internal: bool, approved_host: str = "") -> None:
        super().__init__()
        self._allow_internal = bool(allow_internal)
        self._approved_host = str(approved_host or "").strip().lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        validate_url(
            newurl,
            allow_internal=self._allow_internal,
            approved_host=self._approved_host,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(
    url: str | urllib.request.Request,
    *,
    timeout: float = 10,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    allow_internal: bool | None = None,
    approved_host: str = "",
) -> Any:
    """Open one validated URL and revalidate every redirect hop.

    ``approved_host`` is the only supported way for product runtime code to
    access an approved private/on-premise target. Global ``allow_internal`` is
    retained solely for explicit local tooling compatibility.
    """

    if allow_internal is None:
        allow_internal = _allow_internal_default()
    grant_host = str(approved_host or "").strip().lower()
    if isinstance(url, urllib.request.Request):
        req = url
        validate_url(
            req.full_url,
            allow_internal=allow_internal,
            approved_host=grant_host,
        )
    else:
        validate_url(
            url,
            allow_internal=allow_internal,
            approved_host=grant_host,
        )
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers or {},
            method=method,
        )
    opener = urllib.request.build_opener(
        _SsrfSafeRedirectHandler(
            allow_internal=bool(allow_internal),
            approved_host=grant_host,
        )
    )
    return opener.open(req, timeout=timeout)


__all__ = [
    "SsrfBlockedError",
    "safe_urlopen",
    "validate_url",
]
