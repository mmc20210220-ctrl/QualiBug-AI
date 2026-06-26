"""Phase82: Product Incident Collector + Reliability Memory

Local product self-healing infrastructure. Strictly separated from
customer Bug Discovery Memory. Only runs in dev/CI environments.
"""

from __future__ import annotations

import json, hashlib, time, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════
# Product Reliability Baseline
# ═══════════════════════════════════════════════════════════════

@dataclass
class ProductReliabilityBaseline:
    """Current product health snapshot."""

    baseline_version: str = "Phase82"
    generated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    known_failure_modes: list[dict] = field(default_factory=lambda: [
        {
            "id": "FM-001",
            "category": "reasoner_api",
            "description": "DeepSeek API returns empty response or truncated JSON",
            "severity": "high",
            "affected_modules": ["stage_reason_all_v2.py", "discovery_engine.py"],
            "current_mitigation": "2-attempt retry with client rebuild, JSON truncation recovery via raw_decode",
            "status": "mitigated_not_fixed",
        },
        {
            "id": "FM-002",
            "category": "syntax_error",
            "description": "Missing parenthesis/brace causes module import failure, silent crash",
            "severity": "critical",
            "affected_modules": ["discovery_engine.py"],
            "current_mitigation": "AGENTS.md rule: syntax check after every edit",
            "status": "mitigated_not_fixed",
        },
        {
            "id": "FM-003",
            "category": "loop_crash",
            "description": "Background daemon dies silently on Windows due to session binding",
            "severity": "high",
            "affected_modules": ["loop_daemon.py"],
            "current_mitigation": "Cross-platform daemon with signal handling + auto-restart",
            "status": "fixed",
        },
        {
            "id": "FM-004",
            "category": "hardcoded_paths",
            "description": "Output paths hardcoded to real_project_demo, breaks in other projects",
            "severity": "medium",
            "affected_modules": ["self_improving_loop.py"],
            "current_mitigation": "Dynamic paths based on project_id",
            "status": "fixed",
        },
        {
            "id": "FM-005",
            "category": "test_flakiness",
            "description": "Bug validation queue tests fail intermittently due to shared state",
            "severity": "medium",
            "affected_modules": ["test_bug_validation_queue.py"],
            "current_mitigation": "Relaxed assertion to accept both valid verdicts",
            "status": "mitigated_not_fixed",
        },
        {
            "id": "FM-006",
            "category": "config_hardcoded",
            "description": "40+ parameters hardcoded across modules, max_tokens has two different values",
            "severity": "medium",
            "affected_modules": ["discovery_engine.py", "self_improving_loop.py", "stage_reason_all_v2.py"],
            "current_mitigation": "Phase81 Policy Registry created, not yet fully wired",
            "status": "in_progress",
        },
    ])

    test_health: dict = field(default_factory=lambda: {
        "total_suites": 35,
        "stable_passing": 90,
        "intermittent": 1,  # test_bug_validation_queue
        "known_failures": 0,
    })

    release_health: dict = field(default_factory=lambda: {
        "release_verifier_status": "passed",
        "packaging_audit_status": "passed",
        "sensitive_scan_status": "passed",
    })

    runtime_health: dict = field(default_factory=lambda: {
        "daemon_status": "running",
        "loop_rounds_completed": 0,
        "bugs_found_total": 0,
        "memory_entries": 0,
        "last_heartbeat": "",
    })

    architecture_health: dict = field(default_factory=lambda: {
        "total_modules": 111,
        "syntax_errors": 0,
        "dead_code_modules": ["sweep_loop.py"],
        "unified_entry_points": 0,
    })

    risk_level: str = "medium"


# ═══════════════════════════════════════════════════════════════
# Product Incident
# ═══════════════════════════════════════════════════════════════

