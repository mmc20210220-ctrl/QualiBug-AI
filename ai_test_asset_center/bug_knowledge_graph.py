from __future__ import annotations

"""
Enterprise Pattern Library — Private-Deployment Bug Knowledge Base.

This is the knowledge moat adapted for enterprise private cloud / on-premise
deployment. NO customer data ever leaves the deployment. Instead:

1. PRE-SEEDED PATTERNS: Ships with curated industry bug patterns learned
   during QualiBug's own development and testing. New customers get immediate
   value without any data sharing.

2. INTRA-ENTERPRISE LEARNING: Within ONE deployment, patterns learned from
   Project A (e.g. payments team) automatically benefit Project B (e.g. logistics
   team). The knowledge stays inside the enterprise.

3. OPTIONAL ANONYMOUS EXPORT: If a customer chooses, they can export anonymized
   pattern signatures (NO data, NO field values — only token hashes and pattern
   categories) to contribute to the shared pattern library or for audit.

4. SELF-IMPROVING: Every bug found within the deployment enriches the local
   pattern library. The system gets better the more it's used — without any
   external dependency.

Architecture:
    ┌──────────────────────────────────────────┐
    │         Enterprise Deployment            │
    │  ┌────────┐  ┌────────┐  ┌────────┐     │
    │  │Project A│  │Project B│  │Project C│    │
    │  └───┬────┘  └───┬────┘  └───┬────┘     │
    │      │           │           │           │
    │      └───────────┼───────────┘           │
    │                  │                       │
    │      ┌───────────▼───────────┐           │
    │      │  Pattern Library      │           │
    │      │  (local, air-gapped)  │           │
    │      └───────────────────────┘           │
    │                  │                       │
    │      ┌───────────▼───────────┐           │
    │      │  Pre-seeded Patterns  │           │
    │      │  (ships with product) │           │
    │      └───────────────────────┘           │
    └──────────────────────────────────────────┘
"""

import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .bug_pattern_memory import BugPatternMemory, _cosine_similarity, _weighted_tokens


# ===========================================================================
# PRE-SEEDED PATTERN LIBRARY
# ===========================================================================
# These patterns ship with every QualiBug deployment. They represent
# high-value bug classes discovered across industries during development.
# New customers get immediate detection value without any data sharing.

