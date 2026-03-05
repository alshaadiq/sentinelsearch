/**
 * App – root component.
 *
 * Layout: two-column (map left, panel right).
 * Manages all shared state: AOI, dates, job lifecycle, composite layers.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { MapView } from "./components/MapView";
import { JobPanel } from "./components/JobPanel";
import { LayersPanel } from "./components/LayersPanel";
import type { CompositeLayer } from "./components/LayersPanel";
import {
  submitJob,
  getJobStatus,
  getJobResult,
  type GeoJSONGeometry,
  type JobStatusResponse,
  type JobResultResponse,
} from "./api";

const POLL_INTERVAL_MS = 3000;
const LAYERS_STORAGE_KEY = "sentinelsearch:layers";

interface FitRequest {
  bbox: [number, number, number, number];
  seq: number;
}

function loadLayers(): CompositeLayer[] {
  try {
    const raw = localStorage.getItem(LAYERS_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as CompositeLayer[]) : [];
  } catch {
    return [];
  }
}

function saveLayers(layers: CompositeLayer[]) {
  try {
    localStorage.setItem(LAYERS_STORAGE_KEY, JSON.stringify(layers));
  } catch { /* storage full – skip */ }
}

function formatDateRange(start: string, end: string): string {
  const fmt = (d: string) => {
    const [year, month] = d.split("-");
    return new Date(+year, +month - 1).toLocaleDateString("en", { month: "short", year: "numeric" });
  };
  return `${fmt(start)} – ${fmt(end)}`;
}

export default function App() {
  // ── State ─────────────────────────────────────────────────────────
  const [aoi, setAoi] = useState<GeoJSONGeometry | null>(null);
  const [dateStart, setDateStart] = useState<string>(() => {
    const d = new Date();
    d.setMonth(d.getMonth() - 2);
    return d.toISOString().split("T")[0];
  });
  const [dateEnd, setDateEnd] = useState<string>(() =>
    new Date().toISOString().split("T")[0]
  );

  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatusResponse | null>(null);
  const [jobResult, setJobResult] = useState<JobResultResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Capture date range label at submit time
  const pendingLabelRef = useRef<string>("");

  // Composite layers – persisted in localStorage
  const [layers, setLayers] = useState<CompositeLayer[]>(loadLayers);

  // Fit-to-bounds request for the map
  const [fitRequest, setFitRequest] = useState<FitRequest | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Persist layers on every change ──────────────────────────────
  useEffect(() => { saveLayers(layers); }, [layers]);

  // ── AOI drawn ────────────────────────────────────────────────────
  const handleAoiDrawn = useCallback((geom: GeoJSONGeometry) => {
    setAoi(geom);
    setError(null);
  }, []);

  // ── Submit ───────────────────────────────────────────────────────
  const handleSubmit = useCallback(async () => {
    if (!aoi || !dateStart || !dateEnd) return;

    setError(null);
    setJobResult(null);
    setJobStatus(null);
    setIsSubmitting(true);
    pendingLabelRef.current = formatDateRange(dateStart, dateEnd);

    try {
      const { job_id } = await submitJob({
        aoi,
        date_start: dateStart,
        date_end: dateEnd,
        output_crs: "EPSG:4326",
      });
      setJobId(job_id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to submit job.");
      setIsSubmitting(false);
    }
  }, [aoi, dateStart, dateEnd]);

  // ── Poll job status ──────────────────────────────────────────────
  useEffect(() => {
    if (!jobId) return;

    const poll = async () => {
      try {
        const status = await getJobStatus(jobId);
        setJobStatus(status);

        if (status.status === "succeeded") {
          clearInterval(pollRef.current!);
          setIsSubmitting(false);
          const result = await getJobResult(jobId);
          setJobResult(result);

          // Add to layers list
          const newLayer: CompositeLayer = {
            id: result.job_id,
            label: pendingLabelRef.current || result.job_id.slice(0, 8),
            previewUrl: result.preview_url,
            cogUrl: result.cog_url,
            bbox: result.bbox,
            sceneCount: result.scene_count,
            crs: result.crs,
            bands: result.bands,
            visible: true,
            opacity: 0.85,
            addedAt: Date.now(),
          };
          setLayers((prev) => {
            const withoutDup = prev.filter((l) => l.id !== newLayer.id);
            return [...withoutDup, newLayer];
          });
          setFitRequest({ bbox: result.bbox, seq: Date.now() });
        } else if (status.status === "failed") {
          clearInterval(pollRef.current!);
          setIsSubmitting(false);
          setError(status.error ?? "Job failed.");
        }
      } catch (err: unknown) {
        clearInterval(pollRef.current!);
        setIsSubmitting(false);
        setError(err instanceof Error ? err.message : "Polling error.");
      }
    };

    poll(); // immediate first poll
    pollRef.current = setInterval(poll, POLL_INTERVAL_MS);
    return () => clearInterval(pollRef.current!);
  }, [jobId]);

  // ── Layer controls ───────────────────────────────────────────────
  const handleToggleVisible = useCallback((id: string) => {
    setLayers((prev) => prev.map((l) => l.id === id ? { ...l, visible: !l.visible } : l));
  }, []);

  const handleSetOpacity = useCallback((id: string, opacity: number) => {
    setLayers((prev) => prev.map((l) => l.id === id ? { ...l, opacity } : l));
  }, []);

  const handleRemoveLayer = useCallback((id: string) => {
    setLayers((prev) => prev.filter((l) => l.id !== id));
  }, []);

  const handleFitLayer = useCallback((id: string) => {
    const layer = layers.find((l) => l.id === id);
    if (layer) setFitRequest({ bbox: layer.bbox, seq: Date.now() });
  }, [layers]);

  return (
    <div className="flex h-screen bg-gray-950 overflow-hidden">
      {/* Map – takes remaining width */}
      <div className="flex-1 relative">
        <MapView
          onAoiDrawn={handleAoiDrawn}
          layers={layers}
          fitRequest={fitRequest}
        />
      </div>

      {/* Sidebar */}
      <aside className="w-80 min-w-72 bg-gray-900 border-l border-gray-800 p-5 flex flex-col gap-4 overflow-y-auto">
        <JobPanel
          aoi={aoi}
          dateStart={dateStart}
          dateEnd={dateEnd}
          onDateStartChange={setDateStart}
          onDateEndChange={setDateEnd}
          onSubmit={handleSubmit}
          jobStatus={jobStatus}
          jobResult={jobResult}
          isSubmitting={isSubmitting}
          error={error}
        />

        <LayersPanel
          layers={layers}
          onToggleVisible={handleToggleVisible}
          onSetOpacity={handleSetOpacity}
          onRemove={handleRemoveLayer}
          onFit={handleFitLayer}
        />
      </aside>
    </div>
  );
}