@dataclass
class ProductIncident:
    """A product-level failure or anomaly in the dev environment."""

    incident_id: str
    source: str  # test | runtime | release | packaging | watchdog | ci
    category: str
    severity: str  # low | medium | high | critical
    fingerprint: str = ""  # hash for dedup (auto-computed)
    affected_modules: list[str] = field(default_factory=list)
    stack_trace_redacted: str = ""
    logs_reference: list[str] = field(default_factory=list)
    reproduction_command: str = ""
    product_version: str = "Phase82"
    policy_version: str = ""
    first_seen_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    last_seen_at: str = ""
    occurrence_count: int = 1
    status: str = "open"  # open|diagnosing|candidate_ready|fixed|rolled_back|blocked

    def __post_init__(self):
        if not self.incident_id:
            self.incident_id = f"INC-{self.fingerprint[:12]}"
        if not self.last_seen_at:
            self.last_seen_at = self.first_seen_at
        if not self.fingerprint:
            self.fingerprint = self._compute_fingerprint()

    def _compute_fingerprint(self) -> str:
        raw = f"{self.source}|{self.category}|{self.stack_trace_redacted[:200]}|{','.join(sorted(self.affected_modules))}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════
# Incident Collector
# ═══════════════════════════════════════════════════════════════

class ProductIncidentCollector:
    """Collects, deduplicates, and stores product incidents."""

    def __init__(self, storage_path: Path | str | None = None):
        self._path = Path(storage_path) if storage_path else Path(
            "platform_outputs/product_incidents.jsonl"
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def collect(
        self,
        source: str,
        category: str,
        severity: str,
        affected_modules: list[str] | None = None,
        stack_trace: str = "",
        reproduction: str = "",
    ) -> ProductIncident:
        """Record or update an incident. Deduplicates by fingerprint."""
        incident = ProductIncident(
            incident_id="",  # Auto-generated
            source=source, category=category, severity=severity,
            affected_modules=affected_modules or [],
            stack_trace_redacted=self._redact(stack_trace),
            reproduction_command=reproduction,
        )

        # Check if this incident already exists
        existing = self._find_by_fingerprint(incident.fingerprint)
        if existing:
            existing.occurrence_count += 1
            existing.last_seen_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._update(existing)
            return existing

        self._append(incident)
        return incident

    def collect_from_test_failure(self, test_name: str, error: str, modules: list[str]) -> ProductIncident:
        return self.collect(
            source="test", category="test_failure", severity="medium",
            affected_modules=modules,
            stack_trace=error,
            reproduction=f"pytest {test_name}",
        )

    def collect_from_exception(self, module: str, error: str) -> ProductIncident:
        category = "syntax_error" if "SyntaxError" in error else "runtime_exception"
        severity = "critical" if "SyntaxError" in error else "high"
        return self.collect(
            source="runtime", category=category, severity=severity,
            affected_modules=[module], stack_trace=error,
        )

    def list_open(self) -> list[ProductIncident]:
        incidents = self._load_all()
        return [i for i in incidents if i.status in ("open", "diagnosing", "candidate_ready")]

    def list_by_module(self, module: str) -> list[ProductIncident]:
        return [i for i in self._load_all() if module in i.affected_modules]

    def mark_status(self, incident_id: str, status: str):
        incidents = self._load_all()
        for i in incidents:
            if i.incident_id == incident_id:
                i.status = status
                break
        self._save_all(incidents)

    def get_baseline(self) -> ProductReliabilityBaseline:
        """Generate current reliability baseline."""
        baseline = ProductReliabilityBaseline()
        incidents = self._load_all()
        open_count = sum(1 for i in incidents if i.status in ("open", "diagnosing"))
        baseline.runtime_health["open_incidents"] = open_count
        baseline.runtime_health["total_incidents"] = len(incidents)
        baseline.risk_level = "critical" if open_count > 5 else ("high" if open_count > 2 else "medium")
        return baseline

    def _redact(self, text: str) -> str:
        """Remove secrets, tokens, API keys from stack traces."""
        import re
        patterns = [
            (r'sk-[a-zA-Z0-9]{20,}', '[API_KEY]'),
            (r'Bearer\s+[A-Za-z0-9+/=]{20,}', '[BEARER_TOKEN]'),
            (r'password\s*[=:]\s*["\']?\S+', '[PASSWORD]'),
            (r'Authorization[=:]\s*["\']?\S+', '[AUTH]'),
        ]
        for pat, repl in patterns:
            text = re.sub(pat, repl, text, flags=re.IGNORECASE)
        return text[:2000]

    def _find_by_fingerprint(self, fp: str) -> ProductIncident | None:
        for i in self._load_all():
            if i.fingerprint == fp:
                return i
        return None

    def _append(self, incident: ProductIncident):
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(incident.__dict__, ensure_ascii=False, default=str) + "\n")

    def _update(self, incident: ProductIncident):
        incidents = self._load_all()
        for i, existing in enumerate(incidents):
            if existing.incident_id == incident.incident_id:
                incidents[i] = incident
                break
        self._save_all(incidents)

    def _load_all(self) -> list[ProductIncident]:
        if not self._path.exists():
            return []
        incidents = []
        for line in self._path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    d = json.loads(line)
                    incidents.append(ProductIncident(**{k: d.get(k, "") for k in ProductIncident.__dataclass_fields__}))
                except Exception:
                    pass
        return incidents

    def _save_all(self, incidents: list[ProductIncident]):
        with open(self._path, "w", encoding="utf-8") as f:
            for i in incidents:
                f.write(json.dumps(i.__dict__, ensure_ascii=False, default=str) + "\n")


