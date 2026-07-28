from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from ai_test_asset_center import visual_baseline_registry as registry


def _png(path: Path) -> None:
    image = Image.new("RGBA", (10, 10), (255, 255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    path.write_bytes(output.getvalue())


def _register(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "orders.png"
    if not source.exists():
        _png(source)
    return registry.register_visual_baseline(
        "visual-project",
        file_path=source,
        baseline_name="orders",
        viewport_width=1280,
        viewport_height=720,
        full_page=False,
        root=tmp_path,
        actor={"name": "alice", "role": "qa_lead"},
    )


def test_versioned_re_registration_is_idempotent_for_active_logical_identity(
    tmp_path: Path,
) -> None:
    first_result = _register(tmp_path)
    first = first_result["baseline"]
    assert first["logical_name"] == "orders"
    assert first["logical_version"] == 1

    registry.revoke_visual_baseline(
        "visual-project",
        baseline_id=first["baseline_id"],
        reason="replace reviewed capture",
        root=tmp_path,
        actor={"name": "alice", "role": "qa_lead"},
    )

    second_result = _register(tmp_path)
    second = second_result["baseline"]
    assert second_result["status"] == "REGISTERED"
    assert second["logical_name"] == "orders"
    assert second["logical_version"] == 2
    assert second["supersedes_baseline_id"] == first["baseline_id"]
    assert second["ref"] != first["ref"]

    duplicate = _register(tmp_path)
    assert duplicate["status"] == "DUPLICATE_ACTIVE"
    assert duplicate["baseline"]["baseline_id"] == second["baseline_id"]
    assert duplicate["baseline"]["ref"] == second["ref"]
    assert duplicate["baseline"]["logical_version"] == 2

    inventory = registry.list_visual_baselines(
        "visual-project",
        root=tmp_path,
        include_revoked=True,
    )
    source_rows = [
        row
        for row in inventory["baselines"]
        if row["authority"] == "source_registered"
    ]
    assert len(source_rows) == 2
    assert sorted(row["logical_version"] for row in source_rows) == [1, 2]
