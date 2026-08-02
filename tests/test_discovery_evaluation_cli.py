from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import pytest

from tools.discovery_evaluation import REPOSITORY_ROOT, _resolve_signing_key


def test_cli_loads_binary_external_evaluator_key_file() -> None:
    key = bytes(range(48))
    with tempfile.TemporaryDirectory() as directory:
        key_path = Path(directory) / "evaluator-hmac.key"
        key_path.write_bytes(key)

        resolved = _resolve_signing_key(
            argparse.Namespace(hmac_key_file=key_path)
        )

        assert resolved == key


def test_cli_rejects_evaluator_key_file_inside_product_workspace() -> None:
    with pytest.raises(ValueError, match="outside product workspace"):
        _resolve_signing_key(
            argparse.Namespace(
                hmac_key_file=REPOSITORY_ROOT / "tools" / "discovery_evaluation.py"
            )
        )
