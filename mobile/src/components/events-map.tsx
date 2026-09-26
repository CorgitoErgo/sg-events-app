import { useState } from 'react';
import { Image, Linking, Platform, Pressable, StyleSheet, View } from 'react-native';
import MapView, { Marker, UrlTile } from 'react-native-maps';

import type { Event } from '@/api/schemas';
import { EventCard } from '@/components/event-card';
import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useColorScheme } from '@/hooks/use-color-scheme';
import type { Coords } from '@/lib/location';
import {
  ONEMAP_ATTRIBUTION,
  ONEMAP_LOGO,
  ONEMAP_MAX_ZOOM,
  ONEMAP_MIN_ZOOM,
  oneMapTileUrl,
} from '@/lib/onemap';
import type { Clock } from '@/lib/time';

const SINGAPORE = { latitude: 1.3521, longitude: 103.8198, latitudeDelta: 0.35, longitudeDelta: 0.35 };

// Android: Expo Go (SDK 57) can't load Google's base tiles and shows a black map
// (github.com/expo/expo/issues/49323), so Android draws OneMap tiles, Singapore Land
// Authority's official basemap (MRT stations, HDB blocks). iOS keeps Apple Maps.
const USE_ONEMAP_TILES = Platform.OS === 'android';

type Props = {
  events: Event[];
  center: Coords | null;
  clock: Clock;
  onOpen: (id: number) => void;
};

/** Map view of the loaded events; tapping a marker shows its card at the bottom. */
export function EventsMap({ events, center, clock, onOpen }: Props) {
  const [selected, setSelected] = useState<Event | null>(null);
  const dark = useColorScheme() === 'dark';
  const initialRegion = center
    ? { latitude: center.lat, longitude: center.lng, latitudeDelta: 0.08, longitudeDelta: 0.08 }
    : SINGAPORE;
  const located = events.filter((e) => e.location);

  return (
    <View style={styles.wrap}>
      <MapView
        style={StyleSheet.absoluteFill}
        initialRegion={initialRegion}
        mapType={USE_ONEMAP_TILES ? 'none' : 'standard'}
        minZoomLevel={USE_ONEMAP_TILES ? ONEMAP_MIN_ZOOM : undefined}
        maxZoomLevel={USE_ONEMAP_TILES ? ONEMAP_MAX_ZOOM : undefined}
        showsUserLocation={Boolean(center)}
        onPress={() => setSelected(null)}
        accessibilityLabel={`Map with ${located.length} events`}>
        {USE_ONEMAP_TILES ? (
          <UrlTile
            urlTemplate={oneMapTileUrl(dark)}
            minimumZ={ONEMAP_MIN_ZOOM}
            maximumZ={ONEMAP_MAX_ZOOM}
            maximumNativeZ={ONEMAP_MAX_ZOOM}
            zIndex={-1}
          />
        ) : null}
        {located.map((e) => (
          <Marker
            key={e.id}
            coordinate={{ latitude: e.location!.lat, longitude: e.location!.lng }}
            title={e.title}
            onPress={(ev) => {
              ev.stopPropagation();
              setSelected(e);
            }}
          />
        ))}
      </MapView>
      {USE_ONEMAP_TILES ? <OneMapAttribution /> : null}
      {selected ? (
        <View style={styles.card}>
          <EventCard event={selected} clock={clock} onPress={() => onOpen(selected.id)} />
        </View>
      ) : null}
    </View>
  );
}

/** Required by OneMap's terms whenever its basemap is shown: logo and attribution. */
function OneMapAttribution() {
  return (
    <Pressable
      onPress={() => void Linking.openURL('https://www.onemap.gov.sg/')}
      accessibilityRole="link"
      accessibilityLabel="Map data: OneMap, Singapore Land Authority"
      style={styles.attribution}>
      <Image source={{ uri: ONEMAP_LOGO }} style={styles.logo} accessibilityIgnoresInvertColors />
      <ThemedText type="small" style={styles.attributionText}>
        {ONEMAP_ATTRIBUTION}
      </ThemedText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1 },
  card: { position: 'absolute', left: Spacing.three, right: Spacing.three, bottom: Spacing.three },
  attribution: {
    position: 'absolute',
    top: Spacing.two,
    left: Spacing.two,
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.one,
    paddingHorizontal: Spacing.two,
    paddingVertical: Spacing.half,
    borderRadius: Spacing.two,
    backgroundColor: 'rgba(255,255,255,0.85)',
  },
  logo: { width: 16, height: 16 },
  attributionText: { color: '#1b1b1b', fontSize: 11, lineHeight: 14 },
});
