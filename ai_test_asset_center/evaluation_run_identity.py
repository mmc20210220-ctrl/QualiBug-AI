"""Immutable Git, policy and target identity for evaluator-bound runs."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


RUN_IDENTITY_SCHEMA = "qualibug.evaluation-run-identity.v1"
TARGET_ASSET_SCHEMA = "qualibug.evaluator-target-asset.v1"
PROMOTION_BOUND_MODES = frozenset(
    {"baseline", "replay", "shadow", "audit_pack", "promotion"}
)
VALID_MODES = PROMOTION_BOUND_MODES | frozenset({"operational"})
SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")


class EvaluationRunIdentityError(ValueError):
    """Raised when a run cannot prove immutable evaluation identity."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any, field: str) -> str:
    match = SHA256_RE.fullmatch(_text(value))
    if not match:
        raise EvaluationRunIdentityError(f"{field} must be a SHA-256 fingerprint")
    return f"sha256:{match.group(1).lower()}"


def _git(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvaluationRunIdentityError(
            f"RUN_IDENTITY_GIT_UNAVAILABLE: git {' '.join(args)} failed"
        ) from exc
    return completed.stdout.strip()


def build_evaluation_run_identity(
    *,
    repo_root: Path | str,
    target_asset_receipt: dict[str, Any],
    policy_id: str,
    source_snapshot_hash: str,
    fixture_fingerprint: str,
    reset_fingerprint: str,
    evaluation_mode: str,
) -> dict[str, Any]:
    """Bind one evaluation run to clean code and immutable input identities."""
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise EvaluationRunIdentityError("repo_root must be an existing directory")
    if not isinstance(target_asset_receipt, dict) or target_asset_receipt.get("schema_version") != TARGET_ASSET_SCHEMA:
        raise EvaluationRunIdentityError("target_asset_receipt uses an unsupported schema")
    target_id = _text(target_asset_receipt.get("target_id"))
    environment_id = _text(target_asset_receipt.get("environment_id"))
    if not target_id or not environment_id or target_id == environment_id:
        raise EvaluationRunIdentityError(
            "target_asset_receipt must separate target and environment identities"
        )
    mode = _text(evaluation_mode).lower()
    if mode not in VALID_MODES:
        raise EvaluationRunIdentityError(f"unsupported evaluation_mode: {mode!r}")
    policy = _text(policy_id)
    if not policy:
        raise EvaluationRunIdentityError("policy_id is required")

    commit = _git(root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
        raise EvaluationRunIdentityError("RUN_IDENTITY_GIT_COMMIT_INVALID")
    branch = _git(root, "branch", "--show-current")
    porcelain = _git(root, "status", "--porcelain", "--untracked-files=all")
    clean = not bool(porcelain)
    blockers = [] if clean else ["RUN_IDENTITY_DIRTY_WORKTREE"]
    if not clean and mode in PROMOTION_BOUND_MODES:
        raise EvaluationRunIdentityError(
            f"RUN_IDENTITY_DIRTY_WORKTREE: {mode} requires a clean committed worktree"
        )

    canonical = {
        "schema_version": RUN_IDENTITY_SCHEMA,
        "evaluation_mode": mode,
        "git_commit": commit.lower(),
        "git_branch": branch,
        "worktree_clean": clean,
        "promotion_eligible": clean and mode in PROMOTION_BOUND_MODES,
        "blocking_codes": blockers,
        "target_id": target_id,
        "environment_id": environment_id,
        "target_asset_fingerprint": _fingerprint(
            target_asset_receipt.get("receipt_fingerprint"),
            "target_asset_receipt.receipt_fingerprint",
        ),
        "policy_id": policy,
        "source_snapshot_fingerprint": _fingerprint(
            source_snapshot_hash,
            "source_snapshot_hash",
        ),
        "fixture_fingerprint": _fingerprint(
            fixture_fingerprint,
            "fixture_fingerprint",
        ),
        "reset_fingerprint": _fingerprint(
            reset_fingerprint,
            "reset_fingerprint",
        ),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**canonical, "receipt_fingerprint": f"sha256:{digest}"}


def validate_evaluation_run_identity(
    identity: dict[str, Any],
    *,
    policy_id: str,
    evaluation_mode: str,
    target_id: str | None = None,
) -> dict[str, Any]:
    """Validate identity integrity and agreement with an evaluator envelope."""
    if not isinstance(identity, dict) or identity.get("schema_version") != RUN_IDENTITY_SCHEMA:
        raise EvaluationRunIdentityError("run_identity uses an unsupported schema")
    unsigned = dict(identity)
    claimed = _fingerprint(unsigned.pop("receipt_fingerprint", None), "run_identity.receipt_fingerprint")
    expected = "sha256:" + hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if claimed != expected:
        raise EvaluationRunIdentityError("run_identity fingerprint mismatch")
    if _text(identity.get("policy_id")) != _text(policy_id):
        raise EvaluationRunIdentityError("run_identity.policy_id does not match the evaluated run")
    if _text(identity.get("evaluation_mode")).lower() != _text(evaluation_mode).lower():
        raise EvaluationRunIdentityError("run_identity.evaluation_mode does not match the evaluated run")
    if target_id is not None and _text(identity.get("target_id")) != _text(target_id):
        raise EvaluationRunIdentityError(
            "run_identity.target_id does not match the evaluated target"
        )
    mode = _text(evaluation_mode).lower()
    if mode in PROMOTION_BOUND_MODES and identity.get("worktree_clean") is not True:
        raise EvaluationRunIdentityError("RUN_IDENTITY_DIRTY_WORKTREE")
    return dict(identity)
