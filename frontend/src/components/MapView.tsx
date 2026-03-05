/**
 * MapView – interactive Leaflet map with draw tools for AOI selection
 * and result overlay once a composite is ready.
 */
import React, { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet-draw";
import type { GeoJSONGeometry } from "../api";

interface MapViewProps {
  onAoiDrawn: (geom: GeoJSONGeometry) => void;
  previewUrl?: string;
  previewBbox?: [number, number, number, number]; // [west, south, east, north]
}

export const MapView: React.FC<MapViewProps> = ({ onAoiDrawn, previewUrl, previewBbox }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const drawnItemsRef = useRef<L.FeatureGroup>(new L.FeatureGroup());
  const overlayRef = useRef<L.ImageOverlay | null>(null);

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
      const layers = drawnItemsRef.current.getLayers();
      if (layers.length > 0) {
        const geom = (layers[0] as L.Polygon).toGeoJSON().geometry as GeoJSONGeometry;
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

  // ── Show preview overlay when result arrives ───────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (overlayRef.current) {
      map.removeLayer(overlayRef.current);
      overlayRef.current = null;
    }

    if (previewUrl && previewBbox) {
      const [west, south, east, north] = previewBbox;
      const bounds: L.LatLngBoundsExpression = [[south, west], [north, east]];
      const overlay = L.imageOverlay(previewUrl, bounds, { opacity: 0.85 });
      overlay.addTo(map);
      overlayRef.current = overlay;
      map.fitBounds(bounds, { padding: [20, 20] });
    }
  }, [previewUrl, previewBbox]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full rounded-lg overflow-hidden border border-gray-700"
      style={{ minHeight: "400px" }}
    />
  );
};
