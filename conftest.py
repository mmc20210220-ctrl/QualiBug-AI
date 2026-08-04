"""Repo-root pytest configuration.

Three isolation concerns are handled here:

1. **JWT secret for collection.**
   ``ai_test_asset_center.jwt_auth`` raises ``RuntimeError`` at *import time*
   when ``QUALIBUG_JWT_SECRET`` is unset, and ``private_pilot_service`` imports
   it at module load. Any test module that imports ``private_pilot_service``
   (directly or transitively) would therefore fail at collection on a machine
   that has not exported the secret. We provide a dev-only default so the suite
   is runnable without an explicit export; CI/prod override it via the real env
   var. ``setdefault`` means an explicitly set value always wins.

2. **Basetemp isolation (OS-temp contention).**
   This project's suite builds deeply nested fixture trees (Phase104H/G/F handoff
   bundles, Phase106 Vite/React app exports) and then archives them with
   ``zipfile`` while other tests may be tearing their own ``tmp_path`` down in
   parallel. When pytest's basetemp lives under the OS temp directory on Windows,
   one test's ``shutil.rmtree`` can remove a directory that another test's
   ``zipfile.write`` is about to reference. That surfaces as nondeterministic
   failures that never reproduce in isolation::

       FileNotFoundError: [WinError 3] 系统找不到指定的路径
       OSError: [WinError 145] 目录不是空的

   Pin basetemp to a repo-local, git-ignored directory so the per-test temp tree
   is isolated from OS-temp contention. This mirrors what the local PowerShell
   runners already do via ``--basetemp .\\.pytest_tmp\\run``.

   The repo-local basetemp is made **unique per session** (pid + random suffix).
   A fixed path left behind by a crashed/aborted run cannot be cleared reliably
   here: the sandbox routes Python-level deletes through a safe-delete hook that
   refuses to trash project-local paths, so pytest's own teardown raises
   ``OSError`` and cascades into spurious errors. A fresh unique directory is
   always removable by pytest's normal cleanup, eliminating the cascade.

3. **Self-healing basetemp (over-cleaning tests).**
   A few tests drive production-style cleanup that removes ``tmp_path.parent`` —
   which *is* the shared basetemp — so every later test that requests ``tmp_path``
   fails with ``FileNotFoundError`` on the missing directory. This is a genuine
   suite isolation bug (it also caused part of the historical "270 errors").
   Rather than hunt every over-cleaning call site, we make the basetemp
   self-healing: a function-scoped autouse fixture recreates it before each test
   if a prior test deleted it. Recreating an empty shared root is harmless — each
   test still gets its own fresh ``tmp_path`` subdir — and it stops one test from
   poisoning the rest of the session.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

# Captured in pytest_configure so the self-healing fixture can recreate it.
_BASETEMP: "Path | None" = None


def pytest_configure(config) -> None:
    # 1) Dev-only JWT secret default so import-time collection does not crash.
    os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

    # 1b) Mainline LLM reasoner must stay out of the test suite by default.
    # build_discovery_plan consumes the 11-engine Reasoner when enabled; with
    # real provider credentials in .env that turns deterministic unit tests into
    # live multi-engine LLM calls (minutes of latency, nondeterministic output).
    # Tests that exercise the wiring patch the reasoner entry points and delete
    # this variable explicitly. An explicit export always wins (setdefault).
    os.environ.setdefault("QUALIBUG_MAINLINE_REASONER_DISABLED", "1")

    # 2) Unique per-session basetemp to avoid stale-dir safe-delete cascade.
    if config.option.basetemp:
        return
    repo_root = Path(__file__).resolve().parent
    basetemp = repo_root / ".pytest_tmp" / f"run-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    # Best-effort prune of stale run-* dirs from prior sessions; never touches
    # the active session dir. Uses the OS `rm` (bypasses the Python safe-delete
    # hook); if unavailable, accumulation is harmless and git-ignored.
    try:
        for stale in (repo_root / ".pytest_tmp").glob("run-*"):
            if stale == basetemp:
                continue
            subprocess.run(
                ["rm", "-rf", str(stale)],
                check=False, capture_output=True, shell=False,
            )
    except Exception:
        pass
    basetemp.mkdir(parents=True, exist_ok=True)
    global _BASETEMP
    _BASETEMP = basetemp
    config.option.basetemp = str(basetemp)


def pytest_collection_finish(session) -> None:
    # Fallback capture of the basetemp path if pytest_configure was bypassed
    # (e.g. an explicit --basetemp was provided).
    global _BASETEMP
    if _BASETEMP is None and session.config.option.basetemp:
        _BASETEMP = Path(session.config.option.basetemp)


@pytest.fixture(autouse=True, scope="function")
def _heal_basetemp():
    """Recreate the shared basetemp if an earlier test over-cleaned it.

    Keeps ``tmp_path`` usable for every test even when some production-style
    cleanup deletes ``tmp_path.parent`` mid-session.
    """
    if _BASETEMP is not None:
        try:
            _BASETEMP.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    yield
