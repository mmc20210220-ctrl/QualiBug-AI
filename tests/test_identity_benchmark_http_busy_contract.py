from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
)
from ai_test_asset_center.private_pilot_identity_benchmark_handlers import (
    IdentityBenchmarkHttpMixin,
)


class _Handler(IdentityBenchmarkHttpMixin):
    path = "/api/v1/projects/acme/identity-benchmark/ground-truth"
    _qualibug_corr_id = "test"

    def _json(self, payload, status=200, **kwargs):
        return status, payload


def test_busy_knowledge_transaction_is_retryable_conflict() -> None:
    status, payload = _Handler()._identity_benchmark_error(
        KnowledgeTransactionBusy(
            {"operation": "knowledge source ingestion", "project_id": "acme"}
        ),
        project="acme",
    )

    assert status == 409
    assert payload["error"] == "IDENTITY_BENCHMARK_TRANSACTION_BUSY"
    assert payload["retryable"] is True
    assert "owner" not in payload
