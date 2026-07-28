from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from ai_test_asset_center import professional_ui_visual_baseline as visual
from ai_test_asset_center import visual_baseline_registry as registry
from ai_test_asset_center.enterprise_knowledge_center import _visual_baselines as private_registry
from ai_test_asset_center.professional_ui_visual_registry_binding import (
    _require_registry_identity,
)


def _png(path: Path, *, size: tuple[int, int] = (16, 12)) -> bytes:
    image = Image.new("RGBA", size, (255, 255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    data = output.getvalue()
    path.write_bytes(data)
    return data


def _actor(name: str = "alice") -> dict[str, str]:
    return {"name": name, "role": "qa_lead"}


def _register(
    tmp_path: Path,
    *,
    project: str = "visual-project",
    viewport_width: int = 1280,
    viewport_height: int = 720,
    full_page: bool = False,
) -> dict[str, Any]:
    source = tmp_path / "orders.png"
    if not source.exists():
        _png(source)
    return registry.register_visual_baseline(
        project,
        file_path=source,
        baseline_name="orders",
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        full_page=full_page,
        root=tmp_path,
        actor=_actor(),
    )


def _step(record: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "action": visual.ACTION,
        "baseline_ref": record["ref"],
        "baseline_sha256": record["sha256"],
        "max_changed_pixel_ratio": 0.0,
        "channel_tolerance": 0,
        "full_page": record["full_page"],
        "animations_disabled": True,
        "renderer_profile": record["renderer_profile"],
        "scroll_origin": record["scroll_origin"],
        "font_readiness": record["font_readiness"],
        "viewport_width": record["viewport_width"],
        "viewport_height": record["viewport_height"],
        "mask_selectors": [],
        "mask_locator_intents": [],
        "mask_regions": [],
    }
    step.update(overrides)
    return step


def test_public_registry_aliases_are_bound_after_integrity_guard() -> None:
    assert registry.register_visual_baseline is private_registry.register_visual_baseline
    assert registry.approve_visual_baseline is private_registry.approve_visual_baseline
    assert registry.revoke_visual_baseline is private_registry.revoke_visual_baseline


def test_register_is_audited_immutable_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "orders.png"
    data = _png(source)

    first = _register(tmp_path)
    record = first["baseline"]

    assert first["status"] == "REGISTERED"
    assert record["created_by"] == "alice:qa_lead"
    assert record["sha256"] == hashlib.sha256(data).hexdigest()
    assert record["authority"] == "source_registered"
    assert record["raw_pixels_embedded_in_registry"] is False
    stored = tmp_path / "platform_inputs" / "visual-project" / record["ref"]
    assert stored.read_bytes() == data

    duplicate = _register(tmp_path)
    assert duplicate["status"] == "DUPLICATE_ACTIVE"
    assert duplicate["baseline"]["baseline_id"] == record["baseline_id"]


def test_same_ref_cannot_acquire_conflicting_active_viewport(tmp_path: Path) -> None:
    _register(tmp_path, viewport_width=1280)

    with pytest.raises(
        RuntimeError,
        match=r"^visual_baseline_active_identity_conflict$",
    ):
        _register(tmp_path, viewport_width=1440)


def test_re_registration_after_revocation_receives_new_version_id(
    tmp_path: Path,
) -> None:
    registered = _register(tmp_path)["baseline"]
    approved = registry.approve_visual_baseline(
        "visual-project",
        baseline_id=registered["baseline_id"],
        root=tmp_path,
        actor=_actor("approver"),
    )["baseline"]

    assert approved["authority"] == "approved_copy"
    assert approved["baseline_id"] != registered["baseline_id"]
    assert approved["created_by"] == "approver:qa_lead"

    revoked = registry.revoke_visual_baseline(
        "visual-project",
        baseline_id=registered["baseline_id"],
        reason="superseded by reviewed capture",
        root=tmp_path,
        actor=_actor("reviewer"),
    )["baseline"]
    assert revoked["status"] == "revoked"
    assert revoked["revoked_by"] == "reviewer:qa_lead"

    replacement = _register(tmp_path)["baseline"]
    assert replacement["baseline_id"] != registered["baseline_id"]
    assert replacement["ref"] == registered["ref"]
    assert replacement["sha256"] == registered["sha256"]

    inventory = registry.list_visual_baselines(
        "visual-project",
        root=tmp_path,
        include_revoked=True,
    )
    ids = [row["baseline_id"] for row in inventory["baselines"]]
    assert len(ids) == len(set(ids))
    assert inventory["summary"]["active_count"] == 2
    assert inventory["summary"]["revoked_count"] == 1


def test_corrupt_registry_is_not_silently_overwritten(tmp_path: Path) -> None:
    project = "visual-project"
    path = tmp_path / "platform_workspace" / project / "visual_baseline_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    source = tmp_path / "orders.png"
    _png(source)

    with pytest.raises(RuntimeError, match=r"^visual_baseline_registry_corrupt$"):
        registry.register_visual_baseline(
            project,
            file_path=source,
            baseline_name="orders",
            viewport_width=1280,
            viewport_height=720,
            full_page=False,
            root=tmp_path,
            actor=_actor(),
        )

    assert path.read_text(encoding="utf-8") == "{broken"


def test_unauthorized_actor_is_rejected_before_source_file_read(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "not-present.png"

    with pytest.raises(PermissionError):
        registry.register_visual_baseline(
            "visual-project",
            file_path=missing,
            baseline_name="orders",
            viewport_width=1280,
            viewport_height=720,
            full_page=False,
            root=tmp_path,
            actor={"name": "viewer", "role": "viewer"},
        )


def test_runtime_identity_requires_one_active_matching_record(tmp_path: Path) -> None:
    record = _register(tmp_path)["baseline"]
    token = visual._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": "visual-project",
    })
    try:
        resolved = _require_registry_identity(_step(record))
        assert resolved["baseline_id"] == record["baseline_id"]

        with pytest.raises(
            visual.VisualBaselineObservationError,
            match=r"^UI_VISUAL_BASELINE_VIEWPORT_IDENTITY_MISMATCH$",
        ):
            _require_registry_identity(_step(record, viewport_width=1440))
    finally:
        visual._RUNTIME_CONTEXT.reset(token)

    registry.revoke_visual_baseline(
        "visual-project",
        baseline_id=record["baseline_id"],
        reason="invalidated",
        root=tmp_path,
        actor=_actor(),
    )
    token = visual._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": "visual-project",
    })
    try:
        with pytest.raises(
            visual.VisualBaselineObservationError,
            match=r"^UI_VISUAL_BASELINE_NOT_ACTIVE$",
        ):
            _require_registry_identity(_step(record))
    finally:
        visual._RUNTIME_CONTEXT.reset(token)
