/**
 * API client for SentinelSearch backend.
 */

const BASE = "/jobs";

export interface GeoJSONGeometry {
  type: "Polygon" | "MultiPolygon";
  coordinates: number[][][] | number[][][][];
}

export type CompositeMethod = "greenest_pixel" | "cloud_patching";

export interface CompositeRequest {
  aoi: GeoJSONGeometry;
  date_start: string; // YYYY-MM-DD
  date_end: string;   // YYYY-MM-DD
  output_crs?: string;
  method?: CompositeMethod;
}

export interface JobProgress {
  stage: string;
  pct: number;
  message: string;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface JobStatusResponse {
  job_id: string;
  status: JobStatus;
  progress: JobProgress;
  created_at: string;
  updated_at: string;
  error?: string;
}

export interface BandInfo {
  index: number;
  name: string;
  description: string;
}

export interface JobResultResponse {
  job_id: string;
  cog_url: string;
  preview_url: string;
  bands: BandInfo[];
  scene_count: number;
  crs: string;
  bbox: [number, number, number, number];
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`HTTP ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export async function submitJob(req: CompositeRequest): Promise<{ job_id: string }> {
  return jsonFetch<{ job_id: string }>(BASE, {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  return jsonFetch<JobStatusResponse>(`${BASE}/${jobId}`);
}

export async function getJobResult(jobId: string): Promise<JobResultResponse> {
  return jsonFetch<JobResultResponse>(`${BASE}/${jobId}/result`);
}
