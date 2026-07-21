"""Unit tests for version.py default_api_base_url function."""
import os
import pytest
from unittest.mock import patch

from ai_test_asset_center.version import default_api_base_url, DEFAULT_PRIVATE_PILOT_PORT


class TestDefaultApiBaseUrl:
    """Tests for the default_api_base_url configuration function."""

    def test_returns_default_port_when_no_env_vars(self):
        """Should return default port 8088 when no environment variables are set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove any existing env vars
            os.environ.pop("QUALIBUG_API_BASE_URL", None)
            os.environ.pop("QUALIBUG_PORT", None)
            result = default_api_base_url()
            assert result == f"http://127.0.0.1:{DEFAULT_PRIVATE_PILOT_PORT}"
            assert result == "http://127.0.0.1:8088"

    def test_uses_explicit_base_url_from_env(self):
        """Should use QUALIBUG_API_BASE_URL when set."""
        with patch.dict(os.environ, {"QUALIBUG_API_BASE_URL": "http://custom-host:9999"}):
            result = default_api_base_url()
            assert result == "http://custom-host:9999"

    def test_strips_trailing_slash_from_explicit_url(self):
        """Should strip trailing slashes from explicit URL."""
        with patch.dict(os.environ, {"QUALIBUG_API_BASE_URL": "http://custom-host:9999/"}):
            result = default_api_base_url()
            assert result == "http://custom-host:9999"

    def test_uses_port_from_env(self):
        """Should use QUALIBUG_PORT when set."""
        with patch.dict(os.environ, {"QUALIBUG_PORT": "9090"}, clear=False):
            os.environ.pop("QUALIBUG_API_BASE_URL", None)
            result = default_api_base_url()
            assert result == "http://127.0.0.1:9090"

    def test_explicit_url_takes_precedence_over_port(self):
        """QUALIBUG_API_BASE_URL should take precedence over QUALIBUG_PORT."""
        with patch.dict(os.environ, {
            "QUALIBUG_API_BASE_URL": "http://explicit:8888",
            "QUALIBUG_PORT": "9090"
        }):
            result = default_api_base_url()
            assert result == "http://explicit:8888"

    def test_handles_empty_string_env_var(self):
        """Should fall back to default when env var is empty string."""
        with patch.dict(os.environ, {"QUALIBUG_API_BASE_URL": ""}):
            os.environ.pop("QUALIBUG_PORT", None)
            result = default_api_base_url()
            assert result == f"http://127.0.0.1:{DEFAULT_PRIVATE_PILOT_PORT}"

    def test_handles_whitespace_only_env_var(self):
        """Should fall back to default when env var is whitespace only."""
        with patch.dict(os.environ, {"QUALIBUG_API_BASE_URL": "   "}):
            os.environ.pop("QUALIBUG_PORT", None)
            result = default_api_base_url()
            assert result == f"http://127.0.0.1:{DEFAULT_PRIVATE_PILOT_PORT}"
