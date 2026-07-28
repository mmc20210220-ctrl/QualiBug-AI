from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from ai_test_asset_center import visual_baseline_registry as registry


def _png(path: Path) -> None:
    image = Image.new("RGBA", (12, 8), (255, 255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    path.write_bytes(output.getvalue())


def _actor() -> dict[str, str]:
    return {"name": "alice", "role": "qa_lead"}


def test_history_visibility_does_not_change_active_authority_counters(
    tmp_path: Path,
) -> None:
    source = tmp_path / "orders.png"
    _png(source)
    registered = registry.register_visual_baseline(
        "visual-project",
        file_path=source,
        baseline_name="orders",
        viewport_width=1280,
        viewport_height=720,
        full_page=False,
        root=tmp_path,
        actor=_actor(),
    )["baseline"]
    approved = registry.approve_visual_baseline(
        "visual-project",
        baseline_id=registered["baseline_id"],
        root=tmp_path,
        actor=_actor(),
    )["baseline"]
    registry.revoke_visual_baseline(
        "visual-project",
        baseline_id=approved["baseline_id"],
        reason="approval withdrawn",
        root=tmp_path,
        actor=_actor(),
    )

    active_only = registry.list_visual_baselines(
        "visual-project",
        root=tmp_path,
        include_revoked=False,
    )
    with_history = registry.list_visual_baselines(
        "visual-project",
        root=tmp_path,
        include_revoked=True,
    )

    assert active_only["summary"]["active_count"] == 1
    assert active_only["summary"]["source_registered_count"] == 1
    assert active_only["summary"]["approved_copy_count"] == 0
    assert active_only["summary"]["visible_count"] == 1

    assert with_history["summary"]["active_count"] == 1
    assert with_history["summary"]["source_registered_count"] == 1
    assert with_history["summary"]["approved_copy_count"] == 0
    assert with_history["summary"]["visible_count"] == 2
    assert with_history["summary"]["visible_source_registered_count"] == 1
    assert with_history["summary"]["visible_approved_copy_count"] == 1
    assert with_history["summary_scope"] == {
        "source_registered_count": "active_authority",
        "approved_copy_count": "active_authority",
        "visible_counts": "returned_rows_after_include_revoked_filter",
    }
