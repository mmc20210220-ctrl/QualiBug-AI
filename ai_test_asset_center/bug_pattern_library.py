"""Bug pattern library for cross-project knowledge transfer.

This module maintains a library of confirmed bug patterns that can be
applied to new targets for faster discovery. Patterns are abstracted
from confirmed findings and matched against new hypotheses.

Key features:
- Pattern extraction from confirmed findings
- Pattern matching against new hypotheses
- Cross-project pattern application
- Pattern effectiveness tracking

Design: All detection hints and indicators are data-driven and loaded
from configurable sources. No industry-specific hardcoding.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

# ── Default pattern storage path ──
_DEFAULT_PATTERN_DIR = Path.home() / ".qualibug" / "patterns"

# ── Pattern categories (industry-neutral software bug types) ──
# These are universal software defect categories, not industry-specific.
PATTERN_CATEGORIES = {
    "permission_bypass": "Permission/authorization bypass patterns",
    "state_violation": "State machine violation patterns",
    "data_corruption": "Data integrity/corruption patterns",
    "boundary_failure": "Boundary value handling failures",
    "concurrency_issue": "Race condition/concurrency patterns",
    "validation_gap": "Input validation gap patterns",
    "error_handling": "Error handling deficiency patterns",
    "idempotency_failure": "Idempotency violation patterns",
}

# ── Data-driven detection indicators ──
# Loaded from semantic_lexicon.json or configurable source.
# Fallback to generic software testing terms (industry-neutral).
_DEFAULT_DETECTION_INDICATORS = [
    # Generic software defect indicators (bilingual)
    "负值", "negative", "不一致", "mismatch", "重复", "duplicate",
    "未授权", "unauthorized", "绕过", "bypass", "泄露", "leak",
    "溢出", "overflow", "死锁", "deadlock", "丢失", "lost",
    "异常", "exception", "超时", "timeout", "崩溃", "crash",
    "越界", "out_of_bounds", "空指针", "null_pointer",
]


def _load_detection_indicators() -> list[str]:
    """Load detection indicators from data-driven source.
    
    Priority:
    1. policies/semantic_lexicon.json risk_hints (if available)
    2. Environment variable QUALIBUG_DETECTION_INDICATORS (JSON list)
    3. Default generic indicators (industry-neutral)
    """
    # Try loading from semantic_lexicon.json
    try:
        lexicon_path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
        if lexicon_path.exists():
            with open(lexicon_path, "r", encoding="utf-8") as f:
                lexicon = json.load(f)
            risk_hints = lexicon.get("risk_hints", {})
            if risk_hints:
                # Flatten all hint keywords
                indicators = []
                for hint_list in risk_hints.values():
                    if isinstance(hint_list, list):
                        indicators.extend(str(h).lower() for h in hint_list)
                if indicators:
                    return list(set(indicators))
    except Exception:
        pass
    
    # Try environment variable
    env_indicators = os.environ.get("QUALIBUG_DETECTION_INDICATORS", "")
    if env_indicators:
        try:
            parsed = json.loads(env_indicators)
            if isinstance(parsed, list) and parsed:
                return [str(i).lower() for i in parsed]
        except Exception:
            pass
    
    # Fallback to default generic indicators
    return _DEFAULT_DETECTION_INDICATORS


class BugPatternLibrary:
    """Library of reusable bug detection patterns."""

    def __init__(self, pattern_dir: Path | None = None):
        self.pattern_dir = pattern_dir or _DEFAULT_PATTERN_DIR
        self.pattern_dir.mkdir(parents=True, exist_ok=True)
        self._patterns = self._load_patterns()

    def _patterns_file(self) -> Path:
        return self.pattern_dir / "bug_patterns.json"

    def _load_patterns(self) -> dict[str, Any]:
        """Load pattern library."""
        path = self._patterns_file()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "version": 1,
            "created_at": time.time(),
            "updated_at": time.time(),
            "patterns": {},
            "statistics": {
                "total_patterns": 0,
                "total_applications": 0,
                "total_matches": 0,
            },
        }

    def _save_patterns(self) -> None:
        """Persist pattern library."""
        self._patterns["updated_at"] = time.time()
        path = self._patterns_file()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._patterns, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def extract_pattern_from_finding(
        self,
        finding: dict[str, Any],
        project: str = "",
    ) -> dict[str, Any] | None:
        """Extract a reusable pattern from a confirmed finding.

        Returns the extracted pattern or None if not extractable.
        """
        verdict = str(finding.get("verdict") or "").lower()
        if verdict != "confirmed":
            return None

        title = str(finding.get("title") or "")
        actual = str(finding.get("actual") or "")
        risk_type = str(finding.get("risk_type") or finding.get("category") or "unknown").lower()
        severity = str(finding.get("severity") or "P2").upper()

        # Generate pattern signature
        signature = self._generate_pattern_signature(title, risk_type)
        if signature in self._patterns["patterns"]:
            # Pattern exists, update statistics
            existing = self._patterns["patterns"][signature]
            existing["occurrences"] = existing.get("occurrences", 1) + 1
            existing["last_seen"] = time.time()
            self._save_patterns()
            return existing

        # Create new pattern
        pattern = {
            "pattern_id": signature,
            "category": self._categorize_pattern(risk_type, title),
            "title_template": self._extract_title_template(title),
            "risk_type": risk_type,
            "severity": severity,
            "detection_hints": self._extract_detection_hints(actual, title),
            "verification_strategy": self._extract_verification_strategy(finding),
            "keywords": self._extract_keywords(title, actual),
            "created_at": time.time(),
            "last_seen": time.time(),
            "occurrences": 1,
            "applications": 0,
            "matches": 0,
            "hit_rate": 0.0,
            "source_projects": [project] if project else [],
        }

        self._patterns["patterns"][signature] = pattern
        self._patterns["statistics"]["total_patterns"] = len(self._patterns["patterns"])
        self._save_patterns()

        return pattern

    def _generate_pattern_signature(self, title: str, risk_type: str) -> str:
        """Generate a stable signature for a pattern."""
        # Normalize title
        import re
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", title.lower()).strip()
        # Take key parts
        key_parts = normalized.split()[:10]
        key = f"{risk_type}:{' '.join(key_parts)}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _categorize_pattern(self, risk_type: str, title: str) -> str:
        """Categorize pattern by risk type."""
        category_map = {
            "permission_boundary": "permission_bypass",
            "authorization": "permission_bypass",
            "isolation": "permission_bypass",
            "state_machine": "state_violation",
            "data_conservation": "data_corruption",
            "data_reconciliation": "data_corruption",
            "idempotency": "idempotency_failure",
            "concurrency": "concurrency_issue",
            "input_validation": "validation_gap",
            "error_handling": "error_handling",
        }
        return category_map.get(risk_type, "validation_gap")

    def _extract_title_template(self, title: str) -> str:
        """Extract a reusable title template."""
        import re
        # Replace specific IDs/values with placeholders
        template = re.sub(r"\b\d+\b", "{number}", title)
        template = re.sub(r"\b[A-Z]{2,}-?\d+\b", "{code}", template)
        template = re.sub(r"\b[0-9a-f]{8,}\b", "{uuid}", template, flags=re.IGNORECASE)
        return template[:200]

    def _extract_detection_hints(self, actual: str, title: str) -> list[str]:
        """Extract detection hints from finding details.
        
        Uses data-driven indicators loaded from semantic_lexicon.json
        or configurable source. No industry-specific hardcoding.
        """
        hints = []
        # Load indicators from data-driven source
        indicators = _load_detection_indicators()
        combined = (actual + " " + title).lower()
        for ind in indicators:
            if ind.lower() in combined:
                hints.append(ind)
        return hints[:5]

    def _extract_verification_strategy(self, finding: dict[str, Any]) -> dict[str, Any]:
        """Extract verification strategy from finding evidence."""
        evidence = finding.get("evidence", {})
        if not isinstance(evidence, dict):
            return {}

        strategy: dict[str, Any] = {}

        # Extract call patterns
        calls = evidence.get("calls", [])
        if calls:
            methods = []
            for call in calls:
                call_str = str(call.get("call") or "")
                if " " in call_str:
                    methods.append(call_str.split()[0])
            strategy["call_sequence"] = methods

        # Extract check type
        if evidence.get("before_after_diff"):
            strategy["check_type"] = "state_comparison"
        elif evidence.get("cross_validation_mismatch"):
            strategy["check_type"] = "cross_validation"

        return strategy

    def _extract_keywords(self, title: str, actual: str) -> list[str]:
        """Extract keywords for pattern matching."""
        import re
        combined = f"{title} {actual}".lower()
        # Extract meaningful words
        words = re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]{2,}", combined)
        # Filter common words
        stop_words = {"the", "and", "for", "with", "from", "that", "this", "should", "could"}
        keywords = [w for w in words if w not in stop_words]
        return list(set(keywords))[:10]

    def match_patterns(
        self,
        hypothesis: dict[str, Any],
        min_score: float = 0.5,
    ) -> list[tuple[dict[str, Any], float]]:
        """Match hypothesis against known patterns.

        Returns list of (pattern, score) tuples sorted by score descending.
        """
        title = str(hypothesis.get("title") or hypothesis.get("hypothesis") or "").lower()
        risk_type = str(hypothesis.get("risk_type") or hypothesis.get("category") or "").lower()
        description = str(hypothesis.get("description") or "").lower()

        matches: list[tuple[dict[str, Any], float]] = []

        for pattern in self._patterns["patterns"].values():
            score = self._compute_match_score(
                title, risk_type, description, pattern
            )
            if score >= min_score:
                matches.append((pattern, score))

        # Sort by score descending
        matches.sort(key=lambda x: -x[1])
        return matches[:5]

    def _compute_match_score(
        self,
        title: str,
        risk_type: str,
        description: str,
        pattern: dict[str, Any],
    ) -> float:
        """Compute match score between hypothesis and pattern."""
        score = 0.0

        # Risk type match (strong signal)
        if risk_type and risk_type == pattern.get("risk_type"):
            score += 0.40

        # Keyword overlap
        pattern_keywords = set(pattern.get("keywords", []))
        hypothesis_text = f"{title} {description}"
        keyword_matches = sum(1 for kw in pattern_keywords if kw in hypothesis_text)
        if pattern_keywords:
            score += 0.30 * (keyword_matches / len(pattern_keywords))

        # Detection hint match
        hints = pattern.get("detection_hints", [])
        hint_matches = sum(1 for h in hints if h in hypothesis_text)
        if hints:
            score += 0.20 * (hint_matches / len(hints))

        # Pattern effectiveness (historical hit rate)
        hit_rate = pattern.get("hit_rate", 0.0)
        score += 0.10 * hit_rate

        return min(1.0, score)

    def apply_pattern_to_hypothesis(
        self,
        pattern: dict[str, Any],
        hypothesis: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply pattern knowledge to enhance a hypothesis."""
        enhanced = dict(hypothesis)

        # Add pattern reference
        enhanced["_pattern_match"] = {
            "pattern_id": pattern.get("pattern_id"),
            "category": pattern.get("category"),
            "occurrences": pattern.get("occurrences", 1),
            "hit_rate": pattern.get("hit_rate", 0.0),
        }

        # Boost confidence based on pattern effectiveness
        pattern_hit_rate = pattern.get("hit_rate", 0.0)
        if pattern_hit_rate > 0.3:
            enhanced["confidence"] = min(0.95, float(hypothesis.get("confidence", 0.5)) + 0.15)

        # Add verification strategy hints
        strategy = pattern.get("verification_strategy", {})
        if strategy:
            enhanced["_pattern_verification_strategy"] = strategy

        # Update pattern statistics
        pattern["applications"] = pattern.get("applications", 0) + 1
        self._patterns["statistics"]["total_applications"] += 1
        self._save_patterns()

        return enhanced

    def record_pattern_outcome(
        self,
        pattern_id: str,
        confirmed: bool,
    ) -> None:
        """Record whether a pattern application led to confirmation."""
        pattern = self._patterns["patterns"].get(pattern_id)
        if not pattern:
            return

        if confirmed:
            pattern["matches"] = pattern.get("matches", 0) + 1
            self._patterns["statistics"]["total_matches"] += 1

        # Update hit rate
        applications = pattern.get("applications", 0)
        matches = pattern.get("matches", 0)
        if applications > 0:
            pattern["hit_rate"] = matches / applications

        self._save_patterns()

    def get_top_patterns(
        self,
        category: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get top patterns by effectiveness."""
        patterns = list(self._patterns["patterns"].values())

        # Filter by category if specified
        if category:
            patterns = [p for p in patterns if p.get("category") == category]

        # Sort by hit rate and occurrences
        patterns.sort(key=lambda p: (
            -p.get("hit_rate", 0.0),
            -p.get("occurrences", 0),
        ))

        return patterns[:limit]

    def get_library_summary(self) -> dict[str, Any]:
        """Get summary of pattern library."""
        patterns = list(self._patterns["patterns"].values())
        by_category: dict[str, int] = {}
        for p in patterns:
            cat = p.get("category", "unknown")
            by_category[cat] = by_category.get(cat, 0) + 1

        return {
            "total_patterns": len(patterns),
            "by_category": by_category,
            "statistics": self._patterns.get("statistics", {}),
            "top_patterns": [
                {
                    "pattern_id": p.get("pattern_id"),
                    "category": p.get("category"),
                    "title_template": p.get("title_template"),
                    "hit_rate": p.get("hit_rate", 0.0),
                    "occurrences": p.get("occurrences", 0),
                }
                for p in self.get_top_patterns(limit=5)
            ],
        }


# ── Module-level singleton ──
_global_library: BugPatternLibrary | None = None


def get_pattern_library() -> BugPatternLibrary:
    """Get or create the global pattern library instance."""
    global _global_library
    if _global_library is None:
        _global_library = BugPatternLibrary()
    return _global_library


def extract_and_store_pattern(
    finding: dict[str, Any],
    project: str = "",
) -> dict[str, Any] | None:
    """Convenience function to extract and store a pattern from a finding."""
    library = get_pattern_library()
    return library.extract_pattern_from_finding(finding, project)


def match_hypothesis_to_patterns(
    hypothesis: dict[str, Any],
    min_score: float = 0.5,
) -> list[tuple[dict[str, Any], float]]:
    """Convenience function to match hypothesis against patterns."""
    library = get_pattern_library()
    return library.match_patterns(hypothesis, min_score)
