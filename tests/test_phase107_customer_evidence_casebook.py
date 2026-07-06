from ai_test_asset_center.runtime_customer_evidence_casebook_v2 import build_evidence_case


def _finding() -> dict:
    return {
        "finding_id": "F-GEN-1",
        "method": "PATCH",
        "path": "/v2/cases/42/resolve",
        "source_refs": [{"file": "policy.md", "section": "Resolution", "quote": "A closed case cannot be resolved twice."}],
        "responses": [{"status_code": 200}],
        "snapshots": {"before": [{"observer_kind": "primary_record"}], "after": [{"observer_kind": "audit_projection"}]},
        "verification": {"verdict": "validated_candidate", "reason": "A terminal record accepted another resolution."},
    }


def test_domain_neutral_write_proof_is_customer_ready() -> None:
    proof = build_evidence_case(_finding())
    assert proof["proof_status"] == "customer_ready"
    assert proof["coverage_score"] == 1.0
    assert "admin" not in str(proof).lower()
    assert "single-tenant" not in str(proof).lower()


def test_missing_write_snapshots_stays_visible() -> None:
    finding = _finding()
    finding["snapshots"] = {}
    proof = build_evidence_case(finding)
    assert proof["proof_status"] == "needs_more_evidence"
    assert proof["evidence_gaps"] == ["before_after_observation"]
