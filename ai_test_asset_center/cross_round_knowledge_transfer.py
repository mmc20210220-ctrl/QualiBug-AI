"""
Cross-Round Knowledge Transfer - Transfer learning across discovery rounds.

This module provides knowledge transfer between discovery rounds:
- Persistent knowledge base for long-term storage
- Cross-round pattern extraction
- Effective probe template reuse
- Failure analysis accumulation
- Domain-specific heuristic evolution

Key features:
- Versioned knowledge entries with confidence scoring
- Similarity-based retrieval
- Automatic decay of old knowledge
- Cross-project knowledge sharing (opt-in)
- Knowledge export/import for portability

Usage:
    from .cross_round_knowledge_transfer import CrossRoundKnowledgeTransfer
    
    transfer = CrossRoundKnowledgeTransfer(project="my_project")
    
    # Transfer knowledge from previous round
    transfer.transfer_from_round(previous_round_id="round_001")
    
    # Get learned patterns for current round
    patterns = transfer.get_learned_patterns()
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class RoundKnowledgeSummary:
    """Summary of knowledge learned in a round."""
    round_id: str
    timestamp: datetime
    risk_patterns_discovered: int
    effective_probes_stored: int
    failure_patterns_analyzed: int
    domains_covered: list[str]
    overall_confidence: float  # 0-1
    
    def to_dict(self) -> dict:
        return {
            "round_id": self.round_id,
            "timestamp": self.timestamp.isoformat(),
            "risk_patterns_discovered": self.risk_patterns_discovered,
            "effective_probes_stored": self.effective_probes_stored,
            "failure_patterns_analyzed": self.failure_patterns_analyzed,
            "domains_covered": self.domains_covered,
            "overall_confidence": self.overall_confidence
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "RoundKnowledgeSummary":
        return cls(
            round_id=data["round_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            risk_patterns_discovered=data["risk_patterns_discovered"],
            effective_probes_stored=data["effective_probes_stored"],
            failure_patterns_analyzed=data["failure_patterns_analyzed"],
            domains_covered=data.get("domains_covered", []),
            overall_confidence=data.get("overall_confidence", 0.0)
        )


class CrossRoundKnowledgeTransfer:
    """Cross-round knowledge transfer manager."""
    
    def __init__(self, project: str):
        self.project = project
        self.kb_dir = REPO_ROOT / "platform_outputs" / project / "learning_knowledge_base"
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        
        # Round history
        self.history_file = self.kb_dir / "round_history.json"
        self.round_history: list[RoundKnowledgeSummary] = self._load_round_history()
        
    def _load_round_history(self) -> list[RoundKnowledgeSummary]:
        """Load round history from disk."""
        if not self.history_file.exists():
            return []
            
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [RoundKnowledgeSummary.from_dict(d) for d in data.get("rounds", [])]
        except Exception as e:
            logger.warning("Failed to load round history: %s", e)
            return []
            
    def _save_round_history(self) -> None:
        """Save round history to disk."""
        data = {
            "project": self.project,
            "updated_at": datetime.now().isoformat(),
            "rounds": [r.to_dict() for r in self.round_history]
        }
        
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
    def record_round_completion(
        self,
        round_id: str,
        risk_patterns: list[dict],
        effective_probes: list[dict],
        failure_patterns: list[dict],
        domains: list[str],
        avg_confidence: float
    ) -> RoundKnowledgeSummary:
        """Record knowledge learned in a completed round."""
        summary = RoundKnowledgeSummary(
            round_id=round_id,
            timestamp=datetime.now(),
            risk_patterns_discovered=len(risk_patterns),
            effective_probes_stored=len(effective_probes),
            failure_patterns_analyzed=len(failure_patterns),
            domains_covered=domains,
            overall_confidence=avg_confidence
        )
        
        self.round_history.append(summary)
        self._save_round_history()
        
        # Store in knowledge base
        self._store_risk_patterns(risk_patterns)
        self._store_effective_probes(effective_probes)
        self._store_failure_patterns(failure_patterns)
        
        logger.info(
            "Recorded round completion: %d patterns, %d probes, %d failures",
            len(risk_patterns), len(effective_probes), len(failure_patterns)
        )
        
        return summary
        
    def _store_risk_patterns(self, patterns: list[dict]) -> None:
        """Store discovered risk patterns in knowledge base."""
        from .learning_knowledge_base import LearningKnowledgeBase
        
        kb = LearningKnowledgeBase(self.project)
        
        for pattern in patterns:
            key = pattern.get("signature", pattern.get("type", "unknown"))
            kb.store(
                category="risk_pattern",
                key=key,
                content=pattern,
                confidence=pattern.get("confidence", 0.7),
                domains=pattern.get("domains", []),
                expiry_days=365  # Keep for 1 year
            )
            
    def _store_effective_probes(self, probes: list[dict]) -> None:
        """Store effective probe templates in knowledge base."""
        from .learning_knowledge_base import LearningKnowledgeBase
        
        kb = LearningKnowledgeBase(self.project)
        
        for probe in probes:
            key = probe.get("probe_type", probe.get("name", "unknown"))
            kb.store(
                category="probe_template",
                key=key,
                content=probe,
                confidence=probe.get("effectiveness", 0.7),
                domains=probe.get("domains", []),
                expiry_days=180  # Keep for 6 months
            )
            
    def _store_failure_patterns(self, patterns: list[dict]) -> None:
        """Store failure patterns in knowledge base."""
        from .learning_knowledge_base import LearningKnowledgeBase
        
        kb = LearningKnowledgeBase(self.project)
        
        for pattern in patterns:
            key = pattern.get("failure_type", pattern.get("reason", "unknown"))
            kb.store(
                category="failure_analysis",
                key=key,
                content=pattern,
                confidence=pattern.get("confidence", 0.6),
                expiry_days=90  # Keep for 3 months
            )
            
    def transfer_from_round(self, source_round_id: str) -> dict[str, Any]:
        """Transfer knowledge from a specific previous round."""
        # Find source round
        source_summary = None
        for summary in self.round_history:
            if summary.round_id == source_round_id:
                source_summary = summary
                break
                
        if not source_summary:
            logger.warning("Source round not found: %s", source_round_id)
            return {"transferred": 0, "message": f"Round {source_round_id} not found"}
            
        logger.info("Transferring knowledge from round: %s", source_round_id)
        
        from .learning_knowledge_base import LearningKnowledgeBase
        
        kb = LearningKnowledgeBase(self.project)
        
        # Retrieve and transfer risk patterns
        risk_patterns = kb.retrieve("risk_pattern", query={"domains": source_summary.domains_covered}, top_k=20)
        
        # Retrieve and transfer effective probes
        effective_probes = kb.retrieve("probe_template", query={"domains": source_summary.domains_covered}, top_k=20)
        
        # Retrieve and transfer failure patterns
        failure_patterns = kb.retrieve("failure_analysis", query={}, top_k=10)
        
        transferred_count = len(risk_patterns) + len(effective_probes) + len(failure_patterns)
        
        return {
            "transferred": transferred_count,
            "source_round": source_round_id,
            "risk_patterns": len(risk_patterns),
            "effective_probes": len(effective_probes),
            "failure_patterns": len(failure_patterns),
            "domains": source_summary.domains_covered
        }
        
    def get_learned_patterns(self, category: str, limit: int = 10) -> list[dict]:
        """Get all learned patterns in a category."""
        from .learning_knowledge_base import LearningKnowledgeBase
        
        kb = LearningKnowledgeBase(self.project)
        
        # Get most effective patterns
        effective = kb.get_effective_patterns(category, min_usage=2)
        
        # Convert to dict format
        return [e.content for e in effective[:limit]]
        
    def get_cross_round_insights(self) -> dict[str, Any]:
        """Get insights across multiple rounds."""
        if len(self.round_history) < 2:
            return {
                "message": "Insufficient rounds for cross-round analysis",
                "round_count": len(self.round_history)
            }
            
        # Calculate trends
        recent_rounds = self.round_history[-3:]  # Last 3 rounds
        
        total_patterns = sum(r.risk_patterns_discovered for r in recent_rounds)
        total_probes = sum(r.effective_probes_stored for r in recent_rounds)
        total_failures = sum(r.failure_patterns_analyzed for r in recent_rounds)
        
        avg_confidence = sum(r.overall_confidence for r in recent_rounds) / len(recent_rounds)
        
        # Domains covered across rounds
        all_domains = set()
        for r in recent_rounds:
            all_domains.update(r.domains_covered)
            
        # Confidence trend
        confidence_trend = []
        for i in range(1, len(self.round_history)):
            prev_conf = self.round_history[i-1].overall_confidence
            curr_conf = self.round_history[i].overall_confidence
            confidence_trend.append(curr_conf - prev_conf)
            
        return {
            "total_rounds": len(self.round_history),
            "recent_rounds": len(recent_rounds),
            "patterns_discovered_total": total_patterns,
            "probes_stored_total": total_probes,
            "failures_analyzed_total": total_failures,
            "average_confidence": avg_confidence,
            "domains_covered": list(all_domains),
            "confidence_trend": confidence_trend,
            "trend_direction": "improving" if confidence_trend and sum(confidence_trend) > 0 else "stable"
        }
        
    def prune_old_knowledge(self, max_rounds: int = 10) -> int:
        """Prune knowledge from very old rounds."""
        if len(self.round_history) <= max_rounds:
            return 0
            
        # Keep only last N rounds
        kept_rounds = self.round_history[-max_rounds:]
        pruned_count = len(self.round_history) - max_rounds
        
        self.round_history = kept_rounds
        self._save_round_history()
        
        logger.info("Pruned %d old rounds, keeping %d", pruned_count, len(self.round_history))
        
        return pruned_count
        
    def export_knowledge_package(self, output_path: Optional[Path] = None) -> Path:
        """Export knowledge package for sharing or migration."""
        if output_path is None:
            output_path = (
                REPO_ROOT / 
                "platform_outputs" / 
                f"knowledge_package_{self.project}_{datetime.now().strftime('%Y%m%d')}.json"
            )
            
        # Export knowledge base
        from .learning_knowledge_base import LearningKnowledgeBase
        
        kb = LearningKnowledgeBase(self.project)
        kb_export_path = kb.export_to_json(output_path.parent / f"kb_{output_path.name}")
        
        # Create package manifest
        package = {
            "project": self.project,
            "exported_at": datetime.now().isoformat(),
            "round_history": [r.to_dict() for r in self.round_history],
            "insights": self.get_cross_round_insights(),
            "kb_file": kb_export_path.name
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(package, f, indent=2)
            
        logger.info("Exported knowledge package to %s", output_path)
        return output_path
