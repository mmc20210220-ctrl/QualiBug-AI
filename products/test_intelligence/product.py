from __future__ import annotations

"""Stable product contract for Test Intelligence v1.

Test Intelligence owns obligation, supported-semantic coverage, and structured
Test Design semantics. It does not ground designs onto a runtime surface,
execute a target system, persist a second evidence model, or import Bug
Discovery runtime authorities.
"""

from dataclasses import dataclass
from enum import Enum


class TestIntelligenceStatus(str, Enum):
    EXPERIMENTAL = "experimental"


class TestObligationKind(str, Enum):
    BUSINESS_RULE = "business_rule"
    LIFECYCLE_TRANSITION = "lifecycle_transition"
    AUTHORIZATION = "authorization"
    SIDE_EFFECT = "side_effect"
    REQUIREMENT_RISK = "requirement_risk"


@dataclass(frozen=True, slots=True)
class TestIntelligenceManifest:
    product_id: str
    display_name: str
    status: TestIntelligenceStatus
    evidence_required: bool
    structured_test_design_owned: bool
    runtime_grounding_owned: bool
    runtime_execution_owned: bool
    supported_obligation_kinds: tuple[TestObligationKind, ...]
    implemented_obligation_kinds: tuple[TestObligationKind, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "product_id": self.product_id,
            "display_name": self.display_name,
            "status": self.status.value,
            "evidence_required": self.evidence_required,
            "structured_test_design_owned": self.structured_test_design_owned,
            "runtime_grounding_owned": self.runtime_grounding_owned,
            "runtime_execution_owned": self.runtime_execution_owned,
            "supported_obligation_kinds": tuple(
                item.value for item in self.supported_obligation_kinds
            ),
            "implemented_obligation_kinds": tuple(
                item.value for item in self.implemented_obligation_kinds
            ),
        }


MANIFEST = TestIntelligenceManifest(
    product_id="test_intelligence",
    display_name="Test Intelligence",
    status=TestIntelligenceStatus.EXPERIMENTAL,
    evidence_required=True,
    structured_test_design_owned=True,
    runtime_grounding_owned=False,
    runtime_execution_owned=False,
    supported_obligation_kinds=(
        TestObligationKind.BUSINESS_RULE,
        TestObligationKind.LIFECYCLE_TRANSITION,
        TestObligationKind.AUTHORIZATION,
        TestObligationKind.SIDE_EFFECT,
        TestObligationKind.REQUIREMENT_RISK,
    ),
    implemented_obligation_kinds=(
        TestObligationKind.BUSINESS_RULE,
        TestObligationKind.LIFECYCLE_TRANSITION,
        TestObligationKind.AUTHORIZATION,
        TestObligationKind.SIDE_EFFECT,
    ),
)


def get_product_manifest() -> dict[str, object]:
    return MANIFEST.as_dict()