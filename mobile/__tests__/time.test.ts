import { formatDistance, formatPrice, formatWhen } from '@/lib/time';

describe('formatWhen (always Singapore time)', () => {
  it('formats a same-day event', () => {
    expect(formatWhen('2026-10-03T02:00:00Z', '2026-10-03T08:00:00Z', false)).toBe('Sat 3 Oct, 10:00–16:00');
    expect(formatWhen('2026-10-03T02:00:00Z', '2026-10-03T08:30:00Z', false, '12h')).toBe('Sat 3 Oct, 10am–4:30pm');
  });

  it('crosses midnight in SGT, not UTC', () => {
    // 22:00–02:00 SGT is 14:00–18:00 UTC on the same UTC day
    expect(formatWhen('2026-10-03T14:00:00Z', '2026-10-03T18:00:00Z', false)).toBe('Sat 3 Oct, 22:00 – Sun 4 Oct, 02:00');
  });

  it('shows all-day ranges with an exclusive end', () => {
    expect(formatWhen('2026-09-24T16:00:00Z', '2026-09-27T16:00:00Z', true)).toBe('Fri 25 Sep – Sun 27 Sep');
    expect(formatWhen('2026-09-24T16:00:00Z', '2026-09-25T16:00:00Z', true)).toBe('Fri 25 Sep');
  });
});

describe('labels', () => {
  it('formats distances', () => {
    expect(formatDistance(846)).toBe('850 m');
    expect(formatDistance(1234)).toBe('1.2 km');
    expect(formatDistance(null)).toBeNull();
  });

  it('formats prices', () => {
    expect(formatPrice(true, 0, 0)).toBe('Free');
    expect(formatPrice(false, 10, 25)).toBe('S$10–25');
    expect(formatPrice(false, 8, null)).toBe('from S$8');
    expect(formatPrice(null, null, null)).toBeNull();
  });
});
