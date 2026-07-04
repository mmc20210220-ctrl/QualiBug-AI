"""
Auto Mobile Setup — zero-config Appium provisioning for Android/iOS testing.

Mirrors auto_browser_setup.py pattern: auto-install, system detection, China mirror,
graceful degradation.

Supported:
- Android: Appium + UiAutomator2 driver + Android SDK/emulator detection
- iOS: Appium + XCUITest driver (macOS only, system-silent on other OS)

Single entry point: ensure_appium(platform="android") → returns Appium session or None.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

_APPIUM_READY: dict[str, bool] = {}  # platform -> ready?
_APPIUM_ERROR: dict[str, str] = {}

# China-friendly mirrors
_NPM_MIRROR = "https://registry.npmmirror.com"


def _is_china_network() -> bool:
    return bool(
        os.environ.get("QUALIBUG_CHINA_NETWORK") or
        os.environ.get("SASS_REGISTRY")
    )


def _node_cmd() -> str:
    """Find node executable."""
    node = shutil.which("node") or shutil.which("nodejs") or ""
    if not node and sys.platform == "win32":
        for p in [
            r"C:\Program Files\nodejs\node.exe",
            r"C:\Program Files (x86)\nodejs\node.exe",
        ]:
            if Path(p).exists():
                return p
    return node or "node"


def _npm_cmd() -> str:
    npm = shutil.which("npm") or ""
    if not npm and sys.platform == "win32":
        for p in [
            r"C:\Program Files\nodejs\npm.cmd",
            r"C:\Program Files (x86)\nodejs\npm.cmd",
        ]:
            if Path(p).exists():
                return p
    return npm or "npm"


def _detect_adb() -> str | None:
    """Detect Android Debug Bridge."""
    adb = shutil.which("adb")
    if adb:
        return adb
    # ANDROID_HOME
    android_home = os.environ.get("ANDROID_HOME", "")
    if android_home:
        candidates = [
            Path(android_home) / "platform-tools" / "adb",
            Path(android_home) / "platform-tools" / "adb.exe",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
    return None


def _detect_android_emulators() -> list[dict]:
    """List available Android emulators."""
    adb = _detect_adb()
    if not adb:
        return []
    try:
        result = subprocess.run(
            [adb, "devices"], capture_output=True, text=True, timeout=10
        )
        devices = []
        for line in result.stdout.strip().split("\n")[1:]:
            if "\tdevice" in line or "\temulator" in line:
                udid = line.split("\t")[0].strip()
                if udid:
                    devices.append({"udid": udid, "type": "emulator" if "emulator" in udid else "device"})
        return devices
    except Exception:
        return []


def _detect_ios_simulators() -> list[dict]:
    """List available iOS simulators (macOS only)."""
    if sys.platform != "darwin":
        return []
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "available", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        import json
        data = json.loads(result.stdout)
        devices = []
        for runtime, devs in data.get("devices", {}).items():
            for d in devs:
                if d.get("state") == "Booted" or "iPhone" in d.get("name", ""):
                    devices.append({
                        "udid": d["udid"],
                        "name": d["name"],
                        "runtime": runtime,
                        "type": "simulator",
                    })
        return devices[:5]
    except Exception:
        return []


# ── Appium installation ──

def _install_appium() -> str:
    """npm install -g appium, with China mirror."""
    node = _node_cmd()
    npm = _npm_cmd()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    # Check if already installed
    if shutil.which("appium"):
        return ""

    args = [npm, "install", "-g", "appium"]
    if _is_china_network():
        args += ["--registry", _NPM_MIRROR]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=180, env=env,
        )
        if result.returncode != 0:
            return f"appium 安装失败: {result.stderr.strip()[-200:]}"
    except subprocess.TimeoutExpired:
        return f"appium 安装超时（180s），请手动: npm install -g appium --registry {_NPM_MIRROR}"
    except Exception as e:
        return f"appium 安装异常: {e}"

    # Verify
    if shutil.which("appium"):
        return ""
    return "appium 安装后仍不可用，请检查 Node.js 环境"


def _install_appium_driver(driver: str) -> str:
    """appium driver install <driver>"""
    appium = shutil.which("appium")
    if not appium:
        return "appium 未安装"

    env = {**os.environ}
    if _is_china_network():
        env["APPIUM_DRIVER_INSTALL_REGISTRY"] = _NPM_MIRROR
        if driver == "uiautomator2":
            env["APPIUM_SKIP_CHROMEDRIVER_INSTALL"] = "1"  # Skip chromedriver which is hard to download

    try:
        result = subprocess.run(
            [appium, "driver", "install", driver],
            capture_output=True, text=True, timeout=120, env=env,
        )
        if result.returncode != 0:
            return f"driver {driver} 安装失败: {result.stderr.strip()[-150:]}"
    except subprocess.TimeoutExpired:
        return f"driver {driver} 安装超时"
    except Exception as e:
        return f"driver {driver} 安装异常: {e}"
    return ""


# ── Main entry ──

def ensure_appium(platform_name: str = "android",
                  headless: bool = True,
                  timeout: int = 30000):
    """Ensure Appium is ready for the target platform.

    Returns (appium_driver, capabilities) or (None, error_string).
    """
    global _APPIUM_READY

    if _APPIUM_READY.get(platform_name) is False:
        return None, _APPIUM_ERROR.get(platform_name, "unknown error")

    # ── Step 1: Install appium ──
    err = _install_appium()
    if err:
        _APPIUM_READY[platform_name] = False
        _APPIUM_ERROR[platform_name] = err
        return None, err

    # ── Step 2: Install driver ──
    driver_name = "uiautomator2" if platform_name == "android" else "xcuitest"
    if platform_name == "ios" and sys.platform != "darwin":
        _APPIUM_READY["ios"] = False
        _APPIUM_ERROR["ios"] = "iOS 测试需要在 macOS 上运行"
        return None, _APPIUM_ERROR["ios"]

    err = _install_appium_driver(driver_name)
    if err:
        _APPIUM_READY[platform_name] = False
        _APPIUM_ERROR[platform_name] = err
        return None, err

    # ── Step 3: Verify target device exists ──
    caps: dict = {"platformName": "Android" if platform_name == "android" else "iOS"}

    if platform_name == "android":
        devices = _detect_android_emulators()
        if not devices:
            _APPIUM_READY[platform_name] = False
            _APPIUM_ERROR[platform_name] = (
                "未检测到 Android 设备/模拟器。\n"
                "解决方法:\n"
                "  1. Android Studio → AVD Manager → 创建模拟器\n"
                "  2. 或连接真机并开启 USB 调试\n"
                "  3. 运行 adb devices 确认设备可见"
            )
            return None, _APPIUM_ERROR[platform_name]
        caps["appium:udid"] = devices[0]["udid"]
        caps["appium:automationName"] = "UiAutomator2"
        caps["appium:noReset"] = True

    else:
        sims = _detect_ios_simulators()
        if not sims:
            _APPIUM_READY[platform_name] = False
            _APPIUM_ERROR[platform_name] = (
                "未检测到 iOS 模拟器。\n"
                "解决方法:\n"
                "  Xcode → Open Developer Tool → Simulator"
            )
            return None, _APPIUM_ERROR[platform_name]
        caps["appium:udid"] = sims[0]["udid"]
        caps["appium:automationName"] = "XCUITest"
        caps["appium:deviceName"] = sims[0]["name"]

    # ── Step 4: Launch Appium session ──
    # Auto-install Python client if missing
    try:
        from appium import webdriver
        from appium.options.common import AppiumOptions
    except ImportError:
        print("  [INFO] 正在安装 Appium-Python-Client ...", flush=True)
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "Appium-Python-Client"],
                capture_output=True, text=True, timeout=120,
            )
            from appium import webdriver
            from appium.options.common import AppiumOptions
        except Exception as e:
            err_msg = f"Appium-Python-Client 安装失败: {e}"
            _APPIUM_READY[platform_name] = False
            _APPIUM_ERROR[platform_name] = err_msg
            return None, err_msg

    # Try connecting — if Appium server not running, auto-start it
    def _connect():
        options = AppiumOptions()
        options.load_capabilities(caps)
        return webdriver.Remote("http://localhost:4723", options=options)

    try:
        driver = _connect()
    except Exception:
        _start_appium_server()
        time.sleep(3)
        try:
            driver = _connect()
        except Exception as e2:
            _APPIUM_READY[platform_name] = False
            _APPIUM_ERROR[platform_name] = f"Appium 启动失败: {e2}"
            return None, _APPIUM_ERROR[platform_name]

    _APPIUM_READY[platform_name] = True
    print(f"  [OK] Appium ready ({platform_name}, "
          f"device={caps.get('appium:udid', '?')})", flush=True)
    return driver, caps


def _start_appium_server() -> None:
    """Start appium server in background if not already running."""
    appium = shutil.which("appium")
    if not appium:
        return
    subprocess.Popen(
        [appium, "--log-level", "error", "--relaxed-security"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def mobile_health() -> dict:
    """Return mobile testing availability status."""
    return {
        "android": {
            "appium": bool(shutil.which("appium")),
            "adb": bool(_detect_adb()),
            "devices": _detect_android_emulators(),
            "ready": bool(shutil.which("appium") and _detect_adb() and _detect_android_emulators()),
        },
        "ios": {
            "appium": bool(shutil.which("appium")),
            "simulators": _detect_ios_simulators(),
            "ready": sys.platform == "darwin" and bool(shutil.which("appium")) and bool(_detect_ios_simulators()),
            "requires_macos": sys.platform != "darwin",
        },
    }
