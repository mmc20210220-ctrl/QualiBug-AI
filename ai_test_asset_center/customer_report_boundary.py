from __future__ import annotations

"""Customer-facing responsibility boundary helpers.

QualiBug-AI owns defect discovery, evidence chains, post-customer-change
regression verification, and release status. Customer-facing payloads and HTML
reports must not surface remediation guidance, repair plans, code changes, or
root-cause claims.
"""

from copy import deepcopy
from typing import Any

PRODUCT_RESPONSIBILITY_CONTRACT_VERSION = "product_responsibility_boundary.v1"
PRODUCT_RESPONSIBILITY_SCOPE = "defect_discovery_evidence_post_customer_change_regression_release_status"
PRODUCT_RESPONSIBILITY_SOURCE_OF_TRUTH = "ai_test_asset_center.customer_report_boundary"

CUSTOMER_REPORT_BOUNDARY_TEXT = (
    "QualiBug-AI 只提供缺陷事实、证据链、客户处理后的回归验证和发布状态；"
    "不提供客户处理方案、代码改动、根因承诺或开发责任判断。"
)

PRODUCT_RESPONSIBILITY_BOUNDARY: dict[str, Any] = {
    "contract_version": PRODUCT_RESPONSIBILITY_CONTRACT_VERSION,
    "scope": PRODUCT_RESPONSIBILITY_SCOPE,
    "no_fix_advice": True,
    "source": PRODUCT_RESPONSIBILITY_SOURCE_OF_TRUTH,
    "customer_owns": "客户自行负责系统变更、开发实现、代码改动和根因处置决策。",
    "qualibug_owns": "QualiBug-AI 负责缺陷事实、证据链、客户处理后的回归验证和发布/交付状态。",
    "customer_meaning": "QualiBug-AI 只确认问题是否存在、客户处理后是否通过回归验证，以及当前发布/交付状态；不提供客户处理方案、代码改动或根因承诺。",
}

DATA_CONTRACT_PRODUCT_RESPONSIBILITY_BOUNDARY: dict[str, Any] = {
    "display_key": "product_responsibility_boundary",
    "contract_version": PRODUCT_RESPONSIBILITY_CONTRACT_VERSION,
    "source": PRODUCT_RESPONSIBILITY_SOURCE_OF_TRUTH,
    "truth_source": "All customer-visible API envelopes, findings, evidence pages, and HTML reports must carry the same product responsibility boundary.",
    "honesty_rule": "Customer-visible outputs must not contain fix advice, remediation plans, root-cause claims, repair steps, or code changes. The platform verifies defects and post-customer-change closure status only.",
    "customer_meaning": "The customer owns system changes. QualiBug-AI owns defect evidence, post-change regression verification, and release/hand-off status.",
}

FIX_ADVICE_KEYS = frozenset({
    "recommended_fix",
    "fix_advice",
    "fix_suggestion",
    "repair_suggestion",
    "repair_plan",
    "remediation",
    "remediation_advice",
    "remediation_plan",
    "patch_suggestion",
    "code_fix",
    "possible_root_cause",
    "root_cause_hypothesis",
})

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


def product_responsibility_boundary(source: str | None = None) -> dict[str, Any]:
    boundary = deepcopy(PRODUCT_RESPONSIBILITY_BOUNDARY)
    if source:
        boundary["source"] = source
    return boundary


def data_contract_product_responsibility_boundary(source: str | None = None) -> dict[str, Any]:
    contract = deepcopy(DATA_CONTRACT_PRODUCT_RESPONSIBILITY_BOUNDARY)
    if source:
        contract["source"] = source
    return contract


def strip_fix_advice_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [strip_fix_advice_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if str(key) in FIX_ADVICE_KEYS:
            continue
        cleaned[str(key)] = strip_fix_advice_fields(item)
    return cleaned


def attach_product_responsibility_boundary(value: dict[str, Any], source: str | None = None) -> dict[str, Any]:
    existing = value.get("product_responsibility_boundary") if isinstance(value.get("product_responsibility_boundary"), dict) else {}
    value["product_responsibility_boundary"] = {
        **existing,
        **product_responsibility_boundary(source),
    }
    return value


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