PRE_SEEDED_PATTERNS: list[dict[str, Any]] = [
    # --- Universal patterns (cross-industry) ---
    {
        "pattern_id": "UNIV-001",
        "name": "Missing side effect after state transition",
        "category": "causality_coverage",
        "severity": "P0",
        "description": "A business entity enters a terminal/significant state but the required dependent record is not created.",
        "detection_signals": ["paid", "approved", "completed", "shipped", "missing", "payment", "record", "not found"],
        "industries": ["all"],
        "confidence": 0.95,
    },
    {
        "pattern_id": "UNIV-002",
        "name": "Duplicate business record from non-idempotent operation",
        "category": "duplicate_side_effect",
        "severity": "P0",
        "description": "The same business event produces duplicate dependent records. Missing idempotency guarantee.",
        "detection_signals": ["duplicate", "same", "twice", "double", "repeat", "already exists"],
        "industries": ["all"],
        "confidence": 0.95,
    },
    {
        "pattern_id": "UNIV-003",
        "name": "Amount conservation violation",
        "category": "conservation_check",
        "severity": "P0",
        "description": "Sum of component amounts does not equal the total. Refund/reversal exceeds original amount.",
        "detection_signals": ["amount", "total", "sum", "exceed", "refund", "payment", "balance", "mismatch", "difference"],
        "industries": ["all"],
        "confidence": 0.95,
    },
    {
        "pattern_id": "UNIV-004",
        "name": "Referential integrity: orphaned dependent record",
        "category": "referential_integrity",
        "severity": "P1",
        "description": "A dependent record references a source entity that does not exist or has been deleted.",
        "detection_signals": ["not found", "does not exist", "invalid", "reference", "foreign key", "orphaned"],
        "industries": ["all"],
        "confidence": 0.90,
    },
    {
        "pattern_id": "UNIV-005",
        "name": "Cross-view data drift",
        "category": "cross_view_reconciliation",
        "severity": "P1",
        "description": "List/detail endpoints return inconsistent data for the same resource.",
        "detection_signals": ["list", "detail", "mismatch", "inconsistent", "differs", "different value"],
        "industries": ["all"],
        "confidence": 0.85,
    },
    # --- Ecommerce / Retail ---
    {
        "pattern_id": "ECOM-001",
        "name": "Inventory oversell from concurrent orders",
        "category": "population_constraint",
        "severity": "P0",
        "description": "Multiple concurrent orders reduce inventory below zero. Missing atomic stock deduction.",
        "detection_signals": ["inventory", "stock", "quantity", "negative", "oversold", "oversell", "库存", "超卖"],
        "industries": ["ecommerce", "retail", "erp"],
        "confidence": 0.90,
    },
    {
        "pattern_id": "ECOM-002",
        "name": "Order total ≠ sum(line items) + tax + shipping - discounts",
        "category": "conservation_check",
        "severity": "P1",
        "description": "The order total does not match the arithmetic sum of its components.",
        "detection_signals": ["order", "total", "line item", "subtotal", "tax", "shipping", "discount", "sum", "不相等"],
        "industries": ["ecommerce", "retail"],
        "confidence": 0.85,
    },
    # --- Finance / Fintech ---
    {
        "pattern_id": "FINT-001",
        "name": "Double ledger entry for same transaction",
        "category": "duplicate_side_effect",
        "severity": "P0",
        "description": "A single financial transaction produces duplicate ledger entries. Violates double-entry accounting.",
        "detection_signals": ["ledger", "journal", "entry", "debit", "credit", "duplicate", "double", "twice"],
        "industries": ["fintech", "finance", "banking"],
        "confidence": 0.95,
    },
    {
        "pattern_id": "FINT-002",
        "name": "Debit ≠ Credit in accounting entry",
        "category": "conservation_check",
        "severity": "P0",
        "description": "The sum of debits does not equal the sum of credits in a journal entry.",
        "detection_signals": ["debit", "credit", "balance", "not equal", "不平衡", "借贷"],
        "industries": ["fintech", "finance", "banking"],
        "confidence": 0.95,
    },
    # --- Insurance ---
    {
        "pattern_id": "INSU-001",
        "name": "Claim payout exceeds policy coverage",
        "category": "conservation_check",
        "severity": "P0",
        "description": "A claim is paid out for more than the policy's coverage limit.",
        "detection_signals": ["claim", "payout", "coverage", "limit", "exceed", "理赔", "赔付", "保额", "超额"],
        "industries": ["insurance"],
        "confidence": 0.90,
    },
    # --- Healthcare ---
    {
        "pattern_id": "HLTH-001",
        "name": "Controlled substance dispensed without dual authorization",
        "category": "permission_bypass",
        "severity": "P0",
        "description": "A controlled medication is dispensed without the required two-person approval.",
        "detection_signals": ["controlled", "narcotic", "prescription", "dual", "authorization", "麻醉", "处方", "双签"],
        "industries": ["healthcare", "medical"],
        "confidence": 0.85,
    },
    # --- Government ---
    {
        "pattern_id": "GOVT-001",
        "name": "Permit/approval bypass via state manipulation",
        "category": "state_transition",
        "severity": "P1",
        "description": "An application skips required approval steps by direct state manipulation.",
        "detection_signals": ["approval", "bypass", "skip", "state", "transition", "审批", "跳过", "越权"],
        "industries": ["government"],
        "confidence": 0.85,
    },
    # --- Logistics ---
    {
        "pattern_id": "LOGI-001",
        "name": "Shipment tracking status contradicts physical location",
        "category": "state_consistency",
        "severity": "P1",
        "description": "A shipment's tracking status says 'delivered' but GPS shows it's still in transit.",
        "detection_signals": ["tracking", "shipment", "delivered", "location", "gps", "contradict", "物流", "签收"],
        "industries": ["logistics"],
        "confidence": 0.80,
    },
    # --- SaaS / Multi-tenant ---
    {
        "pattern_id": "SAAS-001",
        "name": "Cross-tenant data leak via missing tenant filter",
        "category": "cross_tenant_leak",
        "severity": "P0",
        "description": "Tenant A can see data belonging to Tenant B because a query is missing the tenant_id filter.",
        "detection_signals": ["tenant", "isolation", "leak", "other", "different", "租户", "隔离", "越权", "跨租户"],
        "industries": ["saas", "multi_tenant"],
        "confidence": 0.90,
    },
]


# ===========================================================================
# Enterprise Pattern Library
# ===========================================================================

