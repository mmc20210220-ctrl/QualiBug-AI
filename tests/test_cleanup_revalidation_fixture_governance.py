from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVALIDATION = ROOT / "artifacts" / "spec_v1_6_2_cleanup_reval"


def test_cleanup_revalidation_has_no_direct_target_seed_writer() -> None:
    seed_writer = REVALIDATION / "_seed_shipped_order.py"
    assert not seed_writer.exists()

    offenders = []
    for runner in sorted(REVALIDATION.glob("_run_*_isolated.py")):
        if "_seed_shipped_order.py" in runner.read_text(encoding="utf-8"):
            offenders.append(runner.name)
    assert offenders == []
