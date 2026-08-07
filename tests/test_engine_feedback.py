"""Project-scoped engine attention feedback (B5 closed-loop deepening).

Covers: confirmed-defect engine attribution write, bounded weight merge,
staleness decay, prompt nudge, project isolation, and fail-soft receipts.
All paths are deterministic and file-backed under a temp root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import engine_feedback as EF


@pytest.fixture()
def tmp_root(tmp_path: Path) -> Path:
    return tmp_path


def test_attribution_records_confirmed_findings_per_engine(tmp_root: Path) -> None:
    findings = [
        {"_reasoner_engine": "causality", "title": "x"},
        {"_reasoner_engine": "causality", "title": "y"},
        {"engine": "lifecycle", "title": "z"},
        {"title": "no marker"},  # unattributed -> ignored
    ]
    receipt = EF.record_confirmed_engine_attribution(findings, project="proj", root=tmp_root)
    assert receipt["status"] == "CONSUMED"
    assert receipt["engines_updated"] == 3
    records = EF.load_engine_attention_records("proj", root=tmp_root)
    assert records["causality"]["confirmed"] == 2
    assert records["lifecycle"]["confirmed"] == 1
    assert "no marker" not in records


def test_attribution_is_idempotent_and_skips_without_project(tmp_root: Path) -> None:
    findings = [{"_reasoner_engine": "causality", "title": "x"}]
    EF.record_confirmed_engine_attribution(findings, project="proj", root=tmp_root)
    receipt = EF.record_confirmed_engine_attribution(findings, project="proj", root=tmp_root)
    assert receipt["engines_updated"] == 1
    assert EF.load_engine_attention_records("proj", root=tmp_root)["causality"]["confirmed"] == 2

    skipped = EF.record_confirmed_engine_attribution(findings, project="", root=tmp_root)
    assert skipped["status"] == "SKIPPED"
    assert EF.record_confirmed_engine_attribution(findings, project=None, root=tmp_root)["status"] == "SKIPPED"


def test_weights_are_bounded_and_never_reduce_policy_weight(tmp_root: Path) -> None:
    EF.record_confirmed_engine_attribution(
        [{"_reasoner_engine": "causality", "title": f"x{i}"} for i in range(8)],
        project="proj", root=tmp_root,
    )
    weights, receipt = EF.resolve_engine_attention_weights(
        {"causality": 1.0, "invariant": 1.0}, project="proj", root=tmp_root
    )
    # 8 confirmations -> cap at 4 counted -> 1.0 * (1 + 0.25*4) = 2.0 (MAX_WEIGHT)
    assert weights["causality"] == EF.MAX_WEIGHT
    assert weights["invariant"] == 1.0  # policy-only engine untouched
    assert receipt["status"] == "CONSUMED"
    assert receipt["boosted"][0]["confirmed"] == 8


def test_stale_confirmations_do_not_boost(tmp_root: Path) -> None:
    EF.record_confirmed_engine_attribution(
        [{"_reasoner_engine": "causality", "title": "x"}],
        project="proj", root=tmp_root,
    )
    # Rewrite the record with an ancient last_seen to simulate decay.
    path = tmp_root / "platform_outputs" / "proj" / "closed_loop" / EF.WEIGHT_FILE
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    data["engines"]["causality"]["last_seen"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    weights, receipt = EF.resolve_engine_attention_weights(
        {"causality": 1.0}, project="proj", root=tmp_root
    )
    assert weights["causality"] == 1.0
    assert receipt["status"] == "NO_BOOSTED"


def test_project_isolation_and_missing_store(tmp_root: Path) -> None:
    EF.record_confirmed_engine_attribution(
        [{"_reasoner_engine": "causality", "title": "x"}], project="proj-a", root=tmp_root
    )
    weights, receipt = EF.resolve_engine_attention_weights({}, project="proj-b", root=tmp_root)
    assert weights == {}
    assert receipt["status"] == "EMPTY"

    weights, receipt = EF.resolve_engine_attention_weights({"a": 1.0}, project="", root=tmp_root)
    assert weights == {"a": 1.0}
    assert receipt["status"] == "SKIPPED"


def test_prompt_nudge_only_for_engines_with_history(tmp_root: Path) -> None:
    EF.record_confirmed_engine_attribution(
        [{"_reasoner_engine": "causality", "title": "x"}], project="proj", root=tmp_root
    )
    records = EF.load_engine_attention_records("proj", root=tmp_root)
    nudge = EF.build_engine_attention_nudge("causality", records)
    assert "[PROJECT-LEARNED PRIORITY]" in nudge
    assert "1 confirmed defect" in nudge
    assert EF.build_engine_attention_nudge("invariant", records) == ""
    assert EF.build_engine_attention_nudge("causality", {}) == ""
