"""Harden bundled Playwright engine launch for matrix profiles.

Chromium needs the repository's existing no-sandbox arguments in common container
and private-pilot deployments.  Firefox and WebKit keep their native launch
contract.  The guard preserves the bounded install retry and never falls back to
an unversioned system browser, which would make matrix evidence non-reproducible.
"""
from __future__ import annotations

from typing import Any

from . import professional_ui_browser_matrix as _matrix
from .enterprise_knowledge_center._formal_ui_browser_matrix_guard import (
    SUPPORTED_ENGINES,
)

_INSTALL_MARKER = "_qualibug_browser_matrix_launch_guard_installed"
_ORIGINAL_LAUNCH = "_qualibug_matrix_launch_before_guard"


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def install_professional_ui_browser_matrix_launch_guard() -> None:
    if getattr(_matrix, _INSTALL_MARKER, False):
        return
    setattr(
        _matrix,
        _ORIGINAL_LAUNCH,
        getattr(_matrix, _ORIGINAL_LAUNCH, _matrix._launch_engine),
    )

    def launch_bundled_engine(
        profile: dict[str, Any],
        *,
        headless: bool,
        timeout: int,
    ) -> tuple[Any, Any]:
        engine = _text(profile.get("browser_engine"), limit=20).lower()
        if engine not in SUPPORTED_ENGINES:
            return None, f"browser_matrix_engine_unsupported:{engine or 'missing'}"
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            package_error = _matrix._auto_browser._install_playwright_pkg()
            if package_error:
                return None, "browser_matrix_playwright_package_unavailable"
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                return None, "browser_matrix_playwright_import_missing"

        last_error = ""
        for attempt in range(2):
            runtime = None
            try:
                runtime = sync_playwright().start()
                browser_type = getattr(runtime, engine)
                launch_options: dict[str, Any] = {
                    "headless": headless,
                    "timeout": timeout,
                }
                if engine == "chromium":
                    launch_options["args"] = [
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ]
                browser = browser_type.launch(**launch_options)
                version = _text(getattr(browser, "version", ""), limit=120)
                _matrix._ACTIVE_RUNTIME.set({
                    "browser_engine": engine,
                    "browser_version": version,
                    "playwright_version": _matrix._playwright_version(),
                    "bundled_engine_required": True,
                    "system_browser_fallback_used": False,
                    "chromium_container_sandbox_disabled": engine == "chromium",
                })
                return runtime, _matrix._MatrixBrowser(browser, profile)
            except Exception as exc:  # noqa: BLE001
                last_error = (
                    f"browser_matrix_engine_launch_failed:{engine}:"
                    f"{type(exc).__name__}"
                )
                try:
                    if runtime is not None:
                        runtime.stop()
                except Exception:
                    pass
                if attempt == 0:
                    install_error = _matrix._install_engine(engine)
                    if not install_error:
                        continue
                    last_error = install_error
                break
        return None, last_error or f"browser_matrix_engine_unavailable:{engine}"

    _matrix._launch_engine = launch_bundled_engine
    setattr(_matrix, _INSTALL_MARKER, True)


__all__ = ["install_professional_ui_browser_matrix_launch_guard"]
