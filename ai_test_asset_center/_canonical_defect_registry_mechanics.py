"""Receipt-derived canonical defect identity for formal occurrences.

The registry never trusts titles, descriptions, severity, confidence, or an
executor-authored identity hint.  Every identity dimension is derived from the
validated Gate-v2 evidence bundle embedded in the immutable attempt ledger.
Occurrence identities remain available for audit while commercial counts use
only canonical defect identities.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from .assertion_control_policy import assertion_requires_control
from .customer_delivery_gate import LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
from .discovery_mainline_contract import (
    MainlineContractError,
    validate_mainline_run_contract,
)
from .formal_delivery_authority import (
    FormalDeliveryAuthorityError,
    build_formal_delivery_authority_receipt,
)
from .formal_delivery_scope import formal_customer_deliverable_findings
from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    validate_obligation_attempt_ledger,
)


CANONICAL_IDENTITY_EVIDENCE_SCHEMA = "qualibug.canonical-identity-evidence.v3"
CANONICAL_DEFECT_REGISTRY_SCHEMA = "qualibug.canonical-defect-registry.v3"
DEFECT_IDENTITY_CONSISTENCY_SCHEMA = "qualibug.defect-identity-consistency.v1"
_CANONICAL_SEMANTIC_DOMAIN = "qualibug.canonical-semantic-value.v1"

_EVIDENCE_FIELDS = {
    "schema_version",
    "operation",
    "property",
    "actor_relation",
    "resource_identity_class",
    "mutation",
    "observed_outcome",
    "proof",
}
_IDENTITY_FIELDS = _EVIDENCE_FIELDS - {"schema_version", "proof"}
class CanonicalDefectRegistryError(ValueError):
    """Canonical identity evidence is absent, ambiguous, or inconsistent."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    text = _text(value).lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _incomplete(field: str) -> CanonicalDefectRegistryError:
    return CanonicalDefectRegistryError(f"CANONICAL_IDENTITY_INCOMPLETE:{field}")


def _ambiguous(field: str) -> CanonicalDefectRegistryError:
    return CanonicalDefectRegistryError(f"CANONICAL_IDENTITY_AMBIGUOUS:{field}")


def _normalized_text(value: Any) -> str:
    return " ".join(_text(value).split()).lower()


def _normalized_identity_class(value: Any) -> str:
    text = _normalized_text(value)
    text = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        "{id}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(?<![a-z])\d{4,}(?![a-z])", "{id}", text)
    return text


def _normalize_path_segment(segment: str) -> str:
    """Collapse ID-like path segments into a placeholder (industry-agnostic).

    Mirrors the generic normalization used by the external evaluator so the
    same defect surface reported against different runtime-generated resource
    IDs (pure numeric IDs, qb_test_*/QB-TEST-* test IDs, alphanumeric IDs)
    shares a single canonical identity.
    """
    if not segment or segment == "{param}":
        return segment
    if segment.isdigit():
        return "{id}"
    if re.fullmatch(r"(?i)qb[_-]test[_-].+", segment):
        return "{id}"
    if re.fullmatch(r"[A-Za-z]+\d+[A-Za-z0-9]*", segment) and len(segment) >= 3:
        return "{id}"
    return segment


def _is_operation_locator(locator: Any) -> bool:
    """True when a source-ref locator denotes an operation/path rather than an
    actor instance reference (e.g. ``buyer:buyer01``) or other instance handle.
    """
    text = _text(locator)
    if text.startswith("/"):
        return True
    if re.match(r"^[A-Za-z]+\s+/", text):
        return True
    return False


def _normalized_locator(value: Any) -> str:
    locator = _text(value).split("?", 1)[0]
    if not locator:
        raise _incomplete("operation.source_locator")
    prefix = ""
    path = locator
    match = re.match(r"^([A-Za-z][A-Za-z0-9_.-]*)\s+(.+)$", locator)
    if match:
        prefix = match.group(1).upper() + " "
        path = match.group(2)
    if path.startswith("/"):
        path = re.sub(r"/{2,}", "/", path)
        path = re.sub(r"\{[^{}\/]+\}", "{param}", path)
        path = re.sub(r"/:([A-Za-z_][A-Za-z0-9_]*)", "/{param}", path)
        path = "/".join(_normalize_path_segment(seg) for seg in path.split("/"))
        if len(path) > 1:
            path = path.rstrip("/")
    else:
        path = _normalized_identity_class(path)
    return prefix + path


