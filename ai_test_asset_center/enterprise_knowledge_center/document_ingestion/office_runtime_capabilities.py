"""Runtime capability reporting for native and compatible Office ingestion.

This module does not add another document parser. It inspects and wraps the compatible
Office adapter already used by the ingestion pipeline, exposing deployment readiness and
binding every successful conversion to a source-specific runtime receipt. Recognizing a
suffix is never reported as a verified conversion until that immutable source succeeds.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from .compatible_office_adapter import (
    CompatibleOfficeDocumentAdapter,
    LibreOfficeContainerNormalizer,
    OfficeContainerNormalizer,
    _COMPATIBLE_SUFFIXES,
    _PRESENTATION_SUFFIXES,
    _SPREADSHEET_SUFFIXES,
    _WORD_SUFFIXES,
    _capabilities_for_target,
    _target_suffix,
)
from .contract import AdapterMatch, DocumentSource

OFFICE_RUNTIME_CAPABILITY_SCHEMA = "qualibug.office-runtime-capability-report.v1"
OFFICE_SOURCE_RUNTIME_PROBE_SCHEMA = "qualibug.office-source-runtime-probe.v1"


def _family(source_suffix: str) -> str:
    suffix = str(source_suffix or "").lower()
    if suffix in _WORD_SUFFIXES:
        return "word"
    if suffix in _SPREADSHEET_SUFFIXES:
        return "spreadsheet"
    if suffix in _PRESENTATION_SUFFIXES:
        return "presentation"
    return "unknown"


def _normalizer_available(normalizer: OfficeContainerNormalizer) -> bool:
    try:
        return bool(normalizer.available())
    except Exception:
        return False


def _normalizer_binary(normalizer: OfficeContainerNormalizer) -> str:
    binary = getattr(normalizer, "_binary", None)
    if callable(binary):
        try:
            return str(binary() or "")
        except Exception:
            return ""
    return ""


def _normalizer_version(binary: str) -> str:
    if not binary:
        return ""
    try:
        completed = subprocess.run(
            [binary, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
            check=False,
        )
    except Exception:
        return ""
    value = (completed.stdout or completed.stderr or b"").decode("utf-8", errors="replace")
    return value.strip()[:300]


def build_compatible_office_runtime_report(
    normalizer: OfficeContainerNormalizer | None = None,
) -> dict[str, Any]:
    """Describe what this deployment can attempt without claiming sample verification."""

    resolved = normalizer or LibreOfficeContainerNormalizer()
    dependency_available = _normalizer_available(resolved)
    binary = _normalizer_binary(resolved)
    version = _normalizer_version(binary) if dependency_available else ""
    rows: list[dict[str, Any]] = []
    for suffix in sorted(_COMPATIBLE_SUFFIXES):
        target = _target_suffix(suffix)
        rows.append(
            {
                "source_suffix": suffix,
                "source_family": _family(suffix),
                "normalized_target_format": target.lstrip("."),
                "adapter_name": CompatibleOfficeDocumentAdapter.name,
                "normalizer_name": str(getattr(resolved, "name", "") or ""),
                "normalizer_version": str(getattr(resolved, "version", "") or ""),
                "runtime_dependency_available": dependency_available,
                "runtime_status": (
                    "READY_FOR_SOURCE_CONVERSION"
                    if dependency_available
                    else "BLOCKED_RUNTIME_DEPENDENCY_UNAVAILABLE"
                ),
                "declared_capabilities": sorted(_capabilities_for_target(target)),
                "format_conversion_verified_with_real_source": False,
                "verification_requirement": "verify_on_first_real_source_or_corpus_fixture",
                "original_source_remains_evidence_root": True,
                "derived_container_is_evidence_root": False,
                "embedded_automation_semantics_interpreted": False,
                "network_isolation_enforced": False,
                "external_resource_resolution_isolation_verified": False,
            }
        )
    ready_count = sum(1 for row in rows if row["runtime_dependency_available"])
    return {
        "schema": OFFICE_RUNTIME_CAPABILITY_SCHEMA,
        "status": "READY" if ready_count == len(rows) else "BLOCKED_DEPENDENCY_UNAVAILABLE",
        "normalizer_name": str(getattr(resolved, "name", "") or ""),
        "normalizer_version": str(getattr(resolved, "version", "") or ""),
        "normalizer_binary": binary,
        "normalizer_binary_version": version,
        "runtime_dependency_available": dependency_available,
        "recognized_format_count": len(rows),
        "ready_for_conversion_count": ready_count,
        "real_source_verified_format_count": 0,
        "formats": rows,
        "recognition_is_not_conversion_verification": True,
        "network_isolation_enforced": False,
        "business_semantics_added": False,
    }


def probe_compatible_office_source_runtime(
    source: DocumentSource,
    normalizer: OfficeContainerNormalizer | None = None,
) -> dict[str, Any]:
    """Return a cheap, non-converting readiness probe for one immutable source."""

    resolved = normalizer or LibreOfficeContainerNormalizer()
    suffix = source.suffix
    target = _target_suffix(suffix)
    recognized = bool(target)
    dependency_available = _normalizer_available(resolved)
    if not recognized:
        status = "UNSUPPORTED_SOURCE_SUFFIX"
    elif not dependency_available:
        status = "BLOCKED_RUNTIME_DEPENDENCY_UNAVAILABLE"
    else:
        status = "READY_FOR_SOURCE_CONVERSION"
    return {
        "schema": OFFICE_SOURCE_RUNTIME_PROBE_SCHEMA,
        "status": status,
        "source_id": source.source_id,
        "source_filename": source.filename,
        "source_hash": source.content_hash,
        "source_suffix": suffix,
        "source_family": _family(suffix),
        "recognized_compatible_office_format": recognized,
        "normalized_target_format": target.lstrip("."),
        "runtime_dependency_available": dependency_available,
        "normalizer_name": str(getattr(resolved, "name", "") or ""),
        "normalizer_version": str(getattr(resolved, "version", "") or ""),
        "declared_capabilities": sorted(_capabilities_for_target(target)),
        "format_conversion_verified_with_this_source": False,
        "conversion_must_succeed_before_activation": True,
        "original_source_remains_evidence_root": True,
        "network_isolation_enforced": False,
        "business_semantics_added": False,
    }


class RuntimeAwareCompatibleOfficeDocumentAdapter(CompatibleOfficeDocumentAdapter):
    """Enrich compatible Office receipts while using the shared runtime-ready contract."""

    parser_version = "3"

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        match = super().probe(source)
        if match is None:
            return None
        runtime = probe_compatible_office_source_runtime(source, self.normalizer)
        runtime_ready = bool(runtime["runtime_dependency_available"])
        return AdapterMatch(
            adapter_name=match.adapter_name,
            score=match.score,
            reason=match.reason,
            capabilities=match.capabilities,
            mode=match.mode,
            runtime_ready=runtime_ready,
            runtime_reason=("" if runtime_ready else "RUNTIME_DEPENDENCY_UNAVAILABLE"),
        )

    def receipt(self, source: DocumentSource, match: AdapterMatch) -> dict[str, Any]:
        receipt = super().receipt(source, match)
        runtime = probe_compatible_office_source_runtime(source, self.normalizer)
        receipt["runtime_source_probe"] = runtime
        receipt["runtime_dependency_available"] = runtime["runtime_dependency_available"]
        receipt["format_conversion_verified_with_this_source"] = False
        receipt["recognition_is_not_conversion_verification"] = True
        return receipt

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        runtime = probe_compatible_office_source_runtime(source, self.normalizer)
        if runtime["status"] != "READY_FOR_SOURCE_CONVERSION":
            raise RuntimeError(
                "compatible Office normalization is unavailable: " + str(runtime["status"])
            )
        result = super().extract(source)
        normalization_receipt = dict(result.get("office_normalization_receipt") or {})
        normalization_receipt.update(
            {
                "runtime_source_probe_before_conversion": runtime,
                "source_conversion_succeeded": True,
                "format_conversion_verified_with_this_source": True,
                "network_access_used": "UNKNOWN",
                "network_isolation_enforced": False,
                "external_resource_resolution_isolation_verified": False,
            }
        )
        result["office_normalization_receipt"] = normalization_receipt
        structure_receipt = dict(result.get("structure_receipt") or {})
        structure_receipt["normalization_receipt"] = normalization_receipt
        structure_receipt["runtime_dependency_available"] = True
        structure_receipt["format_conversion_verified_with_this_source"] = True
        result["structure_receipt"] = structure_receipt
        return result


def main() -> int:
    print(json.dumps(build_compatible_office_runtime_report(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "OFFICE_RUNTIME_CAPABILITY_SCHEMA",
    "OFFICE_SOURCE_RUNTIME_PROBE_SCHEMA",
    "RuntimeAwareCompatibleOfficeDocumentAdapter",
    "build_compatible_office_runtime_report",
    "probe_compatible_office_source_runtime",
]
