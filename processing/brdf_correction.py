"""
BRDF c-factor normalisation for Sentinel-2 L2A surface reflectance.

Background
----------
Sentinel-2 L2A is atmospherically corrected but NOT BRDF-corrected.
Each scene is observed at a different sun/view geometry, so the same
surface can appear 5–15% brighter or darker depending on acquisition
date and position within the swath.  In a multi-date composite this
creates visible band-boundary artefacts.

Method
------
Roy et al. 2017 (RSE, 176:255-271) c-factor approach:

    c(band) = BRDF_model(nadir_geometry) / BRDF_model(actual_geometry)
    corrected_DN = raw_DN × c(band)

where the BRDF model is the semi-empirical RossThick–LiSparse-R model:

    BRDF = f_iso + f_vol × Kvol + f_geo × Kgeo

with band-specific (f_iso, f_vol, f_geo) from Roy et al. 2017 Table 1
extended to Sentinel-2 red-edge bands via spectral interpolation
(Claverie et al. 2018, HSINAEF-2, following Franch et al. 2019).

Normalisation target
--------------------
Sun zenith = 45°, view zenith = 0° (nadir), relative azimuth = 0°.
This is the standard Roy et al. 2017 normalisation geometry.

Sun/view angles are read from Planetary Computer STAC item properties
(``sun_elevation``, ``sun_azimuth``, ``view:incidence_angle``,
``view:azimuth``).  Missing view-angle metadata falls back to 0° view
zenith (nadir) which still corrects the sun-angle component.

Spectral coverage
-----------------
Correction is applied to all optical bands:
B02, B03, B04, B05, B06, B07, B08, B8A, B11, B12.
SCL is unchanged (classification, not reflectance).
"""
from __future__ import annotations

import logging
import math
from typing import List

import numpy as np
import pystac
import xarray as xr

logger = logging.getLogger(__name__)

# ── Band-specific BRDF shape parameters ──────────────────────────────────────
# Roy et al. 2017 Table 1 (Landsat OLI) extended to S-2 red-edge bands.
# f_iso: isotropic weight
# f_vol: Ross-Thick volumetric kernel weight
# f_geo: Li-Sparse-R geometric kernel weight
BRDF_COEFFICIENTS: dict[str, dict[str, float]] = {
    "B02": {"f_iso": 0.0774, "f_vol": 0.0372, "f_geo": 0.0079},  # Blue     ~490 nm
    "B03": {"f_iso": 0.1306, "f_vol": 0.0580, "f_geo": 0.0178},  # Green    ~560 nm
    "B04": {"f_iso": 0.1690, "f_vol": 0.0574, "f_geo": 0.0227},  # Red      ~665 nm
    "B05": {"f_iso": 0.2085, "f_vol": 0.0845, "f_geo": 0.0256},  # RE1      ~705 nm
    "B06": {"f_iso": 0.2316, "f_vol": 0.1003, "f_geo": 0.0273},  # RE2      ~740 nm
    "B07": {"f_iso": 0.2599, "f_vol": 0.1197, "f_geo": 0.0294},  # RE3      ~783 nm
    "B08": {"f_iso": 0.3093, "f_vol": 0.1535, "f_geo": 0.0330},  # NIR      ~842 nm
    "B8A": {"f_iso": 0.3093, "f_vol": 0.1535, "f_geo": 0.0330},  # Narrow NIR ~865 nm
    "B11": {"f_iso": 0.3430, "f_vol": 0.1560, "f_geo": 0.0453},  # SWIR1   ~1610 nm
    "B12": {"f_iso": 0.2658, "f_vol": 0.1240, "f_geo": 0.0387},  # SWIR2   ~2190 nm
}

# Standard nadir normalisation geometry (Roy et al. 2017)
_NORM_SZN = math.radians(45.0)   # sun zenith
_NORM_VZN = math.radians(0.0)    # view zenith  (nadir)
_NORM_PHI = math.radians(0.0)    # relative azimuth

# Li-Sparse-R shape parameters
_B_R = 1.0   # b/r ratio
_H_B = 2.0   # h/b ratio


# ── Kernel functions ──────────────────────────────────────────────────────────

