from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from ai_test_asset_center import professional_ui_visual_baseline as visual
from ai_test_asset_center.professional_ui_visual_registry_binding import (
    _REGISTRY_REQUIRED,
    install_visual_registry_binding,
)


def _png() -> bytes:
    image = Image.new("RGBA", (8, 8), (255, 255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _step(data: bytes) -> dict[str, object]:
    return {
        "action": visual.ACTION,
        "baseline_ref": "visual_baselines/orders.png",
        "baseline_sha256": hashlib.sha256(data).hexdigest(),
        "max_changed_pixel_ratio": 0.0,
        "channel_tolerance": 0,
        "full_page": False,
        "animations_disabled": True,
        "renderer_profile": "chromium_css_scale_v1",
        "scroll_origin": "document_start",
        "font_readiness": "document_fonts_ready",
        "viewport_width": 1280,
        "viewport_height": 720,
        "mask_selectors": [],
        "mask_locator_intents": [],
        "mask_regions": [],
    }


def test_low_level_image_read_remains_independent_but_formal_scope_requires_registry(
    tmp_path: Path,
) -> None:
    install_visual_registry_binding()
    data = _png()
    path = (
        tmp_path
        / "platform_inputs"
        / "visual-project"
        / "visual_baselines"
        / "orders.png"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    step = _step(data)
    runtime_token = visual._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": "visual-project",
    })
    try:
        loaded, digest = visual._baseline_bytes(step)
        assert loaded == data
        assert digest == hashlib.sha256(data).hexdigest()

        required_token = _REGISTRY_REQUIRED.set(True)
        try:
            with pytest.raises(
                visual.VisualBaselineObservationError,
                match=r"^UI_VISUAL_BASELINE_NOT_ACTIVE$",
            ):
                visual._baseline_bytes(step)
        finally:
            _REGISTRY_REQUIRED.reset(required_token)
    finally:
        visual._RUNTIME_CONTEXT.reset(runtime_token)
