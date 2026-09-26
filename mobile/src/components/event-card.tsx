import Ionicons from '@expo/vector-icons/Ionicons';
import { Pressable, StyleSheet, View } from 'react-native';

import type { Event } from '@/api/schemas';
import { categoryIcon } from '@/components/category-icon';
import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { titleCase } from '@/lib/filters';
import { type Clock, formatDistance, formatPrice, formatWhen } from '@/lib/time';

type Props = {
  event: Event;
  clock: Clock;
  onPress: () => void;
  compact?: boolean;
};

/** "1.2 km · Sengkang", or the venue, or Online. */
export function placeLine(event: Event): string {
  const distance = formatDistance(event.distance_m);
  const area = event.venue?.planning_area ? titleCase(event.venue.planning_area) : null;
  if (event.is_online && !event.venue && !event.location) return 'Online';
  if (distance) return [distance, area ?? event.venue?.name].filter(Boolean).join(' · ');
  return event.venue?.name ?? area ?? 'Venue on the event page';
}

export function EventCard({ event, clock, onPress, compact = false }: Props) {
  const theme = useTheme();
  const price = formatPrice(event.is_free, event.price_min_sgd, event.price_max_sgd);
  const when = formatWhen(event.starts_at, event.ends_at, event.all_day, clock);
  const place = placeLine(event);
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`${event.title}, ${when} Singapore time, ${place}${price ? `, ${price}` : ''}`}
      style={({ pressed }) => [
        styles.card,
        compact && styles.compact,
        { backgroundColor: theme.backgroundElement, borderColor: theme.border, opacity: pressed ? 0.8 : 1 },
      ]}>
      <View style={[styles.icon, { backgroundColor: theme.background }]}>
        <Ionicons name={categoryIcon(event.categories[0])} size={compact ? 18 : 22} color={theme.primary} />
      </View>
      <View style={styles.body}>
        <ThemedText type="smallBold" numberOfLines={2}>
          {event.title}
        </ThemedText>
        <ThemedText type="small" themeColor="textSecondary">
          {when}
        </ThemedText>
        <ThemedText type="small" themeColor="textSecondary" numberOfLines={1}>
          {place}
        </ThemedText>
      </View>
      {price ? (
        <View style={[styles.badge, { backgroundColor: price === 'Free' ? theme.primary : theme.background }]}>
          <ThemedText type="small" style={{ color: price === 'Free' ? theme.onPrimary : theme.text }}>
            {price}
          </ThemedText>
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    gap: Spacing.three,
    padding: Spacing.three,
    borderRadius: Spacing.three,
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: 'flex-start',
  },
  compact: { padding: Spacing.two, gap: Spacing.two },
  icon: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  body: { flex: 1, gap: Spacing.half },
  badge: { paddingHorizontal: Spacing.two, paddingVertical: Spacing.half, borderRadius: Spacing.two },
});
