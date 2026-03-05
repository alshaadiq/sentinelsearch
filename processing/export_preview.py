"""
Generate a small RGB PNG quicklook from the composite COG.

- Reads B04 (Red), B03 (Green), B02 (Blue) from the COG
- Applies 2–98 percentile stretch per channel
- Downsamples to max 768 px on the longer axis
- Saves as PNG with JPEG-like quality (lossless PNG)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
import rasterio.enums
from PIL import Image

from backend.config import settings
from processing.export_cog import OUTPUT_BAND_ORDER

logger = logging.getLogger(__name__)

PREVIEW_MAX_DIM = 768  # max pixels on the longer side


def export_preview(cog_path: Path, job_id: str) -> Path:
    """
    Create a PNG quicklook from an existing COG.

    Parameters
    ----------
    cog_path : Path
        Path to the Cloud Optimized GeoTIFF produced by :func:`export_cog`.
    job_id : str
        Used to name the output preview file.

    Returns
    -------
    Path
        Absolute path to the PNG preview.
    """
    preview_path = settings.previews_dir / f"{job_id}.png"

    with rasterio.open(cog_path) as src:
        # Determine preview output size (respecting aspect ratio)
        native_h, native_w = src.height, src.width
        scale = min(PREVIEW_MAX_DIM / native_h, PREVIEW_MAX_DIM / native_w, 1.0)
        out_h = max(1, int(native_h * scale))
        out_w = max(1, int(native_w * scale))

        # Identify band indices for B04, B03, B02 in the COG
        tags_list = [src.tags(i) for i in range(1, src.count + 1)]
        band_names_in_cog = [t.get("name", OUTPUT_BAND_ORDER[i]) for i, t in enumerate(tags_list)]

        def _band_index(name: str) -> int:
            """1-based index of a named band in the COG."""
            try:
                return band_names_in_cog.index(name) + 1
            except ValueError:
                return None

        rgb_indices = [_band_index("B04"), _band_index("B03"), _band_index("B02")]

        # Fall back to first three bands if RGB not found
        rgb_indices = [i if i is not None else (j + 1) for j, i in enumerate(rgb_indices)]

        channels = []
        nodata_val = src.nodata
        for band_idx in rgb_indices:
            raw = src.read(
                band_idx,
                out_shape=(out_h, out_w),
                resampling=rasterio.enums.Resampling.average,
            ).astype(np.float32)
            # Mask explicit nodata value
            if nodata_val is not None and not np.isnan(nodata_val):
                raw[raw == nodata_val] = np.nan
            # Sentinel-2 L2A reflectance: 0–10000 DN.  Anything outside is nodata/garbage.
            raw[(raw < 0) | (raw > 10000)] = np.nan
            channels.append(raw)

    # ── Per-channel 2–98 percentile stretch, clouds excluded ─────────
    # Cloud pixels are typically 6000–10000 DN; including them in the
    # percentile crushes all land into the bottom 10-20% of brightness.
    # Solution: compute stretch from "clear-land" range only (≤ CLOUD_DN_THRESH),
    # then clip the full image (clouds clip to white, shadows to black).
    CLOUD_DN_THRESH = 4000   # safe upper bound for S-2 L2A clear land
    GAMMA = 1.4              # gamma < 1 brightens mid-tones; 1.4 lifts shadows

    rgb_stretched = []
    for ch in channels:
        finite_mask = np.isfinite(ch)
        if not np.any(finite_mask):
            rgb_stretched.append(np.full((out_h, out_w), 128, dtype=np.uint8))
            continue

        # Use only clear-land pixels for percentile (exclude cloud-bright pixels)
        clear_pixels = ch[(finite_mask) & (ch <= CLOUD_DN_THRESH)]
        if clear_pixels.size < 100:
            # Fall back to all valid pixels if most are clouds
            clear_pixels = ch[finite_mask]

        lo, hi = np.percentile(clear_pixels, [2, 98])
        if hi <= lo:
            hi = lo + 1.0

        # Stretch and clip (clouds → 1.0 = white; shadows → 0.0 = black)
        stretched = np.clip((ch - lo) / (hi - lo), 0.0, 1.0)
        # Gamma correction to lift mid-tones (gamma > 1 brightens)
        stretched = np.power(stretched, 1.0 / GAMMA)
        out = (np.nan_to_num(stretched, nan=0.0) * 255).astype(np.uint8)
        out[~finite_mask] = 0
        rgb_stretched.append(out)

    rgb_array = np.stack(rgb_stretched, axis=-1)  # (H, W, 3)

    # Build alpha channel: transparent where ANY channel is nodata
    valid_mask = np.all(np.stack([np.isfinite(ch) for ch in channels], axis=-1), axis=-1)
    alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
    rgba_array = np.concatenate([rgb_array, alpha[..., np.newaxis]], axis=-1)  # (H, W, 4)

    img = Image.fromarray(rgba_array, mode="RGBA")
    img.save(preview_path, format="PNG", optimize=True)

    logger.info("Preview written: %s  (%d×%d px)", preview_path, out_w, out_h)
    return preview_path
