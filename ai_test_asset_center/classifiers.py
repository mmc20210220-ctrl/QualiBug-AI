"""
EnvironmentClassifier — Auto-detect environment type and risk level.
DocumentRoleClassifier — Auto-classify document type from content.

Both use: filename patterns, content features, URL patterns, DB names,
header analysis, and LLM fallback.
"""

from __future__ import annotations

import re
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# EnvironmentClassifier
# ═══════════════════════════════════════════════════════════════════


# Known environment name patterns (NOT hardcoded sandbox/test/staging)
ENV_PATTERNS: dict[str, dict[str, Any]] = {
    "development": {
        "names": ["dev", "development", "local", "localhost", "sandbox", "playground", "debug"],
        "url_patterns": [r"localhost", r"127\.0\.0\.1", r"dev\.", r"sandbox\.", r"debug\."],
        "risk_level": 1,
        "description": "开发/沙箱环境 — 允许所有操作",
    },
    "testing": {
        "names": ["uat", "sit", "fat", "qa", "test", "testing", "tst", "int", "integration", "mock"],
        "url_patterns": [r"uat\.", r"sit\.", r"fat\.", r"qa\.", r"test\.", r"tst\.", r"int\.", r"mock\."],
        "risk_level": 2,
        "description": "测试环境 — 允许安全写操作(需rollback)",
    },
    "pre_release": {
        "names": ["pre", "preprod", "gray", "grey", "canary", "pre-release", "staging", "stage", "green", "blue"],
        "url_patterns": [r"pre\.", r"preprod\.", r"gray\.", r"canary\.", r"staging\.", r"stage\."],
        "risk_level": 3,
        "description": "预发布/灰度环境 — 只允许只读探针",
    },
    "production": {
        "names": ["prod", "production", "live", "online", "prd", "master", "main"],
        "url_patterns": [r"prod\.", r"\.com", r"\.cn", r"\.io", r"api\.", r"www\."],
        "risk_level": 4,
        "description": "生产环境 — 禁止任何破坏性测试",
    },
    "unknown": {
        "names": [],
        "url_patterns": [],
        "risk_level": 3,
        "description": "未知环境 — 保守策略(只读探针)",
    },
}


