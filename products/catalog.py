from __future__ import annotations

"""Product capability catalog independent of runtime implementation details."""

from products.requirement_intelligence import get_product_manifest


def get_product_catalog() -> tuple[dict[str, object], ...]:
    """Return the product surfaces exposed by the platform.

    Bug Discovery is declared here without importing its legacy/runtime modules.
    That keeps catalog/navigation concerns independent from execution authority.
    """

    requirement_intelligence = {
        **get_product_manifest(),
        "entry_mode": "analysis",
    }
    bug_discovery = {
        "product_id": "bug_discovery",
        "display_name": "Bug Discovery",
        "status": "experimental",
        "evidence_required": True,
        "entry_mode": "advanced_runtime",
    }
    return (requirement_intelligence, bug_discovery)
