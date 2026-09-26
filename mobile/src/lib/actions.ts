/** Event detail actions: register, directions, calendar, share. */
import { createEventInCalendarAsync } from 'expo-calendar/legacy';
import { Linking, Platform, Share } from 'react-native';

import type { EventDetail } from '@/api/schemas';

export async function openUrl(url: string): Promise<void> {
  await Linking.openURL(url);
}

/** Opens Apple Maps / Google Maps with directions to the venue. */
export async function openDirections(event: EventDetail): Promise<void> {
  const label = event.venue?.name ?? event.title;
  if (!event.location) {
    if (!event.venue?.address && !event.venue?.name) return;
    const query = encodeURIComponent([event.venue?.name, event.venue?.address].filter(Boolean).join(', '));
    await Linking.openURL(`https://www.google.com/maps/search/?api=1&query=${query}`);
    return;
  }
  const { lat, lng } = event.location;
  const url = Platform.select({
    ios: `http://maps.apple.com/?daddr=${lat},${lng}&q=${encodeURIComponent(label)}`,
    default: `geo:0,0?q=${lat},${lng}(${encodeURIComponent(label)})`,
  });
  const fallback = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
  if (await Linking.canOpenURL(url)) await Linking.openURL(url);
  else await Linking.openURL(fallback);
}

/**
 * The system "add event" form, prefilled. The legacy expo-calendar entry point is used on
 * purpose: it needs no calendar permission and works the same on iOS and Android, while
 * the SDK 57 object API has no default calendar on Android.
 */
export async function addToCalendar(event: EventDetail): Promise<void> {
  const start = new Date(event.starts_at);
  let end = event.ends_at ? new Date(event.ends_at) : null;
  if (event.all_day) {
    end = new Date((end ?? new Date(start.getTime() + 86_400_000)).getTime() - 1000); // stored end is exclusive
  } else if (!end) {
    end = new Date(start.getTime() + 2 * 3_600_000);
  }
  await createEventInCalendarAsync({
    title: event.title,
    startDate: start,
    endDate: end,
    allDay: event.all_day,
    timeZone: 'Asia/Singapore',
    location: [event.venue?.name, event.venue?.address].filter(Boolean).join(', ') || undefined,
    notes: [event.summary, event.registration_url ?? event.sources[0]?.url].filter(Boolean).join('\n\n'),
    url: event.registration_url ?? undefined,
  });
}

export async function shareEvent(event: EventDetail): Promise<void> {
  const link = event.registration_url ?? event.sources[0]?.url ?? '';
  await Share.share({ title: event.title, message: link ? `${event.title}\n${link}` : event.title });
}