# ═══════════════════════════════════════════════════════════════
# Root Cause Diagnoser
# ═══════════════════════════════════════════════════════════════

class ProductRootCauseDiagnoser:
    """Rule-based root cause diagnosis for product incidents.

    Uses pattern matching against known failure modes, NOT LLM guessing.
    Only outputs diagnoses with evidence. Low confidence → requires human.
    """

    # Known failure pattern → root cause mapping
    KNOWN_PATTERNS = [
        {
            "symptoms": ["Unterminated string", "Expecting delimiter", "JSONDecodeError",
                        "Expecting value", "char "],
            "category": "reasoner_api",
            "root_cause": "DeepSeek API JSON output truncated due to model token limit or connection interruption",
            "recommended_fix": "retry_with_smaller_prompt",
            "auto_patch_safe": True,
            "confidence": 0.85,
        },
        {
            "symptoms": ["SyntaxError", "was never closed", "unexpected EOF",
                        "invalid syntax", "expected ':'"],
            "category": "syntax_error",
            "root_cause": "Code edit introduced syntax error — missing bracket, paren, or quote",
            "recommended_fix": "fix_syntax_error",
            "auto_patch_safe": True,
            "confidence": 0.95,
        },
        {
            "symptoms": ["Insufficient Balance", "402", "invalid_request_error"],
            "category": "api_balance",
            "root_cause": "LLM API account balance insufficient",
            "recommended_fix": "pause_and_notify",
            "auto_patch_safe": False,
            "confidence": 0.98,
            "requires_human": True,
        },
        {
            "symptoms": ["Remote end closed connection", "Connection reset",
                        "Connection refused", "URLError"],
            "category": "connection_error",
            "root_cause": "Network connection interrupted during API call",
            "recommended_fix": "retry_with_backoff",
            "auto_patch_safe": True,
            "confidence": 0.80,
        },
        {
            "symptoms": ["TimeoutError", "timed out", "timeout"],
            "category": "timeout",
            "root_cause": "Operation exceeded timeout limit",
            "recommended_fix": "increase_timeout_or_split_work",
            "auto_patch_safe": True,
            "confidence": 0.75,
        },
        {
            "symptoms": ["ModuleNotFoundError", "ImportError", "No module named"],
            "category": "import_error",
            "root_cause": "Missing dependency or broken import path",
            "recommended_fix": "fix_import_or_install_dependency",
            "auto_patch_safe": False,
            "confidence": 0.70,
            "requires_human": True,
        },
        {
            "symptoms": ["MemoryError", "Killed", "OOM"],
            "category": "memory_error",
            "root_cause": "Process exceeded available memory",
            "recommended_fix": "reduce_batch_size_or_add_memory_limit",
            "auto_patch_safe": True,
            "confidence": 0.70,
        },
    ]

    def diagnose(self, incident: ProductIncident) -> dict:
        """Diagnose an incident. Returns structured diagnosis."""
        symptoms = incident.stack_trace_redacted + " " + incident.category

        best_match = None
        best_score = 0

        for pattern in self.KNOWN_PATTERNS:
            matches = sum(1 for s in pattern["symptoms"] if s.lower() in symptoms.lower())
            if matches > best_score:
                best_score = matches
                best_match = pattern

        if best_match and best_score >= 1:
            return {
                "diagnosis_id": f"DX-{incident.incident_id}",
                "incident_id": incident.incident_id,
                "suspected_root_causes": [{
                    "module": ", ".join(incident.affected_modules),
                    "reason": best_match["root_cause"],
                    "confidence": best_match["confidence"] * min(best_score / 2, 1.0),
                    "evidence": [f"Matched {best_score} symptom(s) from pattern '{best_match['category']}'"],
                }],
                "recommended_fix_types": [best_match["recommended_fix"]],
                "safe_to_auto_patch": best_match.get("auto_patch_safe", False),
                "requires_human_review": best_match.get("requires_human", best_match["confidence"] < 0.7),
                "validation_plan": self._generate_validation_plan(incident, best_match),
            }

        # No pattern matched
        return {
            "diagnosis_id": f"DX-{incident.incident_id}",
            "incident_id": incident.incident_id,
            "suspected_root_causes": [],
            "recommended_fix_types": ["manual_investigation"],
            "safe_to_auto_patch": False,
            "requires_human_review": True,
            "validation_plan": ["Run full test suite", "Check git diff for recent changes"],
        }

    def _generate_validation_plan(self, incident: ProductIncident, pattern: dict) -> list[str]:
        plan = ["python -c \"import ast; ast.parse(...)\"", "pytest tests/test_reasoner_stability.py"]
        category = pattern.get("category", "")
        if category == "syntax_error":
            plan.insert(0, "python -c 'compile(open(file).read(), file, \"exec\")'")
        elif category == "reasoner_api":
            plan.append("pytest tests/test_reasoner_stability.py -k 'truncation'")
        elif category == "connection_error":
            plan.append("pytest tests/test_production_safety_gate.py")
        plan.append("pytest tests/test_production_safety_gate.py tests/test_release_verifier.py -q")
        return plan


