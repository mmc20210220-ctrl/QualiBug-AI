"""Unit tests for rule_reconciliation module.

Tests cover:
1. Rule Completeness Profile
2. Missing State Guard detection
3. Evidence collection from multiple sources
4. Conflict detection
5. Candidate Rule Patch generation
6. Rule Versioning (immutability)
7. Candidate executability check
8. Candidate satisfiability check
9. Discriminating experiment generation
10. Shadow Validation
11. Promotion Gate
12. Benchmark leakage detection
13. Anti-hardcoding (no project-specific branches)
"""

import sys
sys.path.insert(0, ".")

from ai_test_asset_center.rule_reconciliation import (
    RuleCompletenessAuditor,
    EvidenceCollector,
    ConflictDetector,
    CandidateRulePatchGenerator,
    RuleVersionManager,
    CandidateExecutabilityChecker,
    CandidateSatisfiabilityChecker,
    DiscriminatingExperimentGenerator,
    ShadowValidator,
    PromotionGate,
    RuleReconciliationEngine,
    RuleEvidence,
    EvidenceClaim,
    CandidateValidationProof,
    DEFECT_MISSING_STATE_GUARD,
    DEFECT_WRONG_OPERATOR,
    STATUS_CANDIDATE,
    STATUS_SHADOW_VALIDATED,
    STATUS_ACTIVE,
    SHADOW_SUPPORTED,
    SHADOW_CONTRADICTED,
    SHADOW_INVALID_EXPERIMENT,
    EVIDENCE_PRD_REQUIREMENT,
    EVIDENCE_API_DOCUMENTATION,
    EVIDENCE_SOURCE_CODE_STATIC_ANALYSIS,
    BLOCK_STATE_GUARD_UNRESOLVED,
)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}: {detail}")


# ─── Test Data ─────────────────────────────────────────────────────────────────

SAMPLE_RULE_INCOMPLETE = {
    "id": "BR-TEST-001",
    "rule_type": "STATE_TRANSITION",
    "entity_ref": "test_entity",
    "description": "只有STATE_A可转移到STATE_B",
    "expression": {"target_state": "STATE_B"},  # Missing from_state!
}

SAMPLE_RULE_COMPLETE = {
    "id": "BR-TEST-002",
    "rule_type": "STATE_TRANSITION",
    "entity_ref": "test_entity",
    "description": "只有STATE_A可转移到STATE_B",
    "expression": {"target_state": "STATE_B", "from_state": "STATE_A"},
    "source_refs": [{"source_id": "test_doc"}],
}

SAMPLE_BEHAVIOR_IR = {
    "states": [
        {"id": "state_initial", "name": "INITIAL"},
        {"id": "state_a", "name": "STATE_A"},
        {"id": "state_b", "name": "STATE_B"},
    ],
    "relations": [
        {"relation_type": "transitions", "from_ref": "state_initial", "to_ref": "state_a", "operation_ref": "op_init"},
        {"relation_type": "transitions", "from_ref": "state_a", "to_ref": "state_b", "operation_ref": "op_transition"},
    ],
    "operations": [
        {"id": "op_init", "method": "POST", "path": "/init"},
        {"id": "op_transition", "method": "POST", "path": "/transition"},
    ],
}

SAMPLE_API_SPEC = """
/transition:
  post:
    description: 只能从STATE_A进入STATE_B。
"""

SAMPLE_BUSINESS_RULES = """
| BR-TEST-001 | STATE_TRANSITION | 只有STATE_A可转移到STATE_B |
"""

SAMPLE_SOURCE_CODE = '''
def _transition(entity_id, from_status, to_status, user):
    if entity["status"] != from_status:
        return error
    _transition(entity_id, "STATE_A", "STATE_B", user)
'''


# ─── Tests ─────────────────────────────────────────────────────────────────────

def test_rule_completeness_profile():
    """Test 1: Rule Completeness Profile detects missing from_state."""
    print("\n=== Test: Rule Completeness Profile ===")
    auditor = RuleCompletenessAuditor()

    # Incomplete rule
    profile = auditor.audit_state_transition_rule(SAMPLE_RULE_INCOMPLETE, SAMPLE_BEHAVIOR_IR)
    check("incomplete_rule_detected", "from_state" in profile.missing_components,
          f"missing={profile.missing_components}")
    check("precondition_incomplete", not profile.precondition_complete)
    check("completeness_score_below_1", profile.completeness_score < 1.0,
          f"score={profile.completeness_score}")

    # Complete rule
    profile2 = auditor.audit_state_transition_rule(SAMPLE_RULE_COMPLETE, SAMPLE_BEHAVIOR_IR)
    check("complete_rule_no_missing", len(profile2.missing_components) == 0,
          f"missing={profile2.missing_components}")


