import { formatInTimeZone } from 'date-fns-tz';

/** Always Singapore time, whatever the device's time zone (travellers, emulators). */
export const SG_TZ = 'Asia/Singapore';

export type Clock = '24h' | '12h';

function clockTime(date: Date, clock: Clock): string {
  if (clock === '24h') return formatInTimeZone(date, SG_TZ, 'HH:mm');
  const minutes = formatInTimeZone(date, SG_TZ, 'mm');
  return formatInTimeZone(date, SG_TZ, minutes === '00' ? 'haaa' : 'h:mmaaa');
}

function day(date: Date): string {
  return formatInTimeZone(date, SG_TZ, 'EEE d MMM');
}

function sgDate(date: Date): string {
  return formatInTimeZone(date, SG_TZ, 'yyyy-MM-dd');
}

/** "Sat 3 Oct, 10:00–16:00" · "Fri 25 Sep – Sun 27 Sep" (all-day; stored end is exclusive). */
export function formatWhen(startIso: string, endIso: string | null, allDay: boolean, clock: Clock = '24h'): string {
  const start = new Date(startIso);
  const end = endIso ? new Date(endIso) : null;
  if (allDay) {
    if (!end) return day(start);
    const last = new Date(end.getTime() - 1);
    return sgDate(last) <= sgDate(start) ? day(start) : `${day(start)} – ${day(last)}`;
  }
  if (!end) return `${day(start)}, ${clockTime(start, clock)}`;
  if (sgDate(end) === sgDate(start)) return `${day(start)}, ${clockTime(start, clock)}–${clockTime(end, clock)}`;
  return `${day(start)}, ${clockTime(start, clock)} – ${day(end)}, ${clockTime(end, clock)}`;
}

export function formatDistance(metres: number | null): string | null {
  if (metres == null) return null;
  return metres < 1000 ? `${Math.round(metres / 10) * 10} m` : `${(metres / 1000).toFixed(1)} km`;
}

export function formatPrice(isFree: boolean | null, min: number | null, max: number | null): string | null {
  if (isFree) return 'Free';
  if (min == null) return null;
  if (max == null) return `from S$${min}`;
  return min === max ? `S$${min}` : `S$${min}–${max}`;
}