def _semantic_digest(value: Any) -> str:
    message = _CANONICAL_SEMANTIC_DOMAIN + "\0" + _canonical(value)
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def _semantic_value(value: Any, *, assertion_kind: str, depth: int = 0) -> Any:
    if depth > 4:
        return {"type": "truncated"}
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if assertion_kind in {"http_status", "status_code", "http_status_class"}:
            return value
        if value == 0:
            return {"type": "number", "class": "zero"}
        return {"type": "number", "class": "positive" if value > 0 else "negative"}
    if isinstance(value, str):
        normalized = _normalized_identity_class(value)
        if (
            assertion_kind in {"http_status", "status_code", "http_status_class"}
            and re.fullmatch(r"[1-5][0-9]{2}", normalized)
        ):
            return int(normalized)
        return {
            "type": "string",
            "semantic_digest": _semantic_digest(normalized),
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "items": [
                _semantic_value(
                    item,
                    assertion_kind=assertion_kind,
                    depth=depth + 1,
                )
                for item in value[:12]
            ],
        }
    if isinstance(value, dict):
        entries = [
            {
                "key_digest": _semantic_digest(_normalized_text(key)),
                "value": _semantic_value(
                    item,
                    assertion_kind=assertion_kind,
                    depth=depth + 1,
                ),
            }
            for key, item in sorted(
                value.items(),
                key=lambda pair: _normalized_text(pair[0]),
            )[:24]
        ]
        return {
            "type": "object",
            "entries": entries,
        }
    return {"type": type(value).__name__}


def _observation_class(step: dict[str, Any]) -> str:
    status = step.get("status_code")
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        return f"http:{status // 100}xx"
    for field in ("outcome_class", "state_class", "event_class", "result_class"):
        if _text(step.get(field)):
            return f"typed:{_normalized_text(step[field])}"
    return "typed:observed"


def _source_ref_projection(values: Any) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    for raw in _list(values):
        row = _dict(raw)
        # kind aliases: some historical producers emit "type"/"source_type"
        # instead of "kind"; accept them so identity derivation stays grounded
        # instead of dropping the reference.
        kind = _normalized_text(
            row.get("kind") or row.get("type") or row.get("source_type")
        )
        # Locator aliases: the enterprise fact-evidence chain emits
        # "source_locator" as its canonical locator field; older producers use
        # "path"/"ref". Accept all so grounded references are not dropped.
        locator = _text(
            row.get("locator")
            or row.get("source_locator")
            or row.get("path")
            or row.get("ref")
        )
        if not kind or not locator:
            continue
        projected.append({"kind": kind, "locator": _normalized_locator(locator)})
    unique = {_canonical(item): item for item in projected}
    return [unique[key] for key in sorted(unique)]


def _one_step(reproduction: dict[str, Any], phase: str) -> dict[str, Any]:
    step = _optional_step(reproduction, phase)
    if step is None:
        raise _incomplete(f"reproduction.{phase}_step")
    return step


def _optional_step(
    reproduction: dict[str, Any],
    phase: str,
) -> dict[str, Any] | None:
    rows = [
        _dict(raw)
        for raw in _list(reproduction.get("step_observations"))
        if _normalized_text(_dict(raw).get("phase")) == phase
    ]
    if not rows:
        return None
    # Select the first step when multiple exist (e.g., multi-step treatment).
    return rows[0]


def _one_violation(oracle: dict[str, Any]) -> dict[str, Any]:
    rows = [
        _dict(raw)
        for raw in _list(oracle.get("assertions"))
        if _text(_dict(raw).get("status")).upper() == "VIOLATION"
    ]
    if not rows:
        raise _incomplete("oracle.violation_assertion")
    if len(rows) > 1:
        # Multiple violations: select the most specific one as primary.
        _GENERIC_KINDS = {"http_status_class"}
        _specific = [
            v for v in rows
            if _text(v.get("kind")) not in _GENERIC_KINDS
        ]
        rows = [_specific[0]] if _specific else [rows[0]]
    if len(rows) != 1:
        raise _ambiguous("one_violation_per_occurrence_required")
    return rows[0]


def _observer_kinds(
    assertion: dict[str, Any],
    observer_receipts: list[Any],
) -> list[str]:
    by_id = {
        _text(_dict(raw).get("receipt_id")): _dict(raw)
        for raw in observer_receipts
        if _text(_dict(raw).get("receipt_id"))
    }
    required = [
        _text(value)
        for value in _list(assertion.get("observer_receipt_ids"))
        if _text(value)
    ]
    if not required:
        raise _incomplete("assertion.observer_receipt_ids")
    if any(receipt_id not in by_id for receipt_id in required):
        raise _incomplete("assertion.observer_receipt_missing")
    return sorted({
        _normalized_text(by_id[receipt_id].get("observer_id"))
        for receipt_id in required
        if _normalized_text(by_id[receipt_id].get("observer_id"))
    })


