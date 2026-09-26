import Ionicons from '@expo/vector-icons/Ionicons';
import { useQuery } from '@tanstack/react-query';
import { Stack, useLocalSearchParams } from 'expo-router';
import type { ComponentProps } from 'react';
import { ActivityIndicator, Alert, ScrollView, StyleSheet, View } from 'react-native';

import { api } from '@/api/client';
import type { EventDetail } from '@/api/schemas';
import { Chip } from '@/components/chip';
import { categoryIcon } from '@/components/category-icon';
import { EmptyState } from '@/components/empty-state';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { addToCalendar, openDirections, openUrl, shareEvent } from '@/lib/actions';
import { titleCase } from '@/lib/filters';
import { usePrefs } from '@/lib/prefs';
import { formatPrice, formatWhen } from '@/lib/time';

const AUDIENCE_NOTES: Record<string, string> = {
  students_only: 'For students only',
  members_only: 'For members only',
  alumni: 'For alumni',
};

export default function EventScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const eventId = Number(id);
  const query = useQuery({ queryKey: ['event', eventId], queryFn: ({ signal }) => api.event(eventId, signal) });
  const categories = useQuery({ queryKey: ['categories'], queryFn: ({ signal }) => api.categories(signal), staleTime: 24 * 3600_000 });

  if (query.isPending) return <ActivityIndicator style={styles.loading} accessibilityLabel="Loading event" />;
  if (query.isError) {
    return (
      <ThemedView style={styles.fill}>
        <EmptyState message={query.error.message} actionLabel="Try again" onAction={() => query.refetch()} />
      </ThemedView>
    );
  }
  const labels = Object.fromEntries((categories.data ?? []).map((c) => [c.id, c.label]));
  return <Detail event={query.data} labels={labels} />;
}

function Detail({ event, labels }: { event: EventDetail; labels: Record<string, string> }) {
  const theme = useTheme();
  const { prefs, isSaved, toggleSaved } = usePrefs();
  const price = formatPrice(event.is_free, event.price_min_sgd, event.price_max_sgd);
  const [primary, ...others] = event.sources;
  const saved = isSaved(event.id);

  const run = (action: () => Promise<void>, failure: string) => () =>
    action().catch(() => Alert.alert(failure, 'Please try again.'));

  return (
    <ThemedView style={styles.fill}>
      <Stack.Screen options={{ title: event.title }} />
      <ScrollView contentContainerStyle={styles.content}>
        {event.status !== 'active' ? <Banner text={`This event is ${event.status}.`} /> : null}
        {event.confidence === 'low' ? <Banner text="Details from a news report; check the source before you go." /> : null}

        <ThemedText type="subtitle" accessibilityRole="header">
          {event.title}
        </ThemedText>
        {event.title_alt ? <ThemedText themeColor="textSecondary">{event.title_alt}</ThemedText> : null}

        <Row icon="time" text={`${formatWhen(event.starts_at, event.ends_at, event.all_day, prefs.clock)} (Singapore time)`} />
        <Row
          icon={event.is_online && !event.venue ? 'laptop' : 'location'}
          text={
            event.venue
              ? [event.venue.name, event.venue.address, event.venue.planning_area && titleCase(event.venue.planning_area)]
                  .filter(Boolean)
                  .join(', ')
              : event.is_online
                ? 'Online'
                : 'Venue details on the event page'
          }
        />
        {price ? <Row icon="pricetag" text={price} /> : null}
        {event.organizer ? <Row icon="person" text={`By ${event.organizer}`} /> : null}
        {AUDIENCE_NOTES[event.audience] ? <Row icon="lock-closed" text={AUDIENCE_NOTES[event.audience]} /> : null}

        {event.categories.length ? (
          <View style={styles.chips}>
            {event.categories.map((c) => (
              <Chip key={c} label={labels[c] ?? c} icon={categoryIcon(c)} />
            ))}
          </View>
        ) : null}

        <View style={styles.actions}>
          {event.registration_url ? (
            <Chip label="Register" icon="open" selected onPress={run(() => openUrl(event.registration_url!), "Couldn't open the link")} />
          ) : null}
          {event.location || event.venue ? (
            <Chip label="Directions" icon="navigate" onPress={run(() => openDirections(event), "Couldn't open maps")} />
          ) : null}
          <Chip label="Add to calendar" icon="calendar" onPress={run(() => addToCalendar(event), "Couldn't add to calendar")} />
          <Chip
            label={saved ? 'Saved' : 'Save'}
            icon={saved ? 'bookmark' : 'bookmark-outline'}
            selected={saved}
            onPress={() => toggleSaved(event)}
          />
          <Chip label="Share" icon="share-social" onPress={run(() => shareEvent(event), "Couldn't share")} />
        </View>

        {event.summary ? <ThemedText>{event.summary}</ThemedText> : null}
        {event.description ? (
          <ThemedText type="small" themeColor="textSecondary">
            {event.description}
          </ThemedText>
        ) : null}

        {event.sessions.length > 1 ? (
          <View style={styles.section}>
            <ThemedText type="smallBold">Sessions</ThemedText>
            {event.sessions.map((s) => (
              <ThemedText key={s.starts_at} type="small">
                {formatWhen(s.starts_at, s.ends_at, event.all_day, prefs.clock)}
              </ThemedText>
            ))}
          </View>
        ) : null}

        {primary ? (
          <View style={[styles.section, { borderTopColor: theme.border }]}>
            <ThemedText type="small" themeColor="textSecondary">
              From {sourceName(primary.source_id)}
              {others.length ? ` · Also on ${others.map((s) => sourceName(s.source_id)).join(', ')}` : ''}
            </ThemedText>
            <Chip label="View source" icon="link" onPress={run(() => openUrl(primary.url), "Couldn't open the link")} />
          </View>
        ) : null}
      </ScrollView>
    </ThemedView>
  );
}

function sourceName(sourceId: string): string {
  return ({ luma: 'Luma', onepa: 'onePA', eventbrite: 'Eventbrite' } as Record<string, string>)[sourceId] ?? sourceId;
}

function Row({ icon, text }: { icon: ComponentProps<typeof Ionicons>['name']; text: string }) {
  const theme = useTheme();
  return (
    <View style={styles.row}>
      <Ionicons name={icon} size={18} color={theme.textSecondary} style={styles.rowIcon} />
      <ThemedText style={styles.rowText}>{text}</ThemedText>
    </View>
  );
}

function Banner({ text }: { text: string }) {
  const theme = useTheme();
  return (
    <View style={[styles.banner, { backgroundColor: theme.warningBackground }]} accessibilityRole="alert">
      <ThemedText type="small" style={{ color: theme.warningText }}>
        {text}
      </ThemedText>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  loading: { marginTop: Spacing.six },
  content: { padding: Spacing.three, gap: Spacing.three, paddingBottom: Spacing.six },
  row: { flexDirection: 'row', gap: Spacing.two, alignItems: 'flex-start' },
  rowIcon: { marginTop: 3 },
  rowText: { flex: 1 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
  section: { gap: Spacing.two, paddingTop: Spacing.three, borderTopWidth: StyleSheet.hairlineWidth },
  banner: { padding: Spacing.three, borderRadius: Spacing.two },
});
