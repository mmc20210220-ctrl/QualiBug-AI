from ai_test_asset_center.customer_report_boundary import (
    CUSTOMER_REPORT_BOUNDARY_TEXT,
    data_contract_product_responsibility_boundary,
    product_responsibility_boundary,
    sanitize_customer_report_html,
    strip_fix_advice_fields,
)


def test_customer_report_boundary_text_does_not_offer_fix_advice() -> None:
    assert "QualiBug-AI 只提供缺陷事实" in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "客户处理后的回归验证" in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "修复建议" not in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "修复方案" not in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "修复代码" not in CUSTOMER_REPORT_BOUNDARY_TEXT
    assert "代码改动" in CUSTOMER_REPORT_BOUNDARY_TEXT


def test_shared_product_responsibility_boundary_contract() -> None:
    boundary = product_responsibility_boundary("unit_test")
    contract = data_contract_product_responsibility_boundary("unit_test")

    assert boundary["contract_version"] == "product_responsibility_boundary.v1"
    assert boundary["scope"] == "defect_discovery_evidence_post_customer_change_regression_release_status"
    assert boundary["no_fix_advice"] is True
    assert boundary["source"] == "unit_test"
    assert "客户自行负责系统变更" in boundary["customer_owns"]
    assert "QualiBug-AI 负责缺陷事实" in boundary["qualibug_owns"]
    assert contract["display_key"] == "product_responsibility_boundary"
    assert contract["contract_version"] == boundary["contract_version"]
    assert contract["source"] == "unit_test"
    assert "must not contain fix advice" in contract["honesty_rule"]


def test_strip_fix_advice_fields_removes_nested_repair_fields() -> None:
    payload = {
        "recommended_fix": "add tenant filter",
        "evidence": {"actual": "HTTP 200"},
        "nested": {"remediation_plan": "patch service", "release_gate": {"overall_status": "fail"}},
        "items": [{"code_fix": "diff", "regression": {"latest_status": "failed"}}],
    }

    stripped = strip_fix_advice_fields(payload)

    assert "recommended_fix" not in stripped
    assert stripped["evidence"]["actual"] == "HTTP 200"
    assert "remediation_plan" not in stripped["nested"]
    assert stripped["nested"]["release_gate"]["overall_status"] == "fail"
    assert "code_fix" not in stripped["items"][0]
    assert stripped["items"][0]["regression"]["latest_status"] == "failed"


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
