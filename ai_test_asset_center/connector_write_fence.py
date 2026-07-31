"""Thread-scoped fencing for every file-system mutation made by a connector sync.

Managed connector synchronization enters one context carrying a monotonically increasing
registry token. A process-wide Python audit hook validates that token before the owning thread
creates, replaces, appends, renames, or removes any file. Threads without a connector context
are unaffected, so ordinary uploads and the rest of the product keep their existing behavior.
"""
from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .connector_sync_authority import ConnectorSyncError


class ConnectorWriteFenceRevoked(ConnectorSyncError):
    """The synchronization no longer owns the right to mutate durable state."""


Validator = Callable[[str, str, int], None]
_LOCAL = threading.local()
_INSTALL_LOCK = threading.Lock()
_INSTALLED = False
_WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_RDWR
    | os.O_CREAT
    | os.O_TRUNC
    | os.O_APPEND
    | getattr(os, "O_TMPFILE", 0)
)
_MUTATING_EVENTS = {
    "os.remove",
    "os.rename",
    "os.rmdir",
    "os.mkdir",
    "os.link",
    "os.symlink",
    "os.truncate",
    "os.chmod",
    "os.chown",
    "os.utime",
}


def _stack() -> list[dict[str, Any]]:
    value = getattr(_LOCAL, "connector_write_fences", None)
    if not isinstance(value, list):
        value = []
        _LOCAL.connector_write_fences = value
    return value


def current_connector_write_fence() -> dict[str, Any]:
    stack = _stack()
    return dict(stack[-1]) if stack else {}


def assert_connector_write_fence() -> None:
    context = current_connector_write_fence()
    if not context:
        return
    validator = context.get("validator")
    if not callable(validator):
        raise ConnectorWriteFenceRevoked("connector_sync_fence_validator_missing")
    try:
        validator(
            str(context.get("project_id") or ""),
            str(context.get("connector_instance_id") or ""),
            int(context.get("fencing_token") or 0),
        )
    except ConnectorWriteFenceRevoked:
        raise
    except Exception as exc:
        raise ConnectorWriteFenceRevoked(
            f"connector_sync_fence_revoked:{type(exc).__name__}:{exc}"
        ) from exc


def _open_is_mutating(args: tuple[Any, ...]) -> bool:
    mode = str(args[1] or "") if len(args) > 1 else ""
    try:
        flags = int(args[2] or 0) if len(args) > 2 else 0
    except (TypeError, ValueError):
        flags = 0
    return any(marker in mode for marker in ("w", "a", "x", "+")) or bool(
        flags & _WRITE_FLAGS
    )


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    if not current_connector_write_fence():
        return
    if event == "open":
        if _open_is_mutating(args):
            assert_connector_write_fence()
        return
    if event in _MUTATING_EVENTS:
        assert_connector_write_fence()


def ensure_connector_write_fence_hook() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        sys.addaudithook(_audit_hook)
        _INSTALLED = True


@contextmanager
def connector_write_fence(
    project_id: str,
    connector_instance_id: str,
    fencing_token: int,
    *,
    validator: Validator,
) -> Iterator[dict[str, Any]]:
    token = int(fencing_token)
    if token <= 0:
        raise ConnectorWriteFenceRevoked("connector_sync_fencing_token_invalid")
    context = {
        "project_id": str(project_id or ""),
        "connector_instance_id": str(connector_instance_id or ""),
        "fencing_token": token,
        "validator": validator,
    }
    ensure_connector_write_fence_hook()
    stack = _stack()
    stack.append(context)
    try:
        assert_connector_write_fence()
        yield dict(context)
    finally:
        if stack and stack[-1] is context:
            stack.pop()
        else:
            try:
                stack.remove(context)
            except ValueError:
                pass


ensure_connector_write_fence_hook()


__all__ = [
    "ConnectorWriteFenceRevoked",
    "assert_connector_write_fence",
    "connector_write_fence",
    "current_connector_write_fence",
    "ensure_connector_write_fence_hook",
]
