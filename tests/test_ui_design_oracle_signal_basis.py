from __future__ import annotations


def test_ui_design_oracle_action_reasons_merge_multiple_buckets() -> None:
    from ai_test_asset_center.ui_design_oracle_signal_basis import build_ui_design_oracle_action_reasons

    distribution = {"token": 3, "role": 2, "keyword": 0, "testid": 0, "none": 0, "other": 0}
    legend = {
        "token": {"recommended_actions": ["补齐/规范 data-testid", "补齐 role/aria-label/name"]},
        "role": {"recommended_actions": ["补齐 role/aria-label/name", "补齐/规范 data-testid"]},
    }
    result = build_ui_design_oracle_action_reasons(distribution, legend, limit=5)
    actions = result.get("action_reasons")
    assert isinstance(actions, list)
    merged = next(item for item in actions if isinstance(item, dict) and item.get("action") == "补齐/规范 data-testid")
    triggered = merged.get("triggered_by")
    assert isinstance(triggered, list)
    buckets = {item.get("bucket") for item in triggered if isinstance(item, dict)}
    assert {"token", "role"}.issubset(buckets)

