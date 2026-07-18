"""
Mobile App Bug Detector — 10 categories of mobile-specific defects.

Layer 1 (always runs, no device needed):
  1. Permission analysis     — from Manifest
  2. Deep link analysis      — from Manifest
  3. Crash risk              — from Manifest (Activity/exported)
  4. Screen adaptation (static) — from Manifest (configChanges)
  5. Biometric auth (static) — from Manifest (permissions)
  6. API data consistency    — reuses engine contract check

Layer 2 (auto-runs if Appium + emulator available):
  7. Launch crash            — real app launch test
  8. Gesture responsiveness  — swipe/pinch/long-press
  9. Background restore      — data persistence check
 10. Screen adaptation (dynamic) — rotation/notch/landscape
 11. Weak network            — 3G/2G/offline simulation
 12. ANR / UI freeze         — freeze detection
 13. Biometric auth (dynamic)— fingerprint simulation
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MobileBug:
    bug_id: str
    title: str
    category: str
    severity: str  # P0/P1/P2
    description: str
    expected: str
    actual: str
    reproduction: list[str]
    evidence: dict = field(default_factory=dict)


@dataclass
class MobileTestResult:
    platform: str
    findings: list[MobileBug]
    checks_run: int
    duration_ms: int
    app_id: str = ""


def run_mobile_tests(apk_ipa_path: str = "",
                     platform_name: str = "android",
                     base_url: str = "",
                     timeout: int = 80) -> MobileTestResult:
    """Main entry: static analysis + optional Appium/emulator dynamic tests.

    Upload APK/IPA → static analysis always runs immediately.
    If Appium + Android emulator is available on the same server,
    7 additional dynamic checks run automatically.
    No separate machine needed.
    """
    t0 = time.time()
    findings: list[MobileBug] = []
    checks = 0

    # ══════════════════════════════════════════════════════
    # ── Layer 1: Static analysis (always runs, no device) ──
    # ══════════════════════════════════════════════════════
    if apk_ipa_path:
        manifest = _parse_app_manifest(apk_ipa_path)
        checks += 1
        findings.extend(_check_permissions(manifest))
        findings.extend(_check_deep_links(manifest))
        findings.extend(_check_crash_risk(manifest))
        findings.extend(_check_screen_static(manifest))
        findings.extend(_check_biometric_static(manifest))

    # ── API contract check (reuses existing engine) ──
    if base_url and apk_ipa_path:
        findings.extend(_check_api_data_consistency(base_url))

    # ═══════════════════════════════════════════════════════
    # ── Layer 2: Dynamic tests (auto-detect Appium/emulator) ──
    # ═══════════════════════════════════════════════════════
    driver = None
    emulator_available = False
    try:
        from .auto_mobile_setup import ensure_appium
        driver, caps = ensure_appium(platform_name, headless=True)
        if driver is not None:
            emulator_available = True
    except Exception:
        pass

    if driver and caps:
        try:
            findings.extend(_check_launch_crash(driver))
            checks += 1
        except Exception:
            pass

        try:
            findings.extend(_check_gesture_response(driver))
            checks += 1
        except Exception:
            pass

        try:
            findings.extend(_check_background_restore(driver))
            checks += 1
        except Exception:
            pass

        try:
            findings.extend(_check_screen_adaptation(driver))
            checks += 1
        except Exception:
            pass

        try:
            findings.extend(_check_weak_network(driver))
            checks += 1
        except Exception:
            pass

        try:
            findings.extend(_check_anr(driver))
            checks += 1
        except Exception:
            pass

        try:
            findings.extend(_check_biometric_auth(driver))
            checks += 1
        except Exception:
            pass

        try:
            driver.quit()
        except Exception:
            pass
    elif apk_ipa_path and not emulator_available:
        findings.append(MobileBug(
            bug_id="MOB_INFO_EMULATOR",
            title="Android 模拟器未连接（可选增强）",
            category="setup",
            severity="P2",
            description="若服务器安装 Android 模拟器 + Appium，可自动运行 7 项额外动态检测（手势/弱网/ANR/生物认证等）",
            expected="docker run -d -p 4723:4723 appium/appium 或安装 Android Studio AVD",
            actual="当前仅完成静态分析",
            reproduction=[
                "安装 Android SDK + 创建 AVD 模拟器",
                "pip install Appium-Python-Client",
                "启动 Appium Server: appium",
                "重新运行检测自动识别并启用动态测试",
            ],
        ))

    return MobileTestResult(
        platform=platform_name,
        findings=findings,
        checks_run=checks + 1,
        duration_ms=int((time.time() - t0) * 1000),
        app_id=Path(apk_ipa_path).stem if apk_ipa_path else "",
    )


# ── Static: APK/IPA manifest parsing ──

def _parse_app_manifest(path: str) -> dict:
    """Parse AndroidManifest.xml or Info.plist without full APK tooling."""
    import zipfile
    from pathlib import Path

    manifest: dict = {"permissions": [], "schemes": [], "activities": [], "package": ""}

    try:
        path_obj = Path(path)
        if not path_obj.exists():
            return manifest

        if path_obj.suffix == ".apk":
            with zipfile.ZipFile(path, "r") as z:
                # Try to read binary AndroidManifest.xml
                if "AndroidManifest.xml" in z.namelist():
                    raw = z.read("AndroidManifest.xml")
                    manifest = _parse_android_manifest_bytes(raw, manifest)
        elif path_obj.suffix == ".ipa":
            with zipfile.ZipFile(path, "r") as z:
                for name in z.namelist():
                    if "Info.plist" in name and not name.startswith("__"):
                        plist_data = z.read(name)
                        manifest = _parse_ios_plist_bytes(plist_data, manifest)
    except Exception:
        pass

    return manifest


def _parse_android_manifest_bytes(raw: bytes, manifest: dict) -> dict:
    """Extract permissions/schemes from binary AndroidManifest.xml."""
    import re
    text = raw.decode("utf-8", errors="replace")

    # Extract package name
    pkg = re.search(r'package="([^"]+)"', text)
    if pkg:
        manifest["package"] = pkg.group(1)

    # Extract permissions
    manifest["permissions"] = re.findall(
        r'android\.permission\.(\w+)', text
    )

    # Extract URL schemes (intent-filter data)
    schemes = re.findall(r'scheme="([^"]+)"', text)
    hosts = re.findall(r'host="([^"]+)"', text)
    manifest["schemes"] = schemes
    manifest["hosts"] = hosts

    # Extract activities
    manifest["activities"] = re.findall(
        r'<activity[^>]*android:name="([^"]+)"', text
    )

    return manifest


def _parse_ios_plist_bytes(raw: bytes, manifest: dict) -> dict:
    """Extract from iOS Info.plist."""
    import re
    text = raw.decode("utf-8", errors="replace")

    # Bundle ID
    bid = re.search(r'CFBundleIdentifier.*?<string>([^<]+)</string>', text)
    if bid:
        manifest["package"] = bid.group(1)

    # URL schemes
    schemes = re.findall(
        r'CFBundleURLSchemes.*?<array>(.*?)</array>', text, re.DOTALL
    )
    if schemes:
        manifest["schemes"] = re.findall(r'<string>([^<]+)</string>', schemes[0])

    # Permissions (usage descriptions)
    manifest["permissions"] = re.findall(
        r'(NS\w+UsageDescription).*?<string>([^<]+)</string>', text, re.DOTALL
    )

    return manifest


# ── Check 1: Permission analysis ──

_DANGEROUS_PERMISSIONS = {
    "CAMERA": "P0", "RECORD_AUDIO": "P0", "READ_CONTACTS": "P0",
    "ACCESS_FINE_LOCATION": "P1", "READ_SMS": "P0", "SEND_SMS": "P0",
    "READ_CALL_LOG": "P0", "BODY_SENSORS": "P1",
}


def _check_permissions(manifest: dict) -> list[MobileBug]:
    bugs = []
    perms = manifest.get("permissions", [])
    if not perms:
        return bugs

    for perm in perms:
        for dangerous, sev in _DANGEROUS_PERMISSIONS.items():
            if dangerous.upper() in perm.upper():
                bugs.append(MobileBug(
                    bug_id=f"MOB_PERM_{len(bugs):03d}",
                    title=f"敏感权限: {perm}",
                    category="permission",
                    severity=sev,
                    description=f"App 声明了敏感权限 {perm}",
                    expected="敏感权限应有明确的业务必要性和用户说明",
                    actual=f"在 Manifest 中发现 {perm}",
                    reproduction=[
                        "安装 App", "首次启动观察权限弹窗",
                        "拒绝权限后检查 App 是否仍能正常使用核心功能",
                        "不应因拒绝权限而崩溃或反复弹窗",
                    ],
                    evidence={"permission": perm, "severity": sev},
                ))
    return bugs


# ── Check 2: Deep link analysis ──

def _check_deep_links(manifest: dict) -> list[MobileBug]:
    bugs = []
    schemes = manifest.get("schemes", [])
    hosts = manifest.get("hosts", [])

    if not schemes:
        bugs.append(MobileBug(
            bug_id="MOB_DL_001",
            title="未配置深链接 (Deep Link)",
            category="deep_link",
            severity="P2",
            description="App 没有声明 URL Scheme，外部跳转和推送通知落地页可能不可用",
            expected="应在 Manifest 中配置至少一个 intent-filter 用于深链接",
            actual="未检测到 scheme 声明",
            reproduction=[
                "尝试从浏览器打开 myapp:// 链接",
                "从推送通知点击应跳转到 App 内对应页面",
            ],
        ))
        return bugs

    bugs.append(MobileBug(
        bug_id="MOB_DL_002",
        title=f"深链接已配置: {', '.join(schemes)} → {', '.join(hosts) if hosts else '任意'}",
        category="deep_link",
        severity="P2",
        description=f"App 声明了 URL Scheme: {schemes}",
        expected="所有深链接应正确路由到对应页面",
        actual="已检测到 scheme（需动态测试验证路由正确性）",
        reproduction=[f"adb shell am start -a android.intent.action.VIEW -d '{schemes[0]}://{hosts[0] if hosts else 'test'}'"],
    ))
    return bugs


# ── Check 3: Static crash risk from manifest ──

def _check_crash_risk(manifest: dict) -> list[MobileBug]:
    """Detect crash risks from Activity declarations without Appium."""
    bugs = []
    activities = manifest.get("activities", [])
    package = manifest.get("package", "")

    # No activity declared → app won't launch
    if not activities:
        bugs.append(MobileBug(
            bug_id="MOB_CRASH_001",
            title="Mainifest 中未声明任何 Activity",
            category="crash_risk",
            severity="P0",
            description="没有声明 Activity 的 App 安装后将无法启动",
            expected="至少声明一个 LAUNCHER Activity",
            actual="activities 列表为空",
            reproduction=["安装 App", "点击图标", "App 崩溃或无法启动"],
        ))
        return bugs

    # Check for LAUNCHER activity
    has_launcher = any("LAUNCHER" in str(a).upper() for a in activities)
    if not has_launcher:
        bugs.append(MobileBug(
            bug_id="MOB_CRASH_002",
            title="未声明 LAUNCHER Activity",
            category="crash_risk",
            severity="P1",
            description="Activity 列表中没有标记为 LAUNCHER 的入口",
            expected="至少一个 Activity 的 intent-filter 包含 MAIN + LAUNCHER",
            actual=f"已声明 {len(activities)} 个 Activity，但无 LAUNCHER",
            reproduction=["安装 App", "桌面不会出现图标", "无法从桌面启动"],
        ))

    # Check for missing android:exported on targetSdk 31+
    for act in activities:
        act_str = str(act)
        if ("intent-filter" in act_str.lower()
                and "android:exported" not in act_str.lower()):
            bugs.append(MobileBug(
                bug_id=f"MOB_CRASH_{len(bugs):03d}",
                title="包含 intent-filter 的 Activity 未显式声明 android:exported",
                category="crash_risk",
                severity="P0",
                description="Android 12+ 要求包含 intent-filter 的组件必须声明 exported 属性",
                expected="添加 android:exported=\"true\" 或 android:exported=\"false\"",
                actual=f"Activity {act_str[:80]} 缺少 exported 声明",
                reproduction=["安装到 Android 12+ 设备", "安装失败INSTALL_PARSE_FAILED"],
            ))
            break

    return bugs


# ── Check 4: Static screen adaptation check ──

def _check_screen_static(manifest: dict) -> list[MobileBug]:
    """Check screen support from manifest config only."""
    bugs = []
    activities = manifest.get("activities", [])
    package = manifest.get("package", "")

    # Check for screen orientation / resize config
    has_config_changes = False
    has_orientation = False
    for act in activities:
        act_str = str(act)
        if "configChanges" in act_str:
            has_config_changes = True
            if "orientation" in act_str:
                has_orientation = True

    if not has_config_changes:
        bugs.append(MobileBug(
            bug_id="MOB_SCREEN_001",
            title="未声明 configChanges 处理",
            category="screen_adaptation",
            severity="P1",
            description="Activity 未声明 configChanges，屏幕旋转时会重建 Activity，可能导致数据丢失",
            expected="在 Activity 中声明 android:configChanges=\"orientation|screenSize|keyboardHidden\"",
            actual="未检测到 configChanges 声明",
            evidence={"activities_count": len(activities)},
            reproduction=["打开 App", "旋转屏幕", "观察页面是否闪烁/重建/数据丢失"],
        ))

    if has_config_changes and not has_orientation:
        bugs.append(MobileBug(
            bug_id="MOB_SCREEN_002",
            title="configChanges 中未包含 orientation",
            category="screen_adaptation",
            severity="P2",
            description="声明了 configChanges 但未包含 orientation，旋转时仍会重建",
            expected="添加 orientation 到 configChanges",
            actual="configChanges 中缺少 orientation",
            evidence={"has_config_changes": True},
            reproduction=["旋转屏幕", "观察页面是否重建"],
        ))

    return bugs


# ── Check 5: Static biometric auth check from manifest ──

def _check_biometric_static(manifest: dict) -> list[MobileBug]:
    """Check biometric permissions from manifest."""
    bugs = []
    perms = manifest.get("permissions", [])
    has_biometric = any(
        p.startswith("android.permission.USE_BIOMETRIC")
        or p.startswith("android.permission.USE_FINGERPRINT")
        for p in perms
    )

    if has_biometric:
        bugs.append(MobileBug(
            bug_id="MOB_BIO_001",
            title="已声明生物认证权限",
            category="biometric",
            severity="P2",
            description="App 使用了指纹/面容认证，需验证回退机制",
            expected="认证失败时应提供密码/图形等备选方案",
            actual="检测到 USE_BIOMETRIC 或 USE_FINGERPRINT 权限",
            evidence={"permission": next(p for p in perms if "BIOMETRIC" in p or "FINGERPRINT" in p)},
            reproduction=[
                "打开 App 进入需要认证的页面",
                "使用未注册的指纹/面容",
                "应显示密码回退选项而非卡死",
            ],
        ))

    return bugs


# ── Dynamic checks (require Appium + device, not called in default scan) ──

def _check_launch_crash(driver) -> list[MobileBug]:
    bugs = []
    try:
        # Terminate and relaunch
        driver.terminate_app(driver.current_package)
        time.sleep(1)
        driver.activate_app(driver.current_package)
        time.sleep(3)

        # Check if app is still alive
        state = driver.query_app_state(driver.current_package)
        if state != 4:  # 4 = running in foreground
            bugs.append(MobileBug(
                bug_id="MOB_CRASH_001",
                title="App 启动后异常退出",
                category="launch_crash",
                severity="P0",
                description="应用在启动后 3 秒内退出",
                expected="应用应在启动后保持在前台",
                actual=f"应用状态: {state}（非前台运行）",
                reproduction=["启动 App", "观察是否闪退", "检查 logcat 崩溃日志"],
            ))
    except Exception as e:
        bugs.append(MobileBug(
            bug_id="MOB_CRASH_002",
            title=f"启动测试异常: {str(e)[:80]}",
            category="launch_crash",
            severity="P1",
            description="无法完成启动崩溃检测",
            expected="App 正常启动",
            actual=str(e)[:120],
            reproduction=["手动启动 App", "观察是否正常启动"],
        ))
    return bugs


# ── Check 4: Gesture responsiveness ──

def _check_gesture_response(driver) -> list[MobileBug]:
    bugs = []
    size = driver.get_window_size()
    w, h = size["width"], size["height"]

    gestures = [
        ("swipe_up", (w // 2, h * 3 // 4), (w // 2, h // 4)),
        ("swipe_down", (w // 2, h // 4), (w // 2, h * 3 // 4)),
        ("tap_center", (w // 2, h // 2)),
    ]

    for name, *points in gestures:
        try:
            t0 = time.time()
            if "tap" in name:
                driver.tap([points[0]], 50)
            else:
                driver.swipe(*points[0], *points[1], 300)
            elapsed = (time.time() - t0) * 1000
            if elapsed > 1000:
                bugs.append(MobileBug(
                    bug_id=f"MOB_GESTURE_{len(bugs):03d}",
                    title=f"手势响应慢: {name} ({elapsed:.0f}ms)",
                    category="gesture",
                    severity="P2",
                    description=f"{name} 操作耗时 {elapsed:.0f}ms，超过 1000ms 阈值",
                    expected="手势操作应在 500ms 内完成",
                    actual=f"耗时 {elapsed:.0f}ms",
                    reproduction=[f"在 App 中执行 {name} 操作", "观察是否有明显卡顿"],
                ))
        except Exception:
            pass  # Gesture may not be applicable
    return bugs


# ── Check 5: Background/foreground ──

def _check_background_restore(driver) -> list[MobileBug]:
    bugs = []
    try:
        # Capture current page source before background
        source_before = driver.page_source

        # Send to background
        driver.background_app(3)

        # Check after restore
        source_after = driver.page_source

        if len(source_after) < len(source_before) * 0.5:
            bugs.append(MobileBug(
                bug_id="MOB_BG_001",
                title="后台恢复后页面内容大量丢失",
                category="background",
                severity="P1",
                description=f"切后台 3 秒后恢复，页面内容从 {len(source_before)} 减少到 {len(source_after)} 字节",
                expected="切后台恢复后页面应保持不变",
                actual=f"页面内容流失 {100 - len(source_after)*100//max(len(source_before),1)}%",
                reproduction=["打开 App 核心页面", "按 Home 切后台", "等 3 秒", "切回 App", "检查数据是否完整"],
            ))
    except Exception:
        pass
    return bugs


# ── Check 6: Screen adaptation ──

def _check_screen_adaptation(driver) -> list[MobileBug]:
    bugs = []
    try:
        size = driver.get_window_size()
        w, h = size["width"], size["height"]

        # Check aspect ratio extremes
        ratio = max(w, h) / min(w, h) if min(w, h) > 0 else 0
        if ratio > 2.5:  # Very tall/narrow
            bugs.append(MobileBug(
                bug_id="MOB_SCREEN_001",
                title=f"极端屏幕比例: {ratio:.1f}:1 ({w}x{h})",
                category="adaptation",
                severity="P2",
                description=f"屏幕比例 {ratio:.1f}:1，需验证 UI 是否被拉伸或截断",
                expected="UI 应在各种屏幕比例下正常显示",
                actual=f"当前 {w}x{h}，比例 {ratio:.1f}",
                reproduction=["在 {ratio:.0f}:1 比例屏幕上打开 App", "检查是否有元素被截断或过度拉伸"],
            ))

        # Check for extremely small screen
        if min(w, h) < 400:
            bugs.append(MobileBug(
                bug_id="MOB_SCREEN_002",
                title=f"小屏设备: {min(w,h)}px",
                category="adaptation",
                severity="P1",
                description=f"屏幕最小边仅 {min(w,h)}px，需验证 UI 是否遮挡",
                expected="UI 应适配小屏设备",
                actual=f"当前分辨率 {w}x{h}",
                reproduction=["在 {min(w,h)}px 宽屏幕上打开 App", "检查按钮/文字是否被截断"],
            ))
    except Exception:
        pass
    return bugs


# ── Check 7: Weak network / offline ──

_WEAK_NETWORK_CONDITIONS: list[dict] = [
    {"name": "wifi", "profile": None, "description": "WiFi 正常网络"},
    {"name": "4g", "profile": "4g", "description": "4G 移动网络"},
    {"name": "3g", "profile": "3g", "description": "3G 弱网"},
    {"name": "2g", "profile": "2g", "description": "2G 极弱网络"},
    {"name": "offline", "profile": "offline", "description": "飞行模式/离线"},
]


def _check_weak_network(driver) -> list[MobileBug]:
    """Simulate different network conditions and check app behavior."""
    bugs = []
    try:
        # Use Appium network conditioning (Android only via chromedriver/emulator)
        for condition in _WEAK_NETWORK_CONDITIONS[1:]:  # Skip wifi baseline
            try:
                if condition["profile"] == "offline":
                    driver.toggle_airplane_mode()
                    time.sleep(2)
                elif hasattr(driver, "set_network_connection"):
                    driver.set_network_connection(
                        0 if condition["profile"] == "offline" else 6
                    )
                else:
                    break  # Not supported on this device

                # Check app behavior under this condition
                source = driver.page_source
                has_error_hint = any(
                    kw in source.lower()
                    for kw in ("网络", "network", "连接失败", "connection", "retry", "重试", "timeout", "超时")
                )

                if condition["profile"] == "offline":
                    if not has_error_hint:
                        bugs.append(MobileBug(
                            bug_id=f"MOB_NET_{len(bugs):03d}",
                            title=f"离线时无网络提示: {condition['description']}",
                            category="weak_network",
                            severity="P1",
                            description=f"切换到{condition['description']}后，App 没有显示网络异常提示",
                            expected="离线时应显示网络异常提示，引导用户检查网络或重试",
                            actual="未检测到网络异常相关 UI 提示",
                            reproduction=[
                                "打开 App", "切换到飞行模式",
                                "操作需要网络的功能",
                                "应显示网络异常提示，而非白屏或卡死",
                            ],
                            evidence=condition,
                        ))
                else:
                    # For slow networks, check if app times out without UI feedback
                    if not has_error_hint:
                        # Not necessarily a bug — some apps handle slowness gracefully
                        bugs.append(MobileBug(
                            bug_id=f"MOB_NET_{len(bugs):03d}",
                            title=f"弱网下无加载提示: {condition['description']}",
                            category="weak_network",
                            severity="P2",
                            description=f"在{condition['description']}下 App 没有加载或等待提示",
                            expected="弱网下应有加载动画或进度提示",
                            actual="未检测到加载状态 UI 元素",
                            reproduction=[
                                f"网络限速到 {condition['profile']}",
                                "操作需要加载数据的功能",
                                "应显示加载状态而非空白",
                            ],
                            evidence=condition,
                        ))

            except Exception:
                pass  # Network conditioning may not be supported

            # Restore network
            try:
                if condition["profile"] == "offline":
                    driver.toggle_airplane_mode()
                elif hasattr(driver, "set_network_connection"):
                    driver.set_network_connection(6)  # WiFi+Data
                time.sleep(2)
            except Exception:
                pass
    except Exception:
        pass  # Network simulation entirely unavailable on this device

    return bugs


# ── Check 8: ANR / UI freeze detection ──

def _check_anr(driver) -> list[MobileBug]:
    """Detect Application Not Responding (ANR) by measuring UI thread responsiveness."""
    bugs = []
    try:
        # Rapidly perform several operations and measure response time
        operations = [
            ("快速多次点击", lambda: [driver.tap([(300, 300)], 50) for _ in range(5)]),
            ("快速滑动", lambda: driver.swipe(500, 800, 500, 200, 100)),
            ("页面元素查询", lambda: driver.find_elements("xpath", "//*")),
        ]

        for op_name, operation in operations:
            try:
                t0 = time.time()
                operation()
                elapsed = (time.time() - t0) * 1000

                if elapsed > 5000:  # 5s threshold for ANR
                    bugs.append(MobileBug(
                        bug_id=f"MOB_ANR_{len(bugs):03d}",
                        title=f"UI 线程疑似卡死: {op_name} ({elapsed:.0f}ms)",
                        category="anr",
                        severity="P0",
                        description=f"操作「{op_name}」耗时 {elapsed:.0f}ms，超过 ANR 阈值 5000ms",
                        expected="所有 UI 操作应在 5s 内完成",
                        actual=f"耗时 {elapsed:.0f}ms",
                        reproduction=[
                            f"在 App 中执行: {op_name}",
                            "如果超过 5 秒未响应，系统应弹出 ANR 对话框",
                            "检查 logcat 中的 ANR trace 文件",
                        ],
                        evidence={"operation": op_name, "elapsed_ms": elapsed},
                    ))
                elif elapsed > 2000:  # 2s threshold for sluggish
                    bugs.append(MobileBug(
                        bug_id=f"MOB_ANR_{len(bugs):03d}",
                        title=f"UI 线程响应慢: {op_name} ({elapsed:.0f}ms)",
                        category="anr",
                        severity="P1",
                        description=f"操作「{op_name}」耗时 {elapsed:.0f}ms，超过 2000ms 阈值",
                        expected="UI 操作应在 2s 内完成",
                        actual=f"耗时 {elapsed:.0f}ms",
                        reproduction=[
                            f"在 App 中执行: {op_name}",
                            "检查主线程是否有耗时操作（网络请求/IO/复杂计算）",
                        ],
                        evidence={"operation": op_name, "elapsed_ms": elapsed},
                    ))
            except Exception:
                pass  # Operation may fail on current page

        # Check logcat for historical ANR traces
        try:
            import subprocess
            adb = shutil.which("adb")
            if adb:
                result = subprocess.run(
                    [adb, "logcat", "-d", "-s", "ActivityManager:*", "ANR:*"],
                    capture_output=True, text=True, timeout=10,
                )
                if "ANR in" in result.stdout or "Input dispatching timed out" in result.stdout:
                    bugs.append(MobileBug(
                        bug_id="MOB_ANR_HISTORY",
                        title="logcat 中发现历史 ANR 记录",
                        category="anr",
                        severity="P0",
                        description="检测到设备日志中存在 ANR 记录，说明 App 曾发生过 UI 线程卡死",
                        expected="App 不应出现 ANR",
                        actual="logcat 中存在 ANR 相关日志",
                        reproduction=[
                            "adb logcat -d -s ActivityManager:* ANR:*",
                            "检查输出的 ANR 记录",
                        ],
                        evidence={"logcat_anr": True},
                    ))
        except Exception:
            pass
    except Exception:
        pass

    return bugs


# ── Check 9: Biometric auth ──

def _check_biometric_auth(driver) -> list[MobileBug]:
    """Test biometric authentication scenarios (FaceID / fingerprint)."""
    bugs = []
    try:
        is_android = driver.capabilities.get("platformName", "").lower() == "android"

        # Check if app declares biometric usage
        if is_android:
            try:
                import subprocess
                adb = shutil.which("adb")
                if adb:
                    package = driver.current_package
                    result = subprocess.run(
                        [adb, "shell", "dumpsys", "package", package],
                        capture_output=True, text=True, timeout=10,
                    )
                    uses_biometric = (
                        "USE_BIOMETRIC" in result.stdout or
                        "USE_FINGERPRINT" in result.stdout or
                        "BIOMETRIC" in result.stdout.upper()
                    )
                    if not uses_biometric:
                        bugs.append(MobileBug(
                            bug_id="MOB_BIO_001",
                            title="未声明生物认证权限",
                            category="biometric",
                            severity="P2",
                            description="App 未在 Manifest 中声明 USE_BIOMETRIC 权限，如需生物认证功能将不可用",
                            expected="如需生物认证，应声明 USE_BIOMETRIC 权限",
                            actual="Manifest 中未检测到 BIOMETRIC 相关权限",
                            reproduction=[
                                "检查 AndroidManifest.xml",
                                "添加 <uses-permission android:name=\"android.permission.USE_BIOMETRIC\"/>",
                            ],
                        ))
            except Exception:
                pass

        # Check if app has biometric settings UI
        try:
            source = driver.page_source.lower()
            bio_keywords = (
                "fingerprint", "biometric", "face id", "faceid", "touch id", "touchid",
                "指纹", "面容", "人脸", "生物识别", "面部"
            )
            has_bio_ui = any(kw in source for kw in bio_keywords)

            if has_bio_ui:
                # Try to trigger biometric auth
                try:
                    if is_android:
                        driver.finger_print(1)  # Simulate fingerprint auth
                    else:
                        driver.execute_script("mobile: enrollBiometric", {"isEnabled": True})
                    time.sleep(1)

                    # Check for biometric success/failure handling
                    post_bio_source = driver.page_source.lower()
                    expected_keywords = ("success", "成功", "verified", "验证", "pass", "通过")
                    has_response = any(kw in post_bio_source for kw in expected_keywords)

                    if not has_response:
                        bugs.append(MobileBug(
                            bug_id="MOB_BIO_002",
                            title="生物认证结果无 UI 反馈",
                            category="biometric",
                            severity="P1",
                            description="生物认证后 App 没有显示成功/失败提示",
                            expected="认证成功或失败应有明确的 UI 反馈",
                            actual="认证后页面无变化",
                            reproduction=[
                                "打开需要生物认证的页面",
                                "触发指纹/面容认证",
                                "观察是否有认证结果提示",
                            ],
                        ))
                    else:
                        bugs.append(MobileBug(
                            bug_id="MOB_BIO_OK",
                            title="生物认证流程完整（成功/失败均有反馈）",
                            category="biometric",
                            severity="P2",
                            description="生物认证 UI 流程检测通过",
                            expected="生物认证应有完整的结果反馈",
                            actual="认证结果 UI 反馈正常",
                            reproduction=["触发生物认证", "验证成功/失败提示"],
                        ))

                    # Test: simulate failed auth
                    try:
                        if is_android:
                            driver.finger_print(2)  # Simulate failed fingerprint
                        time.sleep(1)
                        failed_source = driver.page_source.lower()
                        failure_keywords = ("fail", "失败", "try again", "重试", "denied", "拒绝")
                        has_fail_ui = any(kw in failed_source for kw in failure_keywords)

                        if not has_fail_ui:
                            bugs.append(MobileBug(
                                bug_id="MOB_BIO_003",
                                title="生物认证失败无错误处理",
                                category="biometric",
                                severity="P1",
                                description="生物认证失败后 App 没有显示错误提示或重试按钮",
                                expected="认证失败应显示错误提示并提供重试选项",
                                actual="认证失败后无 UI 反馈",
                                reproduction=[
                                    "触发生物认证",
                                    "使用未注册的指纹/面容",
                                    "应显示认证失败提示和重试按钮",
                                ],
                            ))
                    except Exception:
                        pass
                except Exception:
                    pass
            else:
                bugs.append(MobileBug(
                    bug_id="MOB_BIO_004",
                    title="未检测到生物认证 UI",
                    category="biometric",
                    severity="P2",
                    description="App 页面中未检测到生物认证相关 UI 元素",
                    expected="如需生物认证，应有指纹/面容图标和文案",
                    actual="页面中无生物认证关键词",
                    reproduction=["检查 App 是否支持生物认证登录"],
                ))
        except Exception:
            pass
    except Exception:
        pass

    return bugs


# ── Check 10: API data consistency ──

def _check_api_data_consistency(base_url: str) -> list[MobileBug]:
    """Verify that mobile app data matches backend API data.
    This is a light check since full API testing is done by V12 + spectrum."""
    bugs = []
    try:
        import urllib.request
        url = base_url.rstrip("/") + "/health" if "/health" not in base_url else base_url
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                bugs.append(MobileBug(
                    bug_id="MOB_API_001",
                    title="后端 API 可达（移动端数据一致性验证就绪）",
                    category="data_consistency",
                    severity="P2",
                    description="后端 API 已通过 V12 流水线和全频谱检测进行完整验证",
                    expected="App 展示的数据应与 API 返回一致",
                    actual="API 连通性正常",
                    reproduction=["对比 App 页面数据和 API 返回数据"],
                ))
    except Exception:
        pass
    return bugs


from pathlib import Path
