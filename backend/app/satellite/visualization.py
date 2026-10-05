"""Render SAR arrays / detection overlays to base64 PNGs for API responses.

Uses Pillow (PIL) instead of matplotlib so that no matplotlib native extension
(in particular _image.pyd) is imported at backend startup.  Pillow is already
a hard dependency (requirements.txt) and its wheel ships with no blocked native
extensions on Windows.  The output format - "data:image/png;base64,..." - is
identical to the previous implementation; no API schema changes.
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _float_to_uint8(arr: np.ndarray) -> np.ndarray:
    """Clip a float array assumed to be in [0, 1] and convert to uint8."""
    return (np.clip(arr, 0.0, 1.0) * 255).astype(np.uint8)


def _png_to_base64(img: Image.Image) -> str:
    """Encode a PIL Image as a data-URI PNG string."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


# ---------------------------------------------------------------------------
# Public API (signatures and return values unchanged)
# ---------------------------------------------------------------------------

def render_grayscale(image: np.ndarray) -> str:
    """Render a float [0,1] 2-D SAR array as a grayscale PNG data-URI."""
    img = Image.fromarray(_float_to_uint8(image), mode="L")
    return _png_to_base64(img)


def render_detection_overlay(image: np.ndarray, mask: np.ndarray) -> str:
    """Render a grayscale base image with a semi-transparent red overlay
    where *mask* is True.  Equivalent to matplotlib's 0.45-alpha red imshow
    overlay used in the previous implementation.
    """
    # Base: grayscale → RGBA so we can composite the coloured overlay on top.
    base_rgba = Image.fromarray(_float_to_uint8(image), mode="L").convert("RGBA")

    # Overlay: transparent everywhere, semi-transparent red over the mask.
    # Alpha 0.45 * 255 ≈ 115.
    overlay_arr = np.zeros((*mask.shape, 4), dtype=np.uint8)
    overlay_arr[mask] = [255, 0, 0, 115]  # R, G, B, A
    overlay = Image.fromarray(overlay_arr, mode="RGBA")

    composited = Image.alpha_composite(base_rgba, overlay)
    return _png_to_base64(composited)


def render_mask(mask: np.ndarray) -> str:
    """Render a boolean or float mask as a grayscale PNG data-URI."""
    arr = mask.astype(np.float32) if mask.dtype == bool else mask.astype(np.float32)
    img = Image.fromarray(_float_to_uint8(arr), mode="L")
    return _png_to_base64(img)
