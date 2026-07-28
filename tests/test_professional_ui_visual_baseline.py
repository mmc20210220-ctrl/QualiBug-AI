from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from ai_test_asset_center import formal_ui_surface
from ai_test_asset_center import formal_ui_surface_guard
from ai_test_asset_center import professional_ui_readonly
from ai_test_asset_center import professional_ui_visual_baseline as visual
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_contracts import (
    extract_formal_ui_contracts,
)
from ai_test_asset_center.observer_contracts_base import _receipt
from ai_test_asset_center.professional_ui_contract_guard import (
    install_professional_ui_contract_guard,
)
from ai_test_asset_center.professional_ui_coverage_projection import (
    build_professional_ui_coverage,
)
from ai_test_asset_center.professional_ui_responsive_accessibility import (
    install_professional_ui_responsive_accessibility,
)
from ai_test_asset_center.professional_ui_visual_baseline_governance import (
    install_visual_baseline_governance,
)


def _install_visual_chain() -> None:
    formal_ui_surface.install_formal_ui_surface()
    formal_ui_surface_guard.install_formal_ui_read_only_guard()
    professional_ui_readonly.install_professional_ui_readonly()
    install_professional_ui_contract_guard()
    install_professional_ui_responsive_accessibility()
    visual.install_professional_ui_visual_baseline()
    install_visual_baseline_governance()


def _png_bytes(
    width: int = 10,
    height: int = 10,
    *,
    color: tuple[int, int, int, int] = (255, 255, 255, 255),
    changed_pixels: dict[tuple[int, int], tuple[int, int, int, int]] | None = None,
) -> bytes:
    image = Image.new("RGBA", (width, height), color)
    for point, value in (changed_pixels or {}).items():
        image.putpixel(point, value)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _baseline(
    tmp_path: Path,
    *,
    project: str = "visual-project",
    data: bytes | None = None,
) -> tuple[Path, bytes]:
    content = data or _png_bytes()
    path = (
        tmp_path
        / "platform_inputs"
        / project
        / "visual_baselines"
        / "orders.png"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path, content


def _step(data: bytes, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "action": visual.ACTION,
        "baseline_ref": "visual_baselines/orders.png",
        "baseline_sha256": hashlib.sha256(data).hexdigest(),
        "max_changed_pixel_ratio": 0.0,
        "channel_tolerance": 0,
        "full_page": False,
        "animations_disabled": True,
        "mask_selectors": [],
        "mask_locator_intents": [],
        "mask_regions": [],
    }
    row.update(overrides)
    return row


class _EmptyLocator:
    def count(self) -> int:
        return 0

    def nth(self, index: int) -> "_EmptyLocator":
        return self

    def bounding_box(self) -> None:
        return None


class _FakePage:
    def __init__(self, screenshot: bytes) -> None:
        self._screenshot = screenshot
        self.screenshot_calls: list[dict[str, Any]] = []

    def locator(self, selector: str) -> _EmptyLocator:
        return _EmptyLocator()

    def screenshot(self, **kwargs: Any) -> bytes:
        self.screenshot_calls.append(dict(kwargs))
        return self._screenshot


def _execute(
    tmp_path: Path,
    *,
    baseline: bytes,
    current: bytes,
    step: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], _FakePage]:
    _install_visual_chain()
    _baseline(tmp_path, data=baseline)
    page = _FakePage(current)
    runtime_token = visual._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": "visual-project",
        "request_id": "visual-request",
    })
    observations_token = visual._OBSERVATIONS.set([])
    try:
        receipt = visual._execute_visual_baseline(page, step or _step(baseline))
        observations = [dict(row) for row in visual._OBSERVATIONS.get()]
    finally:
        visual._OBSERVATIONS.reset(observations_token)
        visual._RUNTIME_CONTEXT.reset(runtime_token)
    return receipt, observations, page


def test_visual_contract_is_admitted_by_enterprise_parser_without_runtime_import_order() -> None:
    baseline = _png_bytes()
    contract = {
        "contract_id": "visual-orders",
        "operation_ref": "get-orders-page",
        "actor_role": "public",
        "ui_request": {
            "request_id": "visual-orders",
            "provider": "playwright_browser_plan",
            "start_url": "/orders",
            "execution_mode": "safe_read_only",
            "browser_plan": {
                "execution_mode": "safe_read_only",
                "steps": [
                    {"action": "goto", "url": "/orders"},
                    _step(baseline),
                ],
            },
        },
    }

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [contract]}),
        source_id="ui-design-orders",
    )

    assert gaps == []
    assert len(contracts) == 1
    actions = [
        row["action"]
        for row in contracts[0]["ui_request"]["browser_plan"]["steps"]
    ]
    assert actions == ["goto", visual.ACTION]


