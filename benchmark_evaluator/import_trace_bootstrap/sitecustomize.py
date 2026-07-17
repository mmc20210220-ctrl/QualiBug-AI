"""Process-local observer injected only by the evaluator import-trace collector."""
from __future__ import annotations

import atexit
import json
import os
import sys
import tempfile
from pathlib import Path


_OUTPUT_DIRECTORY = os.environ.get("QUALIBUG_IMPORT_TRACE_PROCESS_DIR", "").strip()
_SESSION_NONCE = os.environ.get("QUALIBUG_IMPORT_TRACE_SESSION_NONCE", "").strip()
_TARGET_MODULE = os.environ.get("QUALIBUG_IMPORT_TRACE_TARGET_MODULE", "").strip()
_TARGET_CALLABLE = os.environ.get("QUALIBUG_IMPORT_TRACE_TARGET_CALLABLE", "").strip()
_TARGET_CALLABLE_OBSERVED = False
_PREVIOUS_PROFILE = sys.getprofile()


def _profile(frame: object, event: str, arg: object) -> None:
    global _TARGET_CALLABLE_OBSERVED
    if event == "call":
        globals_value = getattr(frame, "f_globals", {})
        code = getattr(frame, "f_code", None)
        module = str(globals_value.get("__name__") or "")
        qualified = str(getattr(code, "co_qualname", "") or "")
        if (
            module == _TARGET_MODULE
            and _TARGET_CALLABLE
            and (
                qualified == _TARGET_CALLABLE
                or qualified.endswith("." + _TARGET_CALLABLE)
            )
        ):
            _TARGET_CALLABLE_OBSERVED = True
    if _PREVIOUS_PROFILE is not None:
        _PREVIOUS_PROFILE(frame, event, arg)


def _persist_observation() -> None:
    modules = sorted(
        name
        for name in sys.modules
        if isinstance(name, str)
        and name
        and all(part.isidentifier() for part in name.split("."))
    )
    payload = {
        "schema_version": "qualibug.python-import-trace-process.v1",
        "session_nonce": _SESSION_NONCE,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "modules": modules,
        "target_module_observed": _TARGET_MODULE in modules,
        "target_callable_observed": (
            _TARGET_CALLABLE_OBSERVED if _TARGET_CALLABLE else True
        ),
    }
    directory = Path(_OUTPUT_DIRECTORY)
    directory.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{os.getpid()}.",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / f"process-{os.getpid()}.json")
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


if _OUTPUT_DIRECTORY and _SESSION_NONCE and _TARGET_MODULE:
    if _TARGET_CALLABLE:
        sys.setprofile(_profile)
    atexit.register(_persist_observation)
