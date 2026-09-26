import { fireEvent, render, screen } from '@testing-library/react-native';

import type { Event } from '@/api/schemas';
import { EventCard, placeLine } from '@/components/event-card';

const event: Event = {
  id: 42,
  title: 'NTUC e2i Career Fair @ Sengkang',
  summary: null,
  starts_at: '2026-10-03T02:00:00Z',
  ends_at: '2026-10-03T08:00:00Z',
  all_day: false,
  is_online: false,
  is_free: true,
  price_min_sgd: 0,
  price_max_sgd: 0,
  categories: ['career_fair'],
  audience: 'public',
  organizer: 'e2i',
  image_url: null,
  registration_url: null,
  confidence: 'high',
  status: 'active',
  venue: { name: 'Sengkang CC', address: null, postal_code: '545025', planning_area: 'SENGKANG', region: 'NORTH-EAST' },
  location: { lat: 1.3917, lng: 103.8953 },
  distance_m: 1234,
  sources: [{ source_id: 'luma', url: 'https://luma.com/event/evt-1' }],
};

describe('EventCard', () => {
  it('shows title, SGT time, distance with area, and a price badge', async () => {
    const onPress = jest.fn();
    await render(<EventCard event={event} clock="24h" onPress={onPress} />);
    expect(screen.getByText('NTUC e2i Career Fair @ Sengkang')).toBeTruthy();
    expect(screen.getByText('Sat 3 Oct, 10:00–16:00')).toBeTruthy();
    expect(screen.getByText('1.2 km · Sengkang')).toBeTruthy();
    expect(screen.getByText('Free')).toBeTruthy();
    await fireEvent.press(screen.getByRole('button'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('has a spoken label for screen readers', async () => {
    await render(<EventCard event={event} clock="12h" onPress={() => {}} />);
    expect(screen.getByLabelText(/Career Fair @ Sengkang, Sat 3 Oct, 10am–4pm Singapore time, 1.2 km · Sengkang, Free/)).toBeTruthy();
  });
});

describe('placeLine', () => {
  it('describes online and unknown venues', () => {
    expect(placeLine({ ...event, venue: null, location: null, distance_m: null, is_online: true })).toBe('Online');
    expect(placeLine({ ...event, venue: null, distance_m: null })).toBe('Venue on the event page');
  });
});
