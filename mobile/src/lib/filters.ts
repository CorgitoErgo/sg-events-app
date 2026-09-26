/** Discover filters: chip state -> GET /events params, labels, and empty-state suggestions (pure; unit-tested). */

export type When = 'today' | 'weekend' | 'week' | 'month';
export type RadiusKm = 1 | 3 | 5 | 10 | null; // null = anywhere in Singapore

export const WHEN_OPTIONS: When[] = ['today', 'weekend', 'week', 'month'];
export const RADIUS_OPTIONS: RadiusKm[] = [1, 3, 5, 10, null];

export const WHEN_LABELS: Record<When, string> = {
  today: 'Today',
  weekend: 'This weekend',
  week: 'Next 7 days',
  month: 'Next 30 days',
};
const WHEN_PHRASES: Record<When, string> = {
  today: 'today',
  weekend: 'this weekend',
  week: 'in the next 7 days',
  month: 'in the next 30 days',
};

export type DiscoverFilters = {
  categories: string[];
  when: When;
  radiusKm: RadiusKm;
  free: boolean;
  includeOnline: boolean;
};

/** Where to search: the device's position, a saved home planning area, or nowhere in particular. */
export type SearchPlace = { kind: 'coords'; lat: number; lng: number } | { kind: 'area'; area: string } | { kind: 'none' };

export type QueryParams = Record<string, string | number | boolean | string[]>;

export function radiusLabel(radius: RadiusKm): string {
  return radius == null ? 'Anywhere' : `${radius} km`;
}

export function toEventsParams(f: DiscoverFilters, place: SearchPlace): QueryParams {
  const params: QueryParams = { when: f.when, online: f.includeOnline ? 'include' : 'exclude' };
  if (f.categories.length) params.category = f.categories;
  if (f.free) params.free = true;
  if (place.kind === 'coords' && f.radiusKm != null) {
    params.lat = round(place.lat);
    params.lng = round(place.lng);
    params.radius_km = f.radiusKm;
  } else if (place.kind === 'area') {
    params.area = place.area;
  }
  return params;
}

// ~11 m precision is plenty for a radius search and avoids sending more than needed.
function round(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

export function toQueryString(params: QueryParams): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(params)) {
    for (const v of Array.isArray(value) ? value : [value]) {
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(v))}`);
    }
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

export type Suggestion = { label: string; patch: Partial<DiscoverFilters> };

/** "No career fairs within 3 km this weekend." plus one way to widen the search. */
export function emptyState(
  f: DiscoverFilters,
  place: SearchPlace,
  categoryLabels: Record<string, string>,
): { message: string; suggestion: Suggestion | null } {
  const what =
    f.categories.length === 1
      ? (categoryLabels[f.categories[0]] ?? 'events').toLowerCase()
      : f.categories.length > 1
        ? 'events in your categories'
        : 'events';
  const free = f.free ? 'free ' : '';
  const where =
    place.kind === 'coords' && f.radiusKm != null
      ? ` within ${f.radiusKm} km`
      : place.kind === 'area'
        ? ` in ${titleCase(place.area)}`
        : '';
  const message = `No ${free}${what}${where} ${WHEN_PHRASES[f.when]}.`;

  let suggestion: Suggestion | null = null;
  if (place.kind === 'coords' && f.radiusKm != null && f.radiusKm < 10) {
    const next = RADIUS_OPTIONS[RADIUS_OPTIONS.indexOf(f.radiusKm) + 1] as RadiusKm;
    suggestion = { label: `Try ${radiusLabel(next)}`, patch: { radiusKm: next } };
  } else if (f.when !== 'month') {
    suggestion = { label: 'Try the next 30 days', patch: { when: 'month' } };
  } else if (f.free) {
    suggestion = { label: 'Include paid events', patch: { free: false } };
  } else if (f.categories.length) {
    suggestion = { label: 'Show all categories', patch: { categories: [] } };
  } else if (place.kind === 'coords' && f.radiusKm != null) {
    suggestion = { label: 'Search all of Singapore', patch: { radiusKm: null } };
  }
  return { message, suggestion };
}

export function titleCase(text: string): string {
  return text.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}
