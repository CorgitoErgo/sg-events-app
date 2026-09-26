import { type DiscoverFilters, emptyState, type SearchPlace, toEventsParams, toQueryString } from '@/lib/filters';

const base: DiscoverFilters = { categories: [], when: 'weekend', radiusKm: 3, free: false, includeOnline: true };
const near: SearchPlace = { kind: 'coords', lat: 1.3917254, lng: 103.8953111 };
const labels = { career_fair: 'Career fairs & job fairs', music: 'Music & performances' };

describe('toEventsParams', () => {
  it('sends a rounded point and radius when location is on', () => {
    expect(toEventsParams({ ...base, categories: ['career_fair'], free: true }, near)).toEqual({
      when: 'weekend',
      online: 'include',
      category: ['career_fair'],
      free: true,
      lat: 1.3917,
      lng: 103.8953,
      radius_km: 3,
    });
  });

  it('drops the point for "Anywhere" so the API applies no radius', () => {
    const params = toEventsParams({ ...base, radiusKm: null }, near);
    expect(params).not.toHaveProperty('lat');
    expect(params).not.toHaveProperty('radius_km');
  });

  it('uses the home planning area when location is off', () => {
    expect(toEventsParams(base, { kind: 'area', area: 'SENGKANG' })).toMatchObject({ area: 'SENGKANG' });
  });

  it('builds repeated query parameters for categories', () => {
    expect(toQueryString({ category: ['a', 'b c'], free: true })).toBe('?category=a&category=b%20c&free=true');
  });
});

describe('emptyState', () => {
  it('names what was searched and suggests the next radius (the skill example)', () => {
    const { message, suggestion } = emptyState({ ...base, categories: ['career_fair'] }, near, labels);
    expect(message).toBe('No career fairs & job fairs within 3 km this weekend.');
    expect(suggestion).toEqual({ label: 'Try 5 km', patch: { radiusKm: 5 } });
  });

  it('widens dates, then price, then categories', () => {
    const wide = { ...base, radiusKm: 10 as const };
    expect(emptyState(wide, near, labels).suggestion?.patch).toEqual({ when: 'month' });
    expect(emptyState({ ...wide, when: 'month', free: true }, near, labels).suggestion?.patch).toEqual({ free: false });
    expect(emptyState({ ...wide, when: 'month', categories: ['music'] }, near, labels).suggestion?.patch).toEqual({ categories: [] });
  });

  it('describes area searches', () => {
    expect(emptyState({ ...base, free: true }, { kind: 'area', area: 'ANG MO KIO' }, labels).message).toBe(
      'No free events in Ang Mo Kio this weekend.',
    );
  });
});