def test_visual_source_contract_rejects_arbitrary_png_namespace() -> None:
    baseline = _png_bytes()
    contract = {
        "contract_id": "visual-arbitrary-png",
        "operation_ref": "get-orders-page",
        "actor_role": "public",
        "ui_request": {
            "request_id": "visual-arbitrary-png",
            "provider": "playwright_browser_plan",
            "start_url": "/orders",
            "execution_mode": "safe_read_only",
            "browser_plan": {
                "execution_mode": "safe_read_only",
                "steps": [
                    {"action": "goto", "url": "/orders"},
                    _step(baseline, baseline_ref="screenshots/orders.png"),
                ],
            },
        },
    }

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [contract]}),
        source_id="ui-design-orders",
    )

    assert contracts == []
    assert any(
        "browser_visual_baseline_scope_invalid" in requirement
        for requirement in gaps[0]["missing_requirements"]
    )


def test_visual_step_validation_requires_hash_budget_mode_and_governed_scope() -> None:
    _install_visual_chain()
    baseline = _png_bytes()
    valid = _step(baseline)

    visual._validate_visual_step(valid)
    assert valid["baseline_ref"] == "visual_baselines/orders.png"

    with pytest.raises(
        visual._professional._browser.BrowserExecutionError,
        match=r"^browser_visual_baseline_scope_invalid$",
    ):
        visual._validate_visual_step(
            _step(baseline, baseline_ref="other/orders.png")
        )
    with pytest.raises(
        visual._professional._browser.BrowserExecutionError,
        match=r"^browser_visual_baseline_sha256_invalid$",
    ):
        visual._validate_visual_step(_step(baseline, baseline_sha256="bad"))
    invalid_budget = _step(baseline)
    invalid_budget.pop("max_changed_pixel_ratio")
    with pytest.raises(
        visual._professional._browser.BrowserExecutionError,
        match=r"^browser_visual_changed_pixel_budget_missing$",
    ):
        visual._validate_visual_step(invalid_budget)
    with pytest.raises(
        visual._professional._browser.BrowserExecutionError,
        match=r"^browser_visual_animations_must_be_disabled$",
    ):
        visual._validate_visual_step(
            _step(baseline, animations_disabled=False)
        )


def test_identical_visual_baseline_produces_deterministic_observation(
    tmp_path: Path,
) -> None:
    baseline = _png_bytes()

    receipt, observations, page = _execute(
        tmp_path,
        baseline=baseline,
        current=baseline,
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["dimension_match"] is True
    assert receipt["changed_pixel_count"] == 0
    assert receipt["changed_pixel_ratio"] == 0.0
    assert receipt["baseline_sha256"] == hashlib.sha256(baseline).hexdigest()
    assert receipt["raw_pixels_in_receipt"] is False
    assert receipt["ai_visual_judgement_used"] is False
    assert receipt["baseline_auto_updated"] is False
    assert observations == [receipt]
    assert page.screenshot_calls == [{
        "full_page": False,
        "animations": "disabled",
        "caret": "hide",
        "scale": "css",
    }]


def test_visual_pixel_change_over_budget_is_typed_violation(
    tmp_path: Path,
) -> None:
    baseline = _png_bytes()
    current = _png_bytes(
        changed_pixels={(0, 0): (0, 0, 0, 255)},
    )
    _install_visual_chain()
    _baseline(tmp_path, data=baseline)
    page = _FakePage(current)
    runtime_token = visual._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": "visual-project",
    })
    observations_token = visual._OBSERVATIONS.set([])
    try:
        with pytest.raises(
            visual._professional.ProfessionalUIExpectationError,
            match=r"^UI_EXPECTATION_UNSATISFIED:expect_visual_baseline:",
        ):
            visual._execute_visual_baseline(page, _step(baseline))
        observations = [dict(row) for row in visual._OBSERVATIONS.get()]
    finally:
        visual._OBSERVATIONS.reset(observations_token)
        visual._RUNTIME_CONTEXT.reset(runtime_token)

    assert len(observations) == 1
    observation = observations[0]
    assert observation["status"] == "VIOLATION_OBSERVED"
    assert observation["reason_code"] == (
        "UI_VISUAL_CHANGED_PIXEL_BUDGET_EXCEEDED"
    )
    assert observation["changed_pixel_count"] == 1
    assert observation["changed_pixel_ratio"] == pytest.approx(0.01)
    assert observation["change_bounding_box"] == {
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
    }


