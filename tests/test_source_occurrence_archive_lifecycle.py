from __future__ import annotations

import io
import zipfile

from ai_test_asset_center.enterprise_knowledge_center._utils import _load_registry
from ai_test_asset_center.enterprise_knowledge_center.source_occurrence_core import (
    ingest_enterprise_knowledge_documents,
)


ACTOR = {"name": "archive-occurrence-test", "role": "project_owner"}
OLD = b"# Shared Rule\nOnly OPEN orders may be refunded.\n"
NEW = b"# Shared Rule\nOPEN and REVIEW orders may be refunded.\n"


def _zip(entries: dict[str, bytes]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return target.getvalue()


def _archive(ref: str, entries: dict[str, bytes]) -> dict:
    return {
        "content_bytes": _zip(entries),
        "filename": "shared.zip",
        "source_type": "other_document",
        "external_ref": ref,
    }


def _direct(ref: str, data: bytes) -> dict:
    return {
        "content_bytes": data,
        "filename": "rules.md",
        "source_type": "business_rules",
        "external_ref": ref,
    }


def test_removed_archive_member_retires_only_its_occurrence(tmp_path) -> None:
    first = ingest_enterprise_knowledge_documents(
        "archive-occurrence-remove",
        [
            _archive(
                "packages/shared.zip",
                {
                    "a/rules.md": OLD,
                    "b/rules.md": OLD,
                },
            )
        ],
        root=tmp_path,
        actor=ACTOR,
    )
    assert first["ok"] is True
    assert first["source_count"] == 1
    assert first["source_occurrence_count"] == 2

    second = ingest_enterprise_knowledge_documents(
        "archive-occurrence-remove",
        [_archive("packages/shared.zip", {"b/rules.md": OLD})],
        root=tmp_path,
        actor=ACTOR,
    )

    assert second["ok"] is True
    assert second["source_count"] == 1
    assert second["source_occurrence_count"] == 1
    reconciliation = second["source_occurrence_reconciliations"][0]
    assert reconciliation["status"] == "PASS"
    assert len(reconciliation["retired_source_occurrence_ids"]) == 1
    assert reconciliation["historical_source_bytes_retained"] is True

    registry = _load_registry("archive-occurrence-remove", tmp_path)
    active = [
        row
        for row in registry["source_occurrences"]
        if row["status"] == "active"
    ]
    retired = [
        row
        for row in registry["source_occurrences"]
        if row["status"] == "retired_archive_member"
    ]
    assert len(active) == 1
    assert active[0]["source_ref"].endswith("!/b/rules.md")
    assert len(retired) == 1
    assert retired[0]["source_ref"].endswith("!/a/rules.md")
    canonical = next(row for row in registry["sources"] if row["status"] == "active")
    assert canonical["identity_role"] == "CANONICAL_INTERPRETATION"
    assert canonical["archive_provenance"] == {}
    assert canonical["logical_key"].startswith("interpretation:")


def test_archive_member_update_does_not_supersede_shared_direct_occurrence(tmp_path) -> None:
    direct = ingest_enterprise_knowledge_documents(
        "archive-occurrence-update",
        [_direct("departments/support/rules.md", OLD)],
        root=tmp_path,
        actor=ACTOR,
    )
    assert direct["ok"] is True
    old_canonical_id = direct["source_occurrences"][0]["canonical_source_id"]

    shared_archive = ingest_enterprise_knowledge_documents(
        "archive-occurrence-update",
        [_archive("packages/shared.zip", {"rules.md": OLD})],
        root=tmp_path,
        actor=ACTOR,
    )
    assert shared_archive["ok"] is True
    assert shared_archive["source_count"] == 1
    assert shared_archive["source_occurrence_count"] == 2

    changed_archive = ingest_enterprise_knowledge_documents(
        "archive-occurrence-update",
        [_archive("packages/shared.zip", {"rules.md": NEW})],
        root=tmp_path,
        actor=ACTOR,
    )

    assert changed_archive["ok"] is True
    assert changed_archive["source_count"] == 2
    assert changed_archive["source_occurrence_count"] == 2
    registry = _load_registry("archive-occurrence-update", tmp_path)
    active_occurrences = [
        row
        for row in registry["source_occurrences"]
        if row["status"] == "active"
    ]
    direct_occurrence = next(
        row
        for row in active_occurrences
        if row["source_ref"] == "departments/support/rules.md"
    )
    archive_occurrence = next(
        row
        for row in active_occurrences
        if row["source_ref"].endswith("!/rules.md")
    )
    assert direct_occurrence["canonical_source_id"] == old_canonical_id
    assert archive_occurrence["canonical_source_id"] != old_canonical_id

    active_canonical_ids = {
        row["source_id"]
        for row in registry["sources"]
        if row["status"] == "active"
    }
    assert direct_occurrence["canonical_source_id"] in active_canonical_ids
    assert archive_occurrence["canonical_source_id"] in active_canonical_ids
    assert all(
        row.get("archive_provenance") == {}
        for row in registry["sources"]
        if row["status"] == "active"
    )
