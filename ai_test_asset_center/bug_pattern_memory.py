from __future__ import annotations

"""
Bug Pattern Memory — embedding-based learning for the confirmed bug flywheel.

Phase61 moat upgrade: the confirmed_bug_flywheel was a tamper-evident ledger
(append-only JSON). This module adds actual learning:

1. Vectorize bug findings into pattern signatures
2. Find similar confirmed bugs via similarity search
3. Auto-suggest classification, severity, and detection signals
4. Build a growing pattern library that improves future detection

Works in two modes:
- Lightweight (default): TF-IDF + Jaccard similarity, no external deps
- LLM-enhanced: if LLM is available, uses semantic embeddings for richer matching
"""

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Lightweight vectorization (no external deps)
# ---------------------------------------------------------------------------

STOPWORDS_EN = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "this", "that",
    "these", "those", "it", "its", "and", "but", "or", "not", "no",
    "in", "on", "at", "to", "for", "of", "with", "from", "by", "as",
    "if", "then", "else", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such",
    "only", "own", "same", "so", "than", "too", "very", "just", "also",
}

STOPWORDS_ZH = {
    "的", "是", "在", "和", "了", "有", "不", "这", "也", "就", "都", "而",
    "及", "与", "着", "或", "一个", "没有", "我们", "你们", "他们", "它们",
    "自己", "什么", "哪", "那", "这个", "那个", "这些", "那些", "可以",
    "会", "能", "要", "因为", "所以", "但是", "虽然", "如果", "的话",
}

DEFAULT_WEIGHTS = {
    "title": 3.0,
    "expected": 1.5,
    "actual": 1.5,
    "severity": 2.0,
    "category": 2.5,
    "entity": 1.0,
    "field": 1.0,
}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")

def _tokenize(text: str) -> list[str]:
    """Extract meaningful tokens from text (English + Chinese)."""
    tokens = []
    for match in TOKEN_RE.finditer(str(text or "").lower()):
        token = match.group()
        if token not in STOPWORDS_EN and token not in STOPWORDS_ZH:
            tokens.append(token)
    return tokens


def _weighted_tokens(finding: dict[str, Any]) -> Counter[str]:
    """Extract weighted token frequencies from a finding."""
    counter: Counter[str] = Counter()
    for field, weight in DEFAULT_WEIGHTS.items():
        text = str(finding.get(field, ""))
        for token in _tokenize(text):
            counter[token] += weight
    # Also tokenize the raw title more aggressively
    for token in _tokenize(str(finding.get("title", ""))):
        counter[token] += 1.0  # Additional title boost
    return counter


