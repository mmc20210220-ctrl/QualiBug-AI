from __future__ import annotations

import os

PRODUCT_NAME = "QualiBug AI Enterprise Edition"
PRODUCT_VERSION = "95.0.0"
PRODUCT_PHASE = "Phase106"
PRODUCT_CHANNEL = "private-pilot"
DEFAULT_PRIVATE_PILOT_PORT = 8088
DEFAULT_PREVIEW_PORT = 8795
DEFAULT_PAGE_AGENT_BRIDGE_PORT = 8797
DEFAULT_MULTI_SERVICE_BASE_PORT = 8000
CANONICAL_HEALTH_PATH = "/api/health"
LEGACY_HEALTH_PATH = "/health"


def default_api_base_url() -> str:
    """Return the default backend API base URL from environment or fallback.

    Reads QUALIBUG_API_BASE_URL if set, otherwise constructs from
    QUALIBUG_PORT (default 8088). Used by phase105 frontend generators
    to avoid hardcoding localhost URLs.
    """
    explicit = os.environ.get("QUALIBUG_API_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    port = int(os.environ.get("QUALIBUG_PORT", str(DEFAULT_PRIVATE_PILOT_PORT)))
    return f"http://127.0.0.1:{port}"
