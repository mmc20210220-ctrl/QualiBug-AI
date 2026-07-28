from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from ai_test_asset_center import visual_baseline_registry as registry


def _png(path: Path) -> None:
    image = Image.new("RGBA", (8, 8), (255, 255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    path.write_bytes(output.getvalue())


def test_long_baseline_name_keeps_version_suffix_after_revocation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "baseline.png"
    _png(source)
    name = "a" * 120
    actor = {"name": "alice", "role": "qa_lead"}

    first = registry.register_visual_baseline(
        "visual-project",
        file_path=source,
        baseline_name=name,
        viewport_width=1280,
        viewport_height=720,
        full_page=False,
        root=tmp_path,
        actor=actor,
    )["baseline"]
    registry.revoke_visual_baseline(
        "visual-project",
        baseline_id=first["baseline_id"],
        reason="replace reviewed capture",
        root=tmp_path,
        actor=actor,
    )
    second = registry.register_visual_baseline(
        "visual-project",
        file_path=source,
        baseline_name=name,
        viewport_width=1280,
        viewport_height=720,
        full_page=False,
        root=tmp_path,
        actor=actor,
    )["baseline"]

    assert second["ref"] != first["ref"]
    assert "__v2__" in Path(second["ref"]).name
    assert registry.active_visual_baseline_record(
        "visual-project",
        first["ref"],
        root=tmp_path,
    ) is None
    assert registry.active_visual_baseline_record(
        "visual-project",
        second["ref"],
        root=tmp_path,
    )["baseline_id"] == second["baseline_id"]