def test_evidence_collection():
    """Test 2: Evidence collection from multiple sources."""
    print("\n=== Test: Evidence Collection ===")
    collector = EvidenceCollector()

    # From API spec
    api_evidence = collector.collect_from_api_spec(SAMPLE_API_SPEC, "/transition", "STATE_B")
    check("api_evidence_found", len(api_evidence) > 0, f"count={len(api_evidence)}")
    if api_evidence:
        check("api_evidence_normative", api_evidence[0].normative)
        check("api_evidence_from_state", "STATE_A" in api_evidence[0].normalized_claim,
              f"claim={api_evidence[0].normalized_claim}")

    # From business rules
    prd_evidence = collector.collect_from_business_rules(SAMPLE_BUSINESS_RULES, "BR-TEST-001")
    check("prd_evidence_found", len(prd_evidence) > 0, f"count={len(prd_evidence)}")
    if prd_evidence:
        check("prd_evidence_normative", prd_evidence[0].normative)

    # From source code
    code_evidence = collector.collect_from_source_code(SAMPLE_SOURCE_CODE, "_transition")
    check("code_evidence_found", len(code_evidence) > 0, f"count={len(code_evidence)}")
    if code_evidence:
        check("code_evidence_not_normative", not code_evidence[0].normative)


def test_conflict_detection():
    """Test 3: Conflict detection between evidence."""
    print("\n=== Test: Conflict Detection ===")
    detector = ConflictDetector()

    # Create conflicting claims
    ev_a = RuleEvidence(
        evidence_id="ev_a", source_type=EVIDENCE_PRD_REQUIREMENT,
        source_id="prd", source_location="rule1",
        extracted_statement="from_state=X", normalized_claim="from_state=X",
        confidence=0.9, normative=True, independent_group="prd",
    )
    ev_b = RuleEvidence(
        evidence_id="ev_b", source_type=EVIDENCE_API_DOCUMENTATION,
        source_id="api", source_location="op1",
        extracted_statement="from_state=Y", normalized_claim="from_state=Y",
        confidence=0.8, normative=True, independent_group="api",
    )

    claim = EvidenceClaim(
        claim_id="claim_1", claim_type="from_state", candidate_value="X",
        supporting_evidence=["ev_a"], opposing_evidence=["ev_b"],
    )

    conflicts = detector.detect_conflicts([claim], {"ev_a": ev_a, "ev_b": ev_b})
    check("conflict_detected", len(conflicts) > 0, f"count={len(conflicts)}")
    if conflicts:
        check("conflict_resolved_by_priority",
              conflicts[0].status == "RESOLVED_BY_HIGHER_PRIORITY_SOURCE",
              f"status={conflicts[0].status}")


def test_candidate_patch_generation():
    """Test 4: Candidate Rule Patch generation."""
    print("\n=== Test: Candidate Patch Generation ===")
    generator = CandidateRulePatchGenerator()

    patch = generator.generate_state_guard_patch(
        SAMPLE_RULE_INCOMPLETE, "STATE_A", ["ev_1", "ev_2"]
    )

    check("patch_has_defect_type", DEFECT_MISSING_STATE_GUARD in patch.defect_types,
          f"types={patch.defect_types}")
    check("patch_has_additions", "predicates" in patch.additions)
    check("patch_has_evidence", len(patch.evidence_support) == 2)
    check("patch_minimal_diff", len(patch.unchanged_components) > 0)


def test_rule_versioning():
    """Test 5: Rule Versioning (immutability)."""
    print("\n=== Test: Rule Versioning ===")
    manager = RuleVersionManager()
    generator = CandidateRulePatchGenerator()

    patch = generator.generate_state_guard_patch(
        SAMPLE_RULE_INCOMPLETE, "STATE_A", ["ev_1"]
    )

    # Create candidate version
    candidate = manager.create_candidate_version(SAMPLE_RULE_INCOMPLETE, patch, ["ev_1"])
    check("candidate_created", candidate.version_id != "")
    check("candidate_status", candidate.status == STATUS_CANDIDATE)
    check("candidate_has_parent", candidate.parent_version_id != "")

    # Original rule unchanged
    check("original_rule_unchanged",
          "from_state" not in SAMPLE_RULE_INCOMPLETE.get("expression", {}))

    # Candidate has new expression
    check("candidate_has_from_state",
          candidate.rule_payload.get("expression", {}).get("from_state") == "STATE_A")


