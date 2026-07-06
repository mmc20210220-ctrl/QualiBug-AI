def test_product_scope_is_detection_evidence_presentation_and_regression():
    product_scope = {
        "discover": True,
        "evidence": True,
        "present": True,
        "regression": True,
        "implementation_guidance": False,
    }

    assert product_scope["discover"] is True
    assert product_scope["evidence"] is True
    assert product_scope["present"] is True
    assert product_scope["regression"] is True
    assert product_scope["implementation_guidance"] is False
