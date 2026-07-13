from __future__ import annotations

from pathlib import Path

import pytest


def test_missing_optional_json_uses_declared_default(tmp_path: Path) -> None:
    from ai_test_asset_center.project_runtime_primitives import load_json_artifact

    assert load_json_artifact(tmp_path / "missing.json", {"state": "missing"}) == {
        "state": "missing"
    }


def test_corrupt_or_unreadable_json_fails_with_path(tmp_path: Path) -> None:
    from ai_test_asset_center.project_runtime_primitives import (
        ProjectArtifactError,
        load_json_artifact,
    )

    artifact = tmp_path / "corrupt.json"
    artifact.write_text('{"broken":', encoding="utf-8")

    with pytest.raises(ProjectArtifactError, match="project_json_artifact_invalid") as caught:
        load_json_artifact(artifact, {})

    assert str(artifact.resolve()) in str(caught.value)


def test_onboarding_reexports_single_primitive_authority() -> None:
    from ai_test_asset_center import project_runtime_primitives as primitives
    from ai_test_asset_center import real_project_onboarding as onboarding

    assert onboarding._load_json is primitives.load_json_artifact
    assert onboarding._write_json is primitives.write_json_artifact
    assert onboarding._read_text is primitives.read_text_artifact
    assert onboarding._safe_project_id is primitives.safe_project_id
    assert onboarding.config_paths is primitives.project_config_paths
