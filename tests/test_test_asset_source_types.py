from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center._common import SOURCE_TYPES
from ai_test_asset_center.private_pilot_project_assets import (
    KNOWLEDGE_INGEST_SOURCE_TYPES,
    resolve_knowledge_source_type,
)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("testcase", "test_case"),
        ("testcases", "test_case"),
        ("test_case_document", "test_case"),
        ("testplan", "test_plan"),
        ("testreport", "test_report"),
    ],
)
def test_test_asset_aliases_resolve_through_canonical_source_vocabulary(
    requested: str,
    expected: str,
) -> None:
    detected, resolution = resolve_knowledge_source_type(
        "测试资料.xlsx",
        "",
        requested,
    )

    assert detected == expected
    assert resolution == "explicit"
    assert expected in SOURCE_TYPES
    assert expected in KNOWLEDGE_INGEST_SOURCE_TYPES


def test_automatic_source_resolution_still_requires_no_user_choice() -> None:
    detected, resolution = resolve_knowledge_source_type(
        "订单测试用例.xlsx",
        "用例编号 用例名称 测试步骤 预期结果",
        None,
    )

    assert resolution == "automatic"
    assert detected in KNOWLEDGE_INGEST_SOURCE_TYPES
