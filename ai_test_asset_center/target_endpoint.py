from __future__ import annotations

"""Fail-closed target endpoint resolution for discovery execution.

QualiBug's own backend address is not a discovery target.  Callers must pass a
target explicitly or declare one through the target-specific environment
variables; otherwise execution is blocked before any request can be sent.
"""

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse


class TargetEndpointError(ValueError):
    """Raised when a discovery target is missing or invalid."""


def resolve_target_base_url(
    explicit: Any,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    raw = str(
        explicit
        or env.get("QUALIBUG_TARGET_BASE_URL")
        or env.get("QUALIBUG_DEFAULT_BASE_URL")
        or ""
    ).strip()
    if not raw:
        raise TargetEndpointError("target_base_url_required")

    normalized = raw.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TargetEndpointError("target_base_url_invalid")
    if parsed.username is not None or parsed.password is not None:
        raise TargetEndpointError("target_base_url_credentials_forbidden")
    return normalized


__all__ = ["TargetEndpointError", "resolve_target_base_url"]
