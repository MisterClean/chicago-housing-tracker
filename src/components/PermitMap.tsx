import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  GeoJSON,
  MapContainer,
  TileLayer,
  ZoomControl,
  useMapEvents,
} from "react-leaflet";
import type { GeoJSON as LeafletGeoJSON, Layer, PathOptions } from "leaflet";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { basemapUrl, BASEMAP_ATTRIBUTION } from "../lib/basemap";
import { permitColor, PERMIT_NO_DATA, type WardPermits } from "../lib/permits";

type WardFeature = Feature<Geometry, { ward: number }>;
type WardCollection = FeatureCollection<Geometry, { ward: number }>;

const CHICAGO_BOUNDS: [[number, number], [number, number]] = [
  [41.644, -87.941],
  [42.023, -87.524],
];

export interface PermitViewport {
  center: [number, number];
  zoom: number;
}

interface ViewProps {
  viewport: PermitViewport | null;
  onViewportChange: (view: PermitViewport) => void;
}

/** Share geography, never ward selection: ward numbers change meaning between maps. */
function SharedViewport({ viewport, onViewportChange }: ViewProps) {
  const applying = useRef(false);
  const map = useMapEvents({
    moveend: report,
  });
  function report() {
    if (applying.current) return;
    const c = map.getCenter();
    onViewportChange({ center: [c.lat, c.lng], zoom: map.getZoom() });
  }
  useEffect(() => {
    if (!viewport) {
      applying.current = true;
      map.fitBounds(CHICAGO_BOUNDS, { padding: [12, 12], animate: false });
      applying.current = false;
      return;
    }
    const c = map.getCenter();
    if (
      map.getZoom() === viewport.zoom &&
      Math.abs(c.lat - viewport.center[0]) < 0.000001 &&
      Math.abs(c.lng - viewport.center[1]) < 0.000001
    )
      return;
    applying.current = true;
    map.setView(viewport.center, viewport.zoom, { animate: false });
    applying.current = false;
  }, [map, viewport]);
  useEffect(() => {
    const observer = new ResizeObserver(() => {
      applying.current = true;
      map.invalidateSize({ animate: false });
      if (!viewport)
        map.fitBounds(CHICAGO_BOUNDS, { padding: [12, 12], animate: false });
      applying.current = false;
    });
    observer.observe(map.getContainer());
    return () => observer.disconnect();
  }, [map, viewport]);
  return null;
}

/** Make the ward paths operable by keyboard as well as pointer/touch. */
function WardKeyboard({ layer }: { layer: LeafletGeoJSON | null }) {
  useEffect(() => {
    const cleanup: (() => void)[] = [];
    layer?.eachLayer((wardLayer) => {
      const path = (wardLayer as { getElement?: () => Element }).getElement?.();
      const feature = (wardLayer as { feature?: WardFeature }).feature;
      if (!path || !feature) return;
      path.setAttribute("tabindex", "0");
      path.setAttribute("role", "button");
      path.setAttribute("aria-label", `Ward ${feature.properties.ward} on map`);
      const keydown = (event: Event) => {
        const key = (event as KeyboardEvent).key;
        if (key === "Enter" || key === " ") {
          event.preventDefault();
          wardLayer.fire("click");
        }
      };
      path.addEventListener("keydown", keydown);
      cleanup.push(() => path.removeEventListener("keydown", keydown));
    });
    return () => cleanup.forEach((dispose) => dispose());
  }, [layer]);
  return null;
}

interface Props extends ViewProps {
  rows: WardPermits[];
  geometry?: string;
  boundarySource: string;
  selectedWard: number | null;
  hoveredWard: number | null;
  onSelectWard: (w: number | null) => void;
  onHoverWard: (w: number | null) => void;
}

export function PermitMap({
  rows,
  geometry = "chicago_wards.json",
  boundarySource,
  selectedWard,
  hoveredWard,
  onSelectWard,
  onHoverWard,
  viewport,
  onViewportChange,
}: Props) {
  const [geoData, setGeoData] = useState<WardCollection | null>(null);
  const [geoError, setGeoError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [layer, setLayer] = useState<LeafletGeoJSON | null>(null);
  const selected = useRef(selectedWard);
  useEffect(() => {
    selected.current = selectedWard;
  }, [selectedWard]);
  useEffect(() => {
    layer?.eachLayer((wardLayer) => {
      const path = (wardLayer as { getElement?: () => Element }).getElement?.();
      const feature = (wardLayer as { feature?: WardFeature }).feature;
      path?.setAttribute(
        "aria-pressed",
        String(feature?.properties.ward === selectedWard),
      );
    });
  }, [layer, selectedWard]);
  const prefersDark = useMemo(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches,
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    setGeoData(null);
    setGeoError(false);
    fetch(`${import.meta.env.BASE_URL}${geometry}`, {
      signal: controller.signal,
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((d: WardCollection) => {
        const wards = new Set(d.features?.map((f) => f.properties.ward));
        if (
          d.type !== "FeatureCollection" ||
          wards.size !== 50 ||
          Array.from({ length: 50 }, (_, i) => i + 1).some((w) => !wards.has(w))
        ) {
          throw new Error("The boundary file must contain all 50 wards.");
        }
        if (!controller.signal.aborted) setGeoData(d);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setGeoError(true);
        }
      });
    return () => controller.abort();
  }, [geometry, attempt]);

  const byWard = useMemo(() => new Map(rows.map((r) => [r.ward, r])), [rows]);
  const styleFeature = useCallback(
    (feature?: Feature): PathOptions => {
      if (!feature) return {};
      const w = (feature as WardFeature).properties.ward;
      const row = byWard.get(w);
      return {
        fillColor: row ? permitColor(row.perYear) : PERMIT_NO_DATA,
        fillOpacity: 1,
        color: w === selectedWard ? "#0a1014" : "#fff",
        weight: w === selectedWard ? 3 : w === hoveredWard ? 2 : 1,
      };
    },
    [byWard, selectedWard, hoveredWard],
  );

  const onEachFeature = (feature: Feature, wardLayer: Layer) => {
    const w = (feature as WardFeature).properties.ward;
    wardLayer.on({
      mouseover: () => onHoverWard(w),
      mouseout: () => onHoverWard(null),
      click: () => onSelectWard(selected.current === w ? null : w),
    });
  };

  return (
    <>
      {!geoData && (
        <div className="map-status" role="status">
          {geoError ? (
            <>
              Could not load ward boundaries. The ward table is still available.{" "}
              <button
                className="pm-link"
                onClick={() => setAttempt((n) => n + 1)}
              >
                Retry
              </button>
            </>
          ) : (
            "Loading ward boundaries…"
          )}
        </div>
      )}
      <MapContainer
        bounds={CHICAGO_BOUNDS}
        boundsOptions={{ padding: [12, 12] }}
        style={{ height: "100%", width: "100%", background: "var(--surface)" }}
        scrollWheelZoom={true}
        zoomControl={false}
      >
        <SharedViewport
          viewport={viewport}
          onViewportChange={onViewportChange}
        />
        <TileLayer
          url={basemapUrl(prefersDark)}
          attribution={BASEMAP_ATTRIBUTION}
        />
        <ZoomControl position="bottomright" />
        {geoData && (
          <GeoJSON
            key={geometry}
            ref={setLayer}
            data={geoData}
            attribution={`Ward boundaries: <a href="${boundarySource}">City of Chicago</a> (modified)`}
            style={styleFeature}
            onEachFeature={onEachFeature}
          />
        )}
        <WardKeyboard layer={layer} />
      </MapContainer>
    </>
  );
}
