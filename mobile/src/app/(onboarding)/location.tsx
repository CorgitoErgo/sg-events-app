import { useRouter } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AreaPicker } from '@/components/area-picker';
import { Chip } from '@/components/chip';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useDeviceLocation } from '@/lib/location';
import { usePrefs } from '@/lib/prefs';

/** Permission rationale first, then the system prompt; a postal code / area fallback if declined. */
export default function OnboardingLocation() {
  const router = useRouter();
  const { prefs, update } = usePrefs();
  const { request } = useDeviceLocation();
  const [showFallback, setShowFallback] = useState(false);

  const finish = (patch: { homeArea?: string } = {}) => {
    update({ ...patch, onboarded: true });
    router.replace('/');
  };

  return (
    <ThemedView style={styles.fill}>
      <SafeAreaView style={styles.fill}>
        <ScrollView contentContainerStyle={styles.content}>
          <ThemedText type="subtitle" accessibilityRole="header">
            Find events near you
          </ThemedText>
          <ThemedText themeColor="textSecondary">
            We use your location only while you search, to show nearby events and how far they are. It isn&apos;t
            stored or shared.
          </ThemedText>
          {showFallback ? (
            <>
              <ThemedText>No problem. Tell us roughly where you are instead:</ThemedText>
              <AreaPicker selected={prefs.homeArea} onSelect={(area) => finish({ homeArea: area })} />
            </>
          ) : null}
        </ScrollView>
        <View style={styles.footer}>
          {showFallback ? (
            <Chip label="Skip for now" onPress={() => finish()} />
          ) : (
            <>
              <Chip label="Not now" onPress={() => setShowFallback(true)} />
              <Chip
                label="Use my location"
                icon="navigate"
                selected
                onPress={async () => ((await request()) ? finish() : setShowFallback(true))}
              />
            </>
          )}
        </View>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  content: { padding: Spacing.four, gap: Spacing.three },
  footer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.four,
    paddingVertical: Spacing.three,
  },
});
