from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "ai_test_asset_center" / "private_pilot_service.py"
HANDLER = ROOT / "ai_test_asset_center" / "private_pilot_connector_handlers.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_http_server_is_the_only_connector_supervisor_lifecycle_owner():
    service = _text(SERVICE)
    handler = _text(HANDLER)

    assert "ensure_connector_auto_sync_supervisor(resolved_root)" in service
    assert "stop_connector_auto_sync_supervisor(root" in service
    assert "def server_close(self)" in service

    assert "ensure_connector_auto_sync_supervisor" not in handler
    assert "def setup(self)" not in handler
    configure = handler[
        handler.index("def _handle_knowledge_connector_configure"):handler.index(
            "def _handle_knowledge_connector_action"
        )
    ]
    assert "supervisor" not in configure


def test_running_owner_conflicts_are_http_409_not_parameter_errors():
    handler = _text(HANDLER)
    error_status = handler[
        handler.index("def _error_status"):handler.index(
            "class KnowledgeConnectorHandlersMixin"
        )
    ]
    assert '"already_running"' in error_status
    assert '"lock_held"' in error_status
    assert '"owner_active"' in error_status
    assert '"owner_unverified"' in error_status
    assert "return 409" in error_status
