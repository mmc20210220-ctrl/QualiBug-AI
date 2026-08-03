from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
import textwrap

import pytest

from ai_test_asset_center.private_pilot_service import (
    PrivatePilotHandler,
    _get_continuous_state,
    _load_real_project_discovery_payload,
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_present_but_invalid_json_artifact_fails_with_path(tmp_path: Path) -> None:
    artifact = _write(tmp_path / "scan_result.json", '{"findings":')

    with pytest.raises(ValueError, match=r"invalid JSON artifact: .*scan_result\.json"):
        PrivatePilotHandler._read_json_dict(artifact)


def test_present_non_object_json_fails_in_object_reader(tmp_path: Path) -> None:
    artifact = _write(tmp_path / "scan_result.json", "[]")

    with pytest.raises(ValueError, match=r"JSON artifact must be an object: .*scan_result\.json"):
        PrivatePilotHandler._read_json_dict(artifact)


def test_missing_optional_json_stays_absent(tmp_path: Path) -> None:
    assert PrivatePilotHandler._read_json_dict(tmp_path / "missing.json") == {}


def test_real_discovery_does_not_skip_a_corrupt_authoritative_candidate(tmp_path: Path) -> None:
    artifact = _write(
        tmp_path
        / "platform_outputs"
        / "project-a"
        / "real_project"
        / "real_project_defect_data.json",
        '{"items":',
    )

    with pytest.raises(ValueError, match=artifact.name):
        _load_real_project_discovery_payload(tmp_path, "project-a")


def test_db_finding_loader_preserves_source_facts_without_invented_defaults(tmp_path: Path) -> None:
    report = {
        "db_verification": {
            "findings": [
                {
                    "title": "Observed constraint divergence",
                    "evidence": {"db_row": {"entity_key": "E-42"}},
                }
            ]
        }
    }
    _write(
        tmp_path / "platform_outputs" / "project-a" / "scan_result.json",
        json.dumps(report),
    )

    findings = PrivatePilotHandler._load_db_findings(tmp_path, "project-a")

    assert findings[0]["source_value"] == "E-42"
    assert "expected_behavior" not in findings[0]
    assert "confidence_score" not in findings[0]


@pytest.mark.parametrize(
    ("relative_path", "loader"),
    [
        (
            Path("platform_outputs/project-a/performance/baseline.json"),
            PrivatePilotHandler._load_perf_regressions,
        ),
        (
            Path("platform_outputs/project-a/spectrum/spectrum_result.json"),
            PrivatePilotHandler._load_spectrum_findings,
        ),
        (
            Path("platform_outputs/project-a/scan_result.json"),
            PrivatePilotHandler._load_multi_layer_findings,
        ),
    ],
)
def test_present_corrupt_optional_finding_source_fails_fast(
    tmp_path: Path,
    relative_path: Path,
    loader,
) -> None:
    artifact = _write(tmp_path / relative_path, "{")

    with pytest.raises(ValueError, match=artifact.name):
        loader(tmp_path, "project-a")


def test_corrupt_continuous_state_and_scan_counter_do_not_reset_silently(tmp_path: Path) -> None:
    state = _write(
        tmp_path
        / "platform_workspace"
        / "project-a"
        / "defect_discovery"
        / "continuous_discovery_state.json",
        "{",
    )
    counter = _write(
        tmp_path / "platform_outputs" / "project-a" / "scan_counter.json",
        "[]",
    )

    with pytest.raises(ValueError, match=state.name):
        _get_continuous_state(tmp_path, "project-a")
    with pytest.raises(ValueError, match=counter.name):
        PrivatePilotHandler._scan_counter(None, "project-a", tmp_path)


def test_command_center_exception_handlers_raise_or_emit_visible_failure() -> None:
    # The handler class resolves _build_command_center through the mixin MRO
    # (a wrapper mixin delegates via super() to the builder implementation).
    # Every definition on the chain must satisfy the no-swallow contract:
    # each except handler either re-raises or emits the visible
    # quality_projection_failed marker.  A method without except handlers
    # propagates exceptions naturally and is therefore compliant.
    checked = 0
    for cls in PrivatePilotHandler.__mro__:
        member = cls.__dict__.get("_build_command_center")
        if member is None:
            continue
        source = textwrap.dedent(inspect.getsource(member))
        tree = ast.parse(source)
        handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
        for handler in handlers:
            handler_tree = ast.Module(body=handler.body, type_ignores=[])
            has_raise = any(isinstance(node, ast.Raise) for node in ast.walk(handler_tree))
            emits_visible_failure = "quality_projection_failed" in ast.unparse(handler_tree)
            assert has_raise or emits_visible_failure, f"{cls.__name__}: {ast.unparse(handler_tree)}"
        checked += 1
    assert checked >= 1
