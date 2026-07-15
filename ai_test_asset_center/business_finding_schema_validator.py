"""Business Finding Schema Validator — validates every finding against the Evidence Contract.

Only VALIDATED_CANDIDATE can proceed to Human Review.
Missing critical fields → NEEDS_MORE_EVIDENCE or SCHEMA_INVALID.
Sensitive fields are blocked.
"""
from __future__ import annotations
import json as _json
import re as _re
from pathlib import Path
from typing import Any

_SCHEMA_PATH = Path(__file__).parent / "business_finding_schema.json"

_schema: dict[str, Any] | None = None


def _load_schema() -> dict[str, Any]:
    global _schema
    if _schema is None:
        _schema = _json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return _schema


def validate_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Validate a single finding against the Business Finding Evidence Contract.

    Returns:
        {
            "valid": bool,
            "verdict": str,  # VALIDATED_CANDIDATE | SCHEMA_INVALID | NEEDS_MORE_EVIDENCE
            "errors": [str, ...],
            "warnings": [str, ...],
        }
    """
    schema = _load_schema()
    required_fields = schema.get("required", [])
    properties = schema.get("properties", {})
    sensitive_patterns = schema.get("sensitive_field_patterns", [])
    errors: list[str] = []
    warnings: list[str] = []

    # --- Structural checks ---
    if not isinstance(finding, dict):
        return {"valid": False, "verdict": "SCHEMA_INVALID", "errors": ["Finding must be a dict"], "warnings": []}

    # --- Additional properties check ---
    allowed_keys = set(properties.keys())
    for key in finding:
        if key not in allowed_keys:
            errors.append(f"Unknown field: '{key}' — not in schema")

    # --- Required fields ---
    for field in required_fields:
        if field not in finding or finding[field] is None:
            errors.append(f"Missing required field: '{field}'")

    # --- Verdict enum ---
    verdict_schema = properties.get("verdict", {})
    allowed_verdicts = verdict_schema.get("enum", [])
    verdict = finding.get("verdict", "")
    if verdict and verdict not in allowed_verdicts:
        errors.append(f"Invalid verdict '{verdict}'. Allowed: {allowed_verdicts}")

    # --- Entity Binding completeness ---
    entity = finding.get("entity_binding", {})
    if isinstance(entity, dict):
        entity_required = properties.get("entity_binding", {}).get("required", [])
        for field in entity_required:
            if not entity.get(field):
                errors.append(f"entity_binding missing '{field}'")
        binding_conf = entity.get("binding_confidence", 0)
        if binding_conf is not None and (not isinstance(binding_conf, (int, float)) or binding_conf < 0 or binding_conf > 1):
            errors.append("entity_binding.binding_confidence must be 0-1")

    # --- Invariant completeness ---
    invariant = finding.get("violated_invariant", {})
    if isinstance(invariant, dict):
        inv_required = properties.get("violated_invariant", {}).get("required", [])
        for field in inv_required:
            if not invariant.get(field):
                errors.append(f"violated_invariant missing '{field}'")

    # --- Reproduction completeness ---
    repro = finding.get("reproduction", {})
    if isinstance(repro, dict):
        repro_required = properties.get("reproduction", {}).get("required", [])
        for field in repro_required:
            if not repro.get(field):
                errors.append(f"reproduction missing '{field}'")

    # --- Cleanup completeness ---
    cleanup = finding.get("cleanup", {})
    if isinstance(cleanup, dict):
        cleanup_required = properties.get("cleanup", {}).get("required", [])
        for field in cleanup_required:
            if not cleanup.get(field):
                errors.append(f"cleanup missing '{field}'")
        cleanup_status = cleanup.get("status", "")
        if cleanup_status and cleanup_status not in properties.get("cleanup", {}).get("properties", {}).get("status", {}).get("enum", []):
            errors.append(f"cleanup.status '{cleanup_status}' not in allowed enum")

    # --- Adversarial validation completeness ---
    adv = finding.get("adversarial_validation", {})
    if isinstance(adv, dict):
        adv_required = properties.get("adversarial_validation", {}).get("required", [])
        for field in adv_required:
            if not adv.get(field) and adv.get(field) != []:
                errors.append(f"adversarial_validation missing '{field}'")

    # --- Before / After / Action references ---
    for ref_field in ["before_snapshot_ref", "action_evidence_ref", "after_snapshot_ref"]:
        val = finding.get(ref_field, "")
        if not val or not isinstance(val, str) or not val.strip():
            errors.append(f"'{ref_field}' must be a non-empty string")

    # --- Sensitive field scan ---
    _scan_sensitive(finding, "", sensitive_patterns, errors)

    # --- Determine verdict ---
    if errors:
        # Check if critical evidence is just missing (can be fixed) vs schema-violating
        critical_gaps = [e for e in errors if "Missing required field" in e or "missing" in e.lower()]
        if critical_gaps:
            return {"valid": False, "verdict": "NEEDS_MORE_EVIDENCE", "errors": errors, "warnings": warnings}
        return {"valid": False, "verdict": "SCHEMA_INVALID", "errors": errors, "warnings": warnings}

    # Only VALIDATED_CANDIDATE if verdict field itself says so
    if verdict == "VALIDATED_CANDIDATE":
        return {"valid": True, "verdict": "VALIDATED_CANDIDATE", "errors": [], "warnings": warnings}

    return {"valid": True, "verdict": verdict or "NEEDS_MORE_EVIDENCE", "errors": [], "warnings": warnings}


def _scan_sensitive(obj: Any, path: str, patterns: list[str], errors: list[str]) -> None:
    """Recursively reject credentials without rejecting legitimate auth findings.

    Earlier logic treated a long sentence containing the word ``authorization``
    as a secret.  That blocked the very permission/authorization defects this
    product must discover.  Keys remain strict; values require a credential-like
    shape such as a bearer token, sk-* token, JWT, PEM block or password value.
    """
    value_patterns = (
        _re.compile(r"\bbearer\s+[A-Za-z0-9._~+\-/=]{8,}", _re.I),
        _re.compile(r"\bsk-[A-Za-z0-9]{12,}\b", _re.I),
        _re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
        _re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", _re.I),
        _re.compile(r"(?:password|secret|api[_-]?key)\s*[:=]\s*[^\s]{6,}", _re.I),
    )
    # A Business Finding is customer-shareable evidence.  It may describe an
    # authentication defect, but must not embed credential-shaped labels in free
    # text because downstream exports cannot reliably distinguish a harmless
    # mention from a copied secret.  Use a redacted label (for example
    # ``[REDACTED_CREDENTIAL]``) and keep the actual value outside the finding.
    sensitive_mentions = _re.compile(
        r"\b(?:api[_-]?key|access[_-]?key|password|secret|token|jwt|cookie|bearer)\b",
        _re.I,
    )
    if isinstance(obj, dict):
        for key, val in obj.items():
            key_lower = str(key).lower()
            if any(pat in key_lower for pat in patterns):
                errors.append(f"Sensitive key '{path}.{key}' found (redact before registration)")
            _scan_sensitive(val, f"{path}.{key}" if path else str(key), patterns, errors)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_sensitive(item, f"{path}[{i}]", patterns, errors)
    elif isinstance(obj, str):
        if any(pattern.search(obj) for pattern in value_patterns):
            errors.append(f"Potential credential value at '{path}'")
        elif sensitive_mentions.search(obj):
            errors.append(
                f"Sensitive credential reference at '{path}' — redact the credential class/value before registration"
            )


def validate_batch(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate a batch of findings. Returns aggregate results."""
    results = []
    for f in findings:
        results.append(validate_finding(f))
    valid = [r for r in results if r["valid"]]
    invalid = [r for r in results if not r["valid"]]
    return {
        "total": len(results),
        "validated_candidates": len(valid),
        "rejected_or_incomplete": len(invalid),
        "results": results,
    }


# CLI entry point for testing
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python business_finding_schema_validator.py <finding.json>")
        print("       python business_finding_schema_validator.py --batch <findings_array.json>")
        sys.exit(1)
    if sys.argv[1] == "--batch":
        path = Path(sys.argv[2])
        data = _json.loads(path.read_text(encoding="utf-8"))
        findings = data if isinstance(data, list) else data.get("findings", [])
        result = validate_batch(findings)
        print(_json.dumps(result, indent=2, ensure_ascii=False))
    else:
        path = Path(sys.argv[1])
        finding = _json.loads(path.read_text(encoding="utf-8"))
        result = validate_finding(finding)
        print(_json.dumps(result, indent=2, ensure_ascii=False))
