from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center import enterprise_knowledge_center
from ai_test_asset_center.private_pilot_understanding_preflight import (
    UnderstandingPreflightProjectionMixin,
)


class _ExistingPreflight:
    def __init__(self) -> None:
        self.responses: list[tuple[dict[str, Any], int]] = []

    def _json(self, payload: dict[str, Any], status: int = 200, *args: Any, **kwargs: Any):
        self.responses.append((payload, status))
        return payload

    def _handle_scan_preflight(
        self,
        project: str,
        root: Path,
        body: dict[str, Any] | None = None,
    ):
        return self._json(
            {
                "ok": True,
                "ready": False,
                "blocking_codes": ["NO_TARGET"],
                "reasons": [
                    {
                        "code": "NO_TARGET",
                        "message": "未配置被测目标 base_url 或启用连接器端点。",
                    }
                ],
                "input_checks": {
                    "sources": {"status": "passed", "source_count": 2},
                    "target": {"status": "blocked"},
                },
            }
        )


class _Handler(UnderstandingPreflightProjectionMixin, _ExistingPreflight):
    pass


def _passed_asset() -> dict[str, Any]:
    return {
        "summary": {
            "enterprise_understanding_model_id": "eum_existing",
            "enterprise_understanding_status": "PASS",
            "enterprise_understanding_ready": True,
        },
        "enterprise_understanding_model": {
            "model_id": "eum_existing",
            "gate": {"status": "PASS", "entry_allowed": True},
        },
        "scenario_planning_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
        },
        "scenario_ir_gate": {"status": "PASS", "entry_allowed": True},
        "scenario_execution_contract_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "execution_contract_ready": True,
        },
    }


def test_mixin_preserves_original_preflight_and_sends_one_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        enterprise_knowledge_center,
        "load_enterprise_business_knowledge_asset",
        lambda project, root: _passed_asset(),
    )
    handler = _Handler()

    result = handler._handle_scan_preflight("customer_a", tmp_path)

    assert len(handler.responses) == 1
    payload, status = handler.responses[0]
    assert result is payload
    assert status == 200
    assert payload["ready"] is False
    assert payload["blocking_codes"] == ["NO_TARGET"]
    assert payload["reasons"] == [
        {
            "code": "NO_TARGET",
            "message": "未配置被测目标 base_url 或启用连接器端点。",
        }
    ]
    assert payload["input_checks"]["target"]["status"] == "blocked"
    assert payload["input_checks"]["enterprise_understanding"]["status"] == "passed"
    assert payload["understanding_summary"]["model_id"] == "eum_existing"
