# -*- coding: utf-8 -*-
"""Mainline Reasoner 输入指纹：内容寻址判变 + 复用状态持久化。

AGENTS.md Enterprise Understanding Lifecycle Contract: unchanged enterprise
material must not be resent to an LLM.  The 11-engine Reasoner
(stage_reason_all_v2) is the only mainline stage that re-invokes LLM
comprehension on every run regardless of source revision.  This module gives
the planning mainline a content-addressed gate: the Reasoner's full
deterministic input — raw PRD text + raw API text + the projected world model
(comprehension bridge) + model + temperature — is hashed; when the hash matches
the persisted reuse state of a prior successful run, the mainline skips the
LLM entirely and replays the persisted hypotheses through the same governed
bridge.  Any input change (source text, knowledge asset, model, temperature)
misses automatically.

Honesty rules (mirror reasoner_response_cache):
- Reuse state is written ONLY from a real LLM execution whose meta status is
  ``ok``/``empty`` (never from a failed / degraded / provider-unavailable
  run), and it records the run identity (run_id / campaign_id / strategy
  fingerprint) that produced it.
- A reused run is a replay, not fresh comprehension: the mainline receipt is
  stamped ``REUSED`` with the persisted hypotheses count and the persisted
  run identity, so a scan consumer can tell that no new LLM comprehension
  happened.
- Fail-open: any state read/write error or corruption degrades to a normal
  LLM run (never a wrong skip, never a crash).
  ``QUALIBUG_MAINLINE_REASONER_REUSE_DISABLED=1`` bypasses the gate entirely
  for deterministic tests and debugging.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from .enterprise_knowledge_center._utils import _paths

_LOGGER = logging.getLogger("qualibug.discovery_planning.reasoner_fingerprint")

DISABLE_ENV = "QUALIBUG_MAINLINE_REASONER_REUSE_DISABLED"
_STATE_SCHEMA = "qualibug.mainline-reasoner-reuse-state.v1"
_FINGERPRINT_SCHEMA = "qualibug.mainline-reasoner-input-fingerprint.v1"
_REUSABLE_PRIOR_STATUSES = {"ok", "empty"}


def _disabled() -> bool:
    return str(os.environ.get(DISABLE_ENV, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _canonical_json(value: Any) -> str:
    """Deterministic serialization for content addressing."""
    return json.dumps(
        value if isinstance(value, dict) else {},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _provider_identity() -> dict[str, str] | None:
    """Return the model identity the Reasoner would use, or None when the LLM
    provider is not configured (mirror of collect_reasoner_hypotheses)."""
    from .llm_reasoning import ReasoningConfig

    config = ReasoningConfig.from_env()
    if not config.enabled:
        return None
    return {
        "model": str(config.model or ""),
        "temperature": str(config.temperature),
    }


def compute_reasoner_input_fingerprint(
    prd_text: str,
    api_spec_text: str,
    world_model: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Content-addressed fingerprint of the Reasoner's full deterministic input.

    Returns None when the LLM provider is not configured (the Reasoner would
    no-op with ``provider_unavailable``, so there is nothing to reuse).
    """
    if _disabled():
        return None
    identity = _provider_identity()
    if identity is None:
        return None
    components = {
        "prd_text": _sha256(str(prd_text or "")),
        "api_spec_text": _sha256(str(api_spec_text or "")),
        "world_model": _sha256(_canonical_json(world_model)),
        "model": identity["model"],
        "temperature": identity["temperature"],
    }
    fingerprint = _sha256(
        "|".join(
            [
                components["prd_text"],
                components["api_spec_text"],
                components["world_model"],
                components["model"],
                components["temperature"],
            ]
        )
    )
    return {
        "schema": _FINGERPRINT_SCHEMA,
        "sha256": fingerprint,
        "components": components,
    }


def load_reasoner_reuse_state(
    project_id: str, root: Path
) -> dict[str, Any] | None:
    """Load the persisted reuse state. Corrupt/unreadable/missing → None
    (fail-open to a fresh LLM run)."""
    if _disabled():
        return None
    path = _paths(project_id, root)["reasoner_reuse"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("schema") or "") != _STATE_SCHEMA:
        return None
    if not isinstance(payload.get("sha256"), str) or not payload["sha256"]:
        return None
    return payload


def persist_reasoner_reuse_state(
    state: dict[str, Any],
    *,
    project_id: str,
    root: Path,
) -> bool:
    """Atomically persist the reuse state. Best-effort: a write failure must
    never abort the scan, so it logs and returns False (next run recomputes)."""
    if _disabled():
        return False
    try:
        payload = {"schema": _STATE_SCHEMA, **state}
        path = _paths(project_id, root)["reasoner_reuse"]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(path)
        return True
    except (OSError, TypeError, ValueError) as exc:
        _LOGGER.warning(
            "mainline_reasoner_reuse_state_persist_failed %s: %s",
            type(exc).__name__,
            str(exc)[:200],
        )
        return False