def _cosine_similarity(a: Counter[str], b: Counter[str]) -> float:
    """Cosine similarity between two token counters."""
    if not a or not b:
        return 0.0
    keys = set(a.keys()) | set(b.keys())
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    norm_a = math.sqrt(sum(v ** 2 for v in a.values()))
    norm_b = math.sqrt(sum(v ** 2 for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _jaccard_similarity(a: Counter[str], b: Counter[str]) -> float:
    """Jaccard similarity on token sets (presence, not frequency)."""
    set_a = set(a.keys())
    set_b = set(b.keys())
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


# ---------------------------------------------------------------------------
# Pattern memory
# ---------------------------------------------------------------------------

class BugPatternMemory:
    """In-memory pattern index for fast similarity search over confirmed bugs."""

    def __init__(self):
        self._patterns: list[dict[str, Any]] = []  # Original findings
        self._vectors: list[Counter[str]] = []      # Weighted token vectors
        self._categories: Counter[str] = Counter()  # Category distribution
        self._severity_dist: Counter[str] = Counter()

    def add(self, finding: dict[str, Any]) -> None:
        """Index a confirmed bug finding."""
        self._patterns.append(finding)
        self._vectors.append(_weighted_tokens(finding))
        cat = str(finding.get("business_causality_type") or finding.get("counterexample_type") or finding.get("category") or "")
        if cat:
            self._categories[cat] += 1
        sev = str(finding.get("severity", ""))
        if sev:
            self._severity_dist[sev] += 1

    def search(self, finding: dict[str, Any], top_k: int = 5, min_similarity: float = 0.15) -> list[dict[str, Any]]:
        """Find the top-K most similar confirmed bugs.

        Returns list of {finding, cosine_sim, jaccard_sim, shared_tokens}.
        """
        if not self._vectors:
            return []
        query_vec = _weighted_tokens(finding)
        scored: list[tuple[int, float, float, list[str]]] = []
        for idx, vec in enumerate(self._vectors):
            cos = _cosine_similarity(query_vec, vec)
            jac = _jaccard_similarity(query_vec, vec)
            combined = cos * 0.7 + jac * 0.3  # Cosine is more informative
            if combined >= min_similarity:
                shared = sorted(set(query_vec.keys()) & set(vec.keys()),
                               key=lambda t: query_vec[t] + vec[t], reverse=True)[:10]
                scored.append((idx, cos, jac, shared))
        scored.sort(key=lambda x: -(x[1] * 0.7 + x[2] * 0.3))
        results = []
        for idx, cos, jac, shared in scored[:top_k]:
            results.append({
                "finding": self._patterns[idx],
                "cosine_similarity": round(cos, 4),
                "jaccard_similarity": round(jac, 4),
                "shared_tokens": shared,
            })
        return results

    def suggest_classification(self, finding: dict[str, Any]) -> dict[str, Any]:
        """Suggest classification for a new finding based on similar confirmed bugs."""
        matches = self.search(finding, top_k=3, min_similarity=0.1)
        if not matches:
            return {"suggested_category": "unknown", "confidence": 0.0, "reason": "no_similar_confirmed_bugs"}

        cat_votes: Counter[str] = Counter()
        sev_votes: Counter[str] = Counter()
        for match in matches:
            weight = match["cosine_similarity"]
            cat = str(match["finding"].get("business_causality_type") or
                     match["finding"].get("counterexample_type") or
                     "unknown")
            sev = str(match["finding"].get("severity", "P2"))
            cat_votes[cat] += weight
            sev_votes[sev] += weight

        top_cat = cat_votes.most_common(1)[0] if cat_votes else ("unknown", 0)
        top_sev = sev_votes.most_common(1)[0] if sev_votes else ("P2", 0)

        return {
            "suggested_category": top_cat[0],
            "category_confidence": round(top_cat[1] / sum(cat_votes.values()), 3) if cat_votes else 0.0,
            "suggested_severity": top_sev[0],
            "severity_confidence": round(top_sev[1] / sum(sev_votes.values()), 3) if sev_votes else 0.0,
            "similar_confirmed_bugs": [m["finding"].get("title", "")[:100] for m in matches[:3]],
            "shared_patterns": matches[0]["shared_tokens"][:8] if matches else [],
        }

    def extract_detection_signals(self, min_frequency: int = 2) -> list[dict[str, Any]]:
        """Extract recurring patterns that should become permanent detection rules.

        Returns list of {pattern_name, tokens, frequency, example_findings}.
        """
        if len(self._patterns) < min_frequency:
            return []

        # Group by category
        by_category: dict[str, list[int]] = {}
        for idx, pattern in enumerate(self._patterns):
            cat = str(pattern.get("business_causality_type") or
                     pattern.get("counterexample_type") or "other")
            by_category.setdefault(cat, []).append(idx)

        signals = []
        for cat, indices in by_category.items():
            if len(indices) < min_frequency:
                continue
            # Find shared tokens across all findings in this category
            token_sets = [set(_weighted_tokens(self._patterns[i]).keys()) for i in indices]
            if not token_sets:
                continue
            shared = token_sets[0]
            for ts in token_sets[1:]:
                shared = shared & ts

            if shared:
                signals.append({
                    "pattern_name": f"learned_{cat}",
                    "category": cat,
                    "frequency": len(indices),
                    "signal_tokens": sorted(shared),
                    "example_titles": [self._patterns[i].get("title", "")[:120] for i in indices[:3]],
                })

        return signals

    def stats(self) -> dict[str, Any]:
        return {
            "indexed_bugs": len(self._patterns),
            "categories": dict(self._categories.most_common()),
            "severity_distribution": dict(self._severity_dist.most_common()),
            "signals_extracted": len(self.extract_detection_signals()),
        }

    @classmethod
    def from_jsonl(cls, path: Path) -> "BugPatternMemory":
        """Load patterns from a JSONL file of confirmed bugs."""
        memory = cls()
        if not path.exists():
            return memory
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                finding = json.loads(line)
                if isinstance(finding, dict):
                    memory.add(finding)
            except json.JSONDecodeError:
                continue
        return memory


# ---------------------------------------------------------------------------
# LLM-enhanced learning (optional — activates when LLM is configured)
# ---------------------------------------------------------------------------

def llm_enhanced_learn(finding: dict[str, Any], memory: BugPatternMemory) -> dict[str, Any] | None:
    """Use LLM to generate richer learning signals from a confirmed bug.

    Returns enhanced classification + detection signals, or None if LLM unavailable.
    """
    try:
        from .llm_reasoning import reason as _llm_reason

        similar = memory.search(finding, top_k=5)
        bug_history = json.dumps([{
            "title": m["finding"].get("title", ""),
            "category": m["finding"].get("business_causality_type") or m["finding"].get("counterexample_type", ""),
            "severity": m["finding"].get("severity", ""),
            "similarity": m["cosine_similarity"],
        } for m in similar], ensure_ascii=False)

        result = _llm_reason("defect_classification", {
            "finding": json.dumps(finding, ensure_ascii=False, default=str)[:4000],
            "bug_history": bug_history[:4000],
            "prd_text": "",
            "api_schema": "",
            "observed_data": "",
            "heuristic_findings": "",
        })

        if result:
            return {
                "classification": result.get("classification", {}),
                "similar_confirmed_bugs": result.get("similar_confirmed_bugs", []),
                "generalized_pattern": result.get("generalized_pattern", {}),
                "promotion_recommendation": result.get("promotion_recommendation", {}),
            }
    except Exception:
        pass
    return None
