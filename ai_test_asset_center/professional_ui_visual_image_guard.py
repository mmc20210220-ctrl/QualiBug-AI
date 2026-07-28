"""Decode limits for formal visual baseline PNG material.

Compressed PNG size alone does not bound decoded memory. This guard checks image
format and dimensions before ``load()`` and treats Pillow decompression-bomb
warnings as hard failures. It replaces the visual module's decoder without
changing comparison or verdict authority.
"""
from __future__ import annotations

import io
import warnings
from typing import Any

from . import professional_ui_visual_baseline as _visual

MAX_IMAGE_WIDTH = 20_000
MAX_IMAGE_HEIGHT = 20_000
MAX_IMAGE_PIXELS = 40_000_000
_INSTALL_MARKER = "_qualibug_visual_image_guard_installed"
_ORIGINAL_DECODER = "_qualibug_visual_decoder_before_image_guard"


def install_visual_image_guard() -> None:
    if getattr(_visual, _INSTALL_MARKER, False):
        return
    original = getattr(
        _visual,
        _ORIGINAL_DECODER,
        _visual._open_rgba,
    )
    setattr(_visual, _ORIGINAL_DECODER, original)

    def open_guarded_rgba(data: bytes) -> Any:
        try:
            from PIL import Image
        except ImportError as exc:
            raise _visual.VisualBaselineObservationError(
                "UI_VISUAL_PILLOW_UNAVAILABLE"
            ) from exc
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(io.BytesIO(data))
                if _visual._text(image.format).upper() != "PNG":
                    raise _visual.VisualBaselineObservationError(
                        "UI_VISUAL_IMAGE_FORMAT_NOT_PNG"
                    )
                width, height = (int(image.size[0]), int(image.size[1]))
                if (
                    width < 1
                    or height < 1
                    or width > MAX_IMAGE_WIDTH
                    or height > MAX_IMAGE_HEIGHT
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise _visual.VisualBaselineObservationError(
                        "UI_VISUAL_DECODE_DIMENSION_LIMIT_EXCEEDED"
                    )
                image.load()
                return image.convert("RGBA")
        except _visual.VisualBaselineObservationError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise _visual.VisualBaselineObservationError(
                "UI_VISUAL_DECOMPRESSION_BOMB_BLOCKED"
            ) from exc
        except Exception as exc:
            raise _visual.VisualBaselineObservationError(
                "UI_VISUAL_PNG_DECODE_FAILED"
            ) from exc

    _visual._open_rgba = open_guarded_rgba
    setattr(_visual, _INSTALL_MARKER, True)


__all__ = [
    "MAX_IMAGE_HEIGHT",
    "MAX_IMAGE_PIXELS",
    "MAX_IMAGE_WIDTH",
    "install_visual_image_guard",
]
