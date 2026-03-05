/**
 * MapView – interactive Leaflet map with draw tools for AOI selection
 * and result overlays for composites.
 */
import React, { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet-draw";
import type { GeoJSONGeometry } from "../api";
import type { CompositeLayer } from "./LayersPanel";

interface FitRequest {
  bbox: [number, number, number, number];
  seq: number; // incrementing so same bbox re-triggers
}

interface MapViewProps {
  onAoiDrawn: (geom: GeoJSONGeometry) => void;
  layers: CompositeLayer[];
  fitRequest?: FitRequest | null;
}

export const MapView: React.FC<MapViewProps> = ({ onAoiDrawn, layers, fitRequest }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const drawnItemsRef = useRef<L.FeatureGroup>(new L.FeatureGroup());
  // Track Leaflet overlays by layer id
  const overlaysRef = useRef<Map<string, L.ImageOverlay>>(new Map());

  // ── Initialize map ──────────────────────────────────────────────────
  useEffect(() => {
    if (mapRef.current || !containerRef.current) return;

    const map = L.map(containerRef.current, {
      center: [20, 0],
      zoom: 3,
      zoomControl: true,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors",
      maxZoom: 19,
    }).addTo(map);

    // Add satellite layer toggle
    const satellite = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "© Esri", maxZoom: 19 }
    );

    const baseLayers = {
      "OpenStreetMap": L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap contributors",
        maxZoom: 19,
      }),
      "Satellite": satellite,
    };
    L.control.layers(baseLayers).addTo(map);

    drawnItemsRef.current.addTo(map);

    // ── Draw control ─────────────────────────────────────────────────
    const drawControl = new (L.Control as any).Draw({
      edit: { featureGroup: drawnItemsRef.current },
      draw: {
        polygon: {
          allowIntersection: false,
          showArea: true,
          shapeOptions: { color: "#22c55e", weight: 2, fillOpacity: 0.15 },
        },
        rectangle: {
          shapeOptions: { color: "#22c55e", weight: 2, fillOpacity: 0.15 },
        },
        polyline: false,
        circle: false,
        circlemarker: false,
        marker: false,
      },
    });
    map.addControl(drawControl);

    // ── Handle draw events ───────────────────────────────────────────
    map.on(L.Draw.Event.CREATED, (e: any) => {
      drawnItemsRef.current.clearLayers();
      drawnItemsRef.current.addLayer(e.layer);
      const geom = e.layer.toGeoJSON().geometry as GeoJSONGeometry;
      onAoiDrawn(geom);
    });

    map.on(L.Draw.Event.EDITED, () => {
      const ls = drawnItemsRef.current.getLayers();
      if (ls.length > 0) {
        const geom = (ls[0] as L.Polygon).toGeoJSON().geometry as GeoJSONGeometry;
        onAoiDrawn(geom);
      }
    });

    map.on(L.Draw.Event.DELETED, () => {
      onAoiDrawn(null as any);
    });

    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Sync overlays whenever layers array changes ───────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const current = overlaysRef.current;
    const newIds = new Set(layers.map((l) => l.id));

    // Remove overlays for layers that no longer exist
    current.forEach((overlay, id) => {
      if (!newIds.has(id)) {
        map.removeLayer(overlay);
        current.delete(id);
      }
    });

    // Add / update overlays for current layers
    layers.forEach((layer) => {
      const [west, south, east, north] = layer.bbox;
      const bounds: L.LatLngBoundsExpression = [[south, west], [north, east]];

      const existing = current.get(layer.id);
      if (!existing) {
        // Create new overlay
        const overlay = L.imageOverlay(layer.previewUrl, bounds, {
          opacity: layer.visible ? layer.opacity : 0,
          interactive: false,
        });
        overlay.addTo(map);
        current.set(layer.id, overlay);
      } else {
        // Update opacity (handles visibility toggle + slider)
        existing.setOpacity(layer.visible ? layer.opacity : 0);
      }
    });
  }, [layers]);

  // ── Fit bounds on demand ─────────────────────────────────────────
  useEffect(() => {
    if (!fitRequest || !mapRef.current) return;
    const [west, south, east, north] = fitRequest.bbox;
    mapRef.current.fitBounds([[south, west], [north, east]], { padding: [30, 30] });
  }, [fitRequest?.seq]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={containerRef}
      className="w-full h-full rounded-lg overflow-hidden border border-gray-700"
      style={{ minHeight: "400px" }}
    />
  );
};