def classify_environment(
    *,
    env_name: str = "",
    base_url: str = "",
    db_connection_string: str = "",
    headers: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Auto-classify environment type and risk level.

    Priority: env_name > url_patterns > db_name > headers > LLM_fallback.
    Returns: {type, risk_level, confidence, evidence}
    """
    evidence = []
    headers = headers or {}
    config = config or {}

    # 1. Check explicit environment name
    if env_name:
        env_name_lower = env_name.lower().strip()
        for env_type, info in ENV_PATTERNS.items():
            if env_type == "unknown":
                continue
            for name in info["names"]:
                if name in env_name_lower or env_name_lower == name:
                    evidence.append(f"env_name匹配: {env_name} → {env_type}")
                    return {
                        "type": env_type,
                        "risk_level": info["risk_level"],
                        "confidence": 0.90,
                        "evidence": evidence,
                        "description": info["description"],
                    }

    # 2. Check URL patterns
    if base_url:
        url_lower = base_url.lower()
        for env_type, info in ENV_PATTERNS.items():
            if env_type == "unknown":
                continue
            for pattern in info["url_patterns"]:
                if re.search(pattern, url_lower):
                    evidence.append(f"URL匹配: {pattern} → {env_type}")
                    return {
                        "type": env_type,
                        "risk_level": info["risk_level"],
                        "confidence": 0.85,
                        "evidence": evidence,
                        "description": info["description"],
                    }

    # 3. Check database name
    if db_connection_string:
        db_lower = db_connection_string.lower()
        for env_type, info in ENV_PATTERNS.items():
            if env_type == "unknown":
                continue
            for name in info["names"]:
                if name in db_lower:
                    evidence.append(f"DB名匹配: {name} → {env_type}")
                    return {
                        "type": env_type,
                        "risk_level": info["risk_level"],
                        "confidence": 0.80,
                        "evidence": evidence,
                        "description": info["description"],
                    }

    # 4. Check headers for environment hints
    env_header_keys = ["x-env", "x-environment", "x-stage", "x-deploy-env", "env", "environment"]
    for key in env_header_keys:
        val = headers.get(key, "").lower().strip()
        if val:
            for env_type, info in ENV_PATTERNS.items():
                if env_type == "unknown":
                    continue
                if val in info["names"] or any(n in val for n in info["names"]):
                    evidence.append(f"Header匹配: {key}={val} → {env_type}")
                    return {
                        "type": env_type,
                        "risk_level": info["risk_level"],
                        "confidence": 0.75,
                        "evidence": evidence,
                        "description": info["description"],
                    }

    # 5. Check config fields
    config_env_keys = ["environment", "env", "deploy_env", "stage", "target_env"]
    for key in config_env_keys:
        val = str(config.get(key, "")).lower().strip()
        if val:
            for env_type, info in ENV_PATTERNS.items():
                if env_type == "unknown":
                    continue
                if val in info["names"] or any(n in val for n in info["names"]):
                    evidence.append(f"配置匹配: {key}={val} → {env_type}")
                    return {
                        "type": env_type,
                        "risk_level": info["risk_level"],
                        "confidence": 0.70,
                        "evidence": evidence,
                        "description": info["description"],
                    }

    # 6. Heuristics
    if base_url:
        url_lower = base_url.lower()
        if "localhost" in url_lower or "127.0.0.1" in url_lower:
            evidence.append("本地地址 → development")
            return {
                "type": "development", "risk_level": 1, "confidence": 0.95,
                "evidence": evidence, "description": "本地开发环境",
            }
        if any(tld in url_lower for tld in (".com", ".cn", ".io", ".org")):
            evidence.append("公网域名 → production")
            return {
                "type": "production", "risk_level": 4, "confidence": 0.70,
                "evidence": evidence, "description": "疑似生产环境(公网域名)",
            }

    # Fallback
    evidence.append("无法确定 → unknown(保守策略)")
    return {
        "type": "unknown",
        "risk_level": 3,
        "confidence": 0.30,
        "evidence": evidence,
        "description": "未知环境 — 需要人工确认",
    }


# ═══════════════════════════════════════════════════════════════════
# DocumentRoleClassifier
# ═══════════════════════════════════════════════════════════════════


DOCUMENT_SIGNATURES: dict[str, dict[str, Any]] = {
    "openapi_spec": {
        "file_patterns": [r"openapi\.(json|ya?ml)", r"swagger\.(json|ya?ml)", r"api-docs?\.(json|ya?ml)", r"\.oas\.(json|ya?ml)$"],
        "content_signatures": ['"openapi"', '"swagger"', '"paths"', "openapi:", "swagger:"],
        "priority": 100,
        "label": "API规范文档",
    },
    "prd": {
        "file_patterns": [r"prd\.", r"mrd\.", r"brd\.", r"需求", r"requirement", r"spec\.md", r"product-spec"],
        "content_signatures": ["需求", "功能", "用户故事", "user story", "验收标准", "acceptance criteria", "业务规则"],
        "priority": 90,
        "label": "产品需求文档",
    },
    "api_doc": {
        "file_patterns": [r"api\.md", r"接口", r"api-doc", r"endpoint"],
        "content_signatures": ["| 方法 |", "| GET |", "| POST |", "## 基础接口", "接口文档", "API文档"],
        "priority": 85,
        "label": "API接口文档",
    },
    "db_schema": {
        "file_patterns": [r"schema\.sql", r"db_schema", r"数据库", r"database", r"ddl\.sql", r"er图"],
        "content_signatures": ["CREATE TABLE", "ALTER TABLE", "PRIMARY KEY", "FOREIGN KEY", "表名", "字段"],
        "priority": 80,
        "label": "数据库Schema",
    },
    "bug_history": {
        "file_patterns": [r"bug.*\.(md|csv|json)", r"defect.*\.(md|csv|json)", r"缺陷", r"bug.*history", r"issue"],
        "content_signatures": ["bug_id", "severity", "缺陷", "bug", "reproduce", "复现"],
        "priority": 70,
        "label": "历史Bug记录",
    },
    "test_scenario": {
        "file_patterns": [r"test.*scenario", r"测试用例", r"test.*case", r"test.*plan", r"测试计划"],
        "content_signatures": ["测试场景", "test scenario", "前置条件", "测试步骤", "预期结果"],
        "priority": 60,
        "label": "测试场景文档",
    },
    "business_doc": {
        "file_patterns": [r"业务", r"business", r"流程", r"workflow", r"规则", r"rule"],
        "content_signatures": ["业务流程", "业务规则", "状态流转", "审批流程", "workflow", "状态机"],
        "priority": 50,
        "label": "业务规则文档",
    },
    "code": {
        "file_patterns": [r"\.py$", r"\.js$", r"\.ts$", r"\.java$", r"\.go$", r"\.rs$"],
        "content_signatures": ["import ", "def ", "function ", "class ", "package ", "fn "],
        "priority": 30,
        "label": "源代码",
    },
    "postman": {
        "file_patterns": [r"postman.*\.json", r"collection.*\.json"],
        "content_signatures": ['"info"', '"item"', '"request"', '"method"', '"url"', '"_postman_id"'],
        "priority": 88,
        "label": "Postman集合",
    },
    "config": {
        "file_patterns": [r"config\.(ya?ml|json|toml|ini|env)", r"\.env", r"settings\.", r"application\.ya?ml"],
        "content_signatures": ["DB_HOST", "DATABASE_URL", "REDIS_URL", "API_KEY", "SECRET"],
        "priority": 40,
        "label": "配置文件",
    },
}


def classify_document(
    *,
    filename: str = "",
    content_preview: str = "",
    mime_type: str = "",
) -> list[dict[str, Any]]:
    """Auto-classify document type from filename and content.

    Returns list of {type, label, confidence} (multi-label).
    Primary type first, secondary types after.
    """
    results = []
    content_lower = (content_preview or "")[:2000].lower()
    filename_lower = filename.lower()

    for doc_type, info in sorted(DOCUMENT_SIGNATURES.items(),
                                  key=lambda x: x[1]["priority"], reverse=True):
        score = 0
        evidence = []

        # Check file patterns
        for pattern in info["file_patterns"]:
            if re.search(pattern, filename_lower, re.IGNORECASE):
                score += 30
                evidence.append(f"文件名匹配: {pattern}")

        # Check content signatures
        for sig in info["content_signatures"]:
            if sig.lower() in content_lower:
                score += 25
                evidence.append(f"内容特征: {sig}")

        if score >= 25:
            confidence = min(0.95, score / 100)
            results.append({
                "type": doc_type,
                "priority": info["priority"],
                "label": info["label"],
                "confidence": round(confidence, 2),
                "evidence": evidence[:5],
            })

    # Sort by priority * confidence
    results.sort(key=lambda r: r["priority"] * r["confidence"], reverse=True)
    return results


def classify_document_primary(
    *,
    filename: str = "",
    content_preview: str = "",
) -> dict[str, Any]:
    """Return the primary (most likely) document type."""
    results = classify_document(filename=filename, content_preview=content_preview)
    if results:
        return results[0]
    return {"type": "unknown", "priority": 0, "label": "未知文档", "confidence": 0.0}
