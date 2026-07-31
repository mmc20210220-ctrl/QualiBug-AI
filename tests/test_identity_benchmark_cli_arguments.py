from __future__ import annotations

import json

from ai_test_asset_center import identity_benchmark_cli as cli


def test_missing_required_argument_returns_json_error_not_review_code(capsys) -> None:
    code = cli.main(["status"])

    assert code == cli.EXIT_ERROR
    assert code != cli.EXIT_REVIEW_REQUIRED
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "FAILED"
    assert payload["error_type"] == "ValueError"
    assert "identity_benchmark_cli_argument_error" in payload["error"]


def test_unknown_command_returns_json_error_not_review_code(capsys) -> None:
    code = cli.main(["unknown-command"])

    assert code == cli.EXIT_ERROR
    assert code != cli.EXIT_REVIEW_REQUIRED
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "FAILED"
    assert "identity_benchmark_cli_argument_error" in payload["error"]
