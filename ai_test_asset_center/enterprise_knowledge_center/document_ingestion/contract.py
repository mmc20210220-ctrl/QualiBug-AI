"""Contracts for format-agnostic enterprise document ingestion.

Adapters interpret source containers and emit source-preserving Document IR.  They
must not create business facts, infer business flow from document order, or hide
unsupported content.  Higher layers consume the same IR regardless of source format.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Iterable

DOCUMENT_ADAPTER_RECEIPT_SCHEMA = "qualibug.document-adapter-receipt.v1"
DOCUMENT_PARSING_PLAN_SCHEMA = "qualibug.document-parsing-plan.v1"
DOCUMENT_IR_MERGE_RECEIPT_SCHEMA = "qualibug.document-ir-merge-receipt.v1"

CAP_TEXT_EXTRACTION = "TEXT_EXTRACTION"
CAP_PAGE_LAYOUT = "PAGE_LAYOUT"
CAP_TEXT_COORDINATES = "TEXT_COORDINATES"
CAP_FONT_EVIDENCE = "FONT_EVIDENCE"
CAP_HEADING_HIERARCHY = "HEADING_HIERARCHY"
CAP_LIST_HIERARCHY = "LIST_HIERARCHY"
CAP_TABLE_STRUCTURE = "TABLE_STRUCTURE"
CAP_TABLE_REGION_DETECTION = "TABLE_REGION_DETECTION"
CAP_IMAGE_PRESENCE = "IMAGE_PRESENCE"
CAP_HEADER_FOOTER = "HEADER_FOOTER"
CAP_FORMULA_EXTRACTION = "FORMULA_EXTRACTION"
CAP_COMMENT_EXTRACTION = "COMMENT_EXTRACTION"
CAP_REVISION_EXTRACTION = "REVISION_EXTRACTION"
CAP_ATTACHMENT_EXTRACTION = "ATTACHMENT_EXTRACTION"
CAP_OCR = "OCR"
CAP_DIAGRAM_STRUCTURE = "DIAGRAM_STRUCTURE"
CAP_STYLE_SEMANTICS = "STYLE_SEMANTICS"

KNOWN_CAPABILITIES = frozenset(
    {
        CAP_TEXT_EXTRACTION,
        CAP_PAGE_LAYOUT,
        CAP_TEXT_COORDINATES,
        CAP_FONT_EVIDENCE,
        CAP_HEADING_HIERARCHY,
        CAP_LIST_HIERARCHY,
        CAP_TABLE_STRUCTURE,
        CAP_TABLE_REGION_DETECTION,
        CAP_IMAGE_PRESENCE,
        CAP_HEADER_FOOTER,
        CAP_FORMULA_EXTRACTION,
        CAP_COMMENT_EXTRACTION,
        CAP_REVISION_EXTRACTION,
        CAP_ATTACHMENT_EXTRACTION,
        CAP_OCR,
        CAP_DIAGRAM_STRUCTURE,
        CAP_STYLE_SEMANTICS,
    }
)

MODE_PRIMARY = "PRIMARY"
MODE_SUPPLEMENTAL = "SUPPLEMENTAL"
MODE_FALLBACK = "FALLBACK"
ADAPTER_MODES = {MODE_PRIMARY, MODE_SUPPLEMENTAL, MODE_FALLBACK}


def text(value: Any) -> str:
    return str(value or "").strip()


def unique_text(values: Iterable[Any]) -> list[str]:
    return sorted({text(value) for value in values if text(value)})


@dataclass(frozen=True)
class DocumentSource:
    source_id: str
    filename: str
    data: bytes
    declared_mime: str = ""
    legacy_text: str = ""

    @property
    def suffix(self) -> str:
        return Path(self.filename).suffix.lower()

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def signature_hex(self) -> str:
        return self.data[:16].hex()


@dataclass(frozen=True)
class AdapterMatch:
    adapter_name: str
    score: int
    reason: str
    capabilities: tuple[str, ...]
    mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "score": int(self.score),
            "reason": self.reason,
            "capabilities": list(self.capabilities),
            "mode": self.mode,
        }


@dataclass(frozen=True)
class SupplementalContext:
    """Evidence passed to a deferred supplemental adapter.

    Supplemental adapters may inspect structural gaps produced by the primary adapter,
    but they still may not infer business meaning.  ``primary_document_ir`` is immutable
    by contract: supplemental output is merged later by the central merger.
    """

    primary_document_ir: dict[str, Any]
    trigger_gaps: tuple[dict[str, Any], ...] = ()
    requested_capabilities: tuple[str, ...] = ()


class DocumentAdapter:
    """Base adapter contract.

    Subclasses may inspect container bytes and source metadata, but extract() must
    return Document IR only.  Business meaning belongs to the fact-ledger stage.
    """

    name: ClassVar[str] = "document-adapter"
    parser_version: ClassVar[str] = "1"
    priority: ClassVar[int] = 0
    mode: ClassVar[str] = MODE_PRIMARY
    capabilities: ClassVar[frozenset[str]] = frozenset()
    standalone: ClassVar[bool] = False

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        raise NotImplementedError

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        raise NotImplementedError

    def probe_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> AdapterMatch | None:
        """Return a deferred match after primary structure gaps are known."""
        return None

    def extract_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> dict[str, Any]:
        """Produce supplemental IR without mutating the primary result."""
        return self.extract(source)

    def receipt(self, source: DocumentSource, match: AdapterMatch) -> dict[str, Any]:
        return {
            "schema": DOCUMENT_ADAPTER_RECEIPT_SCHEMA,
            "adapter_name": self.name,
            "parser_version": self.parser_version,
            "mode": self.mode,
            "priority": int(self.priority),
            "standalone": bool(self.standalone),
            "match_score": int(match.score),
            "match_reason": match.reason,
            "capabilities": unique_text(self.capabilities),
            "source_id": source.source_id,
            "filename": source.filename,
            "source_hash": source.content_hash,
            "business_semantics_added": False,
            "document_order_is_business_flow": False,
            "filename_is_business_context": False,
        }


def validate_adapter(adapter: DocumentAdapter) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if not text(getattr(adapter, "name", "")):
        violations.append({"code": "ADAPTER_NAME_MISSING"})
    if text(getattr(adapter, "mode", "")) not in ADAPTER_MODES:
        violations.append({"code": "ADAPTER_MODE_INVALID", "value": getattr(adapter, "mode", None)})
    capabilities = set(getattr(adapter, "capabilities", frozenset()) or ())
    unknown = sorted(capabilities - KNOWN_CAPABILITIES)
    if unknown:
        violations.append({"code": "ADAPTER_CAPABILITY_UNKNOWN", "values": unknown})
    return violations


def validate_document_ir(document_ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the stable cross-format shape without repairing semantic gaps."""
    violations: list[dict[str, Any]] = []
    if not isinstance(document_ir, dict):
        return [{"code": "DOCUMENT_IR_NOT_OBJECT"}]
    if not text(document_ir.get("schema")):
        violations.append({"code": "DOCUMENT_IR_SCHEMA_MISSING"})
    if not text(document_ir.get("format")):
        violations.append({"code": "DOCUMENT_IR_FORMAT_MISSING"})
    blocks = document_ir.get("blocks")
    if not isinstance(blocks, list):
        violations.append({"code": "DOCUMENT_IR_BLOCKS_INVALID"})
        return violations
    seen: set[str] = set()
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            violations.append({"code": "DOCUMENT_IR_BLOCK_INVALID", "index": index})
            continue
        block_id = text(block.get("block_id"))
        if not block_id:
            violations.append({"code": "DOCUMENT_IR_BLOCK_ID_MISSING", "index": index})
        elif block_id in seen:
            violations.append({"code": "DOCUMENT_IR_BLOCK_ID_DUPLICATE", "block_id": block_id})
        else:
            seen.add(block_id)
        if not text(block.get("type")):
            violations.append({"code": "DOCUMENT_IR_BLOCK_TYPE_MISSING", "index": index})
        if not text(block.get("source_locator")):
            violations.append({"code": "DOCUMENT_IR_BLOCK_SOURCE_LOCATOR_MISSING", "index": index})
    receipt = document_ir.get("structure_receipt")
    if not isinstance(receipt, dict):
        violations.append({"code": "DOCUMENT_IR_STRUCTURE_RECEIPT_MISSING"})
    return violations


