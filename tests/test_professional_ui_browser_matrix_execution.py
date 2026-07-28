from __future__ import annotations

import copy

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center import formal_ui_surface as formal
from ai_test_asset_center import observer_contracts_base as observers
from ai_test_asset_center import professional_ui_browser_matrix as matrix
from ai_test_asset_center import ui_execution_adapter as adapter
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_browser_matrix_guard import (
    AGGREGATION_POLICY,
    SCHEMA_VERSION,
    normalize_browser_matrix,
)
from ai_test_asset_center.professional_ui_browser_matrix_verdict_guard import (
    _rebuild_receipt,
)


def _profile(
    profile_id: str,
    engine: str,
    *,
    device_class: str = "desktop",
    width: int = 1280,
    height: int = 720,
    is_mobile: bool = False,
    has_touch: bool = False,
) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "browser_engine": engine,
        "device_class": device_class,
        "viewport_width": width,
        "viewport_height": height,
        "device_scale_factor": 1,
        "is_mobile": is_mobile,
        "has_touch": has_touch,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "color_scheme": "light",
        "reduced_motion": "no-preference",
        "user_agent": "",
    }


def _matrix() -> dict[str, object]:
    return normalize_browser_matrix({
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "profiles": [
            _profile("chromium-desktop", "chromium"),
            _profile("firefox-desktop", "firefox"),
            _profile(
                "webkit-mobile",
                "webkit",
                device_class="mobile",
                width=390,
                height=844,
                is_mobile=True,
                has_touch=True,
            ),
        ],
    })


def _request() -> dict[str, object]:
    return {
        "request_id": "orders-matrix",
        "title": "Orders matrix",
        "provider": "playwright_browser_plan",
        "start_url": "/orders",
        "execution_mode": "safe_read_only",
        "browser_matrix": _matrix(),
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "/orders"},
                {"action": "expect_visible", "selector": "#orders"},
            ],
        },
        "source_refs": [{"source_id": "src-orders"}],
        "metadata": {"source_declared": True},
    }


def _result(
    status: str,
    *,
    reason: str = "",
    artifact: str = "",
) -> dict[str, object]:
    return {
        "status": status,
        "reason": reason,
        "execution_status": "executed" if status == "executed" else "failed",
        "confirmation_status": "candidate",
        "steps": [{"step_index": 1, "action": "goto"}],
        "artifacts": (
            [{"artifact_type": "screenshot", "ref": artifact}]
            if artifact
            else []
        ),
        "duration_ms": 25,
        "provider_clues": [],
        "findings": [],
    }


def _children(*results: dict[str, object]):
    profiles = _matrix()["profiles"]
    return [
        (copy.deepcopy(profiles[index]), result, {
            "browser_engine": profiles[index]["browser_engine"],
            "browser_version": f"{index + 1}.0",
            "playwright_version": "1.50.0",
        })
        for index, result in enumerate(results)
    ]


def test_request_normalization_preserves_source_declared_matrix() -> None:
    normalized = adapter.normalize_ui_execution_requests([_request()])

    assert len(normalized) == 1
    assert normalized[0]["browser_matrix"]["schema_version"] == SCHEMA_VERSION
    assert len(normalized[0]["browser_matrix"]["profiles"]) == 3


def test_profile_child_has_unique_id_and_profile_owned_configuration() -> None:
    request = _request()
    profile = _matrix()["profiles"][2]

    child = matrix._request_for_profile(request, profile)

    assert child["request_id"] == "orders-matrix__webkit-mobile"
    assert "browser_matrix" not in child
    steps = child["browser_plan"]["steps"]
    assert steps[0] == {"action": "set_viewport", "width": 390, "height": 844}
    assert steps[1]["action"] == "set_media"
    assert steps[1]["color_scheme"] == "light"
    assert steps[2]["action"] == "goto"
    assert child["metadata"]["browser_engine"] == "webkit"
    assert child["metadata"]["device_class"] == "mobile"


def test_all_profiles_must_execute_before_matrix_can_hold() -> None:
    aggregate = matrix._aggregate_result(
        _request(),
        _matrix(),
        _children(
            _result("executed", artifact="chromium/final.png"),
            _result("executed", artifact="firefox/final.png"),
            _result("executed", artifact="webkit/final.png"),
        ),
    )

    receipt = aggregate["browser_matrix_receipt"]
    assert aggregate["status"] == "executed"
    assert receipt["status"] == "ALL_PROFILES_EXECUTED"
    assert receipt["all_profiles_executed"] is True
    assert receipt["typed_violation_profile_count"] == 0
    assert receipt["runtime_failure_profile_count"] == 0
    assert {row["matrix_profile_id"] for row in aggregate["artifacts"]} == {
        "chromium-desktop",
        "firefox-desktop",
        "webkit-mobile",
    }


