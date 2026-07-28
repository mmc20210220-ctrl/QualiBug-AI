"""Content-driven image decoding for enterprise visual materials.

The decoder layer is intentionally separate from OCR and page rendering.  It discovers
actual image containers from bytes, normalizes decoded frames to PNG, preserves source
format evidence, and exposes optional plug-ins without teaching business semantics.
"""
from __future__ import annotations

import gzip
import io
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .contract import DocumentSource, text

IMAGE_DECODE_RECEIPT_SCHEMA = "qualibug.image-decode-receipt.v1"

# Suffixes are only a coarse fallback for family detection.  Formal support is decided by
# a decoder successfully opening the bytes.  This set is intentionally broader than the
# built-in Pillow core and includes optional plug-in families.
_IMAGE_FAMILY_SUFFIXES = {
    ".png", ".apng", ".jpg", ".jpeg", ".jpe", ".jfif", ".jif",
    ".gif", ".bmp", ".dib", ".tif", ".tiff", ".webp", ".ico", ".cur",
    ".jp2", ".j2k", ".jpf", ".jpx", ".jpm", ".mj2", ".psd", ".dds",
    ".pcx", ".tga", ".icb", ".vda", ".vst", ".pnm", ".pbm", ".pgm",
    ".ppm", ".pam", ".pfm", ".fits", ".fit", ".fts", ".xbm", ".xpm",
    ".mpo", ".sgi", ".rgb", ".rgba", ".bw", ".im", ".msp", ".blp",
    ".fli", ".flc", ".grib", ".h5", ".hdf", ".heic", ".heif", ".avif",
    ".avifs", ".jxl", ".svg", ".svgz", ".dng", ".cr2", ".cr3", ".nef",
    ".nrw", ".arw", ".sr2", ".raf", ".rw2", ".orf", ".pef", ".x3f",
}
_RAW_SUFFIXES = {
    ".dng", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".sr2", ".raf",
    ".rw2", ".orf", ".pef", ".x3f",
}