def _receipted_actor_class(
    actor_ref: Any,
    contract_receipts: list[Any],
) -> str:
    reference = _text(actor_ref)
    if not reference:
        raise _incomplete("actor_relation.reference")
    matches = [
        _dict(raw)
        for raw in contract_receipts
        if _normalized_text(_dict(raw).get("kind")) == "actor"
        and _text(_dict(raw).get("subject_id")) == reference
        and _text(_dict(raw).get("status")).upper() == "OBSERVED"
    ]
    if len(matches) != 1:
        raise _ambiguous("one_actor_receipt_per_reference_required")
    evidence = _dict(matches[0].get("evidence"))
    actor_class = _normalized_identity_class(
        evidence.get("role") or evidence.get("actor_class")
    )
    if not actor_class:
        raise _incomplete("actor_relation.class")
    return actor_class


def _canonical_actor_relation(
    *,
    assertion_kind: str,
    control_actor_class: str,
    treatment_actor_class: str,
) -> dict[str, str]:
    if not assertion_requires_control(assertion_kind):
        return {
            "control_actor_class": "not_identity_defining",
            "treatment_actor_class": "not_identity_defining",
            "relation": "actor_insensitive_property",
        }
    # The resource owner's concrete role/instance is not identity defining:
    # "a non-owner actor can access the owner's resource" is the same defect
    # surface regardless of which owner role/instance holds the resource.
    # Only the accessing (treatment) actor class distinguishes the surface.
    return {
        "control_actor_class": "resource_owner",
        "treatment_actor_class": treatment_actor_class,
        "relation": "control_to_treatment",
    }


def _request_semantics_proof(
    step: dict[str, Any],
    contract_receipts: list[Any],
) -> dict[str, str]:
    semantics = _text(step.get("request_semantics_fingerprint"))
    body_fingerprint = _text(step.get("request_body_fingerprint"))
    if not _is_sha256(semantics) or not _is_sha256(body_fingerprint):
        raise _incomplete("treatment.request_semantics")
    matches = []
    for raw in contract_receipts:
        receipt = _dict(raw)
        evidence = _dict(receipt.get("evidence"))
        if (
            _normalized_text(receipt.get("kind")) == "treatment"
            and _text(evidence.get("request_semantics_fingerprint")) == semantics
        ):
            matches.append(evidence)
    if len(matches) != 1:
        raise _incomplete("treatment.contract_request_semantics_proof")
    evidence = matches[0]
    checks = {
        "path_template": _text(step.get("path_template")),
        "request_body_fingerprint": body_fingerprint,
        "mutation_class": _text(step.get("mutation_class")),
        "mutation_selector": _text(step.get("mutation_selector")),
        "mutation_operator": _text(step.get("mutation_operator")),
    }
    for field, expected in checks.items():
        if _text(evidence.get(field)) != expected:
            raise _incomplete(f"treatment.contract_mismatch:{field}")
    return {
        "request_body_fingerprint": body_fingerprint,
        "request_semantics_fingerprint": semantics,
    }


