/**
 * JobPanel – right sidebar with date pickers, submit button,
 * job status tracking, progress bar, and download links.
 */
import React from "react";
import type {
  BandInfo,
  GeoJSONGeometry,
  JobResultResponse,
  JobStatus,
  JobStatusResponse,
} from "../api";

interface JobPanelProps {
  aoi: GeoJSONGeometry | null;
  dateStart: string;
  dateEnd: string;
  onDateStartChange: (v: string) => void;
  onDateEndChange: (v: string) => void;
  onSubmit: () => void;
  jobStatus: JobStatusResponse | null;
  jobResult: JobResultResponse | null;
  isSubmitting: boolean;
  error: string | null;
}

const STATUS_COLOR: Record<JobStatus, string> = {
  queued: "bg-yellow-500",
  running: "bg-blue-500",
  succeeded: "bg-green-500",
  failed: "bg-red-500",
};

const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  loading: "Loading parameters",
  stac_search: "Searching scenes",
  build_stack: "Building raster stack",
  composite: "Computing composite",
  export_cog: "Exporting COG",
  preview: "Generating preview",
  done: "Complete",
  failed: "Failed",
};

export const JobPanel: React.FC<JobPanelProps> = ({
  aoi,
  dateStart,
  dateEnd,
  onDateStartChange,
  onDateEndChange,
  onSubmit,
  jobStatus,
  jobResult,
  isSubmitting,
  error,
}) => {
  const canSubmit = !!aoi && !!dateStart && !!dateEnd && !isSubmitting;

  return (
    <div className="flex flex-col gap-5 h-full overflow-y-auto">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-sentinel-400 tracking-tight">
          SentinelSearch
        </h1>
        <p className="text-xs text-gray-400 mt-0.5">
          Cloud-free Sentinel-2 composites
        </p>
      </div>

      {/* AOI status */}
      <div className="rounded-lg bg-gray-800 p-3 text-sm">
        <p className="text-gray-400 mb-1 font-medium">Area of Interest</p>
        {aoi ? (
          <p className="text-sentinel-300 flex items-center gap-1">
            <span className="text-sentinel-400">✓</span>
            {aoi.type} drawn
          </p>
        ) : (
          <p className="text-gray-500 italic">
            Use the draw tools on the map to define an AOI (polygon or rectangle).
          </p>
        )}
      </div>

      {/* Date range */}
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-400 font-medium">Start date</span>
          <input
            type="date"
            value={dateStart}
            onChange={(e) => onDateStartChange(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-100 focus:outline-none focus:ring-2 focus:ring-sentinel-500"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-400 font-medium">End date</span>
          <input
            type="date"
            value={dateEnd}
            onChange={(e) => onDateEndChange(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-100 focus:outline-none focus:ring-2 focus:ring-sentinel-500"
          />
        </label>
      </div>

      {/* Submit */}
      <button
        onClick={onSubmit}
        disabled={!canSubmit}
        className="w-full py-2.5 rounded-lg font-semibold text-sm transition-colors
          bg-sentinel-600 hover:bg-sentinel-500 text-white
          disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {isSubmitting ? "Processing…" : "Generate Composite"}
      </button>

      {/* Error message */}
      {error && (
        <div className="rounded-lg bg-red-900/40 border border-red-700 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Job status */}
      {jobStatus && (
        <div className="rounded-lg bg-gray-800 p-3 text-sm space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-gray-400 font-medium">Job status</span>
            <span
              className={`px-2 py-0.5 rounded text-xs font-semibold text-white ${STATUS_COLOR[jobStatus.status]}`}
            >
              {jobStatus.status.toUpperCase()}
            </span>
          </div>

          {/* Progress bar */}
          <div className="w-full bg-gray-700 rounded-full h-2">
            <div
              className="bg-sentinel-500 h-2 rounded-full transition-all duration-500"
              style={{ width: `${jobStatus.progress.pct}%` }}
            />
          </div>

          <p className="text-gray-400 text-xs">
            {STAGE_LABELS[jobStatus.progress.stage] ?? jobStatus.progress.stage}
            {jobStatus.progress.message ? ` – ${jobStatus.progress.message}` : ""}
          </p>

          {jobStatus.error && (
            <p className="text-red-400 text-xs mt-1">{jobStatus.error}</p>
          )}

          <p className="text-gray-600 text-xs break-all">ID: {jobStatus.job_id}</p>
        </div>
      )}

      {/* Result */}
      {jobResult && (
        <div className="rounded-lg bg-gray-800 p-3 text-sm space-y-3">
          <p className="text-sentinel-400 font-medium">✓ Composite ready</p>

          <div className="text-xs text-gray-400 space-y-1">
            <p>Scenes used: <span className="text-gray-200">{jobResult.scene_count}</span></p>
            <p>CRS: <span className="text-gray-200">{jobResult.crs}</span></p>
          </div>

          {/* Band list */}
          <div>
            <p className="text-gray-500 text-xs font-medium mb-1">Output bands</p>
            <div className="flex flex-wrap gap-1">
              {jobResult.bands.map((b: BandInfo) => (
                <span
                  key={b.name}
                  title={b.description}
                  className="bg-gray-700 text-gray-300 text-xs px-1.5 py-0.5 rounded"
                >
                  {b.name}
                </span>
              ))}
            </div>
          </div>

          {/* Download links */}
          <div className="flex flex-col gap-2">
            <a
              href={jobResult.cog_url}
              download
              className="block w-full text-center py-2 rounded bg-sentinel-700 hover:bg-sentinel-600
                text-white text-xs font-semibold transition-colors"
            >
              ↓ Download COG (.tif)
            </a>
            <a
              href={jobResult.preview_url}
              target="_blank"
              rel="noopener noreferrer"
              className="block w-full text-center py-2 rounded bg-gray-700 hover:bg-gray-600
                text-gray-200 text-xs font-semibold transition-colors"
            >
              ↗ Open Preview (PNG)
            </a>
          </div>
        </div>
      )}

      {/* Instructions */}
      <div className="mt-auto text-xs text-gray-600 space-y-1 pt-2 border-t border-gray-800">
        <p>1. Draw polygon or rectangle on map</p>
        <p>2. Select date range (max 6 months)</p>
        <p>3. Click Generate Composite</p>
        <p>4. Wait for processing to complete</p>
        <p>5. Download COG or view preview</p>
      </div>
    </div>
  );
};
