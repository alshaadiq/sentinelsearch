# SentinelSearch

A self-hosted platform for generating cloud-free Sentinel-2 multispectral composites. Draw an area of interest, pick a date range, and download a **12-band analysis GeoTIFF** ready for crop indices, vegetation mapping, and land cover analysis.

---

## Features

- **Interactive map** – draw polygon or rectangle AOI with Leaflet Draw
- **Greenest pixel composite** – NDVI-max scene selection, spectrally consistent
- **12-band COG output** – B02–B12, SCL, NDVI (DEFLATE, tiled, overviews)
- **Instant PNG preview** – 2–98% stretched RGB quicklook overlaid on map
- **Background processing** – Celery + Redis; non-blocking with live progress
- **Safety guardrails** – AOI ≤ 2 500 km², date range ≤ 6 months, ≤ 30 scenes, cloud cover < 40%

---

## Quick Start (Docker – recommended)

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL2 backend (Windows) or Docker Engine (Linux)
- Internet access for Planetary Computer STAC and Sentinel-2 data

### 1. Copy environment file

```bash
cp .env.example .env
```

The defaults work for local Docker deployment out of the box.

### 2. Build and start all services

```bash
docker compose up --build
```

First build downloads Python/Node deps; subsequent builds are cached.

### 3. Open the UI

```
http://localhost:3000
```

### 4. Verify health

```bash
curl http://localhost:8000/health
# {"api":"ok","redis":"ok"}
```

---

## Local Development (without Docker)

### Backend

```bash
# Create and activate Python 3.11 venv
python -m venv .venv
source .venv/bin/activate       # Linux/Mac
.venv\Scripts\activate          # Windows

pip install -r requirements.txt

# Copy env and adjust paths
cp .env.example .env
# Edit .env: set DATA_DIR to an absolute local path, e.g. D:/sentinelsearch/data
# Set REDIS_URL=redis://localhost:6379/0

# Start Redis (requires Docker or local Redis install)
docker run -p 6379:6379 redis:7-alpine

# Start API
uvicorn backend.main:app --reload --port 8000

# Start Celery worker (new terminal)
celery -A workers.celery_app worker --loglevel=info -Q composite
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Opens http://localhost:3000
```

---

## Project Structure

```
sentinelsearch/
├── backend/
│   ├── __init__.py
│   ├── config.py           Settings (pydantic-settings, reads .env)
│   └── main.py             FastAPI app, CORS, router wiring, static files
│
├── api/
│   ├── __init__.py
│   ├── routes_health.py    GET /health
│   ├── routes_jobs.py      POST /jobs, GET /jobs/{id}, GET /jobs/{id}/result
│   └── schemas.py          CompositeRequest, JobStatusResponse, JobResultResponse
│
├── workers/
│   ├── __init__.py
│   ├── celery_app.py       Celery factory (Redis broker + backend)
│   ├── task_state.py       Job metadata persistence (JSON files)
│   └── tasks_composite.py  Main Celery task – orchestrates full pipeline
│
├── processing/
│   ├── __init__.py
│   ├── stac_search.py      Planetary Computer STAC query + signing
│   ├── clip.py             rioxarray AOI clip helper
│   ├── composite.py        stackstac stack builder + greenest pixel algorithm
│   ├── export_cog.py       Write Cloud Optimized GeoTIFF with overviews
│   ├── export_preview.py   Write RGB PNG quicklook
│   └── storage.py          Path/URL helpers
│
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx          Root component, state management, polling
│   │   ├── api.ts           HTTP client for backend
│   │   ├── index.css        Tailwind base
│   │   └── components/
│   │       ├── MapView.tsx  Leaflet map + draw tools + preview overlay
│   │       └── JobPanel.tsx Date pickers, submit, progress bar, downloads
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   ├── nginx.conf
│   └── Dockerfile.frontend
│
├── data/                   (created at runtime, git-ignored)
│   ├── jobs/               Job metadata JSON files
│   ├── cogs/               Output GeoTIFFs
│   └── previews/           Output PNG previews
│
├── docs/
│   ├── architecture.md     System diagram + algorithm description
│   └── api.md              API reference with request/response examples
│
├── Dockerfile.backend
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## Composite Algorithm

See [docs/architecture.md](docs/architecture.md) for the full description.

**Summary**: For each pixel, the time step with the highest NDVI among cloud-free observations is selected. All output bands (B02–B12, SCL, NDVI) are then taken from that same acquisition time to preserve spectral consistency for multi-index analysis.

---

## Output COG

| Property       | Value                                  |
|----------------|----------------------------------------|
| Format         | Cloud Optimized GeoTIFF                |
| Bands          | 12 (B02–B12, SCL, NDVI)               |
| Dtype          | float32                                |
| Compression    | DEFLATE, predictor=2                   |
| Tiling         | 512 × 512 internal tiles               |
| Overviews      | 2 4 8 16 32 (average resampling)       |
| CRS            | EPSG:4326 (default) or configurable    |
| Nodata         | NaN                                    |

Open in QGIS, GDAL, or any rioxarray/rasterio pipeline.

---

## Environment Variables

| Variable              | Default                                     | Description                        |
|-----------------------|---------------------------------------------|------------------------------------|
| `STAC_API_URL`        | Planetary Computer endpoint                 | STAC catalog URL                   |
| `REDIS_URL`           | `redis://redis:6379/0`                      | Redis connection                   |
| `DATA_DIR`            | `/app/data`                                 | Storage root path                  |
| `CORS_ORIGINS`        | `http://localhost:3000`                     | Comma-separated allowed origins    |
| `MAX_SCENES`          | `30`                                        | Max scenes per job                 |
| `MAX_AOI_KM2`         | `2500`                                      | AOI area limit in km²              |
| `MAX_DATE_RANGE_DAYS` | `180`                                       | Date window limit                  |
| `CLOUD_COVER_MAX`     | `40`                                        | Max scene cloud cover %            |
| `LOG_LEVEL`           | `INFO`                                      | Python logging level               |

---

## Hardware Targets

| Resource | Recommended |
|----------|-------------|
| RAM      | 16 GB       |
| CPU      | 4+ cores    |
| Disk     | 50 GB SSD   |

Memory is controlled by: AOI limit, scene cap, Dask chunking (512×512 spatial chunks), and lazy loading. The full numpy compute happens only for the clipped AOI extent, not full Sentinel tiles.

---

## License

MIT
