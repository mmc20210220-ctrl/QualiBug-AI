"""Single request-size authority for the private-pilot HTTP surface."""
from __future__ import annotations

import os
from typing import Any


def _positive_limit(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


MAX_JSON_BODY_BYTES = _positive_limit(
    "QUALIBUG_MAX_JSON_BODY_BYTES",
    2 * 1024 * 1024,
)
MAX_KNOWLEDGE_UPLOAD_BYTES = _positive_limit(
    "QUALIBUG_MAX_KNOWLEDGE_UPLOAD_BYTES",
    100 * 1024 * 1024,
)
MAX_REQUEST_BODY_BYTES = max(MAX_JSON_BODY_BYTES, MAX_KNOWLEDGE_UPLOAD_BYTES)


def content_length(headers: Any) -> int:
    raw = headers.get("Content-Length")
    if raw is None or str(raw).strip() == "":
        return 0
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid Content-Length header.") from exc
    if value < 0:
        raise ValueError("Invalid Content-Length header.")
    return value


__all__ = [
    "MAX_JSON_BODY_BYTES",
    "MAX_KNOWLEDGE_UPLOAD_BYTES",
    "MAX_REQUEST_BODY_BYTES",
    "content_length",
]
