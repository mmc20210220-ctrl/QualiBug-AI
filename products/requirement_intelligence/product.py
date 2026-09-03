from __future__ import annotations

"""Stable product contract for the Requirement Intelligence entry.

This module deliberately contains product metadata only. It does not define a
second Finding/Evidence persistence model and it must not depend on Bug Discovery
runtime patches, v12 scheduling, experiment execution, observers, or oracles.
"""

from dataclasses import dataclass
from enum import Enum


class ProductStatus(str, Enum):
    PRIMARY = "primary"
    EXPERIMENTAL = "experimental"


class RequirementFindingKind(str, Enum):
    """Initial bounded finding surface for the first commercial validation."""

    CONFLICT = "requirement_conflict"
    MISSING = "requirement_missing"
    AMBIGUITY = "requirement_ambiguity"


@dataclass(frozen=True, slots=True)
class RequirementIntelligenceManifest:
    product_id: str
    display_name: str
    status: ProductStatus
    evidence_required: bool
    supported_findings: tuple[RequirementFindingKind, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "product_id": self.product_id,
            "display_name": self.display_name,
            "status": self.status.value,
            "evidence_required": self.evidence_required,
            "supported_findings": tuple(item.value for item in self.supported_findings),
        }


MANIFEST = RequirementIntelligenceManifest(
    product_id="requirement_intelligence",
    display_name="Requirement Intelligence",
    status=ProductStatus.PRIMARY,
    evidence_required=True,
    supported_findings=(
        RequirementFindingKind.CONFLICT,
        RequirementFindingKind.MISSING,
        RequirementFindingKind.AMBIGUITY,
    ),
)


def get_product_manifest() -> dict[str, object]:
    """Return the externally safe product capability declaration."""

    return MANIFEST.as_dict()
