import { useRouter } from 'expo-router';
import { FlatList, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { EmptyState } from '@/components/empty-state';
import { EventCard } from '@/components/event-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { usePrefs } from '@/lib/prefs';

/** Saved events live on the device, so they show without a connection. */
export default function Saved() {
  const router = useRouter();
  const { prefs } = usePrefs();
  const now = Date.now();
  const events = Object.values(prefs.saved).sort((a, b) => a.starts_at.localeCompare(b.starts_at));
  const upcoming = events.filter((e) => new Date(e.ends_at ?? e.starts_at).getTime() >= now);
  const past = events.filter((e) => new Date(e.ends_at ?? e.starts_at).getTime() < now);

  return (
    <ThemedView style={styles.fill}>
      <SafeAreaView edges={['top']} style={styles.fill}>
        <FlatList
          data={[...upcoming, ...past]}
          keyExtractor={(e) => String(e.id)}
          contentContainerStyle={styles.content}
          ListHeaderComponent={
            <ThemedText type="subtitle" accessibilityRole="header">
              Saved
            </ThemedText>
          }
          ListEmptyComponent={<EmptyState message="Save events to find them here, even offline." />}
          renderItem={({ item, index }) => (
            <View style={{ opacity: index >= upcoming.length ? 0.55 : 1 }}>
              {index === upcoming.length && past.length ? (
                <ThemedText type="smallBold" style={styles.pastLabel}>
                  Past
                </ThemedText>
              ) : null}
              <EventCard
                event={item}
                clock={prefs.clock}
                onPress={() => router.push({ pathname: '/event/[id]', params: { id: String(item.id) } })}
              />
            </View>
          )}
        />
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  content: { padding: Spacing.three, gap: Spacing.two },
  pastLabel: { marginVertical: Spacing.two },
});
