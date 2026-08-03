"""DEPRECATED shim — the JSON-file knowledge base has been retired.

The single source of truth for learned knowledge is the SQLite knowledge
base: :mod:`ai_test_asset_center.learning_knowledge_db`
(``platform_outputs/{project}/knowledge.db``). The old per-entry JSON files
under ``platform_outputs/{project}/learning_knowledge_base/`` are legacy
artifacts; ``tools/migrate_json_to_sqlite.py`` migrates them.

This shim keeps the historical import path alive and delegates every
operation to the SQLite implementation so no caller can accidentally fork
the store of truth again. New code must import ``LearningKnowledgeDB``
directly.
"""
from __future__ import annotations

import logging
import warnings

from .learning_knowledge_db import KnowledgeEntry, LearningKnowledgeDB

logger = logging.getLogger(__name__)

__all__ = ["LearningKnowledgeBase", "KnowledgeEntry"]


class LearningKnowledgeBase(LearningKnowledgeDB):
    """Deprecated alias for :class:`LearningKnowledgeDB` (SQLite-backed).

    Kept only for backward compatibility with the retired JSON-file
    knowledge base. All state lives in SQLite; nothing is read from or
    written to the legacy JSON directory by product code anymore.
    """

    def __init__(self, project: str):
        warnings.warn(
            "LearningKnowledgeBase (JSON) is retired; delegating to the "
            "SQLite knowledge base (LearningKnowledgeDB). Import "
            "ai_test_asset_center.learning_knowledge_db instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning(
            "learning_knowledge_base shim engaged for project=%s; "
            "delegating to SQLite knowledge base",
            project,
        )
        super().__init__(project=project)