def derive_canonical_identity_evidence(
    attempt: dict[str, Any],
) -> dict[str, Any]:
    """Derive every stable dimension from validated receipt data only."""

    row = _dict(attempt)
    if _text(row.get("terminal_status")).upper() != "DELIVERABLE":
        raise _incomplete("attempt.deliverable")
    bundle = _dict(row.get("delivery_evidence_bundle"))
    reproduction = _dict(bundle.get("reproduction_receipt"))
    oracle = _dict(bundle.get("oracle_receipt"))
    treatment = _one_step(reproduction, "treatment")
    # The identity-defining assertion comes from the validated oracle receipt and
    # nowhere else.  A finding's own failed_assertions list is unsigned mutable
    # data; letting it define canonical defect identity would let a synthetic
    # assertion mint a customer-visible defect.
    assertion = _one_violation(oracle)
    assertion_kind = _normalized_text(assertion.get("kind"))
    if not assertion_kind:
        raise _incomplete("assertion.kind")
    control = _optional_step(reproduction, "control")
    requires_control = assertion_requires_control(assertion_kind)
    if control is None and requires_control:
        raise _incomplete("reproduction.control_step")
    method = _text(treatment.get("method")).upper()
    operation_ref = _normalized_text(treatment.get("operation_ref"))
    locator = _text(
        treatment.get("path_template")
        or treatment.get("source_locator")
        or treatment.get("operation_locator")
    )
    if not operation_ref or not locator:
        raise _incomplete("operation")
    adapter = _normalized_text(treatment.get("adapter")) or (
        "http" if method and locator.startswith("/") else "typed"
    )
    verb = method or _normalized_text(
        treatment.get("operation_kind") or treatment.get("action")
    )
    if not verb:
        raise _incomplete("operation.verb")
    source_refs = _source_ref_projection(
        assertion.get("source_refs") or reproduction.get("source_refs")
    )
    if not source_refs:
        raise _incomplete("property.source_refs")
    contract_receipts = _list(bundle.get("contract_evidence_receipts"))
    control_actor = (
        "not_identity_defining"
        if not requires_control
        else _receipted_actor_class(
            _dict(control).get("actor_ref"),
            contract_receipts,
        )
    )
    treatment_actor = (
        _receipted_actor_class(
            treatment.get("actor_ref"),
            contract_receipts,
        )
        if requires_control
        else "not_identity_defining"
    )
    mutation_class = _normalized_text(treatment.get("mutation_class"))
    if not mutation_class:
        raise _incomplete("mutation.class")
    request_proof = _request_semantics_proof(
        treatment,
        contract_receipts,
    )
    expected_signature = _semantic_value(
        assertion.get("expected"), assertion_kind=assertion_kind
    )
    actual_signature = _semantic_value(
        assertion.get("actual"), assertion_kind=assertion_kind
    )
    # Observer composition is detection infrastructure, not defect identity.
    # Validate observer-receipt provenance (raises on incomplete receipts) but
    # do NOT embed the observer set into the canonical identity: the same
    # defect surface observed through different observer ensembles must
    # collapse to a single canonical defect instead of fragmenting.
    # Provenance validation is unconditional: an assertion with no receipted
    # observer lineage has no proof it was ever observed.
    _observer_kinds(
        assertion,
        _list(bundle.get("observer_receipts")),
    )
    identity = {
        "operation": {
            "adapter": adapter,
            "verb": verb.upper() if adapter == "http" else verb,
            "operation_ref": operation_ref,
            "source_locator": _normalized_locator(locator),
        },
        "property": {
            "assertion_kind": assertion_kind,
            "expected_signature": expected_signature,
        },
        "actor_relation": _canonical_actor_relation(
            assertion_kind=assertion_kind,
            control_actor_class=control_actor,
            treatment_actor_class=treatment_actor,
        ),
        "resource_identity_class": {
            "source_locators": sorted({
                item["locator"]
                for item in source_refs
                if _is_operation_locator(item["locator"])
            } | {_normalized_locator(locator)}),
        },
        "mutation": {
            "class": mutation_class,
            "selector": _normalized_text(treatment.get("mutation_selector")),
            "operator": _normalized_text(treatment.get("mutation_operator")),
        },
        "observed_outcome": {
            "assertion_kind": assertion_kind,
            "expected_signature": expected_signature,
            "actual_signature": actual_signature,
            "control_observation_class": (
                "not_observed" if control is None else _observation_class(control)
            ),
            "treatment_observation_class": _observation_class(treatment),
        },
    }
    return {
        "schema_version": CANONICAL_IDENTITY_EVIDENCE_SCHEMA,
        **identity,
        "proof": {
            "assertion_receipt_id": _text(
                assertion.get("receipt_id") or assertion.get("assertion_id")
            ),
            "oracle_receipt_id": _text(oracle.get("receipt_id")),
            "reproduction_receipt_id": _text(reproduction.get("receipt_id")),
            **request_proof,
        },
    }


def _validated_identity_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    row = _dict(evidence)
    if set(row) != _EVIDENCE_FIELDS:
        raise _incomplete("evidence.fields")
    if row.get("schema_version") != CANONICAL_IDENTITY_EVIDENCE_SCHEMA:
        raise _incomplete("evidence.schema_version")
    for field in _IDENTITY_FIELDS:
        if not isinstance(row.get(field), dict) or not row[field]:
            raise _incomplete(field)
    proof = _dict(row.get("proof"))
    for field in (
        "assertion_receipt_id",
        "oracle_receipt_id",
        "reproduction_receipt_id",
        "request_body_fingerprint",
        "request_semantics_fingerprint",
    ):
        if not _text(proof.get(field)):
            raise _incomplete(f"proof.{field}")
    if not _is_sha256(proof["request_body_fingerprint"]) or not _is_sha256(
        proof["request_semantics_fingerprint"]
    ):
        raise _incomplete("proof.request_fingerprints")
    return row