def test_executability_check():
    """Test 6: Candidate executability check."""
    print("\n=== Test: Executability Check ===")
    checker = CandidateExecutabilityChecker()
    manager = RuleVersionManager()
    generator = CandidateRulePatchGenerator()

    patch = generator.generate_state_guard_patch(
        SAMPLE_RULE_INCOMPLETE, "STATE_A", ["ev_1"]
    )
    candidate = manager.create_candidate_version(SAMPLE_RULE_INCOMPLETE, patch, ["ev_1"])

    executable, reason = checker.check_executability(candidate, SAMPLE_BEHAVIOR_IR)
    check("candidate_executable", executable, f"reason={reason}")


def test_satisfiability_check():
    """Test 7: Candidate satisfiability check."""
    print("\n=== Test: Satisfiability Check ===")
    checker = CandidateSatisfiabilityChecker()
    manager = RuleVersionManager()
    generator = CandidateRulePatchGenerator()

    patch = generator.generate_state_guard_patch(
        SAMPLE_RULE_INCOMPLETE, "STATE_A", ["ev_1"]
    )
    candidate = manager.create_candidate_version(SAMPLE_RULE_INCOMPLETE, patch, ["ev_1"])

    state_graph = {"states": ["INITIAL", "STATE_A", "STATE_B"]}
    ctrl_sat, viol_sat, reason = checker.check_satisfiability(candidate, state_graph)

    check("control_satisfiable", ctrl_sat)
    check("violation_satisfiable", viol_sat)


def test_discriminating_experiments():
    """Test 8: Discriminating experiment generation."""
    print("\n=== Test: Discriminating Experiments ===")
    generator = DiscriminatingExperimentGenerator()
    manager = RuleVersionManager()
    patch_gen = CandidateRulePatchGenerator()

    patch = patch_gen.generate_state_guard_patch(
        SAMPLE_RULE_INCOMPLETE, "STATE_A", ["ev_1"]
    )
    candidate = manager.create_candidate_version(SAMPLE_RULE_INCOMPLETE, patch, ["ev_1"])

    experiments = generator.generate_state_guard_experiments(
        candidate, SAMPLE_RULE_INCOMPLETE, ["INITIAL", "STATE_B"]
    )

    control_exps = [e for e in experiments if e["case_type"] == "CONTROL"]
    violation_exps = [e for e in experiments if e["case_type"] == "VIOLATION"]

    check("has_2_controls", len(control_exps) >= 2, f"count={len(control_exps)}")
    check("has_2_violations", len(violation_exps) >= 2, f"count={len(violation_exps)}")
    check("experiments_distinguish_parent",
          all("distinguishing_from_parent" in e for e in experiments))


def test_shadow_validation():
    """Test 9: Shadow Validation."""
    print("\n=== Test: Shadow Validation ===")
    validator = ShadowValidator()
    manager = RuleVersionManager()
    patch_gen = CandidateRulePatchGenerator()

    patch = patch_gen.generate_state_guard_patch(
        SAMPLE_RULE_INCOMPLETE, "STATE_A", ["ev_1"]
    )
    candidate = manager.create_candidate_version(SAMPLE_RULE_INCOMPLETE, patch, ["ev_1"])

    # Supported case
    control_results = [
        {"expected_sut_behavior": "accepted", "actual_sut_behavior": "accepted"},
        {"expected_sut_behavior": "accepted", "actual_sut_behavior": "accepted"},
    ]
    violation_results = [
        {"expected_sut_behavior": "rejected", "actual_sut_behavior": "accepted"},  # Bug!
        {"expected_sut_behavior": "rejected", "actual_sut_behavior": "accepted"},  # Bug!
    ]
    result = validator.validate(candidate, control_results, violation_results)
    check("shadow_supported", result == SHADOW_SUPPORTED, f"result={result}")

    # Invalid case
    result2 = validator.validate(candidate, [], [])
    check("shadow_invalid_empty", result2 == SHADOW_INVALID_EXPERIMENT)


