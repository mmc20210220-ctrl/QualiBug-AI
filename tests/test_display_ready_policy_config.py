import json

from ai_test_asset_center import display_ready_formatter


def test_display_ready_technical_guidance_comes_from_policy_config(monkeypatch, tmp_path):
    policy_path = tmp_path / "display_ready_policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "root_cause_by_risk_type": {
                    "custom_contract_risk": "policy root cause for {risk_type}",
                },
                "fix_by_risk_type": {
                    "custom_contract_risk": "policy fix for {method} {path}",
                },
                "defaults": {
                    "possible_root_cause": "default root cause {risk_type}",
                    "recommended_fix": "default fix {risk_type}",
                },
                "regression_suggestion_templates": [
                    "policy regression {method} {path}",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(display_ready_formatter, "DISPLAY_READY_POLICY_PATH", policy_path)
    display_ready_formatter._display_ready_policy.cache_clear()

    details = display_ready_formatter._build_technical_details(
        {
            "risk_type": "custom_contract_risk",
            "_api_method": "post",
            "_api_path": "/orders/{id}/cancel",
        },
        {},
        {},
    )

    # Policy templates still drive regression verification obligations, but
    # customer-facing fix/root-cause fields are stripped at the formatter SSOT.
    assert "possible_root_cause" not in details
    assert "recommended_fix" not in details
    assert "regression_suggestions" not in details
    assert details["regression_verification_obligations"] == [
        "policy regression POST /orders/{id}/cancel"
    ]
    assert details["product_responsibility_boundary"]["no_fix_advice"] is True
    assert details["api_endpoint"]["method"] == "POST"
    assert details["api_endpoint"]["path"] == "/orders/{id}/cancel"

    display_ready_formatter._display_ready_policy.cache_clear()
