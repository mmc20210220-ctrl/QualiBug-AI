"""V1.6.2 Gate B unlock-set and coverage-denominator specialized tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

OUT = Path("artifacts/spec_v1_6_2")


def test_gate_b_first_terminal_rows_equal_1498():
    ledger = json.loads((OUT / "v162_obligation_first_terminal_ledger.json").read_text(encoding="utf-8"))
    assert ledger["terminal_ledger_rows"] == 1498
    assert ledger["missing_rows"] == 0
    assert ledger["duplicate_rows"] == 0
    assert ledger["distribution_sum"] == 1498
    assert not ledger["forbidden_categories_present"]


def test_gate_b_no_forbidden_mass_categories():
    dist = json.loads((OUT / "v162_first_terminal_distribution.json").read_text(encoding="utf-8"))["distribution"]
    assert not ({"UNKNOWN", "FAILED", "OTHER", "NOT_RUN"} & set(dist))
    assert sum(dist.values()) == 1498


def test_gate_b_unlock_set_frozen_single_fix():
    unlock = json.loads((OUT / "v162_candidate_unlock_set.json").read_text(encoding="utf-8"))
    assert unlock["frozen"] is True
    assert unlock["shared_fix_point"] == "FINALIZATION_RECEIPT_FROM_BUNDLE"
    assert unlock["N"] == len(unlock["obligation_ids"]) >= 20
    assert unlock["post_start_mutation_forbidden"] is True


def test_gate_b_unlock_ids_subset_of_canonical_manifest():
    unlock = json.loads((OUT / "v162_candidate_unlock_set.json").read_text(encoding="utf-8"))
    man = json.loads((OUT / "v162_canonical_obligation_manifest.json").read_text(encoding="utf-8"))
    sealed = set(man["canonical_obligation_manifest"]["obligation_ids"])
    assert set(unlock["obligation_ids"]) <= sealed


def test_gate_b_coverage_denominator_is_1498_not_1189():
    man = json.loads((OUT / "v162_canonical_obligation_manifest.json").read_text(encoding="utf-8"))
    assert man["canonical_obligation_manifest"]["obligation_count"] == 1498
    assert man["denominator_authority"]["supersedes"]["prior_count"] == 1189


def test_gate_b_selected_fix_not_budget_relaxation():
    umap = json.loads((OUT / "v162_obligation_unlock_map.json").read_text(encoding="utf-8"))
    assert "BUDGET" not in umap["selected_shared_fix_point"]
    assert umap["selected_shared_fix_point"] != "FORBIDDEN_INDETERMINATE_TO_PASS"
