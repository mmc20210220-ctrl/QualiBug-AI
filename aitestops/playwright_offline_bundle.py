from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class PlaywrightOfflineBundle:
    root: Path
    wheelhouse: Path
    browsers: Path
    manifest: Path


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def _venv_python(venv_dir: Path) -> Path:
    if platform.system().lower().startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _ensure_venv(venv_dir: Path) -> Path:
    python = _venv_python(venv_dir)
    if python.exists():
        return python
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    _run([sys.executable, "-m", "venv", str(venv_dir)])
    python = _venv_python(venv_dir)
    if not python.exists():
        raise FileNotFoundError(f"venv python not found: {python}")
    return python


def _python_smoke(python: Path, *, browsers_path: Path) -> None:
    env = dict(os.environ)
    env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    subprocess.run([str(python), "-c", "import playwright; print('OK')"], check=True, env=env)


def _runtime_browser_smoke(python: Path, *, browsers_path: Path, browser: str) -> tuple[bool, str]:
    env = dict(os.environ)
    env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    code = (
        "from playwright.sync_api import sync_playwright;"
        "p=sync_playwright().start();"
        f"b=getattr(p,'{browser}').launch(headless=True);"
        "b.close();p.stop();print('OK')"
    )
    proc = subprocess.run([str(python), "-c", code], env=env, capture_output=True, text=True)
    if proc.returncode == 0:
        return True, (proc.stdout or "").strip() or "OK"
    stderr = (proc.stderr or "").strip()
    return False, stderr or (proc.stdout or "").strip() or f"exit_code={proc.returncode}"


def _classify_playwright_runtime_error(message: str) -> dict[str, str]:
    text = (message or "").lower()
    if not text:
        return {"category": "unknown", "action": "运行时启动失败但缺少错误输出，建议在客户机上重新执行 verify --runtime-smoke 并保留 stderr。"}
    if "libgtk" in text or "gtk" in text or "libx11" in text or "libxcomposite" in text or "libxrandr" in text or "libasound" in text:
        return {"category": "missing_os_libs", "action": "Linux 缺系统动态库/图形依赖。需要客户 IT 预装 Playwright 依赖或改用容器/专用 runner。"}
    if "no usable sandbox" in text or "sandbox" in text or "seccomp" in text:
        return {"category": "sandbox_restricted", "action": "浏览器沙箱被限制。可尝试调整容器/内核安全策略或使用受支持的 runner 环境。"}
    if "egl" in text or "glx" in text or "gpu" in text:
        return {"category": "graphics_stack", "action": "图形栈/GPU 相关依赖缺失或不兼容。建议使用 headless 且由 IT 补齐依赖，或容器化运行。"}
    if "permission" in text or "access denied" in text:
        return {"category": "permission_denied", "action": "权限不足导致浏览器启动失败。建议检查目录权限（browsers_path/临时目录）或使用用户目录 venv。"}
    if "not found" in text and "executable" in text:
        return {"category": "browser_binary_missing", "action": "浏览器二进制不存在或路径不正确。请确认 PLAYWRIGHT_BROWSERS_PATH 指向 ms-playwright 目录且包含 chromium-/firefox- 子目录。"}
    if "failed to launch" in text or "browser closed" in text:
        return {"category": "browser_launch_failed", "action": "浏览器启动失败。优先在客户机执行 playwright-offline-verify --runtime-smoke 获取更完整依赖信息，必要时走容器/runner。"}
    return {"category": "unknown", "action": "未能自动归因。建议收集 stderr，检查 Linux 依赖或 Windows 运行库，并使用 verify --runtime-smoke 重试。"}


def _default_playwright_cache_dir() -> Path:
    if platform.system().lower().startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE") or ""
        return Path(local) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _bundle_paths(root: Path) -> PlaywrightOfflineBundle:
    return PlaywrightOfflineBundle(
        root=root,
        wheelhouse=root / "wheelhouse",
        browsers=root / "browsers",
        manifest=root / "manifest.json",
    )


def build_playwright_offline_bundle(
    *,
    out_dir: Path,
    requirements_file: Path,
    browsers: Iterable[str],
) -> PlaywrightOfflineBundle:
    bundle = _bundle_paths(out_dir)
    bundle.wheelhouse.mkdir(parents=True, exist_ok=True)
    bundle.browsers.mkdir(parents=True, exist_ok=True)

    python = sys.executable
    _run([python, "-m", "pip", "download", "-r", str(requirements_file), "-d", str(bundle.wheelhouse)])
    _run([python, "-m", "pip", "download", "playwright>=1.40.0", "-d", str(bundle.wheelhouse)])

    _run([python, "-m", "pip", "install", "--no-index", "--find-links", str(bundle.wheelhouse), "playwright>=1.40.0"])
    browser_list = [str(item).strip() for item in browsers if str(item).strip()]
    if not browser_list:
        browser_list = ["chromium"]
    _run([python, "-m", "playwright", "install", *browser_list])

    cache_dir = _default_playwright_cache_dir()
    if cache_dir.exists():
        target = bundle.browsers / "ms-playwright"
        if target.exists():
            return bundle
        target.parent.mkdir(parents=True, exist_ok=True)
        if platform.system().lower().startswith("win"):
            _run(["powershell", "-NoProfile", "-Command", f"Copy-Item -Recurse -Force '{cache_dir}' '{target}'"])
        else:
            _run(["bash", "-lc", f"cp -a '{cache_dir}' '{target}'"])

    manifest = {
        "schema": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version,
        "platform": platform.platform(),
        "browsers": browser_list,
        "wheelhouse": str(bundle.wheelhouse.name),
        "browsers_dir": str(bundle.browsers.name),
        "playwright_cache_source": str(cache_dir),
    }
    bundle.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle


