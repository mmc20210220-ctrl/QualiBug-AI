from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from ai_test_asset_center import professional_ui_visual_evidence_privacy as privacy


def _png() -> bytes:
    image = Image.new("RGBA", (2, 2), (255, 255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class _EmptyLocator:
    def count(self) -> int:
        return 0

    def nth(self, index: int) -> "_EmptyLocator":
        return self

    def bounding_box(self) -> None:
        return None


class _FakePage:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def locator(self, selector: str) -> _EmptyLocator:
        return _EmptyLocator()

    def screenshot(self, **kwargs: Any) -> bytes:
        self.calls.append(dict(kwargs))
        return _png()


class _FakeContext:
    def __init__(self) -> None:
        self.page = _FakePage()

    def new_page(self) -> _FakePage:
        return self.page


class _FakeBrowser:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.context = _FakeContext()

    def new_context(self, **kwargs: Any) -> _FakeContext:
        self.kwargs = dict(kwargs)
        return self.context


def test_visual_browser_removes_har_and_trace_configuration() -> None:
    raw = _FakeBrowser()
    wrapped = privacy._VisualPrivacyBrowser(
        raw,
        {"mask_selectors": [], "mask_locator_intents": [], "mask_regions": []},
    )

    context = wrapped.new_context(
        record_har_path="network.har",
        record_har_content="embed",
        record_har_mode="full",
        storage_state="auth.json",
    )

    assert "record_har_path" not in raw.kwargs
    assert "record_har_content" not in raw.kwargs
    assert "record_har_mode" not in raw.kwargs
    assert raw.kwargs["storage_state"] == "auth.json"
    assert context.tracing.start() is None
    assert context.tracing.stop(path="trace.zip") is None


def test_persisted_visual_screenshot_is_masked_before_first_write(
    tmp_path: Path,
) -> None:
    page = _FakePage()
    wrapped = privacy._VisualPrivacyPage(
        page,
        {
            "mask_selectors": [],
            "mask_locator_intents": [],
            "mask_regions": [{"x": 0, "y": 0, "width": 1, "height": 1}],
        },
    )
    destination = tmp_path / "final.png"

    persisted_bytes = wrapped.screenshot(path=str(destination), full_page=True)

    assert page.calls == [{"full_page": True}]
    assert destination.is_file()
    persisted = Image.open(destination).convert("RGBA")
    returned = Image.open(io.BytesIO(persisted_bytes)).convert("RGBA")
    assert persisted.getpixel((0, 0)) == (0, 0, 0, 0)
    assert persisted.getpixel((1, 1)) == (255, 255, 255, 255)
    assert list(persisted.getdata()) == list(returned.getdata())


def test_visual_result_scrubbing_removes_raw_runtime_evidence() -> None:
    result = {
        "status": "failed",
        "reason": "PlaywrightError: customer-token=secret-value",
        "trace_ref": "trace.zip",
        "har_ref": "network.har",
        "console": [{"type": "error", "text": "secret-value"}],
        "network": [{
            "method": "GET",
            "status": 500,
            "url": "https://example.test/api?token=secret-value",
        }],
    }

    scrubbed = privacy._scrub_result(result)
    serialized = json.dumps(scrubbed, sort_keys=True)

    assert "secret-value" not in serialized
    assert scrubbed["reason"].startswith("UI_VISUAL_RUNTIME_ERROR:")
    assert scrubbed["trace_ref"] == ""
    assert scrubbed["har_ref"] == ""
    assert "text" not in scrubbed["console"][0]
    assert "url" not in scrubbed["network"][0]
    evidence = scrubbed["visual_evidence_privacy"]
    assert evidence["persisted_screenshots_masked_before_first_write"] is True
    assert evidence["stale_browser_evidence_removed_before_run"] is True


def test_first_visual_execution_json_serialization_is_scrubbed() -> None:
    proxy = privacy._VisualJsonProxy(json)
    result = {
        "status": "failed",
        "reason": "BrowserError: secret-value",
        "execution_mode": "safe_read_only",
        "steps": [],
        "duration_ms": 1,
        "professional_ui_expectation_count": 1,
        "console": [{"type": "error", "text": "secret-value"}],
        "network": [],
    }
    token = privacy._CONTEXT.set({"mask_step": {}})
    try:
        serialized = proxy.dumps(result, sort_keys=True)
    finally:
        privacy._CONTEXT.reset(token)

    persisted = json.loads(serialized)
    assert "secret-value" not in serialized
    assert persisted["reason"].startswith("UI_VISUAL_RUNTIME_ERROR:")
    assert persisted["visual_evidence_privacy"]["har_persisted"] is False


def test_stale_visual_browser_evidence_is_removed_only_inside_exact_run(
    tmp_path: Path,
) -> None:
    target = (
        tmp_path
        / "platform_workspace"
        / "project-a"
        / "browser_runs"
        / "run-a"
    )
    target.mkdir(parents=True)
    for name in (
        "trace.zip",
        "network.har",
        "browser_execution.json",
        "final.png",
        "step_2.png",
    ):
        (target / name).write_bytes(b"old")
    keep = target / "cleanup_receipt.txt"
    keep.write_text("keep", encoding="utf-8")
    neighbor = target.parent / "run-b"
    neighbor.mkdir()
    neighbor_file = neighbor / "final.png"
    neighbor_file.write_bytes(b"neighbor")

    privacy._remove_stale_browser_evidence(
        root=tmp_path,
        project_id="project-a",
        run_id="run-a",
    )

    assert sorted(path.name for path in target.iterdir()) == ["cleanup_receipt.txt"]
    assert neighbor_file.read_bytes() == b"neighbor"
