"""
Learning Knowledge Base - Persistent storage and retrieval of learning artifacts.

This module provides a structured knowledge base for storing and retrieving:
- Risk patterns and signatures
- Effective probe templates
- Failed probe analysis
- Successful investigation paths
- Domain-specific heuristics
- Performance benchmarks

Key features:
- Versioned knowledge entries
- Similarity-based retrieval
- Confidence scoring
- Expiration and decay management
- Cross-project knowledge sharing (opt-in)

Usage:
    from .learning_knowledge_base import LearningKnowledgeBase
    
    kb = LearningKnowledgeBase(project="my_project")
    
    # Store knowledge
    kb.store("risk_pattern", "sql_injection_probe", {
        "signature": "UNION SELECT",
        "effectiveness": 0.85,
        "domains": ["web", "api"]
    })
    
    # Retrieve similar knowledge
    results = kb.retrieve("risk_pattern", query={"signature": "SELECT"})
"""
from __future__ import annotations

import json
import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
import difflib

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class KnowledgeEntry:
    """A single knowledge entry."""
    entry_id: str
    category: str  # risk_pattern, probe_template, failure_analysis, etc.
    key: str  # e.g., "sql_injection_probe"
    content: dict[str, Any]
    confidence: float  # 0-1, how reliable is this knowledge
    created_at: datetime
    updated_at: datetime
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    domains: list[str] = field(default_factory=list)  # applicable domains
    
    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "category": self.category,
            "key": self.key,
            "content": self.content,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "usage_count": self.usage_count,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "domains": self.domains
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeEntry":
        return cls(
            entry_id=data["entry_id"],
            category=data["category"],
            key=data["key"],
            content=data["content"],
            confidence=data["confidence"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            usage_count=data.get("usage_count", 0),
            last_used_at=datetime.fromisoformat(data["last_used_at"]) if data.get("last_used_at") else None,
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            domains=data.get("domains", [])
        )


class LearningKnowledgeBase:
    """Persistent knowledge storage and retrieval."""
    
    def __init__(self, project: str):
        self.project = project
        self.kb_dir = REPO_ROOT / "platform_outputs" / project / "learning_knowledge_base"
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        
        # Load existing knowledge
        self.knowledge: dict[str, KnowledgeEntry] = {}
        self._load_all_knowledge()
        
        # Index by category+key
        self.index: dict[str, dict[str, KnowledgeEntry]] = {}
        self._build_index()
        
    def _load_all_knowledge(self) -> None:
        """Load all knowledge entries from disk."""
        if not self.kb_dir.exists():
            return
            
        for file in self.kb_dir.glob("*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    entry = KnowledgeEntry.from_dict(data)
                    key = self._make_entry_key(entry.category, entry.key)
                    self.knowledge[key] = entry
            except Exception as e:
                logger.warning("Failed to load knowledge from %s: %s", file, e)
                
    def _build_index(self) -> None:
        """Build category+key index for fast lookup."""
        self.index.clear()
        for key, entry in self.knowledge.items():
            cat = entry.category
            if cat not in self.index:
                self.index[cat] = {}
            self.index[cat][entry.key] = entry
            
    def _make_entry_key(self, category: str, key: str) -> str:
        """Generate unique key for an entry."""
        return f"{category}:{key}"
    
    def store(
        self,
        category: str,
        key: str,
        content: dict[str, Any],
        confidence: float = 0.8,
        expiry_days: Optional[int] = None,
        domains: Optional[list[str]] = None
    ) -> str:
        """Store a new knowledge entry or update existing."""
        entry_id = hashlib.md5(f"{category}:{key}".encode()).hexdigest()[:12]
        
        now = datetime.now()
        expires_at = None
        if expiry_days:
            expires_at = now + timedelta(days=expiry_days)
            
        # Check if updating existing
        existing_key = self._make_entry_key(category, key)
        if existing_key in self.knowledge:
            existing = self.knowledge[existing_key]
            confidence = max(confidence, existing.confidence)  # Keep higher confidence
            usage_count = existing.usage_count
            last_used_at = existing.last_used_at
            created_at = existing.created_at
        else:
            usage_count = 0
            last_used_at = None
            created_at = now
            
        entry = KnowledgeEntry(
            entry_id=entry_id,
            category=category,
            key=key,
            content=content,
            confidence=confidence,
            created_at=created_at,
            updated_at=now,
            usage_count=usage_count,
            last_used_at=last_used_at,
            expires_at=expires_at,
            domains=domains or []
        )
        
        self.knowledge[existing_key] = entry
        self._save_entry(entry)
        self._build_index()
        
        return entry_id
        
    def _save_entry(self, entry: KnowledgeEntry) -> None:
        """Save single entry to disk."""
        file_path = self.kb_dir / f"{entry.entry_id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(entry.to_dict(), f, indent=2)
            
    def retrieve(
        self,
        category: str,
        query: dict[str, Any],
        top_k: int = 5,
        min_confidence: float = 0.5
    ) -> list[tuple[KnowledgeEntry, float]]:
        """Retrieve similar knowledge entries."""
        if category not in self.index:
            return []
            
        candidates = self.index[category].values()
        
        # Score candidates by similarity to query
        scored = []
        for entry in candidates:
            if entry.confidence < min_confidence:
                continue
                
            # Skip expired entries
            if entry.expires_at and datetime.now() > entry.expires_at:
                continue
                
            score = self._calculate_similarity(entry, query)
            if score > 0:
                scored.append((entry, score))
                
        # Sort by score (descending)
        scored.sort(key=lambda x: x[1], reverse=True)
        
        # Update usage stats for top-k
        for entry, _ in scored[:top_k]:
            entry.usage_count += 1
            entry.last_used_at = datetime.now()
            self._save_entry(entry)
            
        return scored[:top_k]
        
    def _calculate_similarity(self, entry: KnowledgeEntry, query: dict) -> float:
        """Calculate similarity between entry and query."""
        score = 0.0
        
        # Check domain match
        query_domains = query.get("domains", [])
        if query_domains and entry.domains:
            overlap = len(set(query_domains) & set(entry.domains))
            total = len(set(query_domains) | set(entry.domains))
            if total > 0:
                score += 0.3 * (overlap / total)
                
        # Check content similarity using string matching
        entry_content = json.dumps(entry.content, sort_keys=True)
        for key, value in query.items():
            if key in entry.content:
                entry_value = str(entry.content[key])
                query_value = str(value)
                
                # Use difflib for string similarity
                similarity = difflib.SequenceMatcher(None, entry_value, query_value).ratio()
                score += 0.5 * similarity
                
            # Check if key exists in nested content
            elif "." in key:
                parts = key.split(".")
                nested = entry.content
                for part in parts:
                    if isinstance(nested, dict) and part in nested:
                        nested = nested[part]
                    else:
                        nested = None
                        break
                        
                if nested is not None:
                    similarity = difflib.SequenceMatcher(
                        None, 
                        str(nested), 
                        str(value)
                    ).ratio()
                    score += 0.3 * similarity
                    
        # Add confidence bonus
        score += 0.2 * entry.confidence
        
        return score
        
    def get_effective_patterns(self, category: str, min_usage: int = 3) -> list[KnowledgeEntry]:
        """Get most used (effective) patterns in a category."""
        if category not in self.index:
            return []
            
        entries = list(self.index[category].values())
        
        # Filter by usage count
        effective = [e for e in entries if e.usage_count >= min_usage]
        
        # Sort by usage count
        effective.sort(key=lambda e: e.usage_count, reverse=True)
        
        return effective
        
    def decay_old_knowledge(self, max_age_days: int = 90) -> int:
        """Decay confidence of old knowledge entries."""
        now = datetime.now()
        threshold = now - timedelta(days=max_age_days)
        
        decayed = 0
        for entry in self.knowledge.values():
            if entry.updated_at < threshold and entry.confidence > 0.3:
                entry.confidence *= 0.8  # Reduce confidence by 20%
                entry.updated_at = now
                self._save_entry(entry)
                decayed += 1
                
        return decayed
        
    def export_to_json(self, output_path: Optional[Path] = None) -> Path:
        """Export entire knowledge base to JSON."""
        if output_path is None:
            output_path = self.kb_dir.parent / f"knowledge_base_{self.project}_{datetime.now().strftime('%Y%m%d')}.json"
            
        data = {
            "project": self.project,
            "exported_at": datetime.now().isoformat(),
            "entries": [e.to_dict() for e in self.knowledge.values()]
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        logger.info("Exported knowledge base to %s", output_path)
        return output_path
        
    def import_from_json(self, input_path: Path) -> int:
        """Import knowledge base from JSON."""
        if not input_path.exists():
            raise FileNotFoundError(f"Knowledge base file not found: {input_path}")
            
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        imported = 0
        for entry_data in data.get("entries", []):
            entry = KnowledgeEntry.from_dict(entry_data)
            key = self._make_entry_key(entry.category, entry.key)
            self.knowledge[key] = entry
            imported += 1
            
        self._build_index()
        logger.info("Imported %d entries from %s", imported, input_path)
        return imported
