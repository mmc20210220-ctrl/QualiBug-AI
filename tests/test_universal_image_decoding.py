from __future__ import annotations

import io
from typing import Any, Iterable

from PIL import Image

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    DocumentAdapterRegistry,
    DocumentSource,
    ImageDecoderRegistry,
    OcrSupplementalAdapter,
    PageRendererRegistry,
    PillowImageDecoder,
    UniversalImagePageRenderer,
    build_document_structure_ir,
    build_default_image_decoder_registry,
    build_default_page_renderer_registry,
    sniff_image_source,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.builtin_adapters import (
    UnknownBinaryDocumentAdapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.image_decoding import (
    DecodedImageFrame,
)


class _FakeOcrProvider:
    name = "fake-ocr"
    version = "test"

    def __init__(self) -> None:
        self.received: list[bytes] = []

    def available(self) -> bool:
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
        self.received.append(image_bytes)
        return [
            {
                "text": "订单不得删除。",
                "bbox": [10, 10, 180, 40],
                "confidence": 0.96,
                "image_width_px": 64,
                "image_height_px": 48,
            }
        ]


def _image_bytes(format_name: str, *, frames: int = 1) -> bytes:
    images = [Image.new("RGB", (64, 48), (index * 40, 20, 120)) for index in range(frames)]
    buffer = io.BytesIO()
    if frames > 1:
        images[0].save(buffer, format=format_name, save_all=True, append_images=images[1:])
    else:
        images[0].save(buffer, format=format_name)
    return buffer.getvalue()


def test_content_detection_selects_image_path_even_with_wrong_extension() -> None:
    provider = _FakeOcrProvider()
    decoder_registry = ImageDecoderRegistry([PillowImageDecoder()])
    renderer = UniversalImagePageRenderer(decoder_registry=decoder_registry)
    ocr = OcrSupplementalAdapter(
        provider=provider,
        renderer_registry=PageRendererRegistry([renderer]),
    )
    ir = build_document_structure_ir(
        _image_bytes("BMP"),
        filename="企业资料.bin",
        source_id="image-wrong-suffix",
        registry=DocumentAdapterRegistry([ocr, UnknownBinaryDocumentAdapter()]),
    )
    assert ir["parsing_plan"]["detected_family"] == "image"
    assert ir["parsing_plan"]["detection_method"] == "content_signature:BMP"
    assert ir["parsing_plan"]["selected_adapters"][0]["adapter_name"] == "ocr-visual-text"
    assert ir["plain_text"] == "订单不得删除。"
    render_receipt = ir["structure_receipt"]["page_rendering_receipt"]
    assert render_receipt["decoded_image_formats"] == ["BMP"]
    assert render_receipt["image_decode_receipt_count"] == 1
    assert provider.received and provider.received[0].startswith(b"\x89PNG")


def test_multiframe_tiff_is_exposed_as_multiple_pages() -> None:
    source = DocumentSource(
        source_id="multi-tiff",
        filename="巡检扫描.tiff",
        data=_image_bytes("TIFF", frames=2),
    )
    batch = ImageDecoderRegistry([PillowImageDecoder()]).decode(source)
    assert batch.receipt["status"] == "COMPLETE"
    assert batch.receipt["detected_formats"] == ["TIFF"]
    assert batch.receipt["decoded_frame_count"] == 2
    assert [row.page for row in batch.frames] == [1, 2]
    assert all(row.image_bytes.startswith(b"\x89PNG") for row in batch.frames)


def test_pillow_runtime_formats_are_reported_dynamically() -> None:
    capabilities = build_default_image_decoder_registry().runtime_capabilities()
    formats = set(capabilities["runtime_formats"])
    assert {"BMP", "JPEG", "PNG", "TIFF"}.issubset(formats)
    assert len(formats) > 8
    pillow = next(
        row
        for row in capabilities["decoders"]
        if row["decoder_name"] == "pillow-runtime-image-decoder"
    )
    assert pillow["available"] is True
    assert "PNG" in pillow["runtime_formats"]


def test_declared_unknown_image_without_decoder_stays_blocked() -> None:
    source = DocumentSource(
        source_id="unknown-image",
        filename="unknown.visual",
        data=b"not-a-decodable-image",
        declared_mime="image/x-enterprise-private",
    )
    likely, reason = sniff_image_source(source)
    assert likely is True
    assert reason == "declared_mime:image/x-enterprise-private"
    renderer = UniversalImagePageRenderer(decoder_registry=ImageDecoderRegistry([]))
    assert renderer.supports(source) is False
    ir = build_document_structure_ir(
        source.data,
        filename=source.filename,
        source_id=source.source_id,
        declared_mime=source.declared_mime,
        registry=DocumentAdapterRegistry([UnknownBinaryDocumentAdapter()]),
    )
    assert ir["parsing_plan"]["detected_family"] == "image"
    assert ir["structure_receipt"]["status"] == "BLOCKED"


class _FailingDecoder:
    name = "failing-decoder"
    version = "test"
    priority = 200

    def available(self) -> bool:
        return True

    def supports(self, source: DocumentSource) -> bool:
        return True

    def runtime_formats(self) -> list[str]:
        return ["FAKE"]

    def decode(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[DecodedImageFrame]:
        raise RuntimeError("decoder failed")


class _SuccessfulDecoder:
    name = "successful-decoder"
    version = "test"
    priority = 100

    def available(self) -> bool:
        return True

    def supports(self, source: DocumentSource) -> bool:
        return True

    def runtime_formats(self) -> list[str]:
        return ["FAKE"]

    def decode(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[DecodedImageFrame]:
        return [
            DecodedImageFrame(
                page=1,
                frame_index=0,
                image_bytes=_image_bytes("PNG"),
                width_px=64,
                height_px=48,
                source_format="FAKE",
                source_mode="RGB",
                source_locator="fake#decoded_frame=1",
                decoder_name=self.name,
                decoder_version=self.version,
                decode_method="test",
                metadata={},
            )
        ]


def test_decoder_registry_falls_back_and_preserves_failure_receipt() -> None:
    registry = ImageDecoderRegistry([_FailingDecoder(), _SuccessfulDecoder()])
    batch = registry.decode(
        DocumentSource(source_id="fake", filename="fake.image", data=b"fake")
    )
    assert batch.receipt["decoder_name"] == "successful-decoder"
    assert batch.receipt["attempted_decoders"] == ["failing-decoder", "successful-decoder"]
    assert batch.receipt["error_count"] == 1
    assert batch.receipt["errors"][0]["code"] == "IMAGE_DECODER_EXECUTION_FAILED"


def test_default_page_renderer_uses_universal_image_renderer() -> None:
    names = [renderer.name for renderer in build_default_page_renderer_registry().all()]
    assert "universal-image-page-renderer" in names
    assert names.count("raster-image-page-renderer") == 0
