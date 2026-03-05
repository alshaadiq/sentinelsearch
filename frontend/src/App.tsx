/**
 * App – root component.
 *
 * Layout: two-column (map left, panel right).
 * Manages all shared state: AOI, dates, job lifecycle.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { MapView } from "./components/MapView";
import { JobPanel } from "./components/JobPanel";
import {
  submitJob,
  getJobStatus,
  getJobResult,
  type GeoJSONGeometry,
  type JobStatusResponse,
  type JobResultResponse,
} from "./api";

const POLL_INTERVAL_MS = 3000;

export default function App() {
  // ── State ─────────────────────────────────────────────────────────
  const [aoi, setAoi] = useState<GeoJSONGeometry | null>(null);
  const [dateStart, setDateStart] = useState<string>(() => {
    const d = new Date();
    d.setMonth(d.getMonth() - 3);
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

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

  // ── Preview overlay props ────────────────────────────────────────
  const previewUrl = jobResult?.preview_url;
  const previewBbox = jobResult?.bbox;

  return (
    <div className="flex h-screen bg-gray-950 overflow-hidden">
      {/* Map – takes remaining width */}
      <div className="flex-1 relative">
        <MapView
          onAoiDrawn={handleAoiDrawn}
          previewUrl={previewUrl}
          previewBbox={previewBbox}
        />
      </div>

      {/* Sidebar */}
      <aside className="w-80 min-w-72 bg-gray-900 border-l border-gray-800 p-5 flex flex-col overflow-y-auto">
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
      </aside>
    </div>
  );
}