__all__ = [
    "DOCUMENT_ADAPTER_RECEIPT_SCHEMA",
    "DOCUMENT_PARSING_PLAN_SCHEMA",
    "DOCUMENT_IR_MERGE_RECEIPT_SCHEMA",
    "KNOWN_CAPABILITIES",
    "MODE_PRIMARY",
    "MODE_SUPPLEMENTAL",
    "MODE_FALLBACK",
    "DocumentSource",
    "AdapterMatch",
    "SupplementalContext",
    "DocumentAdapter",
    "validate_adapter",
    "validate_document_ir",
    "text",
    "unique_text",
    "CAP_TEXT_EXTRACTION",
    "CAP_PAGE_LAYOUT",
    "CAP_TEXT_COORDINATES",
    "CAP_FONT_EVIDENCE",
    "CAP_HEADING_HIERARCHY",
    "CAP_LIST_HIERARCHY",
    "CAP_TABLE_STRUCTURE",
    "CAP_TABLE_REGION_DETECTION",
    "CAP_IMAGE_PRESENCE",
    "CAP_HEADER_FOOTER",
    "CAP_FORMULA_EXTRACTION",
    "CAP_COMMENT_EXTRACTION",
    "CAP_REVISION_EXTRACTION",
    "CAP_ATTACHMENT_EXTRACTION",
    "CAP_OCR",
    "CAP_DIAGRAM_STRUCTURE",
    "CAP_STYLE_SEMANTICS",
]