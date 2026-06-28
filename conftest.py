"""Repo-root pytest configuration.

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
"""
from __future__ import annotations

from pathlib import Path


def pytest_configure(config) -> None:
    # Only override when the caller has not explicitly chosen a basetemp; we
    # never want to fight a CI-provided --basetemp or PYTEST_* override.
    if config.option.basetemp:
        return
    repo_root = Path(__file__).resolve().parent
    basetemp = repo_root / ".pytest_tmp" / "run"
    basetemp.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(basetemp)
