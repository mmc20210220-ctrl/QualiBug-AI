"""Runtime behaviour of the per-engine hypothesis cap.

Every expectation is expressed against ``_REASONER_MAX_HYPOTHESES_PER_ENGINE``
rather than a hardcoded number.  That constant is the runtime authority (see
``policy_wiring._enforce_stage_reasoner_static_cap``), so binding the tests to it
means raising or lowering the product cap can never leave these tests asserting a
value the product no longer runs.
"""

import ai_test_asset_center.stage_reason_all_v2 as stage_reason_all_v2
from ai_test_asset_center.policy_wiring import (
    _REASONER_MAX_HYPOTHESES_PER_ENGINE as CAP,
    _clamp_reasoner_hypothesis_cap,
    _reasoner_hypothesis_cap,
)


def test_reasoner_hypothesis_cap_defaults_to_product_limit():
    assert _clamp_reasoner_hypothesis_cap(None, CAP) == CAP


def test_reasoner_hypothesis_cap_rejects_values_above_product_limit():
    assert _clamp_reasoner_hypothesis_cap(CAP * 20, CAP) == CAP
    assert _clamp_reasoner_hypothesis_cap(str(CAP * 20 + 9), CAP) == CAP


def test_reasoner_hypothesis_cap_keeps_valid_smaller_policy_value():
    smaller = max(1, CAP // 2)
    assert _clamp_reasoner_hypothesis_cap(smaller, CAP) == smaller


def test_reasoner_hypothesis_cap_recovers_from_invalid_value():
    assert _clamp_reasoner_hypothesis_cap("bad", CAP) == CAP


def test_environment_override_is_still_bounded(monkeypatch):
    monkeypatch.setenv("QUALIBUG_REASONER_MAX_HYPOTHESES_PER_ENGINE", str(CAP * 20))
    assert _reasoner_hypothesis_cap(max(1, CAP // 2), CAP) == CAP


def test_environment_override_can_only_reduce_the_cap(monkeypatch):
    monkeypatch.setenv("QUALIBUG_REASONER_MAX_HYPOTHESES_PER_ENGINE", "4")
    assert _reasoner_hypothesis_cap(CAP, CAP) == 4


def test_loaded_reasoner_static_limits_are_clamped_by_main_policy_path(monkeypatch):
    # monkeypatch.setattr restores the module constants after the test; the
    # previous version assigned them directly and left the whole session
    # running at the clamped value.
    widened = CAP * 20
    monkeypatch.setattr(stage_reason_all_v2, "MAX_HYPOTHESES", widened)
    monkeypatch.setattr(stage_reason_all_v2, "MAX_HYPOTHESES_HARD_LIMIT", widened)

    assert _reasoner_hypothesis_cap(widened, CAP) == CAP
    assert stage_reason_all_v2.MAX_HYPOTHESES == CAP
    assert stage_reason_all_v2.MAX_HYPOTHESES_HARD_LIMIT == CAP
