from __future__ import annotations

"""Product capability catalog independent of runtime implementation details."""

from products.requirement_intelligence import (
    get_product_manifest as get_requirement_intelligence_manifest,
)
from products.test_intelligence import (
    get_product_manifest as get_test_intelligence_manifest,
)


def get_product_catalog() -> tuple[dict[str, object], ...]:
    """Return product surfaces without importing Bug Discovery runtime modules."""

    requirement_intelligence = {
        **get_requirement_intelligence_manifest(),
        "entry_mode": "analysis",
    }
    test_intelligence = {
        **get_test_intelligence_manifest(),
        "entry_mode": "analysis",
    }
    bug_discovery = {
        "product_id": "bug_discovery",
        "display_name": "Bug Discovery",
        "status": "experimental",
        "evidence_required": True,
        "entry_mode": "advanced_runtime",
    }
    return (requirement_intelligence, test_intelligence, bug_discovery)
