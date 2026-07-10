"""Funnel-benchmark preflight: reset target DB + refresh auth tokens.

All modes (baseline/optimized/llm/...) exercise governed write probes against
the non-production benchmark target. Without a fresh DB and live tokens,
permission/isolation probes degrade and cross-run state pollutes recall.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

DEFAULT_BENCHMARK_TARGET_ROOT = Path(
    r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
    r"\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
)

RunFn = Callable[..., subprocess.CompletedProcess]


def resolve_benchmark_target_root(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    raw = str(env.get("QUALIBUG_BENCHMARK_TARGET_ROOT") or "").strip()
    return Path(raw) if raw else DEFAULT_BENCHMARK_TARGET_ROOT


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
        text=True,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()[:800]
        raise RuntimeError(
            f"benchmark target DB reset failed (exit={completed.returncode}): {stderr}"
        )
    print("RESET_TARGET_DB: ok")
    return {"status": "ok", "script": str(script), "exit_code": 0}


def refresh_test_account_tokens(
    *,
    root: Path,
    runner: RunFn = subprocess.run,
) -> dict:
    """Refresh tokens for every funnel mode — expired JWT silently kills auth probes."""
    script = root / "_refresh_tokens.py"
    if not script.is_file():
        raise FileNotFoundError(f"token refresh script missing: {script}")

    print(f"REFRESH_TOKENS: {script}")
    completed = runner(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    out = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if out:
        print(out)
    if completed.returncode != 0:
        raise RuntimeError(
            f"token refresh failed (exit={completed.returncode}): {out[-800:]}"
        )
    print("REFRESH_TOKENS: ok")
    return {"status": "ok", "script": str(script), "exit_code": 0}


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
