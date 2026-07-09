from ai_test_asset_center.customer_report_boundary import (
    CUSTOMER_REPORT_BOUNDARY_TEXT,
    sanitize_customer_report_html,
)


def test_customer_report_boundary_text_does_not_offer_fix_advice() -> None:
    assert "QualiBug-AI 只提供缺陷事实" in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "客户处理后的回归验证" in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "修复建议" not in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "修复方案" not in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "修复代码" not in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "代码改动" in CUSTOMER_REPORT_BOUNDARY_TEXT


def test_customer_report_html_sanitizer_removes_fix_advice_words() -> None:
    html = """
    <h2>修复建议</h2>
    <p>推荐修复方案：修改鉴权代码。</p>
    <pre>recommended_fix: add tenant check</pre>
    <div>remediation plan: patch service</div>
    <span>root cause claim</span>
    """

    sanitized = sanitize_customer_report_html(html)

    assert "修复建议" not in sanitized
    assert "修复方案" not in sanitized
    assert "recommended_fix" not in sanitized
    assert "remediation plan" not in sanitized
    assert "root cause claim" not in sanitized
    assert "客户处理边界" in sanitized
    assert "customer_boundary" in sanitized