def _ross_thick(szn: float, vzn: float, phi: float) -> float:
    """
    Ross-Thick volumetric scattering kernel (scalar, angles in radians).

    Handles the degenerate case szn=0 or vzn=0 without division by zero.
    """
    cos_szn = math.cos(szn)
    sin_szn = math.sin(szn)
    cos_vzn = math.cos(vzn)
    sin_vzn = math.sin(vzn)

    cos_xi = cos_szn * cos_vzn + sin_szn * sin_vzn * math.cos(phi)
    cos_xi = max(-1.0, min(1.0, cos_xi))
    xi = math.acos(cos_xi)

    denom = cos_szn + cos_vzn
    if abs(denom) < 1e-6:
        return 0.0
    return ((math.pi / 2.0 - xi) * cos_xi + math.sin(xi)) / denom - math.pi / 4.0


def _li_sparse_r(szn: float, vzn: float, phi: float) -> float:
    """
    Li-Sparse-Reciprocal geometric kernel (scalar, angles in radians).

    Uses b/r=1, h/b=2 as per Li & Strahler 1992 / Roy et al. 2017.
    """
    # Modified angles (b/r = 1 → theta' = arctan(tan(theta)))
    t_szn = math.atan(_B_R * math.tan(szn))
    t_vzn = math.atan(_B_R * math.tan(vzn))

    cos_ts = math.cos(t_szn)
    cos_tv = math.cos(t_vzn)
    sin_ts = math.sin(t_szn)
    sin_tv = math.sin(t_vzn)
    tan_ts = math.tan(t_szn)
    tan_tv = math.tan(t_vzn)
    sec_ts = 1.0 / max(abs(cos_ts), 1e-6) * (1 if cos_ts >= 0 else -1)
    sec_tv = 1.0 / max(abs(cos_tv), 1e-6) * (1 if cos_tv >= 0 else -1)

    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    D_sq = tan_ts ** 2 + tan_tv ** 2 - 2.0 * tan_ts * tan_tv * cos_phi
    D = math.sqrt(max(D_sq, 0.0))

    inner = D ** 2 + (tan_ts * tan_tv * sin_phi) ** 2
    cos_t = _H_B * math.sqrt(max(inner, 0.0)) / max(abs(sec_ts + sec_tv), 1e-6)
    cos_t = max(-1.0, min(1.0, cos_t))
    t = math.acos(cos_t)

    overlap = (1.0 / math.pi) * (t - math.sin(t) * math.cos(t)) * (sec_ts + sec_tv)

    cos_xi_prime = cos_ts * cos_tv + sin_ts * sin_tv * cos_phi
    kgeo = overlap - sec_ts - sec_tv + 0.5 * (1.0 + cos_xi_prime) * sec_ts * sec_tv
    return kgeo


def _brdf_value(szn: float, vzn: float, phi: float,
                f_iso: float, f_vol: float, f_geo: float) -> float:
    """Evaluate the semi-empirical BRDF model for one geometry."""
    return f_iso + f_vol * _ross_thick(szn, vzn, phi) + f_geo * _li_sparse_r(szn, vzn, phi)


def _c_factor(szn: float, vzn: float, phi: float,
              f_iso: float, f_vol: float, f_geo: float) -> float:
    """
    c-factor = BRDF(nadir_geometry) / BRDF(actual_geometry).

    Returns 1.0 if the denominator is near-zero (degenerate geometry).
    Clamped to [0.5, 2.0] to prevent extreme corrections for edge cases.
    """
    num = _brdf_value(_NORM_SZN, _NORM_VZN, _NORM_PHI, f_iso, f_vol, f_geo)
    den = _brdf_value(szn, vzn, phi, f_iso, f_vol, f_geo)
    if abs(den) < 1e-9:
        return 1.0
    c = num / den
    return max(0.5, min(2.0, c))


# ── Public API ────────────────────────────────────────────────────────────────

