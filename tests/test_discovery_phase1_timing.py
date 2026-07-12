from __future__ import annotations

import json

import pytest

from tools.discovery_phase1_timing import (
    TimingReceiptError,
    build_environment_fingerprint,
    build_timing_receipt,
    compare_timing_receipts,
    write_immutable_json,
)


COMMAND = ["pytest", "tests/test_discovery_mainline_authority.py", "-q"]


def _receipt(*, samples_ms: list[int], command: list[str] | None = None) -> dict:
    return build_timing_receipt(
        command=list(command or COMMAND),
        samples_ms=samples_ms,
        code_fingerprint="commit-candidate",
        input_fingerprint="input-1",
        environment_fingerprint="env-1",
        created_at_utc="2026-07-12T00:00:00Z",
        runtime_identity={"python": "3.test", "implementation": "CPython"},
        system_identity={"os": "Windows", "machine": "test-cpu"},
    )


def test_timing_receipt_requires_five_matching_warm_runs() -> None:
    with pytest.raises(TimingReceiptError, match="five_warm_runs_required"):
        _receipt(samples_ms=[10, 11])


def test_timing_receipt_records_identity_and_percentiles() -> None:
    receipt = _receipt(samples_ms=[104, 100, 103, 101, 102])

    assert receipt["schema_version"] == "qualibug.discovery-phase1-timing.v1"
    assert receipt["samples_ms"] == [100, 101, 102, 103, 104]
    assert receipt["p50_ms"] == 102
    assert receipt["p95_ms"] == 104
    assert receipt["command"] == COMMAND
    assert len(receipt["command_fingerprint"]) == 64
    assert len(receipt["runtime_fingerprint"]) == 64
    assert len(receipt["system_fingerprint"]) == 64
    assert receipt["receipt_id"].startswith("phase1_timing_")


def test_timing_receipt_rejects_non_positive_samples() -> None:
    with pytest.raises(TimingReceiptError, match="sample_duration_invalid"):
        _receipt(samples_ms=[10, 11, 0, 13, 14])


def test_compare_requires_same_command_and_environment() -> None:
    baseline = _receipt(samples_ms=[100, 101, 102, 103, 104])
    candidate = _receipt(
        samples_ms=[30, 31, 32, 33, 34],
        command=["pytest", "tests/test_other_contract.py", "-q"],
    )

    with pytest.raises(TimingReceiptError, match="timing_identity_mismatch"):
        compare_timing_receipts(baseline, candidate)


def test_compare_rejects_tampered_receipt_content() -> None:
    baseline = _receipt(samples_ms=[100, 101, 102, 103, 104])
    candidate = _receipt(samples_ms=[30, 31, 32, 33, 34])
    candidate["code_fingerprint"] = "tampered-after-measurement"

    with pytest.raises(TimingReceiptError, match="candidate_receipt_integrity_invalid"):
        compare_timing_receipts(baseline, candidate)


def test_compare_requires_at_least_sixty_percent_p50_improvement() -> None:
    baseline = _receipt(samples_ms=[98, 99, 100, 101, 102])
    passing = _receipt(samples_ms=[38, 39, 40, 41, 42])
    failing = _receipt(samples_ms=[39, 40, 41, 42, 43])

    passing_result = compare_timing_receipts(baseline, passing)
    failing_result = compare_timing_receipts(baseline, failing)

    assert passing_result["improvement_ratio"] == pytest.approx(0.60)
    assert passing_result["passed"] is True
    assert failing_result["passed"] is False


def test_environment_fingerprint_is_stable_and_tag_sensitive() -> None:
    runtime = {"python": "3.test", "implementation": "CPython"}
    system = {"os": "Windows", "machine": "test-cpu"}

    first = build_environment_fingerprint(runtime, system, tag="same-host")
    second = build_environment_fingerprint(runtime, system, tag="same-host")
    changed = build_environment_fingerprint(runtime, system, tag="other-host")

    assert first == second
    assert first != changed


def test_timing_receipts_are_create_only(tmp_path) -> None:
    path = tmp_path / "candidate.json"
    receipt = _receipt(samples_ms=[10, 11, 12, 13, 14])

    write_immutable_json(path, receipt)
    assert json.loads(path.read_text(encoding="utf-8")) == receipt

    with pytest.raises(TimingReceiptError, match="receipt_path_exists"):
        write_immutable_json(path, receipt)
