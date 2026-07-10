from __future__ import annotations

from types import SimpleNamespace

from ai_test_asset_center.v12_pipeline import _encoded_request_url, _execution_approval_contract


def test_nonproduction_type_governs_write_while_url_remains_target_identity(tmp_path) -> None:
    campaign = SimpleNamespace(environment_ref="http://127.0.0.1:8011")
    result = _execution_approval_contract(
        {
            "environment_ref": "http://127.0.0.1:8011",
            "environment_kind": "sandbox",
            "execution_mode": "approved_sandbox_write",
        },
        campaign,
        "http://127.0.0.1:8011",
        tmp_path,
    )

    assert result["status"] == "approved"
    assert result["environment_ref"] == "http://127.0.0.1:8011"
    assert result["environment_kind"] == "sandbox"


def test_production_type_blocks_write_even_when_target_is_loopback(tmp_path) -> None:
    campaign = SimpleNamespace(environment_ref="http://127.0.0.1:8011")
    result = _execution_approval_contract(
        {
            "environment_ref": "http://127.0.0.1:8011",
            "environment_kind": "production",
            "execution_mode": "approved_sandbox_write",
        },
        campaign,
        "http://127.0.0.1:8011",
        tmp_path,
    )

    assert result["status"] == "blocked"
    assert result["code"] == "PRODUCTION_WRITE_BLOCKED"


def test_non_ascii_source_route_is_percent_encoded_without_losing_query_semantics() -> None:
    url = _encoded_request_url(
        "http://127.0.0.1:8011",
        "/api/租户/订单?keyword=测试 value&limit=10",
    )

    assert url == (
        "http://127.0.0.1:8011/api/%E7%A7%9F%E6%88%B7/%E8%AE%A2%E5%8D%95"
        "?keyword=%E6%B5%8B%E8%AF%95%20value&limit=10"
    )