def brdf_normalize_stack(
    stack: xr.DataArray,
    items: List[pystac.Item],
) -> xr.DataArray:
    """
    Apply per-scene, per-band BRDF c-factor normalisation to a lazy stack.

    Parameters
    ----------
    stack : xr.DataArray
        (time × band × y × x) Dask-backed float32 DataArray from
        :func:`processing.composite.build_stack`.
    items : list of pystac.Item
        Signed STAC items in the *same order* as the time axis.
        Must have ``len(items) == stack.sizes["time"]``.

    Returns
    -------
    xr.DataArray
        Same shape and chunks as input; values multiplied by band/scene
        c-factors.  SCL band is returned unchanged.
    """
    n_times = stack.sizes["time"]
    band_names: list[str] = list(stack.band.values)

    if len(items) != n_times:
        logger.warning(
            "brdf_normalize: items count (%d) ≠ time steps (%d) — skipping",
            len(items), n_times,
        )
        return stack

    # ── Extract per-scene sun / view angles ───────────────────────────
    angles: list[dict[str, float]] = []
    for item in items:
        props = item.properties

        # Sun zenith from sun_elevation (PC stores elevation, not zenith)
        sun_elev = props.get("sun_elevation") or props.get("s2:mean_solar_zenith")
        if sun_elev is None:
            szn_deg = 45.0   # safe neutral fallback
        elif "sun_elevation" in props:
            szn_deg = 90.0 - float(sun_elev)   # elevation → zenith
        else:
            szn_deg = float(sun_elev)           # already zenith (s2: property)

        sun_az = float(props.get("sun_azimuth") or props.get("s2:mean_solar_azimuth") or 135.0)

        # View zenith – many PC S-2 items don't carry this; default to nadir
        vzn_deg = float(
            props.get("view:incidence_angle")
            or props.get("s2:mean_viewing_zenith")
            or 0.0
        )
        vaz_deg = float(
            props.get("view:azimuth")
            or props.get("s2:mean_viewing_azimuth")
            or 0.0
        )

        rel_az_deg = abs(sun_az - vaz_deg)
        if rel_az_deg > 180.0:
            rel_az_deg = 360.0 - rel_az_deg

        angles.append({
            "szn": math.radians(max(0.0, min(89.9, szn_deg))),
            "vzn": math.radians(max(0.0, min(89.9, vzn_deg))),
            "phi": math.radians(rel_az_deg),
        })

    # ── Compute c-factor matrix: shape (n_times, n_bands_with_coeff) ──
    # Build a (time, band, 1, 1) multiplier array for dask broadcasting.
    c_matrix = np.ones((n_times, len(band_names), 1, 1), dtype=np.float32)

    corrected_any = False
    for t_idx, ang in enumerate(angles):
        szn, vzn, phi = ang["szn"], ang["vzn"], ang["phi"]
        for b_idx, bname in enumerate(band_names):
            if bname not in BRDF_COEFFICIENTS:
                continue  # SCL or unknown band – leave c=1
            coeffs = BRDF_COEFFICIENTS[bname]
            c = _c_factor(szn, vzn, phi, **coeffs)
            c_matrix[t_idx, b_idx, 0, 0] = c
            corrected_any = True

    if not corrected_any:
        logger.warning("brdf_normalize: no bands matched BRDF coefficient table — no correction applied")
        return stack

    # Log summary statistics
    c_flat = c_matrix[c_matrix != 1.0]
    if c_flat.size > 0:
        logger.info(
            "brdf_normalize: c-factor range [%.3f, %.3f]  mean=%.3f  "
            "(%d scenes × %d bands)",
            float(c_flat.min()), float(c_flat.max()), float(c_flat.mean()),
            n_times, sum(b in BRDF_COEFFICIENTS for b in band_names),
        )

    # ── Apply via dask broadcasting (no in-memory compute) ────────────
    import dask.array as da
    c_da = da.from_array(c_matrix, chunks=c_matrix.shape)  # single chunk, tiny

    # Preserve CRS before multiplication — xarray drops .attrs when operating
    # with a raw dask array, which causes downstream rio.crs to return None.
    original_crs = None
    try:
        original_crs = stack.rio.crs
    except Exception:
        pass
    original_attrs = dict(stack.attrs)

    normalized = stack * c_da  # broadcasts over y, x axes automatically

    # Restore spatial metadata stripped by the operation
    normalized.attrs.update(original_attrs)
    if original_crs is not None:
        normalized = normalized.rio.write_crs(original_crs)

    return normalized
