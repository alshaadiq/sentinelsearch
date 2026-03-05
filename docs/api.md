# SentinelSearch – API Reference

Base URL (local): `http://localhost:8000`

---

## POST /jobs

Submit a new composite job.

### Request body

```json
{
  "aoi": {
    "type": "Polygon",
    "coordinates": [
      [
        [12.3, 41.8],
        [12.7, 41.8],
        [12.7, 42.1],
        [12.3, 42.1],
        [12.3, 41.8]
      ]
    ]
  },
  "date_start": "2024-04-01",
  "date_end": "2024-09-30",
  "output_crs": "EPSG:4326"
}
```

### Response `202 Accepted`

```json
{
  "job_id": "3f7e2d1a-bc34-4a1c-9d0e-5f8a2b6c7e91"
}
```

### Validation errors `422`

| Condition                        | Message                              |
|---------------------------------|--------------------------------------|
| AOI > 2500 km²                  | AOI area … exceeds limit of 2500 km² |
| Date range > 180 days           | Date range … exceeds limit …         |
| AOI type not Polygon/MultiPolygon | geometry type must be one of …      |
| date_end ≤ date_start           | date_end must be after date_start     |

---

## GET /jobs/{job_id}

Poll job status and progress.

### Response `200 OK`

```json
{
  "job_id": "3f7e2d1a-bc34-4a1c-9d0e-5f8a2b6c7e91",
  "status": "running",
  "progress": {
    "stage": "composite",
    "pct": 40,
    "message": "Applying cloud mask and computing composite"
  },
  "created_at": "2024-05-15T08:30:00Z",
  "updated_at": "2024-05-15T08:31:22Z",
  "error": null
}
```

### Job status lifecycle

```
queued  →  running  →  succeeded
                    →  failed
```

| Status    | pct range | Description                    |
|-----------|-----------|--------------------------------|
| queued    | 0         | In Redis queue, not started    |
| running   | 5–95      | Actively processing            |
| succeeded | 100       | Composite ready for download   |
| failed    | 0         | Terminal error; check `error`  |

---

## GET /jobs/{job_id}/result

Fetch download URLs and metadata (only available when `status == succeeded`).

### Response `200 OK`

```json
{
  "job_id": "3f7e2d1a-bc34-4a1c-9d0e-5f8a2b6c7e91",
  "cog_url": "/data/cogs/3f7e2d1a-bc34-4a1c-9d0e-5f8a2b6c7e91.tif",
  "preview_url": "/data/previews/3f7e2d1a-bc34-4a1c-9d0e-5f8a2b6c7e91.png",
  "bands": [
    { "index": 1, "name": "B02", "description": "Blue (490 nm)" },
    { "index": 2, "name": "B03", "description": "Green (560 nm)" },
    { "index": 3, "name": "B04", "description": "Red (665 nm)" },
    { "index": 4, "name": "B05", "description": "Red Edge 1 (705 nm)" },
    { "index": 5, "name": "B06", "description": "Red Edge 2 (740 nm)" },
    { "index": 6, "name": "B07", "description": "Red Edge 3 (783 nm)" },
    { "index": 7, "name": "B08", "description": "NIR (842 nm)" },
    { "index": 8, "name": "B8A", "description": "Narrow NIR (865 nm)" },
    { "index": 9, "name": "B11", "description": "SWIR 1 (1610 nm)" },
    { "index": 10, "name": "B12", "description": "SWIR 2 (2190 nm)" },
    { "index": 11, "name": "SCL", "description": "Scene Classification Layer" },
    { "index": 12, "name": "NDVI", "description": "Normalized Difference Vegetation Index" }
  ],
  "scene_count": 14,
  "crs": "EPSG:4326",
  "bbox": [12.3, 41.8, 12.7, 42.1]
}
```

### Error `409`

Returned if job is not yet succeeded.  
```json
{ "detail": "Job is not yet succeeded (current status: running)." }
```

---

## GET /health

Check API and Redis connectivity.

### Response `200 OK` (all healthy)

```json
{ "api": "ok", "redis": "ok" }
```

### Response `503` (Redis unavailable)

```json
{ "api": "ok", "redis": "error: Connection refused" }
```

---

## Static result files

Once a job succeeds, results are served directly:

| URL                             | Content              |
|---------------------------------|----------------------|
| `/data/cogs/{job_id}.tif`       | Cloud Optimised GeoTIFF (12 bands, DEFLATE) |
| `/data/previews/{job_id}.png`   | RGB quicklook PNG    |

COG can be opened directly in QGIS, GDAL, or any rasterio client.
