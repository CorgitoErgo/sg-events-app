import { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import MapView, { Marker } from 'react-native-maps';

import type { Event } from '@/api/schemas';
import { EventCard } from '@/components/event-card';
import { Spacing } from '@/constants/theme';
import type { Coords } from '@/lib/location';
import type { Clock } from '@/lib/time';

const SINGAPORE = { latitude: 1.3521, longitude: 103.8198, latitudeDelta: 0.35, longitudeDelta: 0.35 };

type Props = {
  events: Event[];
  center: Coords | null;
  clock: Clock;
  onOpen: (id: number) => void;
};

/** Map view of the loaded events; tapping a marker shows its card at the bottom. */
export function EventsMap({ events, center, clock, onOpen }: Props) {
  const [selected, setSelected] = useState<Event | null>(null);
  const initialRegion = center
    ? { latitude: center.lat, longitude: center.lng, latitudeDelta: 0.08, longitudeDelta: 0.08 }
    : SINGAPORE;
  const located = events.filter((e) => e.location);

  return (
    <View style={styles.wrap}>
      <MapView
        style={StyleSheet.absoluteFill}
        initialRegion={initialRegion}
        showsUserLocation={Boolean(center)}
        onPress={() => setSelected(null)}
        accessibilityLabel={`Map with ${located.length} events`}>
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
      {selected ? (
        <View style={styles.card}>
          <EventCard event={selected} clock={clock} onPress={() => onOpen(selected.id)} />
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1 },
  card: { position: 'absolute', left: Spacing.three, right: Spacing.three, bottom: Spacing.three },
});
