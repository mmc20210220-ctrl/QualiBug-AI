"""Static guardrails for repository root hygiene.

AGENTS.md ("One-off Artifact Convention (Repo-root Hygiene Boundary)") requires
the repository root to hold only product code, configuration, and tracked
release/test baselines.  One-off debug scripts (``_*.py``) and run artifacts
(``*.txt``, ``*.log``, ``*.json``) belong in ``.scratch/``, which is ignored.

This is not cosmetics.  ``.gitignore`` already stops these files from ever being
*committed*, and that is precisely why they piled up unnoticed: 2026-08-31 found
**141 ignored files sitting in the root** — 55 one-off scripts and 79 run
artifacts, including ``scan_result5.json`` through ``scan_result26.json`` and
seven numbered copies of the same debug probe.  Git-clean and physically clean
are different properties; only the second keeps the repository navigable, and
only a test can enforce the second.

Unlike the observability ratchet this gate is a **hard rule**, because the
achievable target is zero: the 134 offending files were moved into ``.scratch/``
in the same change that introduced this test.  A ratchet would have recorded
"134 pieces of clutter" as acceptable forever.

The allowlist is intentionally explicit rather than derived from ``git ls-files``.
Deriving it would mean that ``git add``-ing a stray artifact silently redefines
the boundary; listing them forces each root baseline to be a reviewed decision.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Root-level files that are deliberately kept.  Every entry is either a
# dependency manifest or a tracked release/test baseline consumed by product
# code, tests, or workflows.  Adding to this set is a reviewed decision —
# keep it in sync with the "One-off Artifact Convention" table in AGENTS.md.
ROOT_ALLOWLIST = frozenset(
    {
        # Dependency manifests
        "requirements.txt",
        "requirements-local-run.txt",
        "requirements-optional.txt",
        "package.json",
        "package-lock.json",
        # Tracked baselines consumed by product code, tests, or workflows
        "field_level_golden_rule_set.json",
        "project_f_acceptance_thresholds.json",
        "project_f_finding_seal.json",
        "project_f_release_manifest.json",
        "project_f_runtime_combination_contribution.json",
        "project_f_runtime_effect_final_report.json",
        "project_f_runtime_execution_ledger.json",
        "project_f_runtime_mechanism_contribution.json",
        "project_f_runtime_precision_metrics.json",
        "project_f_runtime_recall_metrics.json",
        "project_f_runtime_result_classification.json",
        "project_g_entry_gate.json",
    }
)

# Patterns AGENTS.md reserves for .scratch/: one-off scripts and run artifacts.
OFFENDING_PATTERNS = ("_*.py", "*.txt", "*.log", "*.json")


def _root_offenders() -> list[str]:
    """Root-level files matching a .scratch/ pattern that are not allowlisted."""

    offenders: list[str] = []
    for pattern in OFFENDING_PATTERNS:
        for path in REPO_ROOT.glob(pattern):
            if not path.is_file():
                continue
            if path.name in ROOT_ALLOWLIST:
                continue
            offenders.append(path.name)
    return sorted(offenders)


def test_root_holds_no_one_off_scripts_or_run_artifacts() -> None:
    """The root must not accumulate .scratch/ material.

    Debug scripts and scan/run output go to ``.scratch/`` (git-ignored).  Being
    ignored is not the same as being tidy: ignored files still fill the working
    directory and hide the files that matter, and ``git status`` will never
    mention them.

    If a file here is a genuine tracked baseline, add it to ``ROOT_ALLOWLIST``
    and to the AGENTS.md convention table.  Otherwise move it to ``.scratch/``.
    """

    offenders = _root_offenders()
    assert not offenders, (
        f"repository root holds {len(offenders)} file(s) that belong in .scratch/.\n"
        "Move one-off debug scripts (_*.py) and run artifacts (*.txt, *.log, "
        "*.json) into .scratch/, or — if one is a real tracked baseline — add "
        "it to ROOT_ALLOWLIST and to the AGENTS.md convention table.\n"
        + "\n".join(f"  {name}" for name in offenders)
    )


def test_allowlisted_root_files_actually_exist() -> None:
    """An allowlist entry for a file that is gone is dead permission.

    Without this, ``ROOT_ALLOWLIST`` quietly grows exemptions for baselines that
    were deleted months ago, and the exemption keeps letting new clutter in
    under a name nobody remembers.
    """

    missing = sorted(
        name
        for name in ROOT_ALLOWLIST
        if not (REPO_ROOT / name).is_file()
    )
    assert not missing, (
        "ROOT_ALLOWLIST names file(s) that no longer exist — remove them so the "
        "exemption cannot be reused by a future file of the same name:\n"
        + "\n".join(f"  {name}" for name in missing)
    )