def _signature_family(data: bytes) -> str:
    value = bytes(data or b"")
    head = value[:64]
    stripped = value.lstrip()[:512]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if head.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
        return "TIFF_OR_DNG"
    if head.startswith(b"BM"):
        return "BMP"
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return "WEBP"
    if head.startswith(b"\x00\x00\x01\x00"):
        return "ICO"
    if head.startswith(b"\x00\x00\x02\x00"):
        return "CUR"
    if head.startswith(b"8BPS"):
        return "PSD"
    if head.startswith(b"DDS "):
        return "DDS"
    if head.startswith(b"\x00\x00\x00\x0cjP  \r\n\x87\n") or head.startswith(b"\xffO\xffQ"):
        return "JPEG2000"
    if head[:2] in {b"P1", b"P2", b"P3", b"P4", b"P5", b"P6", b"P7", b"PF", b"Pf"}:
        return "NETPBM_OR_PFM"
    if head.startswith(b"SIMPLE  ="):
        return "FITS"
    if head.startswith(b"\x0a") and len(head) >= 4 and head[2] in {0, 1}:
        return "PCX_CANDIDATE"
    if len(head) >= 12 and head[4:8] == b"ftyp" and any(
        brand in head[8:32]
        for brand in (b"avif", b"avis", b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1")
    ):
        return "HEIF_OR_AVIF"
    lower = stripped.lower()
    if lower.startswith(b"<?xml") or lower.startswith(b"<svg"):
        if b"<svg" in lower[:512]:
            return "SVG"
    if head.startswith(b"\x1f\x8b"):
        try:
            expanded = gzip.decompress(value[:2_000_000]).lstrip().lower()
        except Exception:
            expanded = b""
        if b"<svg" in expanded[:1024]:
            return "SVGZ"
    return ""


def sniff_image_source(source: DocumentSource) -> tuple[bool, str]:
    """Classify a likely image family without claiming that a decoder is installed."""
    signature = _signature_family(source.data)
    if signature:
        return True, f"content_signature:{signature}"
    mime = text(source.declared_mime).lower()
    if mime.startswith("image/"):
        return True, f"declared_mime:{mime}"
    if source.suffix in _IMAGE_FAMILY_SUFFIXES:
        return True, f"image_family_suffix:{source.suffix}"
    return False, ""


def _register_optional_pillow_plugins() -> list[str]:
    plugins: list[str] = []
    try:
        import pillow_heif
    except ImportError:
        return plugins
    for name in ("register_heif_opener", "register_avif_opener"):
        callback = getattr(pillow_heif, name, None)
        if not callable(callback):
            continue
        try:
            callback()
        except Exception:
            continue
        plugins.append(f"pillow_heif.{name}")
    return plugins


@dataclass(frozen=True)
class DecodedImageFrame:
    page: int
    frame_index: int
    image_bytes: bytes
    width_px: int
    height_px: int
    source_format: str
    source_mode: str
    source_locator: str
    decoder_name: str
    decoder_version: str
    decode_method: str
    metadata: dict[str, Any]

    def evidence(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "frame_index": self.frame_index,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "source_format": self.source_format,
            "source_mode": self.source_mode,
            "source_locator": self.source_locator,
            "decoder_name": self.decoder_name,
            "decoder_version": self.decoder_version,
            "decode_method": self.decode_method,
            "metadata": dict(self.metadata),
            "business_semantics_added": False,
        }


@dataclass(frozen=True)
class ImageDecodeBatch:
    frames: tuple[DecodedImageFrame, ...]
    receipt: dict[str, Any]
    errors: tuple[dict[str, Any], ...] = ()


class ImageDecoder(Protocol):
    name: str
    version: str
    priority: int

    def available(self) -> bool:
        ...

    def supports(self, source: DocumentSource) -> bool:
        ...

    def decode(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[DecodedImageFrame]:
        ...

    def runtime_formats(self) -> list[str]:
        ...


class PillowImageDecoder:
    """Decode every image container supported by the installed Pillow runtime.

    Pillow discovers formats from registered plug-ins, so this decoder is not limited to a
    hard-coded PNG/JPEG list.  Optional pillow-heif registration extends the same path to
    HEIF/HEIC/AVIF when that package is installed.
    """

    name = "pillow-runtime-image-decoder"
    version = "2"
    priority = 120

    def __init__(self, max_pixels: int = 120_000_000, max_frames: int = 512) -> None:
        self.max_pixels = max(1_000_000, int(max_pixels))
        self.max_frames = max(1, int(max_frames))
        self._plugins_registered = False
        self._optional_plugins: list[str] = []

    def _initialize(self) -> None:
        if self._plugins_registered:
            return
        self._optional_plugins = _register_optional_pillow_plugins()
        from PIL import Image

        Image.init()
        self._plugins_registered = True

    def available(self) -> bool:
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        return True

    def supports(self, source: DocumentSource) -> bool:
        if not self.available():
            return False
        self._initialize()
        try:
            from PIL import Image

            with Image.open(io.BytesIO(source.data)) as image:
                return bool(text(getattr(image, "format", "")))
        except Exception:
            return False

    def runtime_formats(self) -> list[str]:
        if not self.available():
            return []
        self._initialize()
        from PIL import Image

        return sorted({text(value).upper() for value in Image.registered_extensions().values() if text(value)})

    def decode(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[DecodedImageFrame]:
        self._initialize()
        from PIL import Image, ImageOps

        requested = {int(value) for value in (pages or ()) if int(value) > 0}
        decoded: list[DecodedImageFrame] = []
        with Image.open(io.BytesIO(source.data)) as image:
            source_format = text(getattr(image, "format", "UNKNOWN")).upper() or "UNKNOWN"
            frame_count = int(getattr(image, "n_frames", 1) or 1)
            if frame_count > self.max_frames:
                raise ValueError(f"image frame count {frame_count} exceeds limit {self.max_frames}")
            for frame_index in range(frame_count):
                page_number = frame_index + 1
                if requested and page_number not in requested:
                    continue
                image.seek(frame_index)
                frame = ImageOps.exif_transpose(image.copy())
                width, height = int(frame.width), int(frame.height)
                if width <= 0 or height <= 0:
                    raise ValueError("decoded image has invalid dimensions")
                if width * height > self.max_pixels:
                    raise ValueError(
                        f"decoded image frame has {width * height} pixels; limit is {self.max_pixels}"
                    )
                source_mode = text(frame.mode)
                normalized = frame.convert("RGBA") if "A" in frame.getbands() else frame.convert("RGB")
                buffer = io.BytesIO()
                normalized.save(buffer, format="PNG")
                metadata = {
                    "frame_count": frame_count,
                    "duration_ms": int(image.info.get("duration") or 0),
                    "loop": int(image.info.get("loop") or 0),
                    "optional_pillow_plugins": list(self._optional_plugins),
                }
                decoded.append(
                    DecodedImageFrame(
                        page=page_number,
                        frame_index=frame_index,
                        image_bytes=buffer.getvalue(),
                        width_px=width,
                        height_px=height,
                        source_format=source_format,
                        source_mode=source_mode,
                        source_locator=(
                            f"{source.filename or 'image'}#decoded_frame={page_number};"
                            f"source_format={source_format};decoder={self.name}"
                        ),
                        decoder_name=self.name,
                        decoder_version=self.version,
                        decode_method="pillow_registered_format_to_png",
                        metadata=metadata,
                    )
                )
        return decoded


class CairoSvgImageDecoder:
    """Optional SVG/SVGZ decoder. External references are rejected before rendering."""

    name = "cairosvg-vector-image-decoder"
    version = "1"
    priority = 115

    def available(self) -> bool:
        try:
            import cairosvg  # noqa: F401
        except ImportError:
            return False
        return True

    def supports(self, source: DocumentSource) -> bool:
        detected, reason = sniff_image_source(source)
        return self.available() and detected and reason.split(":")[-1] in {"SVG", "SVGZ", ".svg", ".svgz"}

    def runtime_formats(self) -> list[str]:
        return ["SVG", "SVGZ"] if self.available() else []

    def decode(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[DecodedImageFrame]:
        requested = {int(value) for value in (pages or ()) if int(value) > 0}
        if requested and 1 not in requested:
            return []
        payload = gzip.decompress(source.data) if source.data.startswith(b"\x1f\x8b") else source.data
        lowered = payload.lower()
        if re.search(rb"(?:href|xlink:href)\s*=\s*['\"]\s*(?:https?:|file:|ftp:|//)", lowered):
            raise ValueError("SVG external references are forbidden")
        import cairosvg
        from PIL import Image

        png = cairosvg.svg2png(bytestring=payload)
        with Image.open(io.BytesIO(png)) as image:
            width, height = int(image.width), int(image.height)
        source_format = "SVGZ" if source.data.startswith(b"\x1f\x8b") else "SVG"
        return [
            DecodedImageFrame(
                page=1,
                frame_index=0,
                image_bytes=png,
                width_px=width,
                height_px=height,
                source_format=source_format,
                source_mode="VECTOR",
                source_locator=(
                    f"{source.filename or 'vector-image'}#decoded_frame=1;"
                    f"source_format={source_format};decoder={self.name}"
                ),
                decoder_name=self.name,
                decoder_version=self.version,
                decode_method="svg_vector_rasterized_to_png",
                metadata={"external_references_allowed": False},
            )
        ]


class RawpyCameraImageDecoder:
    """Optional LibRaw-backed decoder for common camera RAW containers."""

    name = "rawpy-camera-image-decoder"
    version = "1"
    priority = 110

    def available(self) -> bool:
        try:
            import rawpy  # noqa: F401
        except ImportError:
            return False
        return True

    def supports(self, source: DocumentSource) -> bool:
        return self.available() and source.suffix in _RAW_SUFFIXES

    def runtime_formats(self) -> list[str]:
        return sorted(value.lstrip(".").upper() for value in _RAW_SUFFIXES) if self.available() else []

    def decode(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[DecodedImageFrame]:
        requested = {int(value) for value in (pages or ()) if int(value) > 0}
        if requested and 1 not in requested:
            return []
        import rawpy
        from PIL import Image

        with rawpy.imread(io.BytesIO(source.data)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        image = Image.fromarray(rgb).convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        source_format = source.suffix.lstrip(".").upper() or "CAMERA_RAW"
        return [
            DecodedImageFrame(
                page=1,
                frame_index=0,
                image_bytes=buffer.getvalue(),
                width_px=int(image.width),
                height_px=int(image.height),
                source_format=source_format,
                source_mode="RAW_SENSOR",
                source_locator=(
                    f"{source.filename or 'camera-raw'}#decoded_frame=1;"
                    f"source_format={source_format};decoder={self.name}"
                ),
                decoder_name=self.name,
                decoder_version=self.version,
                decode_method="libraw_postprocess_to_png",
                metadata={"camera_white_balance_used": True},
            )
        ]


class ImageDecoderRegistry:
    def __init__(self, decoders: Iterable[ImageDecoder] = ()) -> None:
        self._decoders: dict[str, ImageDecoder] = {}
        for decoder in decoders:
            self.register(decoder)

    def register(self, decoder: ImageDecoder) -> None:
        name = text(getattr(decoder, "name", ""))
        if not name:
            raise ValueError("image decoder name is required")
        if name in self._decoders:
            raise ValueError(f"image decoder already registered: {name}")
        self._decoders[name] = decoder

    def all(self) -> list[ImageDecoder]:
        return sorted(
            self._decoders.values(),
            key=lambda row: (-int(getattr(row, "priority", 0)), text(getattr(row, "name", ""))),
        )

    def matching(self, source: DocumentSource) -> list[ImageDecoder]:
        rows: list[ImageDecoder] = []
        for decoder in self.all():
            try:
                if decoder.available() and decoder.supports(source):
                    rows.append(decoder)
            except Exception:
                continue
        return rows

    def can_decode(self, source: DocumentSource) -> bool:
        return bool(self.matching(source))

    def runtime_capabilities(self) -> dict[str, Any]:
        decoders: list[dict[str, Any]] = []
        formats: set[str] = set()
        for decoder in self.all():
            available = False
            supported: list[str] = []
            try:
                available = bool(decoder.available())
                supported = decoder.runtime_formats() if available else []
            except Exception:
                available = False
            formats.update(text(value).upper() for value in supported if text(value))
            decoders.append(
                {
                    "decoder_name": decoder.name,
                    "decoder_version": decoder.version,
                    "available": available,
                    "runtime_formats": sorted(set(supported)),
                }
            )
        return {"decoders": decoders, "runtime_formats": sorted(formats)}

    def decode(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> ImageDecodeBatch:
        attempted: list[str] = []
        errors: list[dict[str, Any]] = []
        for decoder in self.matching(source):
            attempted.append(decoder.name)
            try:
                frames = decoder.decode(source, pages=pages)
            except Exception as exc:
                errors.append(
                    {
                        "decoder_name": decoder.name,
                        "code": "IMAGE_DECODER_EXECUTION_FAILED",
                        "detail": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
                continue
            if not frames:
                errors.append(
                    {
                        "decoder_name": decoder.name,
                        "code": "IMAGE_DECODER_RETURNED_NO_FRAMES",
                        "detail": "decoder matched but returned no requested frames",
                    }
                )
                continue
            formats = sorted({frame.source_format for frame in frames if frame.source_format})
            receipt = {
                "schema": IMAGE_DECODE_RECEIPT_SCHEMA,
                "status": "COMPLETE",
                "source_id": source.source_id,
                "filename": source.filename,
                "source_hash": source.content_hash,
                "decoder_name": decoder.name,
                "decoder_version": decoder.version,
                "detected_formats": formats,
                "decoded_frame_count": len(frames),
                "decoded_pages": sorted({frame.page for frame in frames}),
                "attempted_decoders": attempted,
                "error_count": len(errors),
                "errors": errors,
                "runtime_capabilities": self.runtime_capabilities(),
                "business_semantics_added": False,
            }
            return ImageDecodeBatch(tuple(frames), receipt, tuple(errors))
        likely, reason = sniff_image_source(source)
        receipt = {
            "schema": IMAGE_DECODE_RECEIPT_SCHEMA,
            "status": "BLOCKED",
            "source_id": source.source_id,
            "filename": source.filename,
            "source_hash": source.content_hash,
            "decoder_name": "",
            "decoder_version": "",
            "detected_formats": [],
            "decoded_frame_count": 0,
            "decoded_pages": [],
            "attempted_decoders": attempted,
            "error_count": len(errors),
            "errors": errors,
            "reason_code": "IMAGE_DECODER_UNAVAILABLE_OR_FAILED",
            "image_family_likely": likely,
            "image_family_detection_reason": reason,
            "runtime_capabilities": self.runtime_capabilities(),
            "business_semantics_added": False,
        }
        return ImageDecodeBatch((), receipt, tuple(errors))


def build_default_image_decoder_registry() -> ImageDecoderRegistry:
    return ImageDecoderRegistry(
        [
            PillowImageDecoder(),
            CairoSvgImageDecoder(),
            RawpyCameraImageDecoder(),
        ]
    )


__all__ = [
    "IMAGE_DECODE_RECEIPT_SCHEMA",
    "DecodedImageFrame",
    "ImageDecodeBatch",
    "ImageDecoder",
    "PillowImageDecoder",
    "CairoSvgImageDecoder",
    "RawpyCameraImageDecoder",
    "ImageDecoderRegistry",
    "build_default_image_decoder_registry",
    "sniff_image_source",
]
