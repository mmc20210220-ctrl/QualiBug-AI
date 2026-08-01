from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    identity_structural_review_command as command,
)


def test_review_command_holds_project_transaction_around_core_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, object]] = []

    @contextmanager
    def fake_transaction(
        root: Path,
        project: str,
        *,
        operation: str,
        actor: dict,
    ):
        events.append(("lock-enter", (root, project, operation, actor)))
        try:
            yield {"token": "lease"}
        finally:
            events.append(("lock-exit", project))

    def fake_record(project: str, **kwargs: object) -> dict:
        events.append(("core-record", (project, kwargs)))
        return {"ok": True, "ground_truth_mutated": False}

    monkeypatch.setattr(command, "knowledge_transaction", fake_transaction)
    monkeypatch.setattr(command, "_record_review_decision", fake_record)

    result = command.record_identity_structural_review_decision(
        "demo",
        candidate_id="candidate:orders",
        action="CONFIRM_IDENTITY_ALIAS",
        canonical_entity_id="entity:order",
        rationale="人工确认",
        actor={"name": "owner", "role": "OWNER", "tenant_id": "tenant-a"},
        root=tmp_path,
        rebuild=True,
    )

    assert [name for name, _payload in events] == [
        "lock-enter",
        "core-record",
        "lock-exit",
    ]
    lock_payload = events[0][1]
    assert lock_payload[1] == "demo"
    assert lock_payload[2] == "identity_structural_review_decision"
    assert lock_payload[3] == {
        "name": "owner",
        "role": "OWNER",
        "tenant_id": "tenant-a",
    }
    core_payload = events[1][1]
    assert core_payload[0] == "demo"
    assert core_payload[1]["candidate_id"] == "candidate:orders"
    assert core_payload[1]["rebuild"] is True
    assert result["knowledge_transaction_serialized"] is True
    assert result["knowledge_transaction_operation"] == (
        "identity_structural_review_decision"
    )
    assert result["ground_truth_mutated"] is False


def test_review_command_requires_actor_before_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entered = False

    @contextmanager
    def fake_transaction(*_args: object, **_kwargs: object):
        nonlocal entered
        entered = True
        yield {}

    monkeypatch.setattr(command, "knowledge_transaction", fake_transaction)

    with pytest.raises(ValueError, match="identity_structural_review_actor_required"):
        command.record_identity_structural_review_decision(
            "demo",
            candidate_id="candidate:orders",
            action="REJECT_IDENTITY_CANDIDATE",
            actor={},
            root=tmp_path,
        )

    assert entered is False
