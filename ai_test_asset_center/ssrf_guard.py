"""SSRF guard — block outbound requests to internal/loopback addresses.

All server-initiated HTTP requests that involve user-controlled URLs should go
through ``safe_urlopen`` (or call ``validate_url`` first) instead of
``urllib.request.urlopen`` so that an attacker cannot make the server fetch
internal services or cloud metadata endpoints (e.g. 169.254.169.254).

The guard:
  * Restricts schemes to http/https.
  * Rejects literal IP addresses that are private, loopback, link-local,
    reserved, multicast or unspecified.
  * Resolves hostnames and rejects any that resolve to an internal address
    (mitigates DNS-rebinding to 169.254.x).
  * Re-validates every redirect hop via a custom redirect handler.

Set ``QUALIBUG_SSRF_ALLOW_INTERNAL=1`` to allow internal targets — this is
intended **only** for local development where the target service runs on
127.0.0.1. It must never be set in production deployments.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOW_INTERNAL_ENV = "QUALIBUG_SSRF_ALLOW_INTERNAL"


class SsrfBlockedError(ValueError):
    """Raised when a URL targets a blocked (internal/loopback) address."""


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
    """Return all resolved IP addresses for *host*."""
    try:
        infos = socket.getaddrinfo(host, None)
        return list({info[4][0] for info in infos})
    except Exception:
        return []


def _check_host(host: str, *, allow_internal: bool) -> None:
    if allow_internal:
        return
    # Try to parse the host as a literal IP first.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_internal_ip(ip):
            raise SsrfBlockedError(
                f"URL host '{host}' is an internal/loopback address"
            )
        return
    # Hostname — resolve and check every address to block DNS rebinding
    # towards 169.254.x or other internal ranges.
    for addr in _resolve_host(host):
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_internal_ip(ip):
            raise SsrfBlockedError(
                f"URL host '{host}' resolves to internal address {addr}"
            )


def _allow_internal_default() -> bool:
    return os.environ.get(_ALLOW_INTERNAL_ENV, "0") == "1"


def validate_url(url: str, *, allow_internal: bool | None = None) -> str:
    """Validate that *url* is safe to fetch.

    Returns the URL unchanged on success, or raises :class:`SsrfBlockedError`.
    """
    if allow_internal is None:
        allow_internal = _allow_internal_default()
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SsrfBlockedError(
            f"URL scheme '{parsed.scheme}' is not allowed (http/https only)"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise SsrfBlockedError("URL has no hostname")
    _check_host(host, allow_internal=allow_internal)
    return url


class _SsrfSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that re-validates every hop against SSRF rules."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        validate_url(newurl, allow_internal=_allow_internal_default())
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(
    url: str | urllib.request.Request,
    *,
    timeout: float = 10,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    allow_internal: bool | None = None,
) -> Any:
    """Drop-in replacement for ``urllib.request.urlopen`` with SSRF protection.

    Builds a :class:`~urllib.request.Request` (if a plain URL is given), uses an
    opener whose redirect handler re-validates every hop, and returns the
    response object just like ``urlopen``.
    """
    if allow_internal is None:
        allow_internal = _allow_internal_default()
    if isinstance(url, urllib.request.Request):
        req = url
        validate_url(req.full_url, allow_internal=allow_internal)
    else:
        validate_url(url, allow_internal=allow_internal)
        req = urllib.request.Request(
            url, data=data, headers=headers or {}, method=method,
        )
    opener = urllib.request.build_opener(_SsrfSafeRedirectHandler)
    return opener.open(req, timeout=timeout)
