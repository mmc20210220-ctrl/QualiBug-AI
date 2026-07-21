"""Scoring: BEI/BDS/BCS, evidence gate, reproducibility confidence."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ._common import *  # noqa: F401,F403
from ._evidence import *  # noqa: F401,F403
from ._quality import *  # noqa: F401,F403
from ._display import *  # noqa: F401,F403


def _compute_scores(findings: list[dict], raw: dict | None = None) -> dict:
    """计算 BEI / BDS / BCS 评分。

    商业化口径：证据可信度只能来自 display-ready 后的证据门控结果，
    不能用原始报告中的高分或 finding 数量把“线索”包装成“已复现缺陷”。
    """
    raw = raw or {}
    if not findings:
        return {"bei": 0, "bds": "0.0", "bcs": 0, "evidence_trust_score": 0}

    # BEI
    base = 50
    p0_weight, p1_weight, p2_weight = 10, 5, 2
    max_weight = max(len(findings), 1) * p0_weight
    total_weight = sum(
        p0_weight if f.get("severity") == "P0"
        else p1_weight if f.get("severity") == "P1"
        else p2_weight
        for f in findings
    )
    bei = round(max(5, min(95, base + (total_weight / max_weight) * 45)), 1)

    # BDS
    p0p1 = sum(1 for f in findings if f.get("severity") in ("P0", "P1"))
    exec_summary = raw.get("executive_summary") or {}
    oracles = exec_summary.get("oracle_count") or len(exec_summary.get("recommended_oracles") or []) or len(findings)
    paths = oracles * 8
    bds = f"{((p0p1 / max(paths, 1)) * 1000):.1f}"

    # BCS 仍保留业务覆盖口径，但不再反推证据可信度。
    db_hit_rate = _deep_get(raw, "db_verification", "hit_rate", default=0) or 0
    bcs = round(min(98, 60 + db_hit_rate + len(findings) * 1.5), 1)

    def _quality_score(item: dict) -> float:
        quality = item.get("evidence_quality") if isinstance(item.get("evidence_quality"), dict) else {}
        try:
            return max(0.0, min(100.0, float(quality.get("score") or 0)))
        except Exception:
            return 0.0

    status_weight = {
        "reproduced": 1.0,
        "suspected": 0.65,
        "risk_clue": 0.35,
        "not_reproduced": 0.0,
    }
    weighted_quality = []
    for item in findings:
        status = str(item.get("bug_status") or "risk_clue")
        weighted_quality.append(_quality_score(item) * status_weight.get(status, 0.35))
    evidence_trust = round(sum(weighted_quality) / max(len(weighted_quality), 1))

    raw_trust = _deep_get(raw, "value_metrics", "evidence_trust_score", default=0) or 0
    try:
        raw_trust = round(float(raw_trust) * 100) if float(raw_trust) <= 1 else round(float(raw_trust))
    except Exception:
        raw_trust = 0
    # 原始分只能作为上限，不能把 display-ready 证据门控后的低可信度抬高。
    if raw_trust:
        evidence_trust = min(evidence_trust, max(0, min(100, raw_trust)))

    return {"bei": bei, "bds": bds, "bcs": bcs, "evidence_trust_score": max(0, min(100, evidence_trust))}


# ═══════════════════════════════════════════════════════════════════════
# 商业价值计算（从前端 computeCommercialValue 迁移）
# ═══════════════════════════════════════════════════════════════════════

def _compute_commercial_value(findings: list[dict], raw: dict | None = None) -> dict:
    """计算商业价值指标和决策卡片"""
    raw = raw or {}
    exec = raw.get("executive_summary") or {}
    runtime = raw.get("runtime_verification") or {}
    value_metrics = raw.get("value_metrics") or {}
    discovery_funnel = raw.get("discovery_funnel") or {}
    capability_matrix = raw.get("full_spectrum_capability_matrix") or {}
    family_coverage = raw.get("bug_family_coverage") or {}

    p0 = int(exec.get("critical_bugs") or sum(1 for f in findings if f.get("severity") == "P0") or 0)
    p1 = int(exec.get("high_priority_bugs") or sum(1 for f in findings if f.get("severity") == "P1") or 0)
    computed_scores = _compute_scores(findings, raw)
    evidence_trust = computed_scores["evidence_trust_score"]
    ai_test_points = value_metrics.get("ai_equivalent_test_points") or exec.get("llm_powered_analyses") or runtime.get("total_probes") or len(findings)
    explored = discovery_funnel.get("explored_paths") or discovery_funnel.get("total_candidates") or runtime.get("total_probes") or ai_test_points
    capability_families = capability_matrix.get("total_capabilities") or capability_matrix.get("covered_capabilities") or len(capability_matrix) or 0
    bug_families = family_coverage.get("covered_families") or family_coverage.get("total_families") or len(family_coverage) or 0
    blocked_risk_count = p0 + p1

    return {
        "executive_message": (
            f"已提前暴露 {blocked_risk_count} 个会影响收入、履约或上线验收的高优先级风险。"
            if blocked_risk_count > 0
            else "当前没有确认的 P0/P1 阻断项，可作为上线评审的正向证据。"
        ),
        "ai_equivalent_test_points": int(ai_test_points or 0),
        "evidence_trust_score": max(0, min(100, int(evidence_trust) or 0)),
        "explored_behavior_paths": int(explored) or 0,
        "blocked_risk_count": blocked_risk_count,
        "capability_families": int(capability_families) or 0,
        "bug_families": int(bug_families) or 0,
        "decision_cards": [
            {
                "role": "管理层",
                "title": "用证据把风险前置" if blocked_risk_count > 0 else "用证据降低上线不确定性",
                "value": f"{blocked_risk_count} 个高优先级风险" if blocked_risk_count > 0 else f"{len(findings)} 个已验证发现",
                "detail": "把上线争议转为可复现证据与可分派整改清单，减少返工与客户投诉。",
            },
            {
                "role": "业务负责人",
                "title": "把业务规则沉淀为持续验证",
                "value": f"{int(ai_test_points or 0):,} 个验证覆盖点",
                "detail": "PRD、接口、DB 与权限规则沉淀为检测基线，变更后自动回归核验。",
            },
            {
                "role": "技术负责人",
                "title": "证据链可复现、可追溯",
                "value": f"{max(0, min(100, int(evidence_trust) or 0))}% 证据可信度",
                "detail": "每条结论关联请求链路、关键状态与数据一致性证据，便于快速定位与复现。",
            },
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# 统一 Bug 状态机（四态）+ 证据完备度门控
# ═══════════════════════════════════════════════════════════════════════

BUG_STATUS_META = {
    "reproduced": {"label": "已复现", "description": "有完整证据链，可重复复现，可直接交付研发修复"},
    "suspected": {"label": "疑似", "description": "有异常信号和部分证据，但缺少关键复现证据或断言，需人工确认"},
    "risk_clue": {"label": "风险线索", "description": "规则/模型/静态分析发现潜在风险，未真实复现，需继续验证"},
    "not_reproduced": {"label": "未复现", "description": "执行过但未触发异常，或证据不足以证明问题存在"},
}


def _compute_bug_status(
    finding: dict,
    evidence_quality: dict,
    evidence_completeness: dict,
) -> dict:
    """计算统一 Bug 状态（四态：已复现/疑似/风险线索/未复现）。

    状态判定逻辑（从高到低优先级）：
    1. reproduced: 有真实执行 + 完备度≥4/6 + 预期实际对比 + API响应或DB证据
    2. suspected: 有部分运行时证据，但缺少关键复现证据或断言
    3. risk_clue: 规则/模型发现潜在风险，无真实复现
    4. not_reproduced: 执行过但未触发异常，或被明确标记为 falsified/rejected
    """
    quality_level = evidence_quality.get("level", "needs_evidence")
    can_reproduce = evidence_quality.get("can_reproduce", False)
    present_count = evidence_completeness.get("present_count", 0)

    dims = {d["key"]: d["present"] for d in evidence_completeness.get("dimensions", [])}
    has_api_response = dims.get("api_response", False)
    has_db_evidence = dims.get("db_evidence", False)
    has_reproduction = dims.get("reproduction", False)
    has_rule = dims.get("rule_source", False)
    has_runtime_response = _has_runtime_response(finding)
    has_anomaly = _has_anomaly_signal(finding)

    has_expected = bool(_clean(finding.get("expected_behavior") or finding.get("expected")))
    has_actual = bool(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))

    raw_status = _clean(finding.get("status") or finding.get("verdict") or finding.get("bug_confirmation")).lower()

    # 明确被标记为未复现/被驳回
    if any(t in raw_status for t in ("falsified", "not_reproduced", "rejected", "false_positive")):
        status = "not_reproduced"
    # 有真实运行时证据 → 可能是 reproduced 或 suspected
    elif can_reproduce or has_runtime_response or has_reproduction:
        if present_count >= 4 and has_expected and has_actual and has_anomaly and (has_api_response or has_db_evidence):
            status = "reproduced"
        else:
            status = "suspected"
    elif present_count >= 2:
        status = "suspected"
    elif has_rule or quality_level == "needs_evidence":
        status = "risk_clue"
    else:
        status = "risk_clue"

    return {
        "status": status,
        "label": BUG_STATUS_META[status]["label"],
        "description": BUG_STATUS_META[status]["description"],
        "is_reproducible": status == "reproduced",
        "gate_passed": status == "reproduced",
    }


def _check_claim_evidence_consistency(finding: dict) -> list[str]:
    """声明-证据一致性检查：finding 声称的异常与实际运行证据是否矛盾。

    检查维度（通用，全行业适用）：
    1. HTTP 状态码矛盾：声称 4xx/5xx 但实际 2xx
    2. 响应体矛盾：声称"错误/失败/异常"但响应体是正常业务数据（无 error/code 字段）
    3. 声称"可复现/已确认"但无任何异常信号（2xx 响应 + 无错误字段 + 无 DB 违规）
    4. 认证错误被误判为 Bug：401/403 是认证/授权失败，如果 finding 声称的不是
       认证类问题，则证据不支持该结论——可能是扫描器遇到认证墙
    5. 认证端点返回 401 是预期行为：/auth/login 等端点返回 401 表示系统正确
       拒绝无效凭证，不构成 Bug

    返回矛盾原因列表（空列表表示无矛盾）。
    """
    contradictions: list[str] = []

    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    actual_status = har.get("status_code") or 0
    actual_body = _clean(har.get("response_body"))

    import re as _re
    import json as _json

    claim_text = " ".join([
        str(finding.get("title") or ""),
        str(finding.get("description") or ""),
        str(finding.get("actual_behavior") or finding.get("actual") or ""),
    ])

    # ── 1. HTTP 状态码矛盾检查 ──
    if actual_status and actual_status > 0:
        claimed_codes = set(int(m) for m in _re.findall(r'(?:服务端|返回|HTTP\s*|状态码\s*)?(\d{3})', claim_text)
                            if 400 <= int(m) <= 599)
        if claimed_codes and 200 <= actual_status < 300:
            contradictions.append(
                f"声明-证据矛盾：声称服务端{'/'.join(str(c) for c in sorted(claimed_codes))}，"
                f"但实际响应 {actual_status}（成功），异常未复现"
            )

    # ── 2. 响应体矛盾检查：声称错误但响应体无错误标识 ──
    if actual_status and 200 <= actual_status < 300 and actual_body:
        # 声称文本中有错误关键词（通用，非业务概念）
        error_keywords = ("错误", "失败", "异常", "崩溃", "error", "fail", "crash", "500", "exception")
        claims_error = any(kw in claim_text.lower() for kw in error_keywords)
        if claims_error:
            # 检查响应体是否真的包含错误标识
            body_has_error = False
            try:
                body_obj = _json.loads(actual_body) if isinstance(actual_body, str) else actual_body
                if isinstance(body_obj, dict):
                    body_has_error = bool(
                        body_obj.get("error") or body_obj.get("error_code")
                        or body_obj.get("message") and any(kw in str(body_obj.get("message", "")).lower() for kw in ("error", "fail", "错误", "失败"))
                    )
            except Exception:
                # 非 JSON 响应体，检查是否包含错误关键词
                body_has_error = any(kw in actual_body.lower() for kw in ("error", "fail", "exception", "错误", "失败"))
            if not body_has_error:
                contradictions.append(
                    f"声明-证据矛盾：声称存在错误/异常，但实际响应 {actual_status} 成功且响应体无错误标识"
                )

    # ── 3. 声称"可复现/已确认"但无异常信号 ──
    raw_status = _clean(finding.get("status") or finding.get("verdict") or finding.get("bug_confirmation")).lower()
    claims_confirmed = any(t in raw_status for t in ("confirmed", "reproduced", "validated", "已复现", "已确认"))
    if claims_confirmed and actual_status and 200 <= actual_status < 300:
        # 2xx 语义违规同样属于有效异常信号，不能只用 DB 违规判定。
        has_anomaly_signal = _has_anomaly_signal(finding)
        if not has_anomaly_signal:
            contradictions.append(
                f"声明-证据矛盾：状态标记为'{raw_status}'（已复现），"
                f"但实际响应 {actual_status} 成功且无 DB 违规或其他异常信号"
            )

    # ── 4. 认证错误被误判为 Bug ──
    # 401/403 是认证/授权失败的标准 HTTP 状态码（通用，非业务概念）。
    # 如果 finding 声称的不是认证/授权类问题，但实际响应是 401/403，
    # 说明证据不支持该结论——可能是扫描器遇到认证墙，把认证失败误当 Bug。
    if actual_status in (401, 403):
        auth_keywords = (
            "auth", "login", "permission", "权限", "认证", "授权", "登录",
            "越权", "unauthorized", "forbidden", "credential", "token",
            "session", "会话", "身份",
        )
        claim_is_auth = any(kw in claim_text.lower() for kw in auth_keywords)
        if not claim_is_auth:
            path = _clean(har.get("path") or finding.get("_api_path") or finding.get("path") or "")
            contradictions.append(
                f"声明-证据矛盾：实际响应 {actual_status}（认证/授权失败），"
                f"但 finding 声称的是非认证类问题"
                f"{'（接口 ' + path + '）' if path else ''}——"
                f"证据可能是扫描器遇到认证墙，不代表所声称的缺陷存在"
            )

    # ── 5. 认证端点返回 401 是预期行为 ──
    # /auth/login、/signin、/token 等端点返回 401 表示系统正确拒绝无效凭证，
    # 这是正常的安全行为，不构成 Bug。
    if actual_status == 401:
        path = _clean(har.get("path") or finding.get("_api_path") or finding.get("path") or "")
        if path:
            # 通用 HTTP 认证端点路径模式（非业务概念）
            auth_endpoint_patterns = ("/auth/", "/login", "/signin", "/token", "/oauth")
            is_auth_endpoint = any(p in path.lower() for p in auth_endpoint_patterns)
            if is_auth_endpoint:
                # 检查响应体是否确实是"无效凭证"类错误
                is_credential_rejection = False
                if actual_body:
                    credential_keywords = (
                        "invalid", "incorrect", "wrong", "expired", "missing",
                        "credentials", "password", "token", "unauthorized",
                        "无效", "错误", "过期", "缺失", "凭证", "密码",
                    )
                    is_credential_rejection = any(kw in actual_body.lower() for kw in credential_keywords)
                if is_credential_rejection:
                    contradictions.append(
                        f"声明-证据矛盾：{path} 返回 401 是认证端点的预期行为"
                        f"（系统正确拒绝了无效凭证），不构成 Bug"
                    )

    return contradictions


def _enforce_evidence_gate(
    finding: dict,
    bug_status: dict,
    evidence_completeness: dict,
) -> dict:
    """证据门控：声明-证据一致性 + 证据完备度双重检查。

    1. 声明-证据一致性（所有状态都检查）：
       如果 finding 声称 HTTP 错误但实际响应是 2xx 成功 → 降级为 not_reproduced
    2. 证据完备度（仅 reproduced 状态检查）：
       不满足"已复现"门槛的自动降级为"疑似"。
    """
    # ── 1. 声明-证据一致性检查（所有状态都执行）──
    contradictions = _check_claim_evidence_consistency(finding)
    contradictions.extend(_runtime_relevance_mismatch_reasons(finding))
    contradictions.extend(_path_mismatch_reasons(finding))
    if contradictions:
        return {
            "status": "not_reproduced",
            "label": BUG_STATUS_META["not_reproduced"]["label"],
            "description": f"声明与证据矛盾：{'；'.join(contradictions)}",
            "is_reproducible": False,
            "gate_passed": False,
            "gate_failures": contradictions,
        }

    # ── 1.5. evidence_consistency.verdict 检查 ──
    evidence_consistency = finding.get("evidence_consistency") or {}
    if isinstance(evidence_consistency, dict):
        ec_verdict = str(evidence_consistency.get("verdict") or "").lower()
        if ec_verdict in ("rejected", "missing", "inconsistent"):
            return {
                "status": "not_reproduced",
                "label": BUG_STATUS_META["not_reproduced"]["label"],
                "description": f"证据一致性审查未通过：{ec_verdict}",
                "is_reproducible": False,
                "gate_passed": False,
                "gate_failures": [f"evidence_consistency.verdict = {ec_verdict}"],
            }

    # ── 1.6. 执行阻断检查（route/auth/environment blocked）──
    execution_block_reason = (
        _clean(finding.get("execution_block") or finding.get("block_reason") or
               finding.get("route_blocked_reason") or finding.get("_block_reason") or
               (evidence_consistency.get("block_reason") if isinstance(evidence_consistency, dict) else ""))
    )
    value_lane = _clean(finding.get("value_lane") or finding.get("_value_lane") or "")
    if not execution_block_reason and value_lane:
        execution_block_reason = value_lane
    block_keywords = ("route_blocked", "auth_blocked", "environment_blocked",
                      "coverage_gap", "validation_lead", "not_reproduced")
    if execution_block_reason and any(kw in str(execution_block_reason).lower() for kw in block_keywords):
        return {
            "status": "not_reproduced",
            "label": BUG_STATUS_META["not_reproduced"]["label"],
            "description": f"执行/环境阻断，不能作为客户交付 Bug：{execution_block_reason}",
            "is_reproducible": False,
            "gate_passed": False,
            "gate_failures": [f"blocked: {execution_block_reason}"],
        }

    # ── 2. 证据完备度门控（仅 reproduced 状态检查）──
    if bug_status["status"] != "reproduced":
        return bug_status

    dims = {d["key"]: d["present"] for d in evidence_completeness.get("dimensions", [])}
    has_api_response = dims.get("api_response", False)
    has_db_evidence = dims.get("db_evidence", False)
    has_expected = bool(_clean(finding.get("expected_behavior") or finding.get("expected")))
    has_actual = bool(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))
    present_count = evidence_completeness.get("present_count", 0)

    gate_failures: list[str] = []
    if present_count < 4:
        gate_failures.append(f"证据完备度不足（{present_count}/6，需≥4）")
    if not has_expected:
        gate_failures.append("缺少预期结果")
    if not has_actual:
        gate_failures.append("缺少实际结果")
    if not (has_api_response or has_db_evidence):
        gate_failures.append("缺少API响应或DB证据")

    if gate_failures:
        return {
            "status": "suspected",
            "label": BUG_STATUS_META["suspected"]["label"],
            "description": f"证据门控未通过：{'；'.join(gate_failures)}",
            "is_reproducible": False,
            "gate_passed": False,
            "gate_failures": gate_failures,
        }

    return bug_status


def _compute_reproducibility_confidence(finding: dict, bug_status: dict, evidence_quality: dict) -> float:
    """计算复现置信度（0-1）。

    基于：bug状态 + 证据质量分 + 复现率 + 运行时证据
    """
    if bug_status["status"] == "not_reproduced":
        return 0.0
    if bug_status["status"] == "risk_clue":
        return 0.1

    score = evidence_quality.get("score", 0) / 100.0
    can_reproduce = evidence_quality.get("can_reproduce", False)

    # 有运行时证据加权
    runtime_obs = _best_runtime_observation(finding)
    if runtime_obs.get("status_code"):
        score = min(1.0, score + 0.1)
    if can_reproduce:
        score = min(1.0, score + 0.1)

    if bug_status["status"] == "suspected":
        score = min(score, 0.69)  # 疑似上限
    elif bug_status["status"] == "reproduced":
        score = max(score, 0.7)  # 已复现下限

    return round(score, 2)


# ═══════════════════════════════════════════════════════════════════════
# 五层证据模型：摘要 / 业务 / 测试 / 研发 / 原始证据
# ═══════════════════════════════════════════════════════════════════════

def _extract_failed_assertions(finding: dict, reproduction: dict) -> list[dict]:
    """从 finding 提取失败断言列表（通用，不硬编码业务概念）。

    断言来源（均需真实异常信号，非仅有文本描述）：
    1. DB 字段值违反业务约束
    2. API 响应状态码异常（4xx/5xx）
    3. 响应体含错误标识（通用 error/code 字段）
    4. 行为不一致（仅当存在上述异常信号 + expected/actual 对比时才生成）
    """
    assertions: list[dict] = []

    expected = _strip_internal_tags(_clean(finding.get("expected_behavior") or finding.get("expected")))
    actual = _strip_internal_tags(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))

    # 2. DB 约束违规
    db_ev = _extract_verified_db_evidence(finding)
    if db_ev and db_ev.get("violation"):
        assertions.append({
            "type": "db_constraint_violation",
            "label": f"数据库字段 {db_ev.get('table', '')}.{db_ev.get('column', '')} 违反约束",
            "expected": "字段值应符合业务约束",
            "actual": f"当前值 {db_ev.get('value', '')}（{db_ev.get('violation', '')}）",
            "detail": db_ev.get("raw", ""),
        })

    # 3. API 响应状态码异常
    runtime_obs = _best_runtime_observation(finding)
    status_code = _status_code_int(runtime_obs.get("status_code")) if runtime_obs else 0
    body = _runtime_body_excerpt(runtime_obs.get("body"), 1000) if runtime_obs else ""
    if status_code and status_code >= 400:
        assertions.append({
            "type": "http_status_error",
            "label": f"HTTP {status_code} 响应异常",
            "expected": "2xx 成功响应",
            "actual": f"状态码 {status_code}",
            "detail": body[:200] or "响应体未捕获",
        })

    # 4. 响应体含通用错误标识
    if body:
        import json as _json
        try:
            body_obj = _json.loads(body) if isinstance(body, str) else body
            if isinstance(body_obj, dict):
                error_msg = _clean(body_obj.get("error") or body_obj.get("message") or body_obj.get("error_message"))
                error_code = _clean(body_obj.get("code") or body_obj.get("error_code"))
                if error_msg or error_code:
                    assertions.append({
                        "type": "response_error_field",
                        "label": "响应体包含错误信息",
                        "expected": "响应体不应包含错误标识",
                        "actual": f"code={error_code or 'N/A'}, message={error_msg or 'N/A'}",
                        "detail": body[:300],
                    })
        except Exception:
            pass

    # 4. 行为不一致断言（仅当存在真实异常信号 + expected/actual 对比时才生成）
    # 注意：assertions 非空说明已有 DB 违规 / HTTP 错误 / 响应错误等真实异常信号
    if assertions and expected and actual:
        assertions.append({
            "type": "behavior_mismatch",
            "label": "行为不符合预期",
            "expected": expected[:300],
            "actual": actual[:300],
            "detail": "系统实际行为与预期规则不一致（有运行时异常信号佐证）",
        })

    # 不伪造断言——如果没有真实失败断言，返回空列表
    # （预期/实际文本只是描述，不等于"已检测到失败"）
    return assertions


def _build_raw_evidence(finding: dict, reproduction: dict) -> dict:
    """构建原始证据结构（机器可追溯）。

    从 canonical runtime observation / DB / 日志 / 执行记录中提取原始证据，不伪造。
    """
    runtime_obs = _best_runtime_observation(finding)
    inv = finding.get("investigation_guidance") if isinstance(finding.get("investigation_guidance"), dict) else {}
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    original_raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    db_ev = _extract_verified_db_evidence(finding)

    trace_id = _clean(inv.get("trace_id")) or _clean(_deep_get(finding, "evidence", "trace_id"))
    source_file = _clean(_deep_get(finding, "evidence", "source_file") or finding.get("source"))

    # 请求原始数据
    request_raw: dict[str, Any] = {}
    method = _clean(runtime_obs.get("method") or finding.get("_api_method") or "GET").upper()
    path = _clean(runtime_obs.get("path") or finding.get("_api_path") or finding.get("path"))
    if method or (path and not _is_unresolved_path_value(path)):
        request_raw["method"] = method
        if path and not _is_unresolved_path_value(path):
            request_raw["path"] = path
        if runtime_obs.get("source"):
            request_raw["source"] = runtime_obs.get("source")
        if runtime_obs.get("request_url"):
            request_raw["url"] = _clean(runtime_obs.get("request_url"))
        actor = _clean(runtime_obs.get("actor"))
        if actor:
            request_raw["actor"] = actor
        request_body = _runtime_body_excerpt(runtime_obs.get("request_body"), 2000)
        if request_body:
            request_raw["body"] = request_body

    # 响应原始数据
    response_raw: dict[str, Any] = {}
    status_code = _status_code_int(runtime_obs.get("status_code")) if runtime_obs else 0
    response_body = _runtime_body_excerpt(runtime_obs.get("body"), 2000) if runtime_obs else ""
    if status_code or response_body:
        response_raw["status_code"] = status_code
        response_raw["body"] = response_body
        response_raw["duration_ms"] = runtime_obs.get("duration_ms") or 0
        response_raw["source"] = runtime_obs.get("source") or "runtime"

    # DB 快照
    db_snapshot: dict[str, Any] = {}
    if db_ev:
        db_snapshot["table"] = db_ev.get("table", "")
        db_snapshot["column"] = db_ev.get("column", "")
        db_snapshot["value"] = db_ev.get("value", "")
        db_snapshot["violation"] = db_ev.get("violation", "")
        db_snapshot["assertion"] = db_ev.get("db_assertion") or db_ev.get("assertion") or ""
        db_snapshot["business_operation"] = db_ev.get("business_operation") or db_ev.get("operation") or ""
        before_snapshot = db_ev.get("before_db_snapshot") or db_ev.get("before") or {}
        after_snapshot = db_ev.get("after_db_snapshot") or db_ev.get("after") or {}
        if isinstance(before_snapshot, dict) and before_snapshot:
            db_snapshot["before"] = before_snapshot
        if isinstance(after_snapshot, dict) and after_snapshot:
            db_snapshot["after"] = after_snapshot
    elif inv.get("sql_verify"):
        db_snapshot["clue_sql_verify"] = _clean(inv.get("sql_verify"))[:500]

    # 日志
    logs: dict[str, Any] = {}
    if trace_id:
        logs["trace_id"] = trace_id
    if inv.get("log_search"):
        logs["search_hint"] = _clean(inv.get("log_search"))[:300]

    # 执行轨迹
    execution_trace: dict[str, Any] = {}
    if source_file:
        execution_trace["source_file"] = source_file
    if evidence.get("hash"):
        execution_trace["evidence_hash"] = _clean(evidence.get("hash"))

    # UI/browser visual evidence: surface screenshot/trace/HAR artifact refs that
    # the UI execution captured, so the customer evidence chain can show real
    # visual proof — not only HTTP request/response. Data-driven and additive:
    # non-UI findings carry no artifacts and the key is omitted entirely.
    ui_artifacts: list[dict[str, Any]] = []
    ui_result = _deep_get(finding, "raw_evidence", "ui_execution_result")
    if isinstance(ui_result, dict):
        refs = ui_result.get("artifact_refs") if isinstance(ui_result.get("artifact_refs"), list) else []
        types = ui_result.get("artifact_types") if isinstance(ui_result.get("artifact_types"), list) else []
        for index, ref in enumerate(refs):
            ref_text = _clean(ref)
            if not ref_text:
                continue
            type_text = _clean(types[index]) if index < len(types) else ""
            ui_artifacts.append({"type": type_text or "artifact", "ref": ref_text})

    raw_evidence = {
        "request_raw": request_raw,
        "response_raw": response_raw,
        "db_snapshot": db_snapshot,
        "logs": logs,
        "execution_trace": execution_trace,
        "timestamp": _clean(
            finding.get("last_verified_at")
            or finding.get("timestamp")
            or _deep_get(finding, "raw_evidence", "timestamp")
            or finding.get("first_seen_at")
        ),
        "has_real_evidence": bool(response_raw or db_snapshot or trace_id or (request_raw.get("path") and _has_runtime_response(finding))),
    }
    if ui_artifacts:
        raw_evidence["ui_artifacts"] = ui_artifacts
    # Preserve only the governance proof needed by the delivery gate. The
    # formatter must not erase a real cleanup receipt and accidentally demote a
    # governed write, nor copy the full audit payload into customer responses.
    sandbox_write = original_raw.get("sandbox_write") if isinstance(original_raw.get("sandbox_write"), dict) else {}
    cleanup = sandbox_write.get("cleanup") if isinstance(sandbox_write.get("cleanup"), dict) else {}
    if not cleanup and isinstance(evidence.get("cleanup"), dict):
        cleanup = evidence.get("cleanup")
    if cleanup:
        raw_evidence["sandbox_write"] = {
            "cleanup": {
                key: cleanup.get(key)
                for key in ("status", "strategy", "reason", "receipt_ref")
                if cleanup.get(key) not in (None, "")
            },
            "governed_write_receipt_count": int(sandbox_write.get("governed_write_receipt_count") or 0),
        }
    execution_semantics = _clean(
        original_raw.get("execution_semantics")
        or evidence.get("execution_semantics")
        or finding.get("execution_semantics")
    )
    if execution_semantics:
        raw_evidence["execution_semantics"] = execution_semantics
    return raw_evidence


