from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.runtime_materialization_messages import (
    materialization_reason_message,
)


def test_known_materialization_reasons_have_readable_chinese_messages() -> None:
    assert materialization_reason_message(
        "RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_MISSING"
    ) == "运行实例缺少必填动态值绑定"
    assert materialization_reason_message(
        "RUNTIME_MATERIALIZATION_PRODUCTION_WRITE_FORBIDDEN"
    ) == "生产环境写入被安全策略禁止"
    assert materialization_reason_message(
        "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_HAS_NO_VALUE_SOURCE"
    ) == "测试数据绑定没有Fixture、实体、值引用、Literal或Generator来源"


def test_unknown_reason_remains_visible_without_becoming_a_resolution() -> None:
    assert materialization_reason_message("RUNTIME_MATERIALIZATION_NEW_REASON") == (
        "RUNTIME MATERIALIZATION NEW REASON"
    )