def test_one_typed_profile_failure_proves_profile_specific_violation() -> None:
    aggregate = matrix._aggregate_result(
        _request(),
        _matrix(),
        _children(
            _result("executed"),
            _result(
                "failed",
                reason="UI_EXPECTATION_UNSATISFIED:expect_visible:target_missing",
            ),
            _result("executed"),
        ),
    )

    receipt = aggregate["browser_matrix_receipt"]
    assert aggregate["status"] == "failed"
    assert receipt["status"] == "VIOLATION_OBSERVED"
    assert receipt["violation_observed"] is True
    assert receipt["typed_violation_profile_count"] == 1
    failed = [row for row in receipt["profiles"] if row["status"] == "failed"]
    assert failed[0]["profile_id"] == "firefox-desktop"
    assert failed[0]["reason_code"] == "UI_EXPECTATION_UNSATISFIED"


def test_browser_runtime_failure_is_not_formal_violation() -> None:
    aggregate = matrix._aggregate_result(
        _request(),
        _matrix(),
        _children(
            _result("executed"),
            _result("failed", reason="TimeoutError:browser process exited"),
            _result("executed"),
        ),
    )

    receipt = aggregate["browser_matrix_receipt"]
    assert aggregate["status"] == "failed"
    assert receipt["status"] == "PROFILE_EXECUTION_FAILED"
    assert receipt["violation_observed"] is False
    assert receipt["typed_violation_profile_count"] == 0
    assert receipt["runtime_failure_profile_count"] == 1
    assert receipt["runtime_failures_are_formal_violations"] is False


def test_blocked_profile_keeps_matrix_incomplete() -> None:
    blocked = _result("blocked", reason="BROWSER_RUNTIME_UNAVAILABLE")
    blocked["execution_status"] = "not_executed"
    aggregate = matrix._aggregate_result(
        _request(),
        _matrix(),
        _children(
            _result("executed"),
            blocked,
            _result("executed"),
        ),
    )

    receipt = aggregate["browser_matrix_receipt"]
    assert aggregate["status"] == "blocked"
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["all_profiles_executed"] is False
    assert receipt["blocked_profile_count"] == 1


class _FakeBrowser:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def new_context(self, **kwargs: object) -> dict[str, object]:
        self.options = kwargs
        return kwargs


def test_firefox_desktop_context_omits_false_mobile_flags() -> None:
    fake = _FakeBrowser()
    proxy = matrix._MatrixBrowser(fake, _profile("firefox", "firefox"))

    proxy.new_context(record_har_path="network.har")

    assert fake.options["viewport"] == {"width": 1280, "height": 720}
    assert "is_mobile" not in fake.options
    assert "has_touch" not in fake.options
    assert fake.options["record_har_path"] == "network.har"


def test_webkit_mobile_context_keeps_touch_and_mobile_identity() -> None:
    fake = _FakeBrowser()
    proxy = matrix._MatrixBrowser(fake, _profile(
        "webkit-mobile",
        "webkit",
        device_class="mobile",
        width=390,
        height=844,
        is_mobile=True,
        has_touch=True,
    ))

    proxy.new_context()

    assert fake.options["is_mobile"] is True
    assert fake.options["has_touch"] is True
    assert fake.options["viewport"] == {"width": 390, "height": 844}


def test_registered_ui_observer_is_matrix_aware_and_receipts_are_recomputed() -> None:
    assert observers._REGISTERED_OBSERVER_HANDLERS[
        formal.OBSERVER_ID
    ] is formal._ui_observer_handler

    original = observers._receipt(
        observer_id=formal.OBSERVER_ID,
        status="OBSERVED",
        evidence={formal.EVIDENCE_KEY: {"expectation_satisfied": True}},
    )
    mutated = copy.deepcopy(original)
    mutated["evidence"][formal.EVIDENCE_KEY]["browser_matrix"] = {
        "schema_version": SCHEMA_VERSION,
        "status": "ALL_PROFILES_EXECUTED",
    }
    rebuilt = _rebuild_receipt(mutated)

    assert rebuilt["receipt_id"] != original["receipt_id"]
    assert observers.validate_observer_receipt(rebuilt) == rebuilt
