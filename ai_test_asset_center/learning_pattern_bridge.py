"""
Learning Pattern Bridge - Integrates closed-loop feedback with SQLite knowledge base.

This module provides seamless integration between the closed-loop learning system
and the enterprise-grade SQLite knowledge storage, ensuring:
- All learned patterns are stored in SQLite (not just JSON files)
- Cross-round knowledge transfer works automatically
- Performance benefits from indexed queries
- ACID transactions for data integrity

Usage:
    from .learning_pattern_bridge import LearningPatternBridge
    
    bridge = LearningPatternBridge(project="my_project")
    
    # Store patterns from closed-loop feedback
    bridge.store_patterns(patterns, scan_id="scan_001")
    
    # Retrieve patterns for next scan
    patterns = bridge.get_top_patterns(limit=20)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Import SQLite knowledge base
import sys
from pathlib import Path as PathLib
_REPO_ROOT = PathLib(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from ai_test_asset_center.learning_knowledge_db import LearningKnowledgeDB


class LearningPatternBridge:
    """Bridge between closed-loop feedback and SQLite knowledge base."""
    
    def __init__(self, project: str):
        self.project = project
        self.kb = LearningKnowledgeDB(project=project)
        
        # Legacy JSON file path for backward compatibility
        self.pool_dir = Path(_REPO_ROOT) / "platform_outputs" / project / "closed_loop"
        self.patterns_file = self.pool_dir / "bug_patterns.json"
        
    def store_patterns(
        self,
        patterns: list[dict],
        scan_id: str,
        confidence: float = 0.8,
        *,
        confidence_map: dict[str, float] | None = None,
    ) -> int:
        """Store learned patterns in SQLite knowledge base.
        
        Args:
            patterns: List of pattern dicts from closed-loop feedback
            scan_id: Current scan identifier
            confidence: Default confidence for each pattern
            confidence_map: Optional per-signature confidence overrides.
                Entries present in the map use that value; others use
                ``confidence``. Callers use this to keep decayed entries
                decayed (store() otherwise keeps max(new, existing), which
                would resurrect non-reinforced decay).
            
        Returns:
            Number of patterns stored
        """
        stored_count = 0
        
        for pattern in patterns:
            try:
                # Extract key fields
                key = pattern.get("signature", pattern.get("type", "unknown"))
                content = {
                    "signature": pattern.get("signature"),
                    "type": pattern.get("type"),
                    "entity": pattern.get("entity"),
                    "mutation": pattern.get("mutation_hint", ""),
                    "source_scan": scan_id,
                    "stored_at": datetime.now().isoformat(),
                    # Comprehension-layer semantics carried through from the
                    # closed-loop extractor (observed finding fields only).
                    "assertion_kind": pattern.get("assertion_kind") or pattern.get("type"),
                    "actor": pattern.get("actor", ""),
                    "semantic_summary": pattern.get("semantic_summary", ""),
                    "behavior_delta": pattern.get("behavior_delta"),
                }
                entry_confidence = float(
                    (confidence_map or {}).get(str(key), confidence)
                )
                
                # Store in SQLite
                self.kb.store(
                    category="risk_pattern",
                    key=key,
                    content=content,
                    confidence=entry_confidence,
                    domains=["web", "api"],  # Default domains
                    expiry_days=365  # Keep for 1 year
                )
                stored_count += 1
                
            except Exception as e:
                logger.warning("Failed to store pattern %s: %s", key, e)
                continue
        
        logger.info("Stored %d patterns to SQLite knowledge base", stored_count)

        # Decay scheduling: stale knowledge loses confidence so the pool
        # stays honest. Propagates on failure (fail-visible upstream).
        decayed = self.kb.decay_old_knowledge(max_age_days=90)
        if decayed:
            logger.info("Decayed %d stale knowledge entries", decayed)
        return stored_count
        
    def get_top_patterns(self, limit: int = 20, min_usage: int = 1) -> list[dict]:
        """Get top patterns by usage count from SQLite.
        
        Args:
            limit: Maximum number of patterns to return
            min_usage: Minimum usage count filter
            
        Returns:
            List of pattern dicts sorted by usage
        """
        try:
            effective_patterns = self.kb.get_effective_patterns(
                "risk_pattern", 
                min_usage=min_usage
            )
            
            # Convert to dict format
            patterns = [e.content for e in effective_patterns[:limit]]
            
            return patterns
            
        except Exception as e:
            logger.warning("Failed to retrieve patterns: %s", e)
            return []

    def load_learned_context(self, limit: int = 20) -> dict[str, Any]:
        """Load learned knowledge from SQLite as scan-ready context.

        This is the READ side of the closed loop: called at scan start so
        knowledge learned in previous rounds is consumed by the next scan.
        Fails safe with an explicit reason so missing knowledge is visible.

        Returns:
            Dict with learned_patterns, pattern_count, source metadata.
        """
        try:
            entries = self.kb.get_effective_patterns("risk_pattern", min_usage=0)
            entries = sorted(
                entries, key=lambda e: (e.usage_count, e.confidence), reverse=True
            )[:limit]

            patterns = []
            for e in entries:
                item = dict(e.content) if isinstance(e.content, dict) else {}
                item.setdefault("_key", e.key)
                item.setdefault("_confidence", e.confidence)
                item.setdefault("_usage_count", e.usage_count)
                patterns.append(item)

            # Usage feedback: patterns loaded for a scan are consumed by the
            # planning stage, so record the usage here. Failures propagate to
            # stay visible instead of silently breaking the feedback loop.
            usage_recorded = self.kb.record_usage([e.entry_id for e in entries])

            return {
                "source": "sqlite_knowledge_base",
                "project": self.project,
                "pattern_count": len(patterns),
                "learned_patterns": patterns,
                "usage_recorded": usage_recorded,
                "loaded_at": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.warning("Failed to load learned context: %s", e)
            return {
                "source": "sqlite_knowledge_base",
                "project": self.project,
                "pattern_count": 0,
                "learned_patterns": [],
                "load_failure": "{}:{}".format(type(e).__name__, str(e)[:200]),
            }
            
    def load_binding_experience(self, limit: int = 50) -> list[dict[str, Any]]:
        """Load verified binding-resolver mappings from the knowledge base.

        READ side of binding-experience learning: entries recorded by
        ``binding_experience_learning.build_binding_experience_context``.
        Only source-declared resolver identities are carried (operation_ref,
        target, path) — never resolved business values. Usage is recorded so
        the reinforcement loop observes consumption.
        """
        try:
            entries = self.kb.get_effective_patterns(
                "binding_resolver", min_usage=0
            )
            entries = sorted(
                entries, key=lambda e: (e.usage_count, e.confidence), reverse=True
            )[:limit]
            resolvers = []
            for e in entries:
                content = dict(e.content) if isinstance(e.content, dict) else {}
                resolvers.append({
                    "key": e.key,
                    "operation_ref": str(content.get("operation_ref") or ""),
                    "target": str(content.get("target") or ""),
                    "path": str(content.get("path") or ""),
                    "success_count": int(content.get("success_count") or e.usage_count or 1),
                    "confidence": e.confidence,
                    "_usage_count": e.usage_count,
                })
            if entries:
                self.kb.record_usage([e.entry_id for e in entries])
            return resolvers
        except Exception as e:
            logger.warning("Failed to load binding experience: %s", e)
            return []

    def migrate_legacy_patterns_to_sqlite(self) -> int:
        """Migrate patterns from legacy JSON file to SQLite.
        
        Returns:
            Number of patterns migrated
        """
        if not self.patterns_file.exists():
            logger.info("No legacy patterns file found")
            return 0
            
        try:
            # Load legacy JSON
            with open(self.patterns_file, "r", encoding="utf-8") as f:
                history = json.load(f)
            
            patterns_data = history.get("patterns", {})
            migrated_count = 0
            
            for key, value in patterns_data.items():
                pattern = value.get("pattern", {})
                
                # Store in SQLite
                self.kb.store(
                    category="risk_pattern",
                    key=key,
                    content=pattern,
                    confidence=0.7,  # Default confidence for migrated data
                    domains=["web", "api"],
                    expiry_days=None  # No expiry for legacy data
                )
                migrated_count += 1
                
            logger.info("Migrated %d legacy patterns to SQLite", migrated_count)
            return migrated_count
            
        except Exception as e:
            logger.error("Failed to migrate legacy patterns: %s", e)
            return 0
            
    def get_cross_round_insights(self) -> dict[str, Any]:
        """Get insights across multiple discovery rounds.
        
        Returns:
            Dictionary with cross-round analysis
        """
        try:
            # Get all risk patterns
            all_patterns = self.kb.get_effective_patterns("risk_pattern", min_usage=0)
            
            if not all_patterns:
                return {
                    "total_patterns": 0,
                    "high_confidence_patterns": 0,
                    "average_confidence": 0.0,
                    "domains_covered": []
                }
            
            # Calculate statistics
            total = len(all_patterns)
            high_conf = sum(1 for p in all_patterns if p.confidence > 0.7)
            avg_conf = sum(p.confidence for p in all_patterns) / total
            
            # Extract unique domains
            domains = set()
            for pattern in all_patterns:
                domains.update(pattern.domains)
            
            return {
                "total_patterns": total,
                "high_confidence_patterns": high_conf,
                "average_confidence": avg_conf,
                "domains_covered": list(domains),
                "top_patterns": [
                    {"key": e.key, "usage": e.usage_count, "confidence": e.confidence}
                    for e in sorted(all_patterns, key=lambda x: x.usage_count, reverse=True)[:10]
                ]
            }
            
        except Exception as e:
            logger.warning("Failed to get cross-round insights: %s", e)
            return {}