def test_visual_mask_region_excludes_declared_dynamic_pixels(
    tmp_path: Path,
) -> None:
    baseline = _png_bytes()
    current = _png_bytes(
        changed_pixels={(0, 0): (0, 0, 0, 255)},
    )

    receipt, observations, _page = _execute(
        tmp_path,
        baseline=baseline,
        current=current,
        step=_step(
            baseline,
            mask_regions=[{"x": 0, "y": 0, "width": 1, "height": 1}],
        ),
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["changed_pixel_count"] == 0
    assert receipt["mask_box_count"] == 1
    assert receipt["baseline_mask_applied_count"] == 1
    assert receipt["current_mask_applied_count"] == 1
    assert observations == [receipt]


def test_visual_channel_tolerance_ignores_only_declared_small_delta(
    tmp_path: Path,
) -> None:
    baseline = _png_bytes(color=(100, 100, 100, 255))
    current = _png_bytes(
        color=(100, 100, 100, 255),
        changed_pixels={(0, 0): (105, 100, 100, 255)},
    )

    receipt, _observations, _page = _execute(
        tmp_path,
        baseline=baseline,
        current=current,
        step=_step(baseline, channel_tolerance=5),
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["changed_pixel_count"] == 0
    assert receipt["mean_max_channel_delta"] == pytest.approx(0.05)


def test_visual_dimension_mismatch_is_typed_violation(tmp_path: Path) -> None:
    baseline = _png_bytes(width=10, height=10)
    current = _png_bytes(width=11, height=10)
    _install_visual_chain()
    _baseline(tmp_path, data=baseline)
    page = _FakePage(current)
    runtime_token = visual._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": "visual-project",
    })
    observations_token = visual._OBSERVATIONS.set([])
    try:
        with pytest.raises(
            visual._professional.ProfessionalUIExpectationError,
            match=r"dimension_mismatch$",
        ):
            visual._execute_visual_baseline(page, _step(baseline))
        observation = dict(visual._OBSERVATIONS.get()[0])
    finally:
        visual._OBSERVATIONS.reset(observations_token)
        visual._RUNTIME_CONTEXT.reset(runtime_token)

    assert observation["status"] == "VIOLATION_OBSERVED"
    assert observation["reason_code"] == "UI_VISUAL_DIMENSION_MISMATCH"
    assert observation["dimension_match"] is False
    assert observation["baseline_width"] == 10
    assert observation["current_width"] == 11


def test_missing_or_hash_mismatched_baseline_is_indeterminate(
    tmp_path: Path,
) -> None:
    _install_visual_chain()
    baseline = _png_bytes()
    page = _FakePage(baseline)
    runtime_token = visual._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": "visual-project",
    })
    observations_token = visual._OBSERVATIONS.set([])
    try:
        with pytest.raises(
            visual.VisualBaselineObservationError,
            match=r"^UI_VISUAL_BASELINE_NOT_FOUND$",
        ):
            visual._execute_visual_baseline(page, _step(baseline))
        missing = dict(visual._OBSERVATIONS.get()[0])
    finally:
        visual._OBSERVATIONS.reset(observations_token)
        visual._RUNTIME_CONTEXT.reset(runtime_token)

    assert missing["status"] == "INDETERMINATE"
    assert missing["reason_code"] == "UI_VISUAL_BASELINE_NOT_FOUND"

    _baseline(tmp_path, data=baseline)
    runtime_token = visual._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": "visual-project",
    })
    observations_token = visual._OBSERVATIONS.set([])
    try:
        with pytest.raises(
            visual.VisualBaselineObservationError,
            match=r"^UI_VISUAL_BASELINE_HASH_MISMATCH$",
        ):
            visual._execute_visual_baseline(
                page,
                _step(baseline, baseline_sha256="0" * 64),
            )
        mismatch = dict(visual._OBSERVATIONS.get()[0])
    finally:
        visual._OBSERVATIONS.reset(observations_token)
        visual._RUNTIME_CONTEXT.reset(runtime_token)

    assert mismatch["status"] == "INDETERMINATE"
    assert mismatch["reason_code"] == "UI_VISUAL_BASELINE_HASH_MISMATCH"
    assert mismatch["raw_pixels_in_receipt"] is False


