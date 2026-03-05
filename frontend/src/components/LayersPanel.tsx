/**
 * LayersPanel – shows all completed composite layers with visibility toggles,
 * zoom-to, opacity slider, and remove controls.
 */
import React, { useState } from "react";
import type { BandInfo } from "../api";

export interface CompositeLayer {
    id: string;          // job_id
    label: string;       // human-readable, e.g. "Jun 2024 – Aug 2024"
    previewUrl: string;
    cogUrl: string;
    bbox: [number, number, number, number]; // [west, south, east, north] WGS-84
    sceneCount: number;
    crs: string;
    bands: BandInfo[];
    visible: boolean;
    opacity: number;     // 0–1
    addedAt: number;     // Date.now()
}

interface LayersPanelProps {
    layers: CompositeLayer[];
    onToggleVisible: (id: string) => void;
    onSetOpacity: (id: string, opacity: number) => void;
    onRemove: (id: string) => void;
    onFit: (id: string) => void;
}

export const LayersPanel: React.FC<LayersPanelProps> = ({
    layers,
    onToggleVisible,
    onSetOpacity,
    onRemove,
    onFit,
}) => {
    const [expandedId, setExpandedId] = useState<string | null>(null);

    return (
        <div className="rounded-lg bg-gray-800 text-sm overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700">
                <span className="font-medium text-gray-300 flex items-center gap-1.5">
                    <svg className="w-3.5 h-3.5 text-sentinel-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
                    </svg>
                    Processed layers
                </span>
                <span className="text-xs text-gray-500 bg-gray-700 rounded-full px-1.5 py-0.5">
                    {layers.length}
                </span>
            </div>

            {/* Empty state */}
            {layers.length === 0 && (
                <p className="px-3 py-4 text-xs text-gray-500 text-center">
                    No layers yet — submit a job to generate a composite.
                </p>
            )}

            {/* Layer list (newest first) */}
            {layers.length > 0 && <ul className="divide-y divide-gray-700/50 max-h-80 overflow-y-auto">
                {[...layers].reverse().map((layer) => {
                    const isExpanded = expandedId === layer.id;
                    return (
                        <li key={layer.id} className="flex flex-col">
                            {/* Main row */}
                            <div className="flex items-center gap-2 px-2.5 py-2">
                                {/* Visibility eye toggle */}
                                <button
                                    onClick={() => onToggleVisible(layer.id)}
                                    title={layer.visible ? "Hide layer" : "Show layer"}
                                    className={`flex-shrink-0 w-6 h-6 rounded flex items-center justify-center transition-colors text-xs ${layer.visible
                                        ? "text-sentinel-400 bg-sentinel-900/50 hover:bg-sentinel-900"
                                        : "text-gray-600 bg-gray-700/60 hover:bg-gray-700"
                                        }`}
                                >
                                    {layer.visible ? (
                                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                                        </svg>
                                    ) : (
                                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                                        </svg>
                                    )}
                                </button>

                                {/* Label — click to zoom */}
                                <button
                                    onClick={() => onFit(layer.id)}
                                    title="Zoom to layer"
                                    className={`flex-1 text-left text-xs truncate transition-colors ${layer.visible ? "text-gray-200 hover:text-sentinel-300" : "text-gray-500 hover:text-gray-400"
                                        }`}
                                >
                                    {layer.label}
                                </button>

                                {/* Expand / collapse details */}
                                <button
                                    onClick={() => setExpandedId(isExpanded ? null : layer.id)}
                                    title="Details"
                                    className="flex-shrink-0 w-5 h-5 rounded text-gray-600 hover:text-gray-400 hover:bg-gray-700 flex items-center justify-center transition-colors"
                                >
                                    <svg
                                        className={`w-3 h-3 transition-transform ${isExpanded ? "rotate-180" : ""}`}
                                        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
                                    >
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                                    </svg>
                                </button>

                                {/* Remove */}
                                <button
                                    onClick={() => {
                                        if (expandedId === layer.id) setExpandedId(null);
                                        onRemove(layer.id);
                                    }}
                                    title="Remove layer"
                                    className="flex-shrink-0 w-5 h-5 rounded text-gray-600 hover:text-red-400 hover:bg-gray-700 flex items-center justify-center transition-colors"
                                >
                                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                                    </svg>
                                </button>
                            </div>

                            {/* Expanded details */}
                            {isExpanded && (
                                <div className="px-3 pb-3 pt-0 space-y-2.5 bg-gray-900/50">
                                    {/* Opacity slider */}
                                    <div className="flex items-center gap-2">
                                        <span className="text-gray-500 text-xs w-12">Opacity</span>
                                        <input
                                            type="range"
                                            min={0}
                                            max={1}
                                            step={0.05}
                                            value={layer.opacity}
                                            onChange={(e) => onSetOpacity(layer.id, parseFloat(e.target.value))}
                                            className="flex-1 accent-sentinel-500 h-1"
                                        />
                                        <span className="text-gray-400 text-xs w-8 text-right">
                                            {Math.round(layer.opacity * 100)}%
                                        </span>
                                    </div>

                                    {/* Metadata */}
                                    <div className="text-xs text-gray-500 space-y-1">
                                        <p>
                                            <span className="text-gray-600">Scenes: </span>
                                            <span className="text-gray-400">{layer.sceneCount}</span>
                                        </p>
                                        <p>
                                            <span className="text-gray-600">CRS: </span>
                                            <span className="text-gray-400">{layer.crs}</span>
                                        </p>
                                        <p>
                                            <span className="text-gray-600">Bands: </span>
                                            <span className="text-gray-400">{layer.bands.map((b) => b.name).join(", ")}</span>
                                        </p>
                                    </div>

                                    {/* Bands grid */}
                                    <div className="flex flex-wrap gap-1">
                                        {layer.bands.map((b) => (
                                            <span
                                                key={b.name}
                                                title={b.description}
                                                className="bg-gray-700 text-gray-400 text-xs px-1.5 py-0.5 rounded"
                                            >
                                                {b.name}
                                            </span>
                                        ))}
                                    </div>

                                    {/* Download */}
                                    <a
                                        href={layer.cogUrl}
                                        download
                                        className="block text-center py-1.5 rounded bg-sentinel-700/60 hover:bg-sentinel-700 text-white text-xs font-medium transition-colors"
                                    >
                                        ↓ Download COG (.tif)
                                    </a>
                                </div>
                            )}
                        </li>
                    );
                })}
            </ul>}
        </div>
    );
};
