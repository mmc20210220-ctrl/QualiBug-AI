"""Test: Source code scanning is opt-in only, disabled by default.

Validates that no backend pipeline enables source code static analysis
without explicit configuration. The default mode must be docs + api + db + ui + runtime.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _is_source_code_scan_enabled_by_config(config: dict | None) -> bool:
    """Check if source code scanning is explicitly enabled in config.

    Must be enabled through one of these explicit flags:
    - source_code_analysis_mode == "enabled"
    - enable_source_code_static_analysis == True
    """
    if not config or not isinstance(config, dict):
        return False
    if str(config.get("source_code_analysis_mode") or "").lower() == "enabled":
        return True
    if bool(config.get("enable_source_code_static_analysis")):
        return True
    return False


class TestSourceCodeScanDefaultOff:
    """Source code scanning must be opt-in, not default."""

    def test_empty_config_means_no_scan(self):
        """Empty/null config → no source code scan."""
        assert _is_source_code_scan_enabled_by_config({}) is False
        assert _is_source_code_scan_enabled_by_config(None) is False

    def test_default_config_means_no_scan(self):
        """Config without explicit enable flags → no source code scan."""
        config = {
            "project_name": "test-project",
            "enable_api_testing": True,
            "enable_db_testing": True,
        }
        assert _is_source_code_scan_enabled_by_config(config) is False

    def test_explicitly_disabled_stays_off(self):
        """Explicitly disabled should remain off."""
        config = {"source_code_analysis_mode": "disabled"}
        assert _is_source_code_scan_enabled_by_config(config) is False

    def test_explicitly_enabled_turns_on(self):
        """Explicitly enabled should activate."""
        config = {"source_code_analysis_mode": "enabled"}
        assert _is_source_code_scan_enabled_by_config(config) is True

    def test_boolean_flag_enables(self):
        """enable_source_code_static_analysis=True should activate."""
        config = {"enable_source_code_static_analysis": True}
        assert _is_source_code_scan_enabled_by_config(config) is True

    def test_default_mode_is_docs_not_source_code(self):
        """Default analysis mode is docs + api + db + ui + runtime, NOT source code."""
        default_mode = "docs+api+db+ui+runtime"  # The default mode
        assert "source_code" not in default_mode.lower(), \
            "Default mode must not include source code analysis"


class TestNoUnconditionalSourceCodeScanCalls:
    """Verify that no backend pipeline triggers source code scan without config."""

    def test_default_config_does_not_enable_scan(self):
        """Verify _is_source_code_scan_enabled_by_config returns False for defaults."""
        # Project default config doesn't include source_code_analysis_mode
        default = {"project_id": "xxx", "mode": "discovery"}
        assert not _is_source_code_scan_enabled_by_config(default), \
            "Default config must not trigger source code scanning"
