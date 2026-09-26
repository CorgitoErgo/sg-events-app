/**
 * OneMap basemap (Singapore Land Authority): tiles and the attribution its terms require
 * (docs: onemap.gov.sg/docs/maps). Used by the Android map (lib/leaflet-map.ts).
 */

export const ONEMAP_MIN_ZOOM = 11;
export const ONEMAP_MAX_ZOOM = 19;
export const ONEMAP_LOGO = 'https://www.onemap.gov.sg/web-assets/images/logo/om_logo.png';
export const ONEMAP_ATTRIBUTION = 'OneMap © contributors | Singapore Land Authority';

export function oneMapTileUrl(dark: boolean): string {
  return `https://www.onemap.gov.sg/maps/tiles/${dark ? 'Night' : 'Default'}/{z}/{x}/{y}.png`;
}
