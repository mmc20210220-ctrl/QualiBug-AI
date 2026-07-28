"""Universal image page renderer backed by the image decoder registry."""
from __future__ import annotations

from typing import Iterable

from .contract import DocumentSource
from .image_decoding import ImageDecoderRegistry, build_default_image_decoder_registry
from .page_rendering import RenderedPage


class UniversalImagePageRenderer:
    """Normalize any runtime-decodable image container into rendered pages.

    Formal support is based on successful content decoding, not on a fixed filename suffix
    list.  The public page-rendering contract remains unchanged for OCR/table/diagram users.
    """

    name = "universal-image-page-renderer"
    version = "2"
    priority = 125

    def __init__(self, decoder_registry: ImageDecoderRegistry | None = None) -> None:
        self.decoder_registry = decoder_registry or build_default_image_decoder_registry()
        self.last_decode_receipt: dict = {}

    def available(self) -> bool:
        return any(row.get("available") for row in self.decoder_registry.runtime_capabilities()["decoders"])

    def supports(self, source: DocumentSource) -> bool:
        return self.decoder_registry.can_decode(source)

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[RenderedPage]:
        batch = self.decoder_registry.decode(source, pages=pages)
        self.last_decode_receipt = dict(batch.receipt)
        rendered: list[RenderedPage] = []
        for frame in batch.frames:
            rendered.append(
                RenderedPage(
                    page=frame.page,
                    image_index=frame.frame_index,
                    image_bytes=frame.image_bytes,
                    width_px=frame.width_px,
                    height_px=frame.height_px,
                    source_locator=frame.source_locator,
                    renderer_name=self.name,
                    renderer_version=self.version,
                    render_method=(
                        f"image_decoder={frame.decoder_name};"
                        f"source_format={frame.source_format};"
                        f"decode_method={frame.decode_method}"
                    ),
                )
            )
        return rendered


__all__ = ["UniversalImagePageRenderer"]
