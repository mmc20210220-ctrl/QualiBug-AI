"""A campaign must be bound to every registered source, not one arbitrary document.

The scan auto-bind iterated the source registry and ``break``-ed on the first asset
with a valid hash. A project with nine ingested enterprise documents therefore ran
its campaign against one of them -- whichever the registry happened to yield first,
or, via the "latest registered source" fallback, whichever was uploaded last.

The truncation was invisible from every angle a reader would check: preflight
reported ``sources: 9 passed``, the runtime contract carried a valid source_id and
a matching 64-char hash, and the result declared ``completion_is_formal: true``.
Nothing anywhere said "eight documents were not read". A run that understood an
eighth of the business looked exactly like a run that understood all of it.

Composition keeps the immutability guarantee the single-source contract provided:
the hash still covers exactly the bytes the campaign reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.enterprise_source_registry import (
    COMPOSED_SOURCE_SUFFIX,
    compose_project_source_manifest,
    list_source_assets,
    load_source_content,
    register_source_asset,
)


PROJECT = "corpus_binding_probe"


def _register(root: Path, sid: str, text: str, source_type: str = "prd") -> None:
    register_source_asset(
        PROJECT, sid, text, source_type=source_type, root=root, origin="test_fixture"
    )


# ── the defect ──────────────────────────────────────────────────────────────

def test_every_registered_source_reaches_the_composition(tmp_path: Path) -> None:
    """The whole point: N documents in, N documents bound."""
    for i in range(9):
        _register(tmp_path, f"src_doc_{i:02d}", f"# Document {i}\n\nRule {i}: value must be {i}.\n")

    manifest = compose_project_source_manifest(PROJECT, root=tmp_path)

    assert manifest["part_count"] == 9
    composed = load_source_content(PROJECT, manifest["source_hash"], root=tmp_path)
    for i in range(9):
        assert f"Rule {i}: value must be {i}." in composed, f"document {i} was dropped"


def test_composition_records_each_part_with_its_own_hash(tmp_path: Path) -> None:
    """An obligation must be traceable to the document it came from.

    A blob of concatenated text with no provenance would fix the breadth problem and
    create an attribution one.
    """
    _register(tmp_path, "src_alpha", "# Alpha\n\nAlpha rule.\n")
    _register(tmp_path, "src_beta", "# Beta\n\nBeta rule.\n")

    manifest = compose_project_source_manifest(PROJECT, root=tmp_path)
    composed = load_source_content(PROJECT, manifest["source_hash"], root=tmp_path)

    for part in manifest["composed_from"]:
        assert part["status"] == "included"
        assert f"source_id={part['source_id']}" in composed
        assert f"source_hash={part['source_hash']}" in composed


# ── the immutability guarantee must survive ─────────────────────────────────

def test_same_corpus_yields_the_same_hash(tmp_path: Path) -> None:
    """Ordering is by source_id, so composition is deterministic.

    A hash that changed per run would make every campaign look like a new source
    and defeat resume entirely.
    """
    _register(tmp_path, "src_b", "# B\n\nB rule.\n")
    _register(tmp_path, "src_a", "# A\n\nA rule.\n")

    first = compose_project_source_manifest(PROJECT, root=tmp_path)
    second = compose_project_source_manifest(PROJECT, root=tmp_path)
    assert first["source_hash"] == second["source_hash"]
    assert len(first["source_hash"]) == 64


def test_a_changed_corpus_yields_a_different_hash(tmp_path: Path) -> None:
    """Adding a document must change the contract, or a resumed campaign would
    silently keep using the old corpus."""
    _register(tmp_path, "src_a", "# A\n\nA rule.\n")
    _register(tmp_path, "src_b", "# B\n\nB rule.\n")
    before = compose_project_source_manifest(PROJECT, root=tmp_path)["source_hash"]

    _register(tmp_path, "src_c", "# C\n\nC rule.\n")
    after = compose_project_source_manifest(PROJECT, root=tmp_path)["source_hash"]

    assert before != after


def test_aggregate_is_not_folded_into_the_next_aggregate(tmp_path: Path) -> None:
    """Self-inclusion would double the corpus every run and never converge."""
    _register(tmp_path, "src_a", "# A\n\nA rule.\n")
    _register(tmp_path, "src_b", "# B\n\nB rule.\n")

    first = compose_project_source_manifest(PROJECT, root=tmp_path)
    second = compose_project_source_manifest(PROJECT, root=tmp_path)

    assert first["part_count"] == second["part_count"] == 2
    assert first["source_hash"] == second["source_hash"]
    ids = {a["source_id"] for a in second["composed_from"]}
    assert not any(i.endswith(COMPOSED_SOURCE_SUFFIX) for i in ids)


def test_legacy_full_docs_aggregate_is_excluded(tmp_path: Path) -> None:
    """Projects carry a hand-made ``*_full_docs`` aggregate from before this existed.

    Including it would double-count every document it already contains.
    """
    _register(tmp_path, "src_a", "# A\n\nA rule.\n")
    _register(tmp_path, "src_b", "# B\n\nB rule.\n")
    _register(tmp_path, "src_project_full_docs", "# A\n\nA rule.\n\n# B\n\nB rule.\n")

    manifest = compose_project_source_manifest(PROJECT, root=tmp_path)
    assert manifest["part_count"] == 2
    ids = {a["source_id"] for a in manifest["composed_from"]}
    assert "src_project_full_docs" not in ids


# ── degenerate corpora ──────────────────────────────────────────────────────

def test_empty_registry_returns_an_empty_manifest(tmp_path: Path) -> None:
    """No sources means no binding -- not a composition of nothing."""
    manifest = compose_project_source_manifest(PROJECT, root=tmp_path)
    assert manifest["source_id"] == ""
    assert manifest["source_hash"] == ""
    assert manifest["part_count"] == 0


def test_single_source_binds_directly_without_composing(tmp_path: Path) -> None:
    """One document needs no aggregate; wrapping it would change its hash for nothing."""
    _register(tmp_path, "src_only", "# Only\n\nOnly rule.\n")
    manifest = compose_project_source_manifest(PROJECT, root=tmp_path)

    assert manifest["source_id"] == "src_only"
    assert manifest["part_count"] == 1
    assert manifest["composed_from"] == []


def test_missing_blob_is_recorded_not_silently_skipped(tmp_path: Path) -> None:
    """A vanished blob must be visible, or the corpus quietly shrinks back toward
    the single-source behaviour this function exists to remove."""
    _register(tmp_path, "src_a", "# A\n\nA rule.\n")
    _register(tmp_path, "src_b", "# B\n\nB rule.\n")

    assets = {a["source_id"]: a["latest_source_hash"] for a in list_source_assets(PROJECT, root=tmp_path)}
    blob = tmp_path / "platform_workspace" / PROJECT / "source_registry" / "blobs" / f"{assets['src_a']}.txt"
    assert blob.exists()
    blob.unlink()

    manifest = compose_project_source_manifest(PROJECT, root=tmp_path)
    statuses = {p["source_id"]: p["status"] for p in manifest["composed_from"]}
    assert statuses["src_a"] == "blob_missing"
    assert statuses["src_b"] == "included"


# ── the call site no longer truncates ───────────────────────────────────────

def test_scan_handler_binds_the_whole_corpus() -> None:
    """Pins the fix at the call site, not just in the helper.

    Asserted against the auto-bind block specifically so it fails on the defect
    returning rather than on unrelated edits elsewhere in the handler.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "private_pilot_scan_handlers.py"
    ).read_text(encoding="utf-8")

    marker = "if not manifest_valid:"
    assert marker in source
    block = source[source.index(marker): source.index(marker) + 1400]

    assert "compose_project_source_manifest" in block
    assert "list_source_assets" not in block, "the first-asset-then-break bind is back"
    assert "source_composition" in block, "part_count must reach the result, not be silent"
