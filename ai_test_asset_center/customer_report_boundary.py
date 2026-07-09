from __future__ import annotations

"""Customer-facing report responsibility boundary helpers.

QualiBug-AI owns defect discovery, evidence chains, post-customer-change
regression verification, and release status. Customer HTML reports must not
surface remediation guidance, repair plans, code changes, or root-cause claims.
"""

from typing import Any

CUSTOMER_REPORT_BOUNDARY_TEXT = (
    "QualiBug-AI 只提供缺陷事实、证据链、客户处理后的回归验证和发布状态；"
    "不提供客户处理方案、代码改动、根因承诺或开发责任判断。"
)

_FIX_ADVICE_REPLACEMENTS = {
    "修复建议": "客户处理边界",
    "修复方案": "客户处理方案",
    "修复代码": "代码改动",
    "修复指引": "客户处理边界",
    "整改建议": "客户处理边界",
    "根因承诺": "根因结论承诺",
    "recommended_fix": "customer_boundary",
    "fix_advice": "customer_boundary",
    "fix suggestion": "customer boundary",
    "recommended fix": "customer boundary",
    "repair suggestion": "customer boundary",
    "repair plan": "customer handling plan",
    "remediation advice": "customer boundary",
    "remediation plan": "customer handling plan",
    "code fix": "code change",
    "root-cause claim": "root-cause conclusion claim",
    "root cause claim": "root-cause conclusion claim",
}


def sanitize_customer_report_html(value: Any) -> str:
    """Remove customer-facing fix-advice wording from generated report HTML.

    This is intentionally a final delivery-layer guard. It does not change the
    evidence facts, reproduction assets, regression result, or release verdict;
    it only removes wording that could be interpreted as repair guidance or a
    development responsibility claim.
    """
    text = str(value if value is not None else "")
    for source, replacement in _FIX_ADVICE_REPLACEMENTS.items():
        text = text.replace(source, replacement)
        text = text.replace(source.title(), replacement)
        text = text.replace(source.upper(), replacement.upper())
    return text
