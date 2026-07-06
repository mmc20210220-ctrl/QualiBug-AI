from ai_test_asset_center.reasoner_quality_report import (
    build_executable_quality_report,
    has_executable_verification_method,
)


def test_has_executable_verification_method_accepts_path_or_steps():
    assert has_executable_verification_method({"verification_method": {"path": "/api/orders"}})
    assert has_executable_verification_method({"verification_method": {"step1": "GET /api/orders"}})
    assert has_executable_verification_method({"verification_method": {"step2": "POST /api/orders"}})
    assert not has_executable_verification_method({"verification_method": {"note": "manual check"}})
    assert not has_executable_verification_method({"title": "narrative only"})


def test_build_executable_quality_report_counts_per_engine_and_final_ratio():
    results_by_engine = {
        "causality": {
            "hypotheses": [
                {"title": "executable", "verification_method": {"path": "/api/orders"}},
                {"title": "narrative", "verification_method": {"note": "inspect manually"}},
            ]
        },
        "invariant": {
            "hypotheses": [
                {"title": "step executable", "verification_method": {"step1": "GET /api/inventory"}},
            ]
        },
        "temporal": {
            "hypotheses": [
                {"title": "no executable", "evidence": "only docs"},
            ]
        },
    }
    final_hypotheses = [
        {"title": "kept executable", "verification_method": {"path": "/api/orders"}},
        {"title": "kept non executable", "evidence": "business docs only"},
    ]

    report = build_executable_quality_report(
        results_by_engine,
        ["causality", "invariant", "temporal"],
        final_hypotheses,
    )

    assert report["executable_hypotheses"] == 1
    assert report["non_executable_hypotheses"] == 1
    assert report["executable_hypothesis_ratio"] == 0.5
    assert report["per_engine_executable_hypotheses"] == {
        "causality": 1,
        "invariant": 1,
        "temporal": 0,
    }
    assert report["per_engine_non_executable_hypotheses"] == {
        "causality": 1,
        "invariant": 0,
        "temporal": 1,
    }
    assert report["per_engine_executable_ratio"] == {
        "causality": 0.5,
        "invariant": 1.0,
        "temporal": 0.0,
    }
    assert report["engines_with_no_executable_output"] == ["temporal"]
