import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { api } from '@/api/client';
import { Chip } from '@/components/chip';
import { EmptyState } from '@/components/empty-state';
import { EventCard } from '@/components/event-card';
import { EventsMap } from '@/components/events-map';
import { FilterBar } from '@/components/filter-bar';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { type DiscoverFilters, emptyState, type SearchPlace, toEventsParams } from '@/lib/filters';
import { useDeviceLocation } from '@/lib/location';
import { usePrefs } from '@/lib/prefs';

export default function Discover() {
  const router = useRouter();
  const { prefs } = usePrefs();
  const { coords, permission, request, refresh } = useDeviceLocation();
  const [view, setView] = useState<'list' | 'map'>('list');
  const [filters, setFilters] = useState<DiscoverFilters>(() => ({
    categories: prefs.categories,
    when: 'month',
    radiusKm: prefs.radiusKm,
    free: false,
    includeOnline: true,
  }));

  const categories = useQuery({
    queryKey: ['categories'],
    queryFn: ({ signal }) => api.categories(signal),
    staleTime: 24 * 3600_000,
  });
  const labels = useMemo(
    () => Object.fromEntries((categories.data ?? []).map((c) => [c.id, c.label])),
    [categories.data],
  );

  const place: SearchPlace = coords
    ? { kind: 'coords', lat: coords.lat, lng: coords.lng }
    : prefs.homeArea
      ? { kind: 'area', area: prefs.homeArea }
      : { kind: 'none' };
  const params = toEventsParams(filters, place);

  const events = useInfiniteQuery({
    queryKey: ['events', params],
    queryFn: ({ pageParam, signal }) => api.events(params, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
  });
  const items = events.data?.pages.flatMap((p) => p.items) ?? [];
  const change = (patch: Partial<DiscoverFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const open = (id: number) => router.push({ pathname: '/event/[id]', params: { id: String(id) } });

  const onSetLocation = async () => {
    if (permission === 'granted') await refresh();
    else if (!(await request())) router.push('/settings');
  };

  let body;
  if (events.isPending) {
    body = <ActivityIndicator style={styles.loading} accessibilityLabel="Loading events" />;
  } else if (events.isError) {
    body = <EmptyState message={events.error.message} actionLabel="Try again" onAction={() => events.refetch()} />;
  } else if (items.length === 0) {
    const { message, suggestion } = emptyState(filters, place, labels);
    body = (
      <EmptyState message={message} actionLabel={suggestion?.label} onAction={suggestion ? () => change(suggestion.patch) : undefined} />
    );
  } else if (view === 'map') {
    body = <EventsMap events={items} center={coords} clock={prefs.clock} onOpen={open} />;
  } else {
    body = (
      <FlatList
        data={items}
        keyExtractor={(e) => String(e.id)}
        renderItem={({ item }) => <EventCard event={item} clock={prefs.clock} onPress={() => open(item.id)} />}
        contentContainerStyle={styles.list}
        onEndReached={() => events.hasNextPage && !events.isFetchingNextPage && events.fetchNextPage()}
        onEndReachedThreshold={0.5}
        refreshControl={<RefreshControl refreshing={events.isRefetching} onRefresh={() => events.refetch()} />}
        ListFooterComponent={events.isFetchingNextPage ? <ActivityIndicator /> : null}
      />
    );
  }

  const total = events.data?.pages[0]?.total;
  return (
    <ThemedView style={styles.fill}>
      <SafeAreaView edges={['top']} style={styles.fill}>
        <View style={styles.header}>
          <ThemedText type="subtitle" accessibilityRole="header">
            Discover
          </ThemedText>
          <View style={styles.toggle}>
            <Chip label="List" icon="list" selected={view === 'list'} onPress={() => setView('list')} />
            <Chip label="Map" icon="map" selected={view === 'map'} onPress={() => setView('map')} />
          </View>
        </View>
        <FilterBar
          filters={filters}
          onChange={change}
          place={place}
          categories={categories.data ?? []}
          onSetLocation={onSetLocation}
        />
        {total != null && total > 0 ? (
          <ThemedText type="small" themeColor="textSecondary" style={styles.count}>
            {total} {total === 1 ? 'event' : 'events'}
          </ThemedText>
        ) : null}
        <View style={styles.fill}>{body}</View>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: Spacing.three,
    paddingTop: Spacing.two,
  },
  toggle: { flexDirection: 'row', gap: Spacing.one },
  count: { paddingHorizontal: Spacing.three },
  list: { padding: Spacing.three, gap: Spacing.two },
  loading: { marginTop: Spacing.five },
});
