# SentinelSearch – System Architecture

## Overview

SentinelSearch is a self-hosted, cloud-free Sentinel-2 composite platform. Users draw an AOI on an interactive map, select a time window, and receive a multiband analysis GeoTIFF with a cloud-free greenest-pixel composite. All heavy processing runs asynchronously via Celery workers.

---

## Component Diagram

```
┌────────────────────────────────────────────────────────────────┐
│                         Browser                                │
│                                                                │
│  React + Leaflet + Tailwind CSS                                │
│  ┌──────────────┐   ┌──────────────────────────────────────┐  │
│  │  MapView     │   │  JobPanel                            │  │
│  │  (Leaflet)   │   │  date pickers / submit / status /    │  │
│  │  Draw AOI    │   │  progress bar / download links       │  │
│  └──────┬───────┘   └───────────────────────┬──────────────┘  │
│         │ GeoJSON geometry                  │ HTTP REST       │
└─────────┼─────────────────────────────────  ┼ ────────────────┘
          │                                   │
          ▼                                   ▼
┌─────────────────────────────────────────────────────────────┐
│                     Nginx (port 3000)                        │
│   Serves React SPA   │   /api/* → proxy → API :8000         │
└──────────────────────┼──────────────────────────────────────┘
                        │
          ┌─────────────▼──────────────────────────┐
          │        FastAPI  (port 8000)             │
          │                                        │
          │  POST /jobs       → enqueue Celery      │
          │  GET  /jobs/{id}  → read JSON meta      │
          │  GET  /jobs/{id}/result                 │
          │  GET  /health                           │
          │  GET  /data/*     → static COG/PNG      │
          └─────────────┬──────────────────────────┘
                        │ Celery task
                        ▼
          ┌─────────────────────────────────────────┐
          │        Redis  (port 6379)                │
          │  broker + result backend                 │
          └──────────────────────────────────────────┘
                        │ dequeue
                        ▼
          ┌─────────────────────────────────────────────────────┐
          │          Celery Worker  (1-N instances)              │
          │                                                     │
          │  tasks_composite.run_composite(job_id)              │
          │                                                     │
          │  ┌────────────────────────────────────────────┐    │
          │  │  Processing Pipeline                        │    │
          │  │                                             │    │
          │  │  stac_search  →  Planetary Computer STAC   │    │
          │  │  build_stack  →  stackstac (lazy Dask)     │    │
          │  │  clip         →  rioxarray AOI crop        │    │
          │  │  composite    →  cloud mask + NDVI argmax  │    │
          │  │  export_cog   →  COG (DEFLATE, tiled)      │    │
          │  │  export_prev  →  PNG quicklook             │    │
          │  └────────────────────────────────────────────┘    │
          │                              │                      │
          │                    writes to /data/                 │
          └──────────────────────────────────────────────────── ┘
                                │
          ┌─────────────────────▼──────────────────────────┐
          │   Local filesystem  /app/data/                  │
          │                                                  │
          │   jobs/      {job_id}.json   (metadata/state)   │
          │   cogs/      {job_id}.tif    (COG output)       │
          │   previews/  {job_id}.png    (RGB quicklook)    │
          └──────────────────────────────────────────────────┘
```

---

## Greenest Pixel Composite Algorithm

```
For each pixel (y, x):

  1.  Load time series of all analysis bands + SCL
      t ∈ [t_0, t_1, … t_n]  →  lazy 4-D stack (time × band × y × x)

  2.  Cloud mask per time step:
      clear[t] = SCL[t] ∉ {3, 8, 9, 10}

  3.  Compute NDVI for clear pixels only:
      NDVI[t] = (B08[t] - B04[t]) / (B08[t] + B04[t])
      NDVI[t] = NaN  if !clear[t]

  4.  Select optimal acquisition time:
      t* = argmax_t( NDVI[t] )
      t* = NaN if all observations are cloudy

  5.  Extract ALL bands at t* (spectral consistency):
      output[band] = stack[t*, band, y, x]  ∀ band

  6.  Add derived NDVI output band from selected pixels:
      NDVI_out = (B08_out - B04_out) / (B08_out + B04_out)
```

**Key property**: All output bands use the same acquisition time per pixel, preserving spectral relationships needed for multi-index crop analysis.

---

## Output COG Band Order

| Index | Name | Description                  | Use                         |
|-------|------|------------------------------|-----------------------------|
| 1     | B02  | Blue 490 nm                  | True colour, LAI            |
| 2     | B03  | Green 560 nm                 | True colour, NDWI           |
| 3     | B04  | Red 665 nm                   | True colour, NDVI           |
| 4     | B05  | Red Edge 1 705 nm            | Chlorophyll, RedEdge NDVI   |
| 5     | B06  | Red Edge 2 740 nm            | Canopy chlorophyll           |
| 6     | B07  | Red Edge 3 783 nm            | Red Edge NDVI               |
| 7     | B08  | NIR 842 nm                   | NDVI, EVI, LAI              |
| 8     | B8A  | Narrow NIR 865 nm            | NDVI, moisture              |
| 9     | B11  | SWIR 1 1610 nm               | Moisture index, NDWI        |
| 10    | B12  | SWIR 2 2190 nm               | Burn area, clay/soil        |
| 11    | SCL  | Scene Classification Layer   | QA / validation             |
| 12    | NDVI | Derived composite NDVI       | Vegetation density          |

### Common Crop Indices Computable from Output

| Index   | Formula                        | Bands Used  |
|---------|--------------------------------|-------------|
| NDVI    | (B08−B04)/(B08+B04)            | 7, 3        |
| EVI     | 2.5*(B08−B04)/(B08+6*B04−7.5*B02+1) | 7,3,1 |
| NDWI    | (B03−B08)/(B03+B08)            | 2, 7        |
| NDRE    | (B08−B05)/(B08+B05)            | 7, 4        |
| SAVI    | 1.5*(B08−B04)/(B08+B04+0.5)    | 7, 3        |
| SWIR    | (B08−B11)/(B08+B11)            | 7, 9        |

---

## Deployment Architecture

```
Docker Compose (Linux)
├── redis      redis:7-alpine
├── api        Python 3.11 + FastAPI + Uvicorn (2 workers)
├── worker     Python 3.11 + Celery (2 concurrency)
└── frontend   Node build → Nginx
```
