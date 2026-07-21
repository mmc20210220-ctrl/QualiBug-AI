"""Product-wide structured logging infrastructure.

Call ``setup_product_logging(root)`` once at process start (entrypoint).
All modules obtain a logger via ``get_logger(__name__)`` which returns a
standard ``logging.Logger`` already wired to the product file handlers.

Design constraints:
- RotatingFileHandler: 10 MB per file, 5 backups → max 50 MB per log stream.
- Three log streams: full (INFO+), error-only (ERROR+), audit (key ops).
- Structured JSON lines for machine-parseable remote diagnostics.
- Automatic redaction of secrets/tokens/passwords in log output.
- Global exception hooks capture unhandled exceptions with full context.
- Zero configuration required from the customer.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOG_DIR_RELATIVE = Path("platform_outputs") / "logs"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5

_FULL_LOG_FILE = "qualibug.log"
_ERROR_LOG_FILE = "qualibug_error.log"
_AUDIT_LOG_FILE = "qualibug_audit.log"

# Fields whose values must never appear in logs
_REDACT_KEYS = frozenset({
    "token", "tokens", "access_token", "refresh_token", "bearer",
    "password", "passwd", "secret", "api_key", "apikey", "api_secret",
    "authorization", "auth", "credential", "credentials",
    "credential_encryption_key", "signing_key", "receipt_signing_key",
    "jwt_secret", "private_key", "client_secret",
})

_REDACT_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*",
)

_REDACTED = "***REDACTED***"

_SETUP_DONE = False
_SETUP_LOCK = threading.Lock()
_LOG_ROOT: Path | None = None

# ---------------------------------------------------------------------------
# Structured JSON Formatter
# ---------------------------------------------------------------------------


class StructuredJsonFormatter(logging.Formatter):
    """Emit one JSON object per log record for machine parsing."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Attach error code if present
        code = getattr(record, "error_code", None)
        if code:
            payload["code"] = code
        # Attach structured context
        context = getattr(record, "context", None)
        if context and isinstance(context, dict):
            payload["context"] = _redact_dict(context)
        # Exception info
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        # Source location
        payload["src"] = f"{record.filename}:{record.lineno}"
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            # Fallback: never crash the logger
            return json.dumps({"ts": payload["ts"], "level": "ERROR", "msg": "log serialization failed"})


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------


def _redact_value(key: str, value: Any) -> Any:
    """Redact a single value if its key is sensitive."""
    if key.lower() in _REDACT_KEYS:
        return _REDACTED
    if isinstance(value, str) and len(value) > 20:
        value = _REDACT_VALUE_RE.sub(r"\1" + _REDACTED, value)
    return value


