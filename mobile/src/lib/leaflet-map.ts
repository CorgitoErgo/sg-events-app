/**
 * The Android map: Leaflet + OneMap tiles in a WebView. Expo Go for Android ships a Google
 * Maps key that Google now rejects ("Authorization failure"), so react-native-maps draws a
 * black map there, tile overlays included (github.com/expo/expo/issues/49323). A WebView needs
 * no Google key. The page gets event data only as escaped JSON, never as HTML.
 */

import type { Event } from '@/api/schemas';
import { ONEMAP_MAX_ZOOM, ONEMAP_MIN_ZOOM, oneMapTileUrl } from '@/lib/onemap';

// Leaflet from jsDelivr, pinned with SRI hashes (checked against the files 2026-09-26).
const LEAFLET_CSS = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css';
const LEAFLET_CSS_SRI = 'sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';
const LEAFLET_JS = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js';
const LEAFLET_JS_SRI = 'sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=';

// OneMap tiles only cover Singapore (onemap.gov.sg/docs/maps).
export const ONEMAP_BOUNDS: [[number, number], [number, number]] = [
  [1.144, 103.535],
  [1.494, 104.502],
];

export type MapPoint = { id: number; lat: number; lng: number };
export type MapMessage =
  | { type: 'ready' }
  | { type: 'select'; id: number }
  | { type: 'clear' }
  | { type: 'error'; message: string };

const UNSAFE_IN_SCRIPT = new RegExp('[<>&\\u2028\\u2029]', 'g');

/** JSON that is safe inside <script> and inside injected JS (no </script>, no line separators). */
export function safeJson(value: unknown): string {
  return JSON.stringify(value).replace(
    UNSAFE_IN_SCRIPT,
    (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'),
  );
}

export function mapPoints(events: Event[]): MapPoint[] {
  return events.flatMap((e) => (e.location ? [{ id: e.id, lat: e.location.lat, lng: e.location.lng }] : []));
}

export function parseMapMessage(data: string): MapMessage | null {
  let msg: unknown;
  try {
    msg = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof msg !== 'object' || msg === null) return null;
  const m = msg as Record<string, unknown>;
  switch (m.type) {
    case 'ready':
    case 'clear':
      return { type: m.type };
    case 'select':
      return Number.isInteger(m.id) ? { type: 'select', id: m.id as number } : null;
    case 'error':
      return { type: 'error', message: String(m.message ?? '') };
    default:
      return null;
  }
}

type MapHtmlOptions = {
  center: { lat: number; lng: number };
  zoom: number;
  dark: boolean;
  userLocation: { lat: number; lng: number } | null;
};

export function mapHtml({ center, zoom, dark, userLocation }: MapHtmlOptions): string {
  const config = safeJson({
    center: [center.lat, center.lng],
    zoom,
    minZoom: ONEMAP_MIN_ZOOM,
    maxZoom: ONEMAP_MAX_ZOOM,
    bounds: ONEMAP_BOUNDS,
    tiles: oneMapTileUrl(dark),
    user: userLocation ? [userLocation.lat, userLocation.lng] : null,
    background: dark ? '#1b1f24' : '#e8eef2',
  });
  return `<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<link rel="stylesheet" href="${LEAFLET_CSS}" integrity="${LEAFLET_CSS_SRI}" crossorigin="">
<style>html,body,#map{margin:0;height:100%;width:100%}</style>
</head><body><div id="map"></div>
<script src="${LEAFLET_JS}" integrity="${LEAFLET_JS_SRI}" crossorigin=""></script>
<script>
(function () {
  var cfg = ${config};
  function post(msg) { window.ReactNativeWebView.postMessage(JSON.stringify(msg)); }
  if (typeof L === 'undefined') { post({ type: 'error', message: 'map library did not load' }); return; }
  document.getElementById('map').style.background = cfg.background;
  var map = L.map('map', {
    center: cfg.center, zoom: cfg.zoom, minZoom: cfg.minZoom, maxZoom: cfg.maxZoom,
    maxBounds: cfg.bounds, maxBoundsViscosity: 1, attributionControl: false, zoomControl: false
  });
  L.tileLayer(cfg.tiles, { minZoom: cfg.minZoom, maxZoom: cfg.maxZoom, detectRetina: true }).addTo(map);
  if (cfg.user) {
    L.circleMarker(cfg.user, { radius: 7, color: '#fff', weight: 2, fillColor: '#1a73e8', fillOpacity: 1, interactive: false }).addTo(map);
  }
  var layer = L.layerGroup().addTo(map), markers = {}, selected = null;
  var normal = { radius: 8, color: '#fff', weight: 2, fillColor: '#d93025', fillOpacity: 0.95 };
  var highlight = { radius: 11, color: '#fff', weight: 3, fillColor: '#b31412', fillOpacity: 1 };
  window.setMarkers = function (points) {
    layer.clearLayers(); markers = {};
    points.forEach(function (p) {
      var m = L.circleMarker([p.lat, p.lng], normal).addTo(layer);
      m.on('click', function (e) { L.DomEvent.stopPropagation(e); post({ type: 'select', id: p.id }); });
      markers[p.id] = m;
    });
    window.select(selected);
  };
  window.select = function (id) {
    if (selected !== null && markers[selected]) markers[selected].setStyle(normal).setRadius(normal.radius);
    selected = id;
    if (id !== null && markers[id]) markers[id].setStyle(highlight).setRadius(highlight.radius).bringToFront();
  };
  map.on('click', function () { post({ type: 'clear' }); });
  post({ type: 'ready' });
})();
</script>
</body></html>`;
}
