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
        for band_idx in rgb_indices:
            data = src.read(
                band_idx,
                out_shape=(out_h, out_w),
                resampling=rasterio.enums.Resampling.average,
                masked=True,
            )
            channels.append(data)

    # Stretch each channel separately (2–98 percentile)
    rgb_stretched = []
    for ch in channels:
        arr = ch.astype(np.float32)
        valid = arr[~np.isnan(arr.data) & ~ch.mask] if hasattr(ch, "mask") else arr[~np.isnan(arr)]
        if valid.size == 0:
            rgb_stretched.append(np.zeros((out_h, out_w), dtype=np.uint8))
            continue
        lo, hi = np.percentile(valid, [2, 98])
        if hi == lo:
            hi = lo + 1
        stretched = np.clip((arr - lo) / (hi - lo), 0, 1)
        rgb_stretched.append((stretched * 255).astype(np.uint8))

    rgb_array = np.stack(rgb_stretched, axis=-1)  # (H, W, 3)

    # Replace NaN pixels (nodata) with light grey
    nodata_mask = np.any(np.isnan(np.stack([c.astype(float) for c in channels], axis=-1)), axis=-1)
    rgb_array[nodata_mask] = 180

    img = Image.fromarray(rgb_array, mode="RGB")
    img.save(preview_path, format="PNG", optimize=True)

    logger.info("Preview written: %s  (%d×%d px)", preview_path, out_w, out_h)
    return preview_path
