from ai_test_asset_center.evidence_package import build_evidence_package, build_evidence_package_report


def test_build_evidence_package_contains_violation_metadata_and_runtime_evidence():
    package = build_evidence_package(
        {
            "violation_id": "VIO-ORDER-001",
            "title": "Order creation returns 500",
            "behavior_id": "BEH-ORDER-CREATE",
            "behavior_name": "Create Order",
            "category": "orders",
            "confirmed": True,
            "runtime_evidence": {"status_code": 500, "body": "Internal Server Error"},
        }
    )

    assert package["package_id"] == "EP-VIO-ORDER-001"
    assert package["violation"] == {
        "violation_id": "VIO-ORDER-001",
        "title": "Order creation returns 500",
        "behavior_id": "BEH-ORDER-CREATE",
        "behavior_name": "Create Order",
        "category": "orders",
        "confirmed": True,
    }
    assert package["runtime_evidence"] == {"status_code": 500, "body": "Internal Server Error"}
    assert package["audit_package"]["evidence_complete"] is True


def test_build_evidence_package_derives_request_response_and_reproduction_steps():
    package = build_evidence_package(
        {
            "violation_id": "VIO-PAYMENT-001",
            "behavior_id": "BEH-PAYMENT-CAPTURE",
            "request": {"method": "POST", "path": "/payments"},
            "response": {"status": 409, "body": "duplicate charge"},
            "runtime_evidence": {"duration_ms": 120},
        }
    )

    assert package["request_response_evidence"] == {
        "request": {"method": "POST", "path": "/payments"},
        "response": {"status": 409, "body": "duplicate charge"},
    }
    assert len(package["reproduction_steps"]) == 3
    assert "captured request" in package["reproduction_steps"][0]


def test_build_evidence_package_preserves_explicit_reproduction_steps_and_risk_context():
    package = build_evidence_package(
        {
            "violation_id": "VIO-EXPORT-001",
            "behavior_id": "BEH-EXPORT",
            "steps_to_reproduce": ["Open export page", "Run CSV export", "Observe empty file"],
            "severity": "P1",
            "risk_score": 80,
            "risk_assessment": {"impact": "customer data unavailable"},
        }
    )

    assert package["reproduction_steps"] == ["Open export page", "Run CSV export", "Observe empty file"]
    assert package["risk_context"] == {
        "impact": "customer data unavailable",
        "severity": "P1",
        "risk_score": 80,
    }


def test_build_evidence_package_includes_traceability_fields():
    package = build_evidence_package(
        {
            "violation_id": "VIO-LOGIN-001",
            "behavior_id": "BEH-LOGIN",
            "evidence_ids": ["EVID-1", "EVID-2"],
            "validation_run_id": "RUN-1",
            "regression_asset_id": "REG-1",
        }
    )

    assert package["traceability"] == {
        "behavior_id": "BEH-LOGIN",
        "violation_id": "VIO-LOGIN-001",
        "evidence_ids": ["EVID-1", "EVID-2"],
        "validation_run_ids": ["RUN-1"],
        "regression_asset_ids": ["REG-1"],
    }


def test_build_evidence_package_report_calculates_completeness():
    report = build_evidence_package_report(
        [
            {"violation_id": "VIO-1", "confirmed": True, "runtime_evidence": {"status": 500}},
            {"violation_id": "VIO-2"},
        ]
    )

    assert report["total_packages"] == 2
    assert report["confirmed_packages"] == 1
    assert report["evidence_complete_packages"] == 1
    assert report["evidence_completeness_percent"] == 50.0


def test_evidence_package_report_does_not_emit_repair_language():
    report = build_evidence_package_report(
        [
            {
                "violation_id": "VIO-PAYMENT-001",
                "behavior_id": "BEH-PAYMENT",
                "runtime_evidence": {"status": 500},
            }
        ]
    )

    serialized = str(report).lower()
    assert "fix" not in serialized
    assert "repair" not in serialized
    assert "recommendation" not in serialized
    assert "remediation" not in serialized
