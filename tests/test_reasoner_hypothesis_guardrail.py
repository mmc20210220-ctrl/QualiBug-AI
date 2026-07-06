from ai_test_asset_center.policy_wiring import _clamp_reasoner_hypothesis_cap


def test_reasoner_hypothesis_cap_defaults_to_product_limit():
    assert _clamp_reasoner_hypothesis_cap(None, 15) == 15


def test_reasoner_hypothesis_cap_rejects_values_above_product_limit():
    assert _clamp_reasoner_hypothesis_cap(300, 15) == 15
    assert _clamp_reasoner_hypothesis_cap("999", 15) == 15


def test_reasoner_hypothesis_cap_keeps_valid_smaller_policy_value():
    assert _clamp_reasoner_hypothesis_cap(6, 15) == 6


def test_reasoner_hypothesis_cap_recovers_from_invalid_value():
    assert _clamp_reasoner_hypothesis_cap("bad", 15) == 15
