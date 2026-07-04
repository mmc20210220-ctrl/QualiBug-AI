"""
Auto Browser Setup — zero-config Playwright browser provisioning.
Handles China network environment (GFW) with mirrors, system browser fallback,
and offline bundle support.

Priority:
1. System-installed Chrome/Edge/Chromium (fastest, bypasses download)
2. PLAYWRIGHT_BROWSERS_PATH / offline bundle (enterprise pre-provisioned)
3. Auto-install via domestic mirrors (npmmirror / tsinghua)
4. Manual guidance (last resort)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


_BROWSER_READY: bool | None = None
_BROWSER_ERROR: str = ""

# ── China-friendly mirrors ──
_PLAYWRIGHT_MIRROR = "https://npmmirror.com/mirrors/playwright/"
_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _is_china_network() -> bool:
    """Heuristic: check if running behind GFW."""
    return bool(
        os.environ.get("QUALIBUG_CHINA_NETWORK") or
        os.environ.get("PLAYWRIGHT_DOWNLOAD_HOST")
    )


def _detect_system_browser() -> str | None:
    """Detect system-installed Chrome/Edge/Chromium executable path."""
    candidates = []

    if sys.platform == "win32":
        candidates = [
            # Chrome
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            # Edge (Windows default)
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            # Chromium
            os.path.expandvars(r"%LocalAppData%\Chromium\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:  # Linux
        candidates = [
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge", "/snap/bin/chromium",
        ]
        # which fallback
        for cmd in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            found = shutil.which(cmd)
            if found and found not in candidates:
                candidates.append(found)

    for path_str in candidates:
        p = Path(path_str)
        if p.exists() and p.is_file():
            return str(p)
    return None


def ensure_browser(headless: bool = True, timeout: int = 30000):
    """Ensure a Playwright browser is ready.

    Returns (playwright, browser) or (None, error_string).
    """
    global _BROWSER_READY, _BROWSER_ERROR

    if _BROWSER_READY is False:
        return None, _BROWSER_ERROR

    # ── Step 1: playwright package ──
    try:
        from playwright.sync_api import sync_playwright  # noqa: F811
    except ImportError:
        err = _install_playwright_pkg()
        if err:
            _BROWSER_READY = False
            _BROWSER_ERROR = err
            return None, err
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _BROWSER_READY = False
            _BROWSER_ERROR = "playwright 安装后仍无法导入，请检查 Python 环境"
            return None, _BROWSER_ERROR

    # ── Step 2: browser binary ──
    # 2a. System-installed browser (fastest, no download)
    system_browser = _detect_system_browser()
    channel: str | None = None
    executable_path: str | None = None

    if system_browser:
        executable_path = system_browser
        # Detect which channel: chrome / msedge
        if "edge" in system_browser.lower() or "msedge" in system_browser.lower():
            channel = "msedge"

    # 2b. PLAYWRIGHT_BROWSERS_PATH or offline bundle
    if not executable_path:
        browsers_path = _find_browsers_cache()
        if not browsers_path:
            # 2c. Auto-install via domestic mirror
            ok, err = _install_chromium_mirrored()
            if not ok:
                _BROWSER_READY = False
                _BROWSER_ERROR = err
                return None, err
            browsers_path = _find_browsers_cache()

    # ── Step 3: Launch ──
    try:
        pw = sync_playwright().start()
        launch_kwargs: dict = {
            "headless": headless,
            "timeout": timeout,
            "args": ["--no-sandbox", "--disable-setuid-sandbox"],
        }
        if channel:
            launch_kwargs["channel"] = channel
        if executable_path and not channel:
            launch_kwargs["executable_path"] = executable_path

        browser = pw.chromium.launch(**launch_kwargs)
        _BROWSER_READY = True
        source = channel or (Path(executable_path).stem if executable_path else "chromium")
        print(f"  [OK] Browser ready ({source}, headless={headless})", flush=True)
        return pw, browser
    except Exception as e:
        _BROWSER_READY = False
        _BROWSER_ERROR = f"浏览器启动失败: {e}"
        return None, _BROWSER_ERROR


def _install_playwright_pkg() -> str:
    """pip install playwright, with China mirror if applicable."""
    python = sys.executable
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    args = [python, "-m", "pip", "install", "playwright"]

    if _is_china_network():
        args += ["-i", _PIP_INDEX, "--trusted-host", "pypi.tuna.tsinghua.edu.cn"]

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=120, env=env)
        if result.returncode != 0:
            tail = result.stderr.strip()[-200:]
            return f"pip install playwright 失败: {tail}"
    except subprocess.TimeoutExpired:
        return "pip install playwright 超时（120s），请手动: pip install playwright -i https://pypi.tuna.tsinghua.edu.cn/simple"
    except Exception as e:
        return f"pip install playwright 异常: {e}"
    return ""


def _find_browsers_cache() -> str | None:
    """Find existing playwright browser cache directory."""
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if env_path:
        p = Path(env_path)
        if _has_chromium_dir(p):
            return str(p)

    try:
        import playwright
        pw_dir = Path(playwright.__file__).parent
    except ImportError:
        return None

    candidates = [
        pw_dir / "driver" / "package" / ".local-browsers",
        Path.home() / ".cache" / "ms-playwright",
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
    ]
    for c in candidates:
        if _has_chromium_dir(c):
            return str(c)
    return None


def _has_chromium_dir(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return any(p.name.startswith("chromium-") for p in path.iterdir())
    except (PermissionError, OSError):
        return False


def _install_chromium_mirrored() -> tuple[bool, str]:
    """Install chromium via playwright, using domestic mirror if in China."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    # Set download mirror
    env["PLAYWRIGHT_DOWNLOAD_HOST"] = env.get(
        "PLAYWRIGHT_DOWNLOAD_HOST", _PLAYWRIGHT_MIRROR
    )

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=300, env=env,
        )
        if result.returncode == 0:
            return True, ""

        stderr_tail = result.stderr.strip()[-300:]
        msg = (
            f"Chromium 下载失败（国内网络已切换 npmmirror 镜像）。\n"
            f"错误: {stderr_tail}\n"
            f"解决方法:\n"
            f"  1. 使用系统自带浏览器: 安装 Chrome 或 Edge，系统会自动检测使用\n"
            f"  2. 离线安装: 从 https://npmmirror.com/mirrors/playwright/ 下载对应版本\n"
            f"     解压后设置 PLAYWRIGHT_BROWSERS_PATH 指向解压目录\n"
            f"  3. 手动执行: set PLAYWRIGHT_DOWNLOAD_HOST={_PLAYWRIGHT_MIRROR}\n"
            f"     playwright install chromium"
        )
        return False, msg
    except subprocess.TimeoutExpired:
        return False, (
            f"Chromium 下载超时（5分钟）。建议:\n"
            f"  1. 使用系统自带 Chrome/Edge（自动检测）\n"
            f"  2. 或将离线包放到 PLAYWRIGHT_BROWSERS_PATH"
        )
    except Exception as e:
        return False, f"Chromium 安装异常: {e}"


def browser_health() -> dict:
    """Return browser availability status."""
    system_browser = _detect_system_browser()
    cache = _find_browsers_cache()

    try:
        import playwright
        pw_ver = getattr(playwright, "__version__", "unknown")
    except ImportError:
        return {
            "available": bool(system_browser),
            "playwright_installed": False,
            "system_browser": system_browser,
            "chromium_cache": cache,
            "action": "pip install playwright（首次自动安装）",
        }

    return {
        "available": bool(system_browser or cache),
        "playwright_installed": True,
        "playwright_version": pw_ver,
        "system_browser": system_browser,
        "chromium_cache": cache,
        "action": "" if (system_browser or cache) else "playwright install chromium（首次自动安装，已切换国内镜像）",
    }