# ═══════════════════════════════════════════════════════════════
# Patch Candidate
# ═══════════════════════════════════════════════════════════════

@dataclass
class PatchCandidate:
    """A controlled, low-risk code fix candidate."""

    patch_id: str
    incident_id: str
    candidate_branch: str = ""
    parent_commit: str = ""
    affected_files: list[str] = field(default_factory=list)
    change_summary: str = ""
    root_cause_hypothesis: str = ""
    risk_level: str = "low"
    auto_apply_allowed: bool = False
    required_tests: list[str] = field(default_factory=list)
    rollback_plan: str = "git checkout parent_commit"
    diff_reference: str = ""
    status: str = "draft"  # draft|applied|validating|passed|failed|promoted|rolled_back|blocked
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    # Safety: max files per patch
    MAX_FILES = 5


class PatchCandidateGenerator:
    """Generates controlled patch candidates from diagnoses."""

    FORBIDDEN_MODULES = {
        "safety_boundary.py",  # Production safety gate
        "unified_http_transport.py",  # Safety enforcement
        # Add more as needed
    }

    def generate(self, incident: ProductIncident, diagnosis: dict) -> PatchCandidate | None:
        """Generate a patch candidate if safe to do so."""
        if not diagnosis.get("safe_to_auto_patch"):
            return None

        affected = incident.affected_modules
        if len(affected) > PatchCandidate.MAX_FILES:
            return None

        # Check forbidden modules
        for mod in affected:
            for forbidden in self.FORBIDDEN_MODULES:
                if forbidden in mod:
                    return None

        return PatchCandidate(
            patch_id=f"PATCH-{incident.incident_id}",
            incident_id=incident.incident_id,
            affected_files=affected,
            change_summary=f"Auto-fix: {diagnosis['suspected_root_causes'][0]['reason'][:200]}" if diagnosis.get("suspected_root_causes") else "",
            root_cause_hypothesis=diagnosis.get("suspected_root_causes", [{}])[0].get("reason", ""),
            risk_level="low",
            auto_apply_allowed=diagnosis.get("safe_to_auto_patch", False),
            required_tests=diagnosis.get("validation_plan", []),
        )