def test_visual_observer_attaches_only_typed_comparison_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_visual_chain()
    observation = {
        "expectation": visual.ACTION,
        "status": "VIOLATION_OBSERVED",
        "reason_code": "UI_VISUAL_CHANGED_PIXEL_BUDGET_EXCEEDED",
        "changed_pixel_count": 1,
        "changed_pixel_ratio": 0.01,
        "raw_pixels_in_receipt": False,
        "ai_visual_judgement_used": False,
    }

    def fake_observer(envelope: dict[str, Any]) -> dict[str, Any]:
        visual._append_observation(observation)
        return _receipt(
            observer_id=formal_ui_surface.OBSERVER_ID,
            status="OBSERVED",
            evidence={
                formal_ui_surface.EVIDENCE_KEY: {
                    "expectation_satisfied": False,
                    "violation_observed": True,
                },
            },
        )

    monkeypatch.setattr(
        formal_ui_surface,
        visual._ORIGINAL_OBSERVER,
        fake_observer,
    )
    receipt = visual._observer_with_visual_receipts({})
    evidence = receipt["evidence"][formal_ui_surface.EVIDENCE_KEY]

    assert evidence["visual_baseline_observation_count"] == 1
    assert evidence["visual_baseline_observations"] == [observation]
    assert evidence["visual_ai_judgement_consumed"] is False
    assert "pixels" not in json.dumps(evidence)


def test_visual_coverage_reports_typed_observation_and_governed_namespace() -> None:
    baseline = _png_bytes()
    obligation_id = "ui-visual-obligation"
    result = {
        "test_obligations": {
            "obligations": [{
                "obligation_id": obligation_id,
                "risk_family": "ui_state_consistency",
                "property": {
                    "ui_request": {
                        "browser_plan": {
                            "steps": [_step(baseline)],
                        },
                    },
                },
            }],
        },
        "experiment_execution": {
            "results": {
                obligation_id: {
                    "obligation_id": obligation_id,
                    "observer_receipts": [{
                        "observer_id": formal_ui_surface.OBSERVER_ID,
                        "status": "OBSERVED",
                        "evidence": {
                            formal_ui_surface.EVIDENCE_KEY: {
                                "visual_baseline_observations": [{
                                    "status": "VIOLATION_OBSERVED",
                                    "reason_code": (
                                        "UI_VISUAL_CHANGED_PIXEL_BUDGET_EXCEEDED"
                                    ),
                                    "dimension_match": True,
                                    "changed_pixel_ratio": 0.01,
                                    "ai_visual_judgement_used": False,
                                }],
                            },
                        },
                    }],
                    "oracle_verdict": {"status": "VIOLATION"},
                },
            },
        },
        "obligation_attempt_ledger": {
            "attempts": [{
                "obligation_id": obligation_id,
                "risk_family": "ui_state_consistency",
                "terminal_status": "DELIVERABLE",
                "reason_code": "",
            }],
        },
    }

    coverage = build_professional_ui_coverage(result)

    visual_dimension = coverage["dimensions"]["visual_regression"]
    assert visual_dimension["declared_contract_count"] == 1
    assert visual_dimension["observed_contract_count"] == 1
    assert visual_dimension["violation_count"] == 1
    assert visual_dimension["deliverable_count"] == 1
    summary = coverage["visual_baseline_contracts"]
    assert summary["declared_visual_contract_count"] == 1
    assert summary["declared_baseline_namespace_counts"] == {
        "visual_baselines": 1,
    }
    assert summary["visual_observation_count"] == 1
    assert summary["comparable_visual_observation_count"] == 1
    assert summary["visual_observation_status_counts"] == {
        "VIOLATION_OBSERVED": 1,
    }
    assert summary["visual_reason_counts"] == {
        "UI_VISUAL_CHANGED_PIXEL_BUDGET_EXCEEDED": 1,
    }
    assert summary["ai_visual_judgement_consumed_count"] == 0
    assert summary["baseline_auto_update_supported"] is False