def _redact_dict(data: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
    """Recursively redact sensitive fields from a dict."""
    if _depth > 6:
        return {"_truncated": True}
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in _REDACT_KEYS:
            result[key] = _REDACTED
        elif isinstance(value, dict):
            result[key] = _redact_dict(value, _depth + 1)
        elif isinstance(value, list):
            result[key] = [
                _redact_dict(item, _depth + 1) if isinstance(item, dict) else _redact_value(key, item)
                for item in value[:50]  # cap list length
            ]
        elif isinstance(value, str):
            result[key] = _redact_value(key, value)
        else:
            result[key] = value
    return result


def redact_text(text: str) -> str:
    """Redact bearer tokens and obvious secrets from free text."""
    return _REDACT_VALUE_RE.sub(r"\1" + _REDACTED, text)


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """Return a logger wired to the product handlers (if initialized)."""
    return logging.getLogger(name)


def audit_log(event: str, *, context: dict[str, Any] | None = None, code: str = "") -> None:
    """Write to the audit log stream for key operational events."""
    logger = logging.getLogger("qualibug.audit")
    extra: dict[str, Any] = {}
    if context:
        extra["context"] = context
    if code:
        extra["error_code"] = code
    logger.info(event, extra=extra)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_product_logging(root: str | Path | None = None) -> Path:
    """Initialize product-wide logging. Idempotent; safe to call multiple times.

    Returns the log directory path.
    """
    global _SETUP_DONE, _LOG_ROOT

    with _SETUP_LOCK:
        if _SETUP_DONE and _LOG_ROOT is not None:
            return _LOG_ROOT

        resolved_root = Path(root).resolve() if root else Path.cwd().resolve()
        log_dir = resolved_root / _LOG_DIR_RELATIVE
        log_dir.mkdir(parents=True, exist_ok=True)
        _LOG_ROOT = log_dir

        formatter = StructuredJsonFormatter()

        # Root logger for the product namespace
        root_logger = logging.getLogger("ai_test_asset_center")
        root_logger.setLevel(logging.DEBUG)
        # Remove any pre-existing handlers to avoid duplicates on re-init
        root_logger.handlers.clear()

        # Also capture qualibug.audit namespace
        audit_logger = logging.getLogger("qualibug")
        audit_logger.setLevel(logging.DEBUG)
        audit_logger.handlers.clear()

        # --- Full log (INFO+) ---
        full_handler = RotatingFileHandler(
            log_dir / _FULL_LOG_FILE,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        full_handler.setLevel(logging.INFO)
        full_handler.setFormatter(formatter)
        root_logger.addHandler(full_handler)
        audit_logger.addHandler(full_handler)

        # --- Error log (ERROR+) ---
        error_handler = RotatingFileHandler(
            log_dir / _ERROR_LOG_FILE,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        root_logger.addHandler(error_handler)
        audit_logger.addHandler(error_handler)

        # --- Audit log (all levels, separate stream) ---
        audit_handler = RotatingFileHandler(
            log_dir / _AUDIT_LOG_FILE,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        audit_handler.setLevel(logging.DEBUG)
        audit_handler.setFormatter(formatter)
        # Audit handler only attached to qualibug.audit logger
        logging.getLogger("qualibug.audit").addHandler(audit_handler)

        # --- Console handler (WARNING+ to avoid flooding stdout) ---
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # Prevent propagation to root Python logger (avoid double output)
        root_logger.propagate = False
        audit_logger.propagate = False

        # --- Global exception hooks ---
        _install_exception_hooks()

        _SETUP_DONE = True

        # Log startup marker
        startup_logger = logging.getLogger("qualibug.startup")
        startup_logger.info(
            "Product logging initialized",
            extra={"context": {
                "log_dir": str(log_dir),
                "python": sys.version.split()[0],
                "platform": platform.system(),
                "pid": os.getpid(),
            }},
        )
        return log_dir


# ---------------------------------------------------------------------------
# Global exception hooks
# ---------------------------------------------------------------------------

_original_excepthook = sys.excepthook


def _global_excepthook(exc_type: type, exc_value: BaseException, exc_tb: Any) -> None:
    """Capture unhandled exceptions into the product error log."""
    logger = logging.getLogger("qualibug.unhandled")
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.error(
        f"Unhandled exception: {exc_type.__name__}: {exc_value}",
        extra={
            "error_code": "QB-S999",
            "context": {"exception_type": exc_type.__name__, "traceback": redact_text(tb_text[:5000])},
        },
    )
    # Still call original hook for stderr output
    _original_excepthook(exc_type, exc_value, exc_tb)


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    """Capture unhandled thread exceptions."""
    logger = logging.getLogger("qualibug.unhandled")
    tb_text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    logger.error(
        f"Unhandled thread exception in {args.thread.name if args.thread else '?'}: {args.exc_type.__name__}: {args.exc_value}",
        extra={
            "error_code": "QB-S998",
            "context": {
                "thread": args.thread.name if args.thread else "unknown",
                "exception_type": args.exc_type.__name__,
                "traceback": redact_text(tb_text[:5000]),
            },
        },
    )


def _install_exception_hooks() -> None:
    sys.excepthook = _global_excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_excepthook


# ---------------------------------------------------------------------------
# Utility: get log directory (for doctor/bundle export)
# ---------------------------------------------------------------------------


def get_log_dir(root: str | Path | None = None) -> Path:
    """Return the log directory path without initializing logging."""
    if _LOG_ROOT is not None:
        return _LOG_ROOT
    resolved = Path(root).resolve() if root else Path.cwd().resolve()
    return resolved / _LOG_DIR_RELATIVE
