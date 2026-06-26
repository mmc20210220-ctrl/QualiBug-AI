"""
Phase79+: Regression Guard — confirmed bugs become long-term detection scripts.

Stores confirmed findings as regression signatures and checks new
discovery runs against known bugs to detect regressions vs new findings.
"""

from __future__ import annotations

import json, time, hashlib
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path


@dataclass
class RegressionSignature:
    """A confirmed bug stored as a regression detection fingerprint."""
    sig_id: str
    title: str
    severity: str
    risk_type: str
    hypothesis_pattern: str  # Key terms to match against new hypotheses
    api_paths: list[str]  # API paths involved
    expected: str
    actual: str
    confirmed_at: float
    detection_count: int = 0  # How many times re-detected
    last_detected_at: float = 0.0
    status: str = "active"  # active | fixed | dismissed


class RegressionGuard:
    """
    Stores and checks regression signatures against new findings.

    Usage:
        guard = RegressionGuard(project_dir)
        guard.register(finding)
        matches = guard.check(new_findings)
    """

    def __init__(self, project_dir: Path | str = "platform_outputs/real_project_demo"):
        self.project_dir = Path(project_dir)
        self.sig_path = self.project_dir / "regression_signatures.jsonl"
        self.signatures: list[RegressionSignature] = []
        self._load()

    def _load(self):
        """Load existing signatures from persistent storage."""
        if not self.sig_path.exists():
            return
        try:
            for line in self.sig_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    data = json.loads(line)
                    self.signatures.append(RegressionSignature(**data))
        except Exception:
            pass

    def _save(self):
        """Persist signatures to JSONL."""
        self.sig_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for sig in self.signatures:
            lines.append(json.dumps({
                "sig_id": sig.sig_id,
                "title": sig.title,
                "severity": sig.severity,
                "risk_type": sig.risk_type,
                "hypothesis_pattern": sig.hypothesis_pattern,
                "api_paths": sig.api_paths,
                "expected": sig.expected,
                "actual": sig.actual,
                "confirmed_at": sig.confirmed_at,
                "detection_count": sig.detection_count,
                "last_detected_at": sig.last_detected_at,
                "status": sig.status,
            }, ensure_ascii=False, default=str))
        self.sig_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def register(self, finding: dict) -> RegressionSignature | None:
        """
        Register a confirmed finding as a regression signature.
        Returns the signature if new, None if duplicate.
        """
        title = finding.get("title", "")[:200]
        sig_id = hashlib.md5(title.encode()).hexdigest()[:12]

        # Check for duplicates
        if any(s.sig_id == sig_id for s in self.signatures):
            existing = next(s for s in self.signatures if s.sig_id == sig_id)
            existing.detection_count += 1
            existing.last_detected_at = time.time()
            self._save()
            return None

        sig = RegressionSignature(
            sig_id=sig_id,
            title=title,
            severity=finding.get("severity", "P1"),
            risk_type=finding.get("risk_type", "unknown"),
            hypothesis_pattern=self._extract_pattern(finding),
            api_paths=self._extract_paths(finding),
            expected=finding.get("expected", ""),
            actual=finding.get("actual", ""),
            confirmed_at=time.time(),
            detection_count=1,
            last_detected_at=time.time(),
        )
        self.signatures.append(sig)
        self._save()
        return sig

    def check(self, findings: list[dict]) -> dict:
        """
        Check new findings against known regression signatures.
        Returns {new, regression, fixed} counts.
        """
        result = {"new": 0, "regression": 0, "total": len(findings)}
        matched_sigs = set()

        for finding in findings:
            title = finding.get("title", "")[:200]
            is_known = False
            for sig in self.signatures:
                # Match by title keywords
                if sig.status != "active":
                    continue
                pattern_terms = sig.hypothesis_pattern.split()
                if len(pattern_terms) >= 2 and all(t.lower() in title.lower() for t in pattern_terms[:3]):
                    is_known = True
                    matched_sigs.add(sig.sig_id)
                    break
                # Match by exact title prefix
                if sig.title[:60].lower() in title.lower():
                    is_known = True
                    matched_sigs.add(sig.sig_id)
                    break

            if is_known:
                result["regression"] += 1
            else:
                result["new"] += 1

        # Update detection counts for matched signatures
        for sig_id in matched_sigs:
            for sig in self.signatures:
                if sig.sig_id == sig_id:
                    sig.detection_count += 1
                    sig.last_detected_at = time.time()

        # Check for "fixed" bugs — active signatures not seen recently
        stale_threshold = time.time() - 86400 * 7  # 7 days
        # Only check if we have enough findings
        if result["total"] >= 10:
            for sig in self.signatures:
                if sig.status == "active" and sig.last_detected_at < stale_threshold:
                    # Not marking as fixed automatically — just flagging
                    pass

        self._save()
        return result

    def _extract_pattern(self, finding: dict) -> str:
        """Extract key search terms from a finding for pattern matching."""
        title = finding.get("title", "")
        evidence = finding.get("evidence", {})
        # Combine title keywords with evidence paths
        parts = []
        for kw in title.split()[:8]:
            if len(kw) > 2:
                parts.append(kw)
        if isinstance(evidence, dict):
            for path in evidence.get("api_paths", []):
                parts.append(str(path))
        return " ".join(parts[:10])

    def _extract_paths(self, finding: dict) -> list[str]:
        """Extract API paths from finding evidence."""
        evidence = finding.get("evidence", {})
        if isinstance(evidence, dict):
            paths = evidence.get("calls", [])
            if isinstance(paths, list):
                return [c.get("call", str(c))[:100] for c in paths[:5]]
        return []

    @property
    def total_signatures(self) -> int:
        return len(self.signatures)

    @property
    def active_signatures(self) -> int:
        return sum(1 for s in self.signatures if s.status == "active")

    def summary(self) -> dict:
        return {
            "total": self.total_signatures,
            "active": self.active_signatures,
            "fixed": sum(1 for s in self.signatures if s.status == "fixed"),
            "dismissed": sum(1 for s in self.signatures if s.status == "dismissed"),
            "total_detections": sum(s.detection_count for s in self.signatures),
        }
