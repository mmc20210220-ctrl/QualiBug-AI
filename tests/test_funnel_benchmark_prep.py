from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _funnel_benchmark_prep import (
    prepare_funnel_benchmark_target,
    refresh_test_account_tokens,
    reset_benchmark_target_db,
    resolve_benchmark_target_root,
    should_skip_target_db_reset,
)


def test_resolve_benchmark_target_root_uses_env_override(tmp_path: Path):
    root = resolve_benchmark_target_root({"QUALIBUG_BENCHMARK_TARGET_ROOT": str(tmp_path)})
    assert root == tmp_path


def test_should_skip_target_db_reset_is_explicit_only():
    assert should_skip_target_db_reset({}) is False
    assert should_skip_target_db_reset({"QUALIBUG_SKIP_TARGET_DB_RESET": "1"}) is True
    assert should_skip_target_db_reset({"QUALIBUG_SKIP_TARGET_DB_RESET": "yes"}) is True


def test_reset_benchmark_target_db_skip_is_loud_and_recorded():
    result = reset_benchmark_target_db(env={"QUALIBUG_SKIP_TARGET_DB_RESET": "1"})
    assert result["status"] == "skipped"
    assert "polluted" in result["operator_note"]


def test_reset_benchmark_target_db_fails_when_script_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="init_db_windows.ps1"):
        reset_benchmark_target_db(
            target_root=tmp_path,
            env={"QUALIBUG_SKIP_TARGET_DB_RESET": "0"},
        )


def test_reset_benchmark_target_db_invokes_powershell(tmp_path: Path):
    script = tmp_path / "scripts" / "init_db_windows.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("# noop", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    result = reset_benchmark_target_db(
        target_root=tmp_path,
        env={"QUALIBUG_SKIP_TARGET_DB_RESET": "0"},
        runner=fake_run,
    )
    assert result["status"] == "ok"
    assert calls and calls[0][0] == "powershell.exe"
    assert str(script) in calls[0]


def test_reset_benchmark_target_db_raises_on_nonzero_exit(tmp_path: Path):
    script = tmp_path / "scripts" / "init_db_windows.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("# noop", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 7, stdout="", stderr="psql missing")

    with pytest.raises(RuntimeError, match="exit=7"):
        reset_benchmark_target_db(
            target_root=tmp_path,
            env={},
            runner=fake_run,
        )


def test_reset_benchmark_target_db_decodes_windows_codepage_error(tmp_path: Path):
    script = tmp_path / "scripts" / "init_db_windows.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("# noop", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            7,
            stdout="",
            stderr="数据库错误".encode("gbk"),
        )

    with pytest.raises(RuntimeError, match="数据库错误"):
        reset_benchmark_target_db(
            target_root=tmp_path,
            env={},
            runner=fake_run,
        )


def test_refresh_test_account_tokens_runs_for_any_mode(tmp_path: Path):
    script = tmp_path / "_refresh_tokens.py"
    script.write_text("print('ok')", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="REFRESHED 3/3", stderr="")

    result = refresh_test_account_tokens(root=tmp_path, runner=fake_run)
    assert result["status"] == "ok"
    assert calls and str(script) in calls[0]


def test_refresh_test_account_tokens_fails_fast_on_error(tmp_path: Path):
    (tmp_path / "_refresh_tokens.py").write_text("raise SystemExit(1)", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="0/3 logged in")

    with pytest.raises(RuntimeError, match="token refresh failed"):
        refresh_test_account_tokens(root=tmp_path, runner=fake_run)


def test_prepare_funnel_benchmark_target_resets_db_then_refreshes_tokens(tmp_path: Path):
    script = tmp_path / "scripts" / "init_db_windows.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("# noop", encoding="utf-8")
    (tmp_path / "_refresh_tokens.py").write_text("print('ok')", encoding="utf-8")
    order: list[str] = []

    def fake_run(cmd, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if "init_db_windows.ps1" in joined:
            order.append("db")
        elif "_refresh_tokens.py" in joined:
            order.append("tokens")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    result = prepare_funnel_benchmark_target(
        root=tmp_path,
        env={
            "QUALIBUG_BENCHMARK_TARGET_ROOT": str(tmp_path),
            "QUALIBUG_SKIP_TARGET_DB_RESET": "0",
        },
        runner=fake_run,
    )
    assert order == ["db", "tokens"]
    assert result["target_db_reset"]["status"] == "ok"
    assert result["token_refresh"]["status"] == "ok"
    assert Path(result["reset_receipt_path"]).is_file()
    assert result["reset_receipt"]["schema_version"] == "benchmark_target_reset.v1"
    assert result["reset_receipt"]["status"] == "completed"


def test_prepare_funnel_benchmark_target_passes_evaluator_env_to_token_refresh(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "init_db_windows.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("# noop", encoding="utf-8")
    (tmp_path / "_refresh_tokens.py").write_text("print('ok')", encoding="utf-8")
    captured_token_env: list[dict[str, str] | None] = []
    evaluator_env = {
        "QUALIBUG_BENCHMARK_TARGET_ROOT": str(tmp_path),
        "QUALIBUG_SKIP_TARGET_DB_RESET": "0",
        "QUALIBUG_TARGET_BASE_URL": "http://127.0.0.1:8080",
        "QUALIBUG_DB_DSN": "postgresql://user:pass@localhost/db",
        "QUALIBUG_JWT_SECRET": "fixture-secret",
    }

    def fake_run(cmd, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if "_refresh_tokens.py" in joined:
            captured_token_env.append(kwargs.get("env"))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    prepare_funnel_benchmark_target(
        root=tmp_path,
        env=evaluator_env,
        runner=fake_run,
    )

    assert captured_token_env == [evaluator_env]


def test_funnel_runtime_never_loads_or_scores_evaluator_private_ground_truth() -> None:
    runner = Path(__file__).resolve().parents[1] / "_funnel_benchmark.py"
    source = runner.read_text(encoding="utf-8")

    assert "benchmark_compute" not in source
    assert "QUALIBUG_BENCHMARK_GROUND_TRUTH" not in source
    assert "discovery_evaluation_submission.v1" not in source
    assert "build_evaluation_submission" in source
    assert "normalize_envelope" in source
    assert (
        '"measurement_status": "NOT_MEASURED"' in source
        or 'measurement_status="NOT_MEASURED"' in source
    )
    assert "external_evaluator_receipt_required" in source
