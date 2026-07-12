from __future__ import annotations

import argparse
import json


def test_legacy_discover_command_rejects_independent_discovery_path(
    capsys,
) -> None:
    from aitestops.cli import cmd_discover

    status = cmd_discover(
        argparse.Namespace(
            prd="requirements.md",
            api="openapi.json",
            base_url="http://target.invalid",
            out="",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 2
    assert payload["status"] == "DEPRECATED_COMMAND"
    assert payload["reason_code"] == "DISCOVERY_MAINLINE_REQUIRED"
    assert payload["independent_discovery_executed"] is False
    assert payload["canonical_entrypoint"] == "ai_test_asset_center scan"


def test_public_cli_no_longer_imports_independent_discovery_engine() -> None:
    from pathlib import Path

    source = Path("aitestops/cli.py").read_text(encoding="utf-8")

    assert "from ai_test_asset_center.discovery_engine import run_discovery" not in source
    assert "result = run_discovery(" not in source