class EnterprisePatternLibrary:
    """Private-deployment bug pattern knowledge base.

    - Ships with pre-seeded industry patterns (immediate value, no data sharing)
    - Learns from bugs found within this deployment (intra-enterprise)
    - Supports optional anonymous pattern export (customer opt-in)
    - Fully air-gapped: no external API calls, no data leaves the deployment
    """

    def __init__(self, storage_path: Path | None = None):
        self._path = storage_path or Path.home() / ".qualibug" / "patterns"
        self._memory = BugPatternMemory()
        self._project_patterns: dict[str, list[dict[str, Any]]] = {}
        self._stats = {
            "pre_seeded": 0,
            "learned": 0,
            "projects_contributed": 0,
        }
        self._load_or_init()

    # ---- Initialization ----

    def _load_or_init(self) -> None:
        """Load existing patterns or seed from pre-built library."""
        state_file = self._path / "pattern_state.json"
        if state_file.exists():
            self._load_state(state_file)
        else:
            self._seed_from_builtin()

    def _seed_from_builtin(self) -> None:
        """Load pre-seeded patterns into the library."""
        for pattern in PRE_SEEDED_PATTERNS:
            self._memory.add({
                "title": pattern["name"],
                "severity": pattern["severity"],
                "category": pattern["category"],
                "description": pattern["description"],
                "source": "pre_seeded",
                "pattern_id": pattern["pattern_id"],
            })
            self._stats["pre_seeded"] += 1
        self._persist()

    def _load_state(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for pattern in data.get("learned_patterns", []):
                self._memory.add(pattern)
                self._stats["learned"] += 1
            self._project_patterns = data.get("project_patterns", {})
            self._stats["projects_contributed"] = len(self._project_patterns)
            # Always re-seed built-in patterns (they're idempotent)
            self._seed_from_builtin()
        except Exception:
            self._seed_from_builtin()

    def _persist(self) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        (self._path / "pattern_state.json").write_text(
            json.dumps({
                "learned_patterns": [
                    p for p in self._memory._patterns
                    if p.get("source") != "pre_seeded"
                ],
                "project_patterns": self._project_patterns,
                "stats": self._stats,
                "updated_at": _now(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- Learning (intra-enterprise only) ----

    def learn_from_project(
        self,
        findings: list[dict[str, Any]],
        project_id: str,
        industry: str = "",
    ) -> dict[str, Any]:
        """Learn from bugs found within this enterprise deployment.

        These patterns stay inside the enterprise. They benefit all projects
        in this deployment but NEVER leave the customer's infrastructure.
        """
        new_patterns = 0
        new_matches = 0

        for finding in findings:
            # Check if this finding matches an existing pattern
            matches = self._memory.search(finding, top_k=1, min_similarity=0.15)
            if matches:
                new_matches += 1
            else:
                # Novel pattern — learn it
                enriched = dict(finding)
                enriched["source"] = "learned"
                enriched["project_id"] = project_id
                enriched["industry"] = industry
                enriched["learned_at"] = _now()
                self._memory.add(enriched)
                new_patterns += 1

        if project_id not in self._project_patterns:
            self._project_patterns[project_id] = []
            self._stats["projects_contributed"] += 1
        self._project_patterns[project_id].extend(findings)
        self._stats["learned"] += new_patterns
        self._persist()

        return {
            "project_id": project_id,
            "findings_processed": len(findings),
            "matched_existing_patterns": new_matches,
            "new_patterns_learned": new_patterns,
            "total_patterns": self._stats["pre_seeded"] + self._stats["learned"],
        }

    # ---- Search ----

    def search(self, finding: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:
        """Search for similar patterns in the library."""
        return self._memory.search(finding, top_k=top_k)

    def classify(self, finding: dict[str, Any]) -> dict[str, Any]:
        """Suggest classification based on similar known patterns."""
        return self._memory.suggest_classification(finding)

    # ---- Optional anonymous export ----

    def export_anonymous_patterns(self) -> dict[str, Any]:
        """Export anonymized pattern signatures.

        Contains NO customer data — only pattern categories, token hashes,
        and detection signals. Suitable for contributing to shared research
        or for compliance audit. Fully opt-in.
        """
        signals = self._memory.extract_detection_signals(min_frequency=1)
        return {
            "exported_at": _now(),
            "summary": {
                "total_patterns": self._stats["pre_seeded"] + self._stats["learned"],
                "pre_seeded": self._stats["pre_seeded"],
                "learned": self._stats["learned"],
                "industries_covered": len(set(
                    p.get("industry", "") for p in self._memory._patterns if p.get("industry")
                )),
            },
            "signal_categories": [
                {
                    "category": s["pattern_name"],
                    "frequency": s["frequency"],
                    "signal_tokens": s["signal_tokens"],
                }
                for s in signals
            ],
            "privacy_note": "NO customer data, field values, or business identifiers. Only anonymized token hashes and pattern categories.",
        }

    # ---- Stats ----

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "total_patterns": self._stats["pre_seeded"] + self._stats["learned"],
            "industries_covered": len(set(
                p.get("industry", "") for p in self._memory._patterns if p.get("industry")
            )),
        }

    # ---- Cross-project insights (within enterprise) ----

    def cross_project_insights(self) -> list[dict[str, Any]]:
        """Find patterns that appear across multiple projects within this enterprise."""
        project_patterns = Counter()
        for pid, findings in self._project_patterns.items():
            for finding in findings:
                matches = self._memory.search(finding, top_k=1, min_similarity=0.2)
                if matches:
                    project_patterns[matches[0]["finding"].get("title", "")[:80]] += 1

        insights = []
        for title, count in project_patterns.most_common(10):
            if count >= 2:
                insights.append({
                    "pattern": title,
                    "projects_affected": count,
                    "recommendation": "This pattern affects multiple projects. Consider enterprise-wide prevention.",
                })
        return insights


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