def derive_legacy_champion_canonical_identity_evidence(
    finding: dict[str, Any],
    attempt: dict[str, Any],
) -> dict[str, Any]:
    """Derive canonical identity from legacy v1 deliverables without a v2 bundle."""

    row = _dict(finding)
    signature = _dict(row.get("semantic_signature"))
    reproduction = _dict(row.get("reproduction"))
    oracle = _dict(row.get("oracle"))
    method = _text(reproduction.get("method") or row.get("method")).upper()
    path = _text(reproduction.get("path") or row.get("path"))
    if not method or not path:
        raise _incomplete("legacy.operation")
    locator = _normalized_locator(path)
    assertion_kind = _normalized_text(
        signature.get("assertion_kind")
        or oracle.get("violated_rule")
        or row.get("category")
        or "http_status_class"
    )
    if assertion_kind in {
        "server_5xx",
        "expected_status_mismatch",
        "wrong_create_status",
        "200_with_error",
        "invariant",
    }:
        assertion_kind = "http_status_class"
    source_refs = _source_ref_projection(
        row.get("source_refs")
        or signature.get("source_refs")
        or [{
            "kind": "legacy_runtime",
            "source_id": _text(row.get("source") or "legacy_champion"),
            "locator": f"{method} {locator}",
        }]
    )
    expected = (
        row.get("expected")
        or oracle.get("expected")
        or signature.get("expected_behavior")
    )
    actual = (
        row.get("actual")
        or oracle.get("actual")
        or signature.get("actual_behavior")
    )
    request_material = _canonical({
        "method": method,
        "path": path,
        "body": _dict(_dict(row.get("raw_evidence")).get("request_raw")).get("body"),
    })
    body_fp = hashlib.sha256(request_material.encode("utf-8")).hexdigest()
    semantics_fp = hashlib.sha256(
        _canonical({
            "method": method,
            "path_template": locator,
            "mutation_class": "legacy_treatment",
        }).encode("utf-8")
    ).hexdigest()
    gate = _dict(attempt.get("gate_receipt"))
    gate_receipt_id = _text(gate.get("gate_receipt_id") or gate.get("receipt_id"))
    if not gate_receipt_id:
        raise _incomplete("legacy.gate_receipt_id")
    treatment = {
        "method": method,
        "path_template": locator,
        "status_code": int(
            _dict(_dict(row.get("raw_evidence")).get("response_raw")).get("status_code")
            or _dict(reproduction.get("har_evidence")).get("status_code")
            or 0
        ),
        "mutation_class": "legacy_treatment",
        "mutation_selector": "",
        "mutation_operator": "",
    }
    identity = {
        "operation": {
            "adapter": "http",
            "verb": method,
            "operation_ref": _normalized_text(signature.get("operation_ref") or locator),
            "source_locator": locator,
        },
        "property": {
            "assertion_kind": assertion_kind,
            "expected_signature": _semantic_value(
                expected,
                assertion_kind=assertion_kind,
            ),
        },
        "actor_relation": _canonical_actor_relation(
            assertion_kind=assertion_kind,
            control_actor_class="",
            treatment_actor_class=_normalized_text(
                _dict(_dict(row.get("raw_evidence")).get("request_raw")).get("actor")
                or row.get("actor")
                or "runtime"
            ),
        ),
        "resource_identity_class": {
            "source_locators": sorted({
                item["locator"]
                for item in source_refs
                if _is_operation_locator(item["locator"])
            } | {locator}),
        },
        "mutation": {
            "class": "legacy_treatment",
            "selector": "",
            "operator": "",
        },
        "observed_outcome": {
            "assertion_kind": assertion_kind,
            "expected_signature": _semantic_value(
                expected,
                assertion_kind=assertion_kind,
            ),
            "actual_signature": _semantic_value(
                actual,
                assertion_kind=assertion_kind,
            ),
            "control_observation_class": "not_observed",
            "treatment_observation_class": _observation_class(treatment),
        },
    }
    return {
        "schema_version": CANONICAL_IDENTITY_EVIDENCE_SCHEMA,
        **identity,
        "proof": {
            "assertion_receipt_id": gate_receipt_id,
            "oracle_receipt_id": gate_receipt_id,
            "reproduction_receipt_id": gate_receipt_id,
            "request_body_fingerprint": body_fp,
            "request_semantics_fingerprint": semantics_fp,
        },
    }


