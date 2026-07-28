"""Fail-visible OCR supplemental adapter for scanned pages and raster images.

The adapter is capability-driven rather than PDF-specific.  It can run standalone for
raster image sources and can run after a primary PDF adapter when that adapter reports
``SCANNED_PAGE_REQUIRES_OCR``.  OCR output remains structural evidence only; it never
creates business facts directly and never hides pages that could not be recovered.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
from collections import Counter, defaultdict
from typing import Any, Protocol

from .._document_structure_ir import DOCUMENT_IR_SCHEMA, STRUCTURE_RECEIPT_SCHEMA
from .contract import (
    AdapterMatch,
    CAP_OCR,
    CAP_PAGE_LAYOUT,
    CAP_TEXT_COORDINATES,
    CAP_TEXT_EXTRACTION,
    DocumentAdapter,
    DocumentSource,
    MODE_SUPPLEMENTAL,
    SupplementalContext,
    text,
)


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"II*\x00",
    b"MM\x00*",
    b"BM",
    b"RIFF",
)


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _looks_like_image(source: DocumentSource) -> bool:
    if source.suffix in _IMAGE_SUFFIXES:
        return True
    data = source.data[:16]
    if any(data.startswith(signature) for signature in _IMAGE_SIGNATURES[:-1]):
        return True
    return data.startswith(b"RIFF") and b"WEBP" in source.data[:16]


class OcrProvider(Protocol):
    name: str
    version: str

    def available(self) -> bool:
        ...

    def recognize(
        self,
        image_bytes: bytes,
        *,
        source_id: str,
        filename: str,
        page: int,
        image_index: int,
    ) -> list[dict[str, Any]]:
        """Return line-level OCR regions with text, bbox and confidence."""
        ...


class TesseractOcrProvider:
    """Optional local Tesseract provider.

    The provider is considered unavailable unless both the Python wrapper and the
    ``tesseract`` executable are present.  This keeps the default registry safe on
    installations that do not provision OCR dependencies.
    """

    name = "tesseract"
    version = "1"

    def __init__(self, language: str | None = None) -> None:
        self.language = text(language or os.getenv("QUALIBUG_OCR_LANG") or "chi_sim+eng")

    def available(self) -> bool:
        if not shutil.which("tesseract"):
            return False
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        return True

    def recognize(
        self,
        image_bytes: bytes,
        *,
        source_id: str,
        filename: str,
        page: int,
        image_index: int,
    ) -> list[dict[str, Any]]:
        if not self.available():
            raise RuntimeError("Tesseract OCR provider is unavailable")
        import pytesseract
        from PIL import Image
        from pytesseract import Output

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        width, height = image.size
        data = pytesseract.image_to_data(
            image,
            lang=self.language,
            output_type=Output.DICT,
            config="--psm 6",
        )
        grouped: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
        count = len(data.get("text") or [])
        for index in range(count):
            value = text((data.get("text") or [""])[index])
            if not value:
                continue
            try:
                confidence = float((data.get("conf") or ["-1"])[index])
            except (TypeError, ValueError):
                confidence = -1.0
            if confidence < 0:
                continue
            key = (
                int((data.get("block_num") or [0])[index]),
                int((data.get("par_num") or [0])[index]),
                int((data.get("line_num") or [0])[index]),
                int((data.get("page_num") or [1])[index]),
            )
            left = int((data.get("left") or [0])[index])
            top = int((data.get("top") or [0])[index])
            word_width = int((data.get("width") or [0])[index])
            word_height = int((data.get("height") or [0])[index])
            grouped[key].append(
                {
                    "text": value,
                    "confidence": confidence / 100.0,
                    "bbox": [left, top, left + word_width, top + word_height],
                }
            )

        rows: list[dict[str, Any]] = []
        for line_index, words in enumerate(grouped.values(), start=1):
            words.sort(key=lambda row: (int(row["bbox"][1]), int(row["bbox"][0])))
            line_text = " ".join(text(row.get("text")) for row in words if text(row.get("text")))
            if not line_text:
                continue
            boxes = [row["bbox"] for row in words]
            bbox = [
                min(int(row[0]) for row in boxes),
                min(int(row[1]) for row in boxes),
                max(int(row[2]) for row in boxes),
                max(int(row[3]) for row in boxes),
            ]
            confidence = sum(float(row.get("confidence") or 0.0) for row in words) / len(words)
            rows.append(
                {
                    "text": line_text,
                    "bbox": bbox,
                    "confidence": round(confidence, 4),
                    "image_width_px": width,
                    "image_height_px": height,
                    "line_index": line_index,
                }
            )
        return rows


class OcrSupplementalAdapter(DocumentAdapter):
    name = "ocr-visual-text"
    parser_version = "1"
    priority = 85
    mode = MODE_SUPPLEMENTAL
    standalone = True
    capabilities = frozenset(
        {
            CAP_OCR,
            CAP_TEXT_EXTRACTION,
            CAP_TEXT_COORDINATES,
            CAP_PAGE_LAYOUT,
        }
    )

    def __init__(self, provider: OcrProvider | None = None, minimum_confidence: float = 0.55) -> None:
        self.provider = provider or TesseractOcrProvider()
        self.minimum_confidence = max(0.0, min(1.0, float(minimum_confidence)))

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        if not self.provider.available() or not _looks_like_image(source):
            return None
        return AdapterMatch(
            self.name,
            110,
            "raster_image_requires_visual_text_recovery",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def probe_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> AdapterMatch | None:
        if not self.provider.available():
            return None
        scanned_pages = sorted(
            {
                int(page)
                for gap in context.trigger_gaps
                if text(gap.get("reason_code") or gap.get("kind")) == "SCANNED_PAGE_REQUIRES_OCR"
                for page in _list(gap.get("pages"))
                if str(page).isdigit()
            }
        )
        if not scanned_pages:
            return None
        return AdapterMatch(
            self.name,
            118,
            "primary_adapter_reported_scanned_pages:" + ",".join(str(page) for page in scanned_pages),
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def _blocks_from_regions(
        self,
        source: DocumentSource,
        regions: list[dict[str, Any]],
        *,
        page: int,
        image_index: int,
    ) -> tuple[list[dict[str, Any]], float]:
        blocks: list[dict[str, Any]] = []
        confidences: list[float] = []
        for order, region in enumerate(regions, start=1):
            value = text(region.get("text"))
            if not value:
                continue
            confidence = float(region.get("confidence") or 0.0)
            confidences.append(confidence)
            bbox = list(region.get("bbox") or [])
            blocks.append(
                {
                    "block_id": _stable_id(
                        "ocr_text_block",
                        source.source_id,
                        page,
                        image_index,
                        order,
                        value,
                    ),
                    "type": "PARAGRAPH",
                    "parent_id": "",
                    "page": page,
                    "order": order,
                    "region": "body",
                    "text": value,
                    "bbox": bbox,
                    "source_locator": (
                        f"{source.filename or 'image'}#page={page};"
                        f"embedded_image={image_index};ocr_line={order};"
                        f"bbox={','.join(str(item) for item in bbox)}"
                    ),
                    "structure_evidence": {
                        "method": "ocr_line_recognition",
                        "provider": self.provider.name,
                        "provider_version": self.provider.version,
                        "confidence": round(confidence, 4),
                        "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_EMBEDDED_IMAGE",
                        "image_width_px": region.get("image_width_px"),
                        "image_height_px": region.get("image_height_px"),
                        "business_semantics_added": False,
                    },
                }
            )
        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return blocks, round(mean_confidence, 4)

    def _build_ir(
        self,
        source: DocumentSource,
        page_images: list[tuple[int, int, bytes]],
        *,
        target_pages: list[int],
        format_name: str,
    ) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []
        successful_pages: set[int] = set()
        page_confidences: dict[int, list[float]] = defaultdict(list)
        page_image_counts: Counter[int] = Counter()

        for page, image_index, image_bytes in page_images:
            page_image_counts[page] += 1
            try:
                regions = self.provider.recognize(
                    image_bytes,
                    source_id=source.source_id,
                    filename=source.filename,
                    page=page,
                    image_index=image_index,
                )
            except Exception as exc:
                unsupported.append(
                    {
                        "kind": "OCR_PROVIDER_EXECUTION_FAILED",
                        "reason_code": "OCR_PROVIDER_EXECUTION_FAILED",
                        "count": 1,
                        "pages": [page],
                        "status": "OCR_PROVIDER_FAILED",
                        "severity": "P0",
                        "blocks_formal_understanding": True,
                        "included_in_plain_text_authority": False,
                        "detail": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
                continue
            image_blocks, mean_confidence = self._blocks_from_regions(
                source,
                regions,
                page=page,
                image_index=image_index,
            )
            blocks.extend(image_blocks)
            if image_blocks:
                successful_pages.add(page)
                page_confidences[page].append(mean_confidence)

        for page in target_pages:
            confidence_values = page_confidences.get(page) or []
            mean_confidence = (
                sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
            )
            page_successful = page in successful_pages and mean_confidence >= self.minimum_confidence
            pages.append(
                {
                    "page": page,
                    "ocr_attempted": True,
                    "ocr_successful": page_successful,
                    "ocr_provider": self.provider.name,
                    "ocr_image_count": int(page_image_counts.get(page) or 0),
                    "ocr_mean_confidence": round(mean_confidence, 4),
                    "coordinates_available": bool(page_successful),
                    "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_EMBEDDED_IMAGE",
                }
            )
            if page_image_counts.get(page, 0) == 0:
                unsupported.append(
                    {
                        "kind": "OCR_SOURCE_IMAGE_NOT_AVAILABLE",
                        "reason_code": "OCR_SOURCE_IMAGE_NOT_AVAILABLE",
                        "count": 1,
                        "pages": [page],
                        "status": "SCANNED_PAGE_COULD_NOT_BE_RENDERED_OR_EXTRACTED",
                        "severity": "P0",
                        "blocks_formal_understanding": True,
                        "included_in_plain_text_authority": False,
                    }
                )
            elif page not in successful_pages:
                unsupported.append(
                    {
                        "kind": "OCR_TEXT_NOT_RECOVERED",
                        "reason_code": "OCR_TEXT_NOT_RECOVERED",
                        "count": 1,
                        "pages": [page],
                        "status": "OCR_RETURNED_NO_TEXT",
                        "severity": "P0",
                        "blocks_formal_understanding": True,
                        "included_in_plain_text_authority": False,
                    }
                )
            elif mean_confidence < self.minimum_confidence:
                unsupported.append(
                    {
                        "kind": "OCR_TEXT_LOW_CONFIDENCE",
                        "reason_code": "OCR_TEXT_LOW_CONFIDENCE",
                        "count": 1,
                        "pages": [page],
                        "status": "OCR_TEXT_RECOVERED_BELOW_FORMAL_CONFIDENCE_THRESHOLD",
                        "severity": "P0",
                        "blocks_formal_understanding": True,
                        "included_in_plain_text_authority": False,
                        "confidence": round(mean_confidence, 4),
                        "minimum_confidence": self.minimum_confidence,
                    }
                )
            else:
                unsupported.append(
                    {
                        "kind": "OCR_PAGE_LAYOUT_PROJECTED",
                        "reason_code": "OCR_PAGE_LAYOUT_PROJECTED",
                        "count": 1,
                        "pages": [page],
                        "status": "TEXT_RECOVERED_WITH_IMAGE_LOCAL_COORDINATES",
                        "severity": "P1",
                        "blocks_formal_understanding": False,
                        "included_in_plain_text_authority": True,
                    }
                )

        formally_resolved_pages = sorted(
            page
            for page in successful_pages
            if page_confidences.get(page)
            and sum(page_confidences[page]) / len(page_confidences[page]) >= self.minimum_confidence
        )
        resolves_gaps = []
        if formally_resolved_pages:
            resolves_gaps.append(
                {
                    "reason_code": "SCANNED_PAGE_REQUIRES_OCR",
                    "pages": formally_resolved_pages,
                    "resolution": "OCR_TEXT_RECOVERED",
                    "provider": self.provider.name,
                }
            )
        critical = [row for row in unsupported if bool(row.get("blocks_formal_understanding"))]
        status = "BLOCKED" if critical else "PARTIAL" if unsupported else "COMPLETE"
        blocks.sort(key=lambda row: (int(row.get("page") or 0), int(row.get("order") or 0)))
        for order, block in enumerate(blocks, start=1):
            block["order"] = order
        plain_text = "\n".join(text(block.get("text")) for block in blocks if text(block.get("text")))
        block_counts = Counter(text(block.get("type")) for block in blocks)
        return {
            "schema": DOCUMENT_IR_SCHEMA,
            "format": format_name,
            "filename": source.filename,
            "plain_text": plain_text,
            "blocks": blocks,
            "sections": [],
            "tables": [],
            "pages": pages,
            "unsupported_content": unsupported,
            "resolves_gaps": resolves_gaps,
            "structure_receipt": {
                "schema": STRUCTURE_RECEIPT_SCHEMA,
                "status": status,
                "format": format_name,
                "page_count": len(target_pages),
                "block_count": len(blocks),
                "source_traceability_rate": 1.0 if blocks else 0.0,
                "block_type_distribution": dict(block_counts),
                "section_count": 0,
                "unsupported_content_count": sum(int(row.get("count") or 0) for row in unsupported),
                "unsupported_content": unsupported,
                "ocr_provider": self.provider.name,
                "ocr_provider_version": self.provider.version,
                "ocr_target_pages": target_pages,
                "ocr_resolved_pages": formally_resolved_pages,
                "ocr_minimum_confidence": self.minimum_confidence,
                "document_order_is_business_flow": False,
                "filename_is_business_context": False,
            },
        }

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        if not self.provider.available():
            raise RuntimeError("OCR provider unavailable")
        return self._build_ir(
            source,
            [(1, 0, source.data)],
            target_pages=[1],
            format_name=source.suffix.lstrip(".") or "image",
        )

    def extract_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> dict[str, Any]:
        if not self.provider.available():
            raise RuntimeError("OCR provider unavailable")
        target_pages = sorted(
            {
                int(page)
                for gap in context.trigger_gaps
                if text(gap.get("reason_code") or gap.get("kind")) == "SCANNED_PAGE_REQUIRES_OCR"
                for page in _list(gap.get("pages"))
                if str(page).isdigit()
            }
        )
        if not target_pages:
            raise ValueError("OCR supplemental adapter received no scanned-page targets")
        if not source.data.lstrip().startswith(b"%PDF-"):
            return self.extract(source)

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(source.data))
        page_images: list[tuple[int, int, bytes]] = []
        for page_number in target_pages:
            if page_number < 1 or page_number > len(reader.pages):
                continue
            page = reader.pages[page_number - 1]
            try:
                images = list(page.images)
            except Exception:
                images = []
            for image_index, image in enumerate(images):
                data = bytes(getattr(image, "data", b"") or b"")
                if data:
                    page_images.append((page_number, image_index, data))
        return self._build_ir(
            source,
            page_images,
            target_pages=target_pages,
            format_name="pdf-ocr-supplement",
        )


__all__ = [
    "OcrProvider",
    "TesseractOcrProvider",
    "OcrSupplementalAdapter",
]
