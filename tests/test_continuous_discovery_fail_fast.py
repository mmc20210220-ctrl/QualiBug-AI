from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center import private_pilot_service as service


def _state_path(root: Path, project: str = "project-a") -> Path:
    return (
        root
        / "platform_workspace"
        / project
        / "defect_discovery"
        / service._CONTINUOUS_STATE_FILE
    )


def _activate_loop(root: Path, project: str = "project-a") -> tuple[str, str]:
    key = (str(root), project)
    service._continuous_threads[key] = {"stop": False, "round": 0, "converged": False}
    return key


def test_scan_failure_is_persisted_and_raised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from ai_test_asset_center import __main__ as main_module

    def fail_scan(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(main_module, "scan", fail_scan)
    key = _activate_loop(tmp_path)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        service._continuous_scan_loop(tmp_path, "project-a", "tenant-a", 0)

    state = json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["converged"] is False
    assert state["last_failure"]["phase"] == "scan"
    assert state["last_failure"]["round"] == 1
    assert state["last_failure"]["error_type"] == "RuntimeError"
    assert state["last_failure"]["error"] == "provider unavailable"
    assert key not in service._continuous_threads

    projection = service._get_continuous_state(tmp_path, "project-a")
    assert projection["status"] == "failed"
    assert projection["last_failure"] == state["last_failure"]
    assert "provider unavailable" in projection["message"]


def test_cumulative_merge_failure_cannot_be_counted_as_zero_new_findings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ai_test_asset_center import __main__ as main_module

    monkeypatch.setattr(
        main_module,
        "scan",
        lambda *args, **kwargs: {"scan_id": "scan-1", "coverage": 1.0, "total_findings": 1},
    )
    monkeypatch.setattr(service.db_persist, "save_scan", lambda *args: "scan-1")
    monkeypatch.setattr(
        service.db_persist,
        "merge_findings_cumulative",
        lambda *args: (_ for _ in ()).throw(RuntimeError("database locked")),
    )
    _activate_loop(tmp_path)

    with pytest.raises(RuntimeError, match="database locked"):
        service._continuous_scan_loop(tmp_path, "project-a", "tenant-a", 0)

    state = json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["last_failure"]["phase"] == "cumulative_merge"
    assert state["last_failure"]["error"] == "database locked"
    assert "converge_reason" not in state


def test_continuous_state_updates_reject_corrupt_existing_state(tmp_path: Path) -> None:
    state_path = _state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match=state_path.name):
        service._update_continuous_state(tmp_path, "project-a", {"total_findings": 1})

    assert state_path.read_text(encoding="utf-8") == "{"


def test_state_update_failure_still_emits_a_separate_failure_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ai_test_asset_center import __main__ as main_module

    state_path = _state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{", encoding="utf-8")
    monkeypatch.setattr(
        main_module,
        "scan",
        lambda *args, **kwargs: {"scan_id": "scan-1", "coverage": 0.5, "total_findings": 1},
    )
    monkeypatch.setattr(service.db_persist, "save_scan", lambda *args: "scan-1")
    monkeypatch.setattr(
        service.db_persist,
        "merge_findings_cumulative",
        lambda *args: {"new": 1},
    )
    _activate_loop(tmp_path)

    with pytest.raises(ValueError, match=state_path.name):
        service._continuous_scan_loop(tmp_path, "project-a", "tenant-a", 0)

    receipt_path = state_path.with_name("continuous_discovery_last_error.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["phase"] == "state_update"
    assert receipt["round"] == 1
    assert receipt["error_type"] == "ValueError"
    assert state_path.read_text(encoding="utf-8") == "{"


def test_state_observation_does_not_invent_convergence(tmp_path: Path) -> None:
    for _ in range(3):
        service._update_continuous_state(
            tmp_path,
            "project-a",
            {"coverage": 0.9, "total_findings": 5},
        )

    state = json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["status"] == "scanning"
    assert state["converged"] is False
    assert "converge_reason" not in state


def test_max_rounds_is_a_visible_non_converged_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ai_test_asset_center import __main__ as main_module

    monkeypatch.setattr(
        main_module,
        "scan",
        lambda *args, **kwargs: {"scan_id": "scan-1", "coverage": 0.9, "total_findings": 1},
    )
    monkeypatch.setattr(service.db_persist, "save_scan", lambda *args: "scan-1")
    monkeypatch.setattr(
        service.db_persist,
        "merge_findings_cumulative",
        lambda *args: {"new": 1},
    )
    _activate_loop(tmp_path)

    service._continuous_scan_loop(tmp_path, "project-a", "tenant-a", 0)

    state = json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["status"] == "max_rounds_reached"
    assert state["converged"] is False
    assert state["termination"]["reason_code"] == "MAX_ROUNDS_REACHED"
    assert state["termination"]["round"] == 20

    projection = service._get_continuous_state(tmp_path, "project-a")
    assert projection["termination"] == state["termination"]
    assert "20" in projection["message"]
    assert "上限" in projection["message"]