def build_canonical_defect_identity(
    *,
    target_id: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    target = _text(target_id)
    if not target:
        raise _incomplete("target_id")
    row = _validated_identity_evidence(evidence)
    operation = _dict(row["operation"])
    identity = {
        "target_id": target,
        "operation": {
            "adapter": operation.get("adapter"),
            "verb": operation.get("verb"),
            "source_locator": operation.get("source_locator"),
        },
        **{
            field: row[field]
            for field in sorted(_IDENTITY_FIELDS - {"operation"})
        },
    }
    fingerprint = _fingerprint(identity)
    return {
        "canonical_defect_id": "cdef_" + fingerprint[:32],
        "identity_fingerprint": fingerprint,
        "identity": identity,
    }


def _attempt_by_finding(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in _list(ledger.get("attempts")):
        attempt = _dict(raw)
        if _text(attempt.get("terminal_status")).upper() != "DELIVERABLE":
            continue
        occurrence_id = _text(attempt.get("finding_id"))
        if not occurrence_id or occurrence_id in result:
            raise CanonicalDefectRegistryError(
                "CANONICAL_OCCURRENCE_IDENTITY_INVALID"
            )
        result[occurrence_id] = attempt
    return result


def build_canonical_defect_registry(
    *,
    mainline_run: dict[str, Any],
    deliverable_occurrences: list[dict[str, Any]],
    obligation_attempt_ledger: dict[str, Any],
) -> dict[str, Any]:
    """Group exact Gate-v2 occurrences by receipt-derived identity."""

    try:
        mainline = validate_mainline_run_contract(mainline_run)
        ledger = validate_obligation_attempt_ledger(obligation_attempt_ledger)
        occurrences = formal_customer_deliverable_findings(
            deliverable_occurrences,
            obligation_attempt_ledger=ledger,
        )
        formal_authority = build_formal_delivery_authority_receipt(
            mainline_run=mainline,
            findings=occurrences,
            obligation_attempt_ledger=ledger,
            obligation_attempt_ledger_prevalidated=ledger,
        )
    except (
        MainlineContractError,
        ObligationAttemptLedgerError,
        FormalDeliveryAuthorityError,
        TypeError,
        ValueError,
    ) as exc:
        raise CanonicalDefectRegistryError(
            f"CANONICAL_AUTHORITY_INVALID:{type(exc).__name__}:{exc}"
        ) from exc

    attempts = _attempt_by_finding(ledger)
    authority_entries = {
        _text(value.get("finding_id")): _dict(value)
        for value in _list(formal_authority.get("deliverable_attempts"))
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mappings: list[dict[str, str]] = []
    for occurrence in occurrences:
        occurrence_id = _text(
            occurrence.get("finding_id") or occurrence.get("id")
        )
        attempt = attempts.get(occurrence_id)
        authority_entry = authority_entries.get(occurrence_id)
        if attempt is None or authority_entry is None:
            raise CanonicalDefectRegistryError(
                f"CANONICAL_OCCURRENCE_AUTHORITY_MISSING:{occurrence_id}"
            )
        gate_receipt = _dict(attempt.get("gate_receipt"))
        if (
            gate_receipt.get("schema_version")
            == LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            and not _dict(attempt.get("delivery_evidence_bundle"))
        ):
            evidence = derive_legacy_champion_canonical_identity_evidence(
                occurrence,
                attempt,
            )
        else:
            try:
                evidence = derive_canonical_identity_evidence(attempt)
            except CanonicalDefectRegistryError as exc:
                # Fail fast, but make the failing attempt traceable: which
                # finding/obligation/experiment produced the incomplete
                # identity evidence, and what the raw source_refs looked like
                # so the non-projectable producer can be found.
                _dbg_bundle = _dict(attempt.get("delivery_evidence_bundle"))
                _dbg_repro = _dict(_dbg_bundle.get("reproduction_receipt"))
                _dbg_oracle = _dict(_dbg_bundle.get("oracle_receipt"))

                def _dbg_ref_shapes(rows: Any) -> str:
                    shapes: list[str] = []
                    for raw in _list(rows)[:3]:
                        row = _dict(raw)
                        shapes.append("|".join(sorted(row.keys())) or "empty")
                    return ";".join(shapes) if shapes else "none"

                _dbg_assertion_rows: list[Any] = []
                for _row in _list(_dbg_oracle.get("assertions")):
                    if _text(_dict(_row).get("status")).upper() != "VIOLATION":
                        continue
                    _dbg_assertion_rows.extend(_list(_dict(_row).get("source_refs")))
                raise CanonicalDefectRegistryError(
                    f"{exc}:occurrence={occurrence_id}"
                    f":obligation={_text(attempt.get('obligation_id'))}"
                    f":experiment={_text(attempt.get('experiment_id'))}"
                    f":assertion_ref_shapes={_dbg_ref_shapes(_dbg_assertion_rows)}"
                    f":reproduction_ref_shapes={_dbg_ref_shapes(_dbg_repro.get('source_refs'))}"
                ) from exc
        canonical = build_canonical_defect_identity(
            target_id=mainline["target_id"],
            evidence=evidence,
        )
        proof = _dict(evidence.get("proof"))
        occurrence_receipt = {
            "finding_id": occurrence_id,
            "obligation_id": _text(attempt.get("obligation_id")),
            "experiment_id": _text(attempt.get("experiment_id")),
            "execution_id": _text(attempt.get("execution_id")),
            "attempt_fingerprint": _text(attempt.get("attempt_fingerprint")),
            "gate_receipt_id": _text(authority_entry.get("gate_receipt_id")),
            "gate_output_fingerprint": _text(
                authority_entry.get("gate_output_fingerprint")
            ),
            "request_semantics_fingerprint": _text(
                proof.get("request_semantics_fingerprint")
            ),
            "assertion_receipt_id": _text(proof.get("assertion_receipt_id")),
        }
        grouped[canonical["canonical_defect_id"]].append({
            "canonical": canonical,
            "occurrence": occurrence_receipt,
        })
        mappings.append({
            "finding_id": occurrence_id,
            "canonical_defect_id": canonical["canonical_defect_id"],
        })

    canonical_defects: list[dict[str, Any]] = []
    for canonical_id in sorted(grouped):
        rows = sorted(
            grouped[canonical_id],
            key=lambda value: value["occurrence"]["finding_id"],
        )
        occurrence_receipts = [row["occurrence"] for row in rows]
        occurrence_ids = [row["finding_id"] for row in occurrence_receipts]
        canonical_defects.append({
            "canonical_defect_id": canonical_id,
            "identity_fingerprint": rows[0]["canonical"]["identity_fingerprint"],
            "identity": rows[0]["canonical"]["identity"],
            "representative_finding_id": occurrence_ids[0],
            "occurrence_count": len(occurrence_ids),
            "occurrence_finding_ids": occurrence_ids,
            "occurrence_receipts": occurrence_receipts,
        })
    mappings.sort(key=lambda value: value["finding_id"])
    occurrence_ids = sorted(
        _text(value.get("finding_id")) for value in occurrences
    )
    canonical_ids = [
        value["canonical_defect_id"] for value in canonical_defects
    ]
    payload: dict[str, Any] = {
        "schema_version": CANONICAL_DEFECT_REGISTRY_SCHEMA,
        "status": "VERIFIED",
        "authority_scope": (
            "customer"
            if mainline["customer_outputs_published"]
            else "private_evaluator"
        ),
        "run_id": mainline["run_id"],
        "campaign_id": mainline["campaign_id"],
        "target_id": mainline["target_id"],
        "environment_id": mainline["environment_id"],
        "mainline_contract_fingerprint": mainline["contract_fingerprint"],
        "attempt_ledger_fingerprint": _text(ledger.get("ledger_fingerprint")),
        "formal_delivery_authority_fingerprint": formal_authority[
            "receipt_fingerprint"
        ],
        "canonical_defect_count": len(canonical_ids),
        "delivery_occurrence_count": len(occurrence_ids),
        "canonical_defect_ids": canonical_ids,
        "delivery_occurrence_finding_ids": occurrence_ids,
        "occurrence_mappings": mappings,
        "canonical_defects": canonical_defects,
    }
    payload["registry_fingerprint"] = _fingerprint(payload)
    return payload


def validate_canonical_defect_registry(
    registry: dict[str, Any],
    *,
    mainline_run: dict[str, Any],
    deliverable_occurrences: list[dict[str, Any]],
    obligation_attempt_ledger: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(registry)
    if row.get("schema_version") != CANONICAL_DEFECT_REGISTRY_SCHEMA:
        raise CanonicalDefectRegistryError("CANONICAL_REGISTRY_SCHEMA_INVALID")
    observed = _text(row.get("registry_fingerprint"))
    expected = _fingerprint({
        key: value for key, value in row.items() if key != "registry_fingerprint"
    })
    if not observed or observed != expected:
        raise CanonicalDefectRegistryError("CANONICAL_REGISTRY_FINGERPRINT_INVALID")
    rebuilt = build_canonical_defect_registry(
        mainline_run=mainline_run,
        deliverable_occurrences=deliverable_occurrences,
        obligation_attempt_ledger=obligation_attempt_ledger,
    )
    if row != rebuilt:
        # Make the mismatch diagnosable: report which top-level keys diverge
        # and, for list payloads, where the first element-level difference is.
        diff_keys: list[str] = []
        for key in sorted(set(row.keys()) | set(rebuilt.keys())):
            if row.get(key) != rebuilt.get(key):
                diff_keys.append(key)
        detail = ",".join(diff_keys[:8]) or "deep"
        raise CanonicalDefectRegistryError(
            f"CANONICAL_REGISTRY_AUTHORITY_MISMATCH:diff={detail}"
        )
    return dict(row)


def canonical_representative_findings(
    registry: dict[str, Any],
    *,
    deliverable_occurrences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project one representative finding per canonical identity."""

    by_id = {
        _text(row.get("finding_id") or row.get("id")): row
        for row in deliverable_occurrences
        if isinstance(row, dict)
    }
    representatives: list[dict[str, Any]] = []
    for defect in _list(_dict(registry).get("canonical_defects")):
        canonical = _dict(defect)
        occurrence_id = _text(canonical.get("representative_finding_id"))
        finding = by_id.get(occurrence_id)
        if finding is None:
            raise CanonicalDefectRegistryError(
                f"CANONICAL_REPRESENTATIVE_MISSING:{occurrence_id}"
            )
        representatives.append({
            **dict(finding),
            "canonical_defect_id": _text(canonical.get("canonical_defect_id")),
            "delivery_occurrence_finding_id": occurrence_id,
            "delivery_occurrence_count": int(
                canonical.get("occurrence_count") or 0
            ),
            "delivery_occurrence_finding_ids": list(
                canonical.get("occurrence_finding_ids") or []
            ),
            "canonical_identity_fingerprint": _text(
                canonical.get("identity_fingerprint")
            ),
        })
    return representatives


def build_defect_identity_consistency(
    *,
    occurrence_scopes: dict[str, list[str]],
    canonical_scopes: dict[str, list[str]],
) -> dict[str, Any]:
    """Compare occurrence and canonical identities without mixing namespaces."""

    if not isinstance(occurrence_scopes, dict) or not occurrence_scopes:
        raise CanonicalDefectRegistryError(
            "DEFECT_IDENTITY_OCCURRENCE_SCOPES_MISSING"
        )
    if not isinstance(canonical_scopes, dict) or not canonical_scopes:
        raise CanonicalDefectRegistryError(
            "DEFECT_IDENTITY_CANONICAL_SCOPES_MISSING"
        )
    normalized_groups: dict[str, dict[str, list[str]]] = {}
    duplicates: dict[str, list[str]] = {}
    for group_name, scopes in (
        ("occurrence_scopes", occurrence_scopes),
        ("canonical_scopes", canonical_scopes),
    ):
        normalized: dict[str, list[str]] = {}
        for name, raw_ids in scopes.items():
            if not _text(name) or not isinstance(raw_ids, list):
                raise CanonicalDefectRegistryError(
                    f"DEFECT_IDENTITY_SCOPE_INVALID:{group_name}:{name}"
                )
            values = [_text(value) for value in raw_ids]
            if not all(values):
                raise CanonicalDefectRegistryError(
                    f"DEFECT_IDENTITY_SCOPE_EMPTY_ID:{group_name}:{name}"
                )
            duplicate_ids = sorted({
                value for value in values if values.count(value) > 1
            })
            if duplicate_ids:
                duplicates[f"{group_name}.{name}"] = duplicate_ids
            normalized[_text(name)] = sorted(set(values))
        normalized_groups[group_name] = dict(sorted(normalized.items()))
    occurrence_sets = {
        tuple(values)
        for values in normalized_groups["occurrence_scopes"].values()
    }
    canonical_sets = {
        tuple(values)
        for values in normalized_groups["canonical_scopes"].values()
    }
    consistent = (
        len(occurrence_sets) == 1
        and len(canonical_sets) == 1
        and not duplicates
    )
    return {
        "schema_version": DEFECT_IDENTITY_CONSISTENCY_SCHEMA,
        "consistent": consistent,
        "status": "OK" if consistent else "PIPELINE_DEGRADED_IDENTITY_MISMATCH",
        **normalized_groups,
        "duplicate_ids": duplicates,
    }


def validate_defect_identity_consistency(
    value: dict[str, Any],
    *,
    required_occurrence_scopes: set[str],
    required_canonical_scopes: set[str],
    allowed_occurrence_scopes: set[str] | None = None,
    allowed_canonical_scopes: set[str] | None = None,
) -> dict[str, Any]:
    row = _dict(value)
    if set(row) != {
        "schema_version",
        "consistent",
        "status",
        "occurrence_scopes",
        "canonical_scopes",
        "duplicate_ids",
    }:
        raise CanonicalDefectRegistryError(
            "DEFECT_IDENTITY_CONSISTENCY_FIELDS_INVALID"
        )
    if row.get("schema_version") != DEFECT_IDENTITY_CONSISTENCY_SCHEMA:
        raise CanonicalDefectRegistryError(
            "DEFECT_IDENTITY_CONSISTENCY_SCHEMA_INVALID"
        )
    rebuilt = build_defect_identity_consistency(
        occurrence_scopes=_dict(row.get("occurrence_scopes")),
        canonical_scopes=_dict(row.get("canonical_scopes")),
    )
    if rebuilt != row or row.get("consistent") is not True:
        raise CanonicalDefectRegistryError(
            "DEFECT_IDENTITY_CONSISTENCY_MISMATCH"
        )
    observed_occurrence = set(_dict(row.get("occurrence_scopes")))
    observed_canonical = set(_dict(row.get("canonical_scopes")))
    if not required_occurrence_scopes.issubset(observed_occurrence):
        raise CanonicalDefectRegistryError(
            "DEFECT_IDENTITY_REQUIRED_OCCURRENCE_SCOPE_MISSING"
        )
    if not required_canonical_scopes.issubset(observed_canonical):
        raise CanonicalDefectRegistryError(
            "DEFECT_IDENTITY_REQUIRED_CANONICAL_SCOPE_MISSING"
        )
    if allowed_occurrence_scopes is not None and not observed_occurrence.issubset(
        allowed_occurrence_scopes
    ):
        raise CanonicalDefectRegistryError(
            "DEFECT_IDENTITY_OCCURRENCE_SCOPE_UNSUPPORTED"
        )
    if allowed_canonical_scopes is not None and not observed_canonical.issubset(
        allowed_canonical_scopes
    ):
        raise CanonicalDefectRegistryError(
            "DEFECT_IDENTITY_CANONICAL_SCOPE_UNSUPPORTED"
        )
    return dict(row)
