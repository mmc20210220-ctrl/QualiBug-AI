from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

import ai_test_asset_center.connector_remote_lifecycle as lifecycle

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
ACTOR = {"name": "tester", "role": "knowledge_admin"}


def test_public_lifecycle_authority_runs_inside_knowledge_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events = []

    @contextmanager
    def transaction(root, project_id, *, operation, actor, wait_seconds):
        events.append(("enter", project_id, operation, wait_seconds, actor["name"]))
        yield {"transaction_id": "txn-one"}
        events.append(("exit", project_id, operation))

    monkeypatch.setattr(lifecycle, "knowledge_transaction", transaction)
    monkeypatch.setattr(
        lifecycle,
        "_reconcile_connector_remote_lifecycle_unlocked",
        lambda *args, **kwargs: {
            "status": "COMPLETE",
            "customer_material_mutation_executed": False,
        },
    )

    result = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[],
        sync_epoch_id="sync-one",
        root=tmp_path,
        actor=ACTOR,
        transaction_wait_seconds=7.5,
    )

    assert result["status"] == "COMPLETE"
    assert events == [
        (
            "enter",
            PROJECT,
            "reconcile_connector_remote_lifecycle",
            7.5,
            "tester",
        ),
        ("exit", PROJECT, "reconcile_connector_remote_lifecycle"),
    ]


def test_transaction_contention_fails_closed_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unlocked_calls = []

    @contextmanager
    def busy(*args, **kwargs):
        raise lifecycle.KnowledgeTransactionBusy()
        yield  # pragma: no cover

    monkeypatch.setattr(lifecycle, "knowledge_transaction", busy)
    monkeypatch.setattr(
        lifecycle,
        "_reconcile_connector_remote_lifecycle_unlocked",
        lambda *args, **kwargs: unlocked_calls.append(kwargs),
    )

    with pytest.raises(
        lifecycle.ConnectorRemoteLifecycleError,
        match="connector_remote_lifecycle_transaction_busy",
    ):
        lifecycle.reconcile_connector_remote_lifecycle(
            PROJECT,
            connector_instance_id=CONNECTOR,
            present_resources=[],
            sync_epoch_id="sync-busy",
            root=tmp_path,
            actor=ACTOR,
        )

    assert unlocked_calls == []