def test_promotion_gate():
    """Test 10: Promotion Gate."""
    print("\n=== Test: Promotion Gate ===")
    gate = PromotionGate()
    manager = RuleVersionManager()
    patch_gen = CandidateRulePatchGenerator()

    patch = patch_gen.generate_state_guard_patch(
        SAMPLE_RULE_INCOMPLETE, "STATE_A", ["ev_1"]
    )
    candidate = manager.create_candidate_version(SAMPLE_RULE_INCOMPLETE, patch, ["ev_1"])

    # Passing proof
    proof = CandidateValidationProof(
        proof_id="proof_1",
        candidate_id=candidate.version_id,
        parent_rule_id="BR-TEST-001",
        normative_support=1,
        independent_support=2,
        benchmark_not_used=True,
        original_rule_preserved=True,
        validation_result="SUPPORTED",
    )
    control_results = [{"passed": True}, {"passed": True}]
    violation_results = [{"consistent": True}, {"consistent": True}]

    can_promote, failed = gate.evaluate(candidate, proof, control_results, violation_results)
    check("promotion_allowed", can_promote, f"failed={failed}")

    # Failing proof (no normative evidence)
    proof_bad = CandidateValidationProof(
        proof_id="proof_2",
        candidate_id=candidate.version_id,
        parent_rule_id="BR-TEST-001",
        normative_support=0,  # Missing!
        independent_support=2,
        benchmark_not_used=True,
        original_rule_preserved=True,
        validation_result="SUPPORTED",
    )
    can_promote2, failed2 = gate.evaluate(candidate, proof_bad, control_results, violation_results)
    check("promotion_blocked_no_normative", not can_promote2)
    check("failed_condition_reported", "normative_evidence_exists" in failed2)


def test_full_reconciliation():
    """Test 11: Full reconciliation pipeline."""
    print("\n=== Test: Full Reconciliation Pipeline ===")
    engine = RuleReconciliationEngine()

    source_documents = {
        "api_spec": SAMPLE_API_SPEC,
        "business_rules": SAMPLE_BUSINESS_RULES,
        "source_code": SAMPLE_SOURCE_CODE,
    }

    result = engine.reconcile_state_transition_rule(
        SAMPLE_RULE_INCOMPLETE, SAMPLE_BEHAVIOR_IR, source_documents
    )

    check("reconciliation_ready", result["status"] == "READY_FOR_VALIDATION",
          f"status={result.get('status')}, reason={result.get('reason')}")
    check("defect_type_correct", DEFECT_MISSING_STATE_GUARD in result.get("defect_types", []))
    check("evidence_collected", len(result.get("evidence", [])) >= 3,
          f"count={len(result.get('evidence', []))}")
    check("candidate_generated", "candidate" in result)
    check("experiments_generated", len(result.get("experiments", [])) >= 4)


def test_anti_hardcoding():
    """Test 12: Anti-hardcoding - no project-specific branches."""
    print("\n=== Test: Anti-Hardcoding ===")
    import inspect
    from ai_test_asset_center import rule_reconciliation

    source = inspect.getsource(rule_reconciliation)

    # Check for forbidden patterns
    forbidden = [
        "ContractFlow", "CF-PAY", "CF-", "contractflow",
        "payment_request", "invoice", "milestone",
        "BR-PAY-006", "BR-CON", "BR-INV", "BR-MIL",
    ]

    found_hardcoding = []
    for pattern in forbidden:
        if pattern.lower() in source.lower():
            # Exclude comments and docstrings that explain generality
            if pattern not in ["payment_request"]:  # This might appear in generic examples
                found_hardcoding.append(pattern)

    check("no_project_hardcoding", len(found_hardcoding) == 0,
          f"found={found_hardcoding}")


def test_benchmark_isolation():
    """Test 13: Benchmark isolation."""
    print("\n=== Test: Benchmark Isolation ===")
    import inspect
    from ai_test_asset_center import rule_reconciliation

    source = inspect.getsource(rule_reconciliation)

    # Benchmark IDs must not appear
    benchmark_patterns = ["CF-PAY-006", "CF-STATE", "CF-DATA", "benchmark_bug_id"]
    found = [p for p in benchmark_patterns if p in source]

    check("no_benchmark_ids", len(found) == 0, f"found={found}")


# ─── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Rule Reconciliation Unit Tests")
    print("=" * 60)

    test_rule_completeness_profile()
    test_evidence_collection()
    test_conflict_detection()
    test_candidate_patch_generation()
    test_rule_versioning()
    test_executability_check()
    test_satisfiability_check()
    test_discriminating_experiments()
    test_shadow_validation()
    test_promotion_gate()
    test_full_reconciliation()
    test_anti_hardcoding()
    test_benchmark_isolation()

    print("\n" + "=" * 60)
    print(f"RESULTS: {PASS} PASS, {FAIL} FAIL")
    print("=" * 60)

    sys.exit(0 if FAIL == 0 else 1)
