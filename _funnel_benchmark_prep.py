"""Funnel-benchmark preflight: reset target DB + refresh auth tokens.

All modes (baseline/optimized/llm/...) exercise governed write probes against
the non-production benchmark target. Without a fresh DB and live tokens,
permission/isolation probes degrade and cross-run state pollutes recall.
"""
from __future__ import annotations

import os
import locale
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

RunFn = Callable[..., subprocess.CompletedProcess]


def _decode_process_output(value: object) -> str:
    """Decode subprocess output without letting Windows codepages kill prep logs."""
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, (bytes, bytearray)):
        return str(value)
    data = bytes(value)
    encodings = [
        "utf-8",
        locale.getpreferredencoding(False),
        "gbk",
        "cp936",
    ]
    seen: set[str] = set()
    for encoding in encodings:
        name = str(encoding or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            return data.decode(name)
        except UnicodeDecodeError:
            continue
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace")


def _combined_output(completed: subprocess.CompletedProcess) -> str:
    return (_decode_process_output(completed.stdout) + _decode_process_output(completed.stderr)).strip()


def resolve_benchmark_target_root(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    raw = str(env.get("QUALIBUG_BENCHMARK_TARGET_ROOT") or "").strip()
    if not raw:
        raise RuntimeError(
            "QUALIBUG_BENCHMARK_TARGET_ROOT is required; target assets must come "
            "from an explicit evaluator-local profile path"
        )
    return Path(raw)


def resolve_benchmark_runtime_input_root(target_root: Path | str) -> Path:
    """Expose only the target's documented runtime materials to discovery."""
    root = Path(target_root).resolve()
    docs = (root / "docs").resolve()
    hidden = (root / "hidden_ground_truth").resolve()
    if not docs.is_dir():
        raise FileNotFoundError(f"benchmark visible docs directory missing: {docs}")
    if docs == hidden or hidden in docs.parents:
        raise RuntimeError("benchmark runtime input must exclude hidden_ground_truth")
    return docs


def load_benchmark_runtime_config(
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Load evaluator-local target configuration without product defaults."""
    env = os.environ if env is None else env
    target_root = resolve_benchmark_target_root(env)
    required = {
        "project": "QUALIBUG_BENCHMARK_PROJECT",
        "base_url": "QUALIBUG_TARGET_BASE_URL",
        "db_dsn": "QUALIBUG_DB_DSN",
        "jwt_secret": "QUALIBUG_JWT_SECRET",
    }
    values: dict[str, str] = {}
    for field, name in required.items():
        value = str(env.get(name) or "").strip()
        if not value:
            raise RuntimeError(f"{name} is required for benchmark execution")
        values[field] = value
    parsed = urlsplit(values["base_url"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("QUALIBUG_TARGET_BASE_URL must be an absolute HTTP(S) URL")
    if not values["db_dsn"].lower().startswith(("postgres://", "postgresql://")):
        raise RuntimeError("QUALIBUG_DB_DSN must be a PostgreSQL DSN")
    return {"target_root": target_root, **values}


def should_skip_target_db_reset(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("QUALIBUG_SKIP_TARGET_DB_RESET") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def reset_benchmark_target_db(
    *,
    target_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    runner: RunFn = subprocess.run,
) -> dict:
    """Re-import schema+seed so write-probe residue cannot poison the next mode.

    Uses the Windows-native benchmark ``scripts/init_db_windows.ps1`` (same as
    ``02_init_database.bat``). Override root with ``QUALIBUG_BENCHMARK_TARGET_ROOT``.
    Explicit opt-out only via ``QUALIBUG_SKIP_TARGET_DB_RESET=1`` (loud, recorded).
    """
    env = os.environ if env is None else env
    if should_skip_target_db_reset(env):
        note = (
            "QUALIBUG_SKIP_TARGET_DB_RESET=1: target DB was NOT reset; "
            "baseline/optimized recall comparison may be polluted by prior write probes"
        )
        print(f"WARN: {note}")
        return {"status": "skipped", "reason": "QUALIBUG_SKIP_TARGET_DB_RESET", "operator_note": note}

    root = target_root or resolve_benchmark_target_root(env)
    script = root / "scripts" / "init_db_windows.ps1"
    if not script.is_file():
        raise FileNotFoundError(
            f"benchmark target DB reset script missing: {script}. "
            "Set QUALIBUG_BENCHMARK_TARGET_ROOT to the Windows-native benchmark root, "
            "or set QUALIBUG_SKIP_TARGET_DB_RESET=1 only if you accept polluted recall."
        )

    print(f"RESET_TARGET_DB: {script}")
    completed = runner(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        check=False,
        capture_output=True,
        text=False,
    )
    if completed.returncode != 0:
        stderr = _combined_output(completed)[:800]
        raise RuntimeError(
            f"benchmark target DB reset failed (exit={completed.returncode}): {stderr}"
        )
    print("RESET_TARGET_DB: ok")
    return {"status": "ok", "script": str(script), "exit_code": 0}


def refresh_test_account_tokens(
    *,
    root: Path,
    runner: RunFn = subprocess.run,
    retries: int = 5,
    retry_delay_seconds: float = 3.0,
) -> dict:
    """Refresh tokens for every funnel mode — expired JWT silently kills auth probes.

    After ``init_db_windows.ps1`` the gateway can briefly refuse connections
    (HTTP 0 / timeout) while services reattach to the reseeded DB. Retry a few
    times before failing hard so prep does not flake on a healthy target.
    """
    script = root / "_refresh_tokens.py"
    if not script.is_file():
        raise FileNotFoundError(f"token refresh script missing: {script}")

    print(f"REFRESH_TOKENS: {script}")
    attempts = max(1, int(retries or 1))
    delay = max(0.0, float(retry_delay_seconds or 0.0))
    last_out = ""
    last_code = 1
    for attempt in range(1, attempts + 1):
        completed = runner(
            [sys.executable, str(script)],
            check=False,
            capture_output=True,
            text=False,
        )
        out = _combined_output(completed)
        last_out = out
        last_code = int(completed.returncode or 0)
        if out:
            print(out)
        if last_code == 0:
            print("REFRESH_TOKENS: ok")
            return {
                "status": "ok",
                "script": str(script),
                "exit_code": 0,
                "attempts": attempt,
            }
        # Transient post-reset unavailability — retry before failing the prep.
        transient = (
            "login rejected HTTP 0" in out
            or "timed out" in out.lower()
            or "connection refused" in out.lower()
        )
        if attempt < attempts and transient:
            print(
                f"REFRESH_TOKENS: transient failure attempt {attempt}/{attempts}; "
                f"retrying in {delay:.1f}s"
            )
            time.sleep(delay)
            continue
        break
    raise RuntimeError(
        f"token refresh failed (exit={last_code}): {last_out[-800:]}"
    )


def prepare_funnel_benchmark_target(
    *,
    root: Path,
    env: Mapping[str, str] | None = None,
    runner: RunFn = subprocess.run,
    project: str = "benchmark_mall",
    target_base_url: str = "http://localhost:8080",
) -> dict:
    """DB reset first (seed accounts), then token refresh against the live API.

    Also emits a benchmark_target_reset receipt so the cleanliness guard can
    verify incomplete sandbox cleanups were superseded by a full DB reseed.
    """
    import json
    import uuid
    from datetime import datetime, timezone

    db = reset_benchmark_target_db(env=env, runner=runner)
    tokens = refresh_test_account_tokens(root=root, runner=runner)
    receipt_dir = Path(root) / "_funnel_runs"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{project}_target_reset_receipt.json"
    reset_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Receipt must be newer than any prior sandbox audit row.
    receipt = {
        "schema_version": "benchmark_target_reset.v1",
        "receipt_id": f"reset_{uuid.uuid4().hex[:16]}",
        "project": project,
        "target_base_url": str(target_base_url or "").rstrip("/"),
        "status": "completed",
        "reset_at_utc": reset_at,
        "target_db_reset": db,
    }
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "target_db_reset": db,
        "token_refresh": tokens,
        "reset_receipt_path": str(receipt_path),
        "reset_receipt": receipt,
    }
