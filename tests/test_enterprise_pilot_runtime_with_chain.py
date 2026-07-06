from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import enterprise_pilot_runtime as runtime
from ai_test_asset_center import enterprise_pilot_runtime_with_chain as wrapper


def test_chain_aware_pilot_runtime_patch_installs_and_restores(monkeypatch) -> None:
    wrapper.restore_chain_aware_pilot_runtime_patch()

    def fake_chain_discovery(project_id: str, root: Path) -> dict:
        return {"status": "succeeded", "main_chain_contract": {"chain_ready": True}}

    monkeypatch.setattr(wrapper, "run_real_project_discovery_with_chain", fake_chain_discovery)
    wrapper.install_chain_aware_pilot_runtime_patch()
    status = wrapper.chain_aware_pilot_runtime_patch_status()

    assert status["patched"] is True
    assert status["source"] == "ai_test_asset_center.enterprise_pilot_runtime_with_chain"
    assert status["has_original_discovery_runner"] is True
    assert runtime.run_real_project_discovery is fake_chain_discovery

    wrapper.restore_chain_aware_pilot_runtime_patch()
    status = wrapper.chain_aware_pilot_runtime_patch_status()
    assert status["patched"] is False
    assert status["source"] == ""
    assert status["has_original_discovery_runner"] is False
    assert runtime.run_real_project_discovery is not fake_chain_discovery


def test_run_next_pilot_task_with_chain_installs_patch_before_delegating(monkeypatch, tmp_path: Path) -> None:
    wrapper.restore_chain_aware_pilot_runtime_patch()
    observed = {}

    def fake_chain_discovery(project_id: str, root: Path) -> dict:
        return {"status": "succeeded", "main_chain_contract": {"chain_ready": True}}

    def fake_run_next(project_id: str, root: Path, actor: dict | None = None) -> dict:
        observed["project_id"] = project_id
        observed["root"] = root
        observed["actor"] = actor
        observed["active_runner"] = runtime.run_real_project_discovery
        return {"ok": True, "idle": True, "main_chain_runtime_patch": wrapper.chain_aware_pilot_runtime_patch_status()}

    monkeypatch.setattr(wrapper, "run_real_project_discovery_with_chain", fake_chain_discovery)
    monkeypatch.setattr(runtime, "run_next_pilot_task", fake_run_next)

    result = wrapper.run_next_pilot_task_with_chain("demo", tmp_path, {"name": "qa", "role": "qa_lead"})

    assert result["ok"] is True
    assert observed["project_id"] == "demo"
    assert observed["root"] == tmp_path
    assert observed["active_runner"] is fake_chain_discovery
    assert result["main_chain_runtime_patch"]["patched"] is True


def test_chain_aware_pilot_runtime_cli_entrypoint_is_registered() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")

    assert 'qualibug-pilot-chain = "ai_test_asset_center.enterprise_pilot_runtime_with_chain:_cli"' in source
