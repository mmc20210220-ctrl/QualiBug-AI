"""Failure-safe operational output for long-running QualiBug workers.

Console output is never allowed to terminate Reader/Reasoner/Loop work.  This
matters on Windows Task Scheduler and detached workers where stdout/stderr may
be closed or invalid even though the worker itself is healthy.
"""
from __future__ import annotations

import builtins
import os
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

_OUTPUT_ERRORS = (UnicodeEncodeError, OSError, ValueError, BrokenPipeError)
_FALLBACK_LOCK = threading.Lock()


def _render(values: tuple[Any, ...], sep: str, end: str) -> str:
    """Render a line for fallback logging without reusing the failed stream."""
    rendered: list[str] = []
    for value in values:
        try:
            rendered.append(str(value))
        except Exception:
            # Logging must not recursively fail because a diagnostic object's
            # __str__ implementation is broken.  Preserve a useful marker.
            rendered.append(f"<unprintable {type(value).__name__}>")
    return sep.join(rendered) + end


def _fallback_log_path() -> Path:
    configured = os.environ.get("QUALIBUG_CONSOLE_FALLBACK_LOG", "").strip()
    if configured:
        return Path(configured)
    return Path("platform_outputs") / "runtime_console_fallback.log"


def _append_fallback(text: str, error: BaseException) -> None:
    """Append directly to a UTF-8 file.  Never call print from this path."""
    try:
        path = _fallback_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = f"[console-fallback {type(error).__name__}: {str(error)[:160]}] "
        with _FALLBACK_LOCK:
            with path.open("a", encoding="utf-8", errors="backslashreplace") as handle:
                handle.write(marker + text)
                handle.flush()
    except Exception:
        # This is intentionally the final containment boundary.  The caller's
        # business operation must continue even if filesystem logging is also
        # unavailable (for example a read-only emergency filesystem).
        return


def safe_print(
    *values: Any,
    sep: str = " ",
    end: str = "\n",
    file: TextIO | None = None,
    flush: bool = False,
) -> None:
    """Best-effort ``print`` that cannot abort a worker due to output handles.

    Only output-path failures are contained.  It does not catch exceptions from
    surrounding business logic, network work, or verification code.
    """
    stream = file if file is not None else getattr(sys, "stdout", None)
    if stream is None:
        _append_fallback(_render(values, sep, end), OSError("stdout unavailable"))
        return
    try:
        builtins.print(*values, sep=sep, end=end, file=stream, flush=flush)
        return
    except UnicodeEncodeError as exc:
        # A live Windows/GBK console can reject emoji or other Unicode even
        # though the handle itself is valid.  Preserve readable ASCII/CJK text
        # on that stream before falling back to a file.
        try:
            encoding = getattr(stream, "encoding", None) or "utf-8"
            safe_text = _render(values, sep, end).encode(encoding, errors="replace").decode(encoding)
            stream.write(safe_text)
            if flush and hasattr(stream, "flush"):
                stream.flush()
            _append_fallback(_render(values, sep, end), exc)
            return
        except _OUTPUT_ERRORS:
            _append_fallback(_render(values, sep, end), exc)
            return
    except (OSError, ValueError, BrokenPipeError) as exc:
        _append_fallback(_render(values, sep, end), exc)
        return