def verify_playwright_offline_bundle(
    *,
    bundle_dir: Path,
    browsers_path: Path | None = None,
    python: Path | None = None,
    run_runtime_smoke: bool = False,
) -> dict[str, object]:
    bundle = _bundle_paths(bundle_dir)
    wheelhouse_ok = bundle.wheelhouse.exists()
    browsers_root_candidates = [bundle.browsers / "ms-playwright", bundle.browsers]
    chosen_browsers = browsers_path or next((p for p in browsers_root_candidates if p.exists()), bundle.browsers)
    browsers_ok = chosen_browsers.exists()
    manifest_ok = bundle.manifest.exists()
    browser_subdirs = [p.name for p in chosen_browsers.iterdir()] if browsers_ok else []
    chromium_count = sum(1 for name in browser_subdirs if name.startswith("chromium-"))
    firefox_count = sum(1 for name in browser_subdirs if name.startswith("firefox-"))
    webkit_count = sum(1 for name in browser_subdirs if name.startswith("webkit-"))
    issues: list[str] = []
    if not wheelhouse_ok:
        issues.append(f"wheelhouse not found: {bundle.wheelhouse}")
    if not browsers_ok:
        issues.append(f"browsers not found: {chosen_browsers}")
    if wheelhouse_ok and not any(bundle.wheelhouse.glob("playwright-*.whl")):
        issues.append("playwright wheel not found in wheelhouse")
    if browsers_ok and chromium_count == 0:
        issues.append("chromium browser cache not found under ms-playwright")
    if browsers_ok and firefox_count == 0:
        issues.append("firefox browser cache not found under ms-playwright")
    runtime = {"enabled": False, "python": str(python or ""), "smoke": {}, "error": ""}
    if run_runtime_smoke and python:
        runtime["enabled"] = True
        runtime["python"] = str(python)
        try:
            subprocess.run([str(python), "-c", "import playwright; print('OK')"], check=True, capture_output=True, text=True, env={**os.environ, "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "PLAYWRIGHT_BROWSERS_PATH": str(chosen_browsers)})
            runtime["smoke"]["import_playwright"] = True
        except Exception as exc:
            runtime["smoke"]["import_playwright"] = False
            runtime["error"] = str(exc)
        ok_chromium, msg_chromium = _runtime_browser_smoke(python, browsers_path=chosen_browsers, browser="chromium")
        ok_firefox, msg_firefox = _runtime_browser_smoke(python, browsers_path=chosen_browsers, browser="firefox")
        runtime["smoke"]["chromium_launch"] = {"ok": ok_chromium, "message": msg_chromium, "diagnosis": _classify_playwright_runtime_error(msg_chromium) if not ok_chromium else {}}
        runtime["smoke"]["firefox_launch"] = {"ok": ok_firefox, "message": msg_firefox, "diagnosis": _classify_playwright_runtime_error(msg_firefox) if not ok_firefox else {}}
        if not ok_chromium or not ok_firefox:
            issues.append("runtime smoke failed: browser launch error (likely missing OS libs on Linux or restricted sandbox)")
    hint = {
        "offline_env": {"PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "PLAYWRIGHT_BROWSERS_PATH": str(chosen_browsers)},
        "linux_note": "若在 Linux 启动失败且无 root 权限，通常是缺系统动态库/字体依赖，需要客户 IT 预装或使用容器镜像/专用 runner。",
    }
    return {
        "bundle_dir": str(bundle_dir),
        "wheelhouse_ok": wheelhouse_ok,
        "browsers_ok": browsers_ok,
        "manifest_ok": manifest_ok,
        "browsers_path": str(chosen_browsers),
        "browsers_detected": {"chromium": chromium_count, "firefox": firefox_count, "webkit": webkit_count},
        "issues": issues,
        "runtime": runtime,
        "hint": hint,
    }


def install_playwright_offline_bundle(
    *,
    bundle_dir: Path,
    requirements_file: Path,
    browsers_path: Path | None = None,
    env_out: Path | None = None,
    venv_dir: Path | None = None,
) -> dict[str, str]:
    bundle = _bundle_paths(bundle_dir)
    if not bundle.wheelhouse.exists():
        raise FileNotFoundError(f"wheelhouse not found: {bundle.wheelhouse}")
    if browsers_path is None:
        candidates = [bundle.browsers / "ms-playwright", bundle.browsers]
        browsers_path = next((p for p in candidates if p.exists()), bundle.browsers)

    python = _ensure_venv(venv_dir) if venv_dir else Path(sys.executable)
    _run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(bundle.wheelhouse), "-r", str(requirements_file)])
    _run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(bundle.wheelhouse), "playwright>=1.40.0"])
    _python_smoke(python, browsers_path=browsers_path)

    env_vars = {
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
        "PLAYWRIGHT_BROWSERS_PATH": str(browsers_path),
        "PLAYWRIGHT_OFFLINE_VENV_PYTHON": str(python),
    }
    if env_out:
        lines = [f"{key}={value}" for key, value in env_vars.items()]
        env_out.parent.mkdir(parents=True, exist_ok=True)
        existing = env_out.read_text(encoding="utf-8", errors="ignore") if env_out.exists() else ""
        merged = existing.rstrip("\n")
        if merged:
            merged += "\n"
        merged += "\n".join(lines) + "\n"
        env_out.write_text(merged, encoding="utf-8")
    return env_vars
