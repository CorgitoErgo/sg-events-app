import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Alert, Linking, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { api } from '@/api/client';
import { API_URL } from '@/api/config';
import { AreaPicker } from '@/components/area-picker';
import { CategoryGrid } from '@/components/category-grid';
import { Chip } from '@/components/chip';
import { OptionSheet } from '@/components/option-sheet';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { RADIUS_OPTIONS, radiusLabel, titleCase } from '@/lib/filters';
import { useDeviceLocation } from '@/lib/location';
import { usePrefs } from '@/lib/prefs';

export default function Settings() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { prefs, update, clearAll } = usePrefs();
  const { permission, request } = useDeviceLocation();
  const [areaSheet, setAreaSheet] = useState(false);
  const categories = useQuery({ queryKey: ['categories'], queryFn: ({ signal }) => api.categories(signal), staleTime: 24 * 3600_000 });

  const confirmDelete = () =>
    Alert.alert('Delete my data?', 'This removes your categories, saved events and settings from this device.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          await clearAll();
          queryClient.clear();
          router.replace('/categories');
        },
      },
    ]);

  return (
    <ThemedView style={styles.fill}>
      <SafeAreaView edges={['top']} style={styles.fill}>
        <ScrollView contentContainerStyle={styles.content}>
          <ThemedText type="subtitle" accessibilityRole="header">
            Settings
          </ThemedText>

          <Section title="Your categories" hint="Discover starts with these selected.">
            {categories.data ? (
              <CategoryGrid categories={categories.data} selected={prefs.categories} onChange={(c) => update({ categories: c })} />
            ) : (
              <ThemedText type="small" themeColor="textSecondary">
                {categories.isError ? categories.error.message : 'Loading…'}
              </ThemedText>
            )}
          </Section>

          <Section title="Default distance">
            <View style={styles.row}>
              {RADIUS_OPTIONS.map((r) => (
                <Chip key={String(r)} label={radiusLabel(r)} selected={prefs.radiusKm === r} onPress={() => update({ radiusKm: r })} />
              ))}
            </View>
          </Section>

          <Section title="Location">
            <ThemedText type="small" themeColor="textSecondary">
              {permission === 'granted'
                ? 'On. Used only while you search; never stored.'
                : 'Off. Events are shown for your home area instead.'}
            </ThemedText>
            <View style={styles.row}>
              {permission === 'granted' ? (
                <Chip label="Open system settings" icon="open" onPress={() => Linking.openSettings()} />
              ) : (
                <Chip label="Use my location" icon="navigate" selected onPress={() => void request()} />
              )}
            </View>
          </Section>

          <Section title="Home area" hint="Used when location is off. We keep only the planning area.">
            <View style={styles.row}>
              <Chip label={prefs.homeArea ? titleCase(prefs.homeArea) : 'Not set'} icon="location" selected={Boolean(prefs.homeArea)} onPress={() => setAreaSheet(true)} />
              {prefs.homeArea ? <Chip label="Clear" onPress={() => update({ homeArea: null })} /> : null}
            </View>
          </Section>

          <Section title="Time format">
            <View style={styles.row}>
              <Chip label="24-hour (18:30)" selected={prefs.clock === '24h'} onPress={() => update({ clock: '24h' })} />
              <Chip label="12-hour (6:30pm)" selected={prefs.clock === '12h'} onPress={() => update({ clock: '12h' })} />
            </View>
          </Section>

          <Section title="Your data">
            <View style={styles.row}>
              <Chip label="Delete my data" icon="trash" onPress={confirmDelete} />
            </View>
          </Section>

          <ThemedText type="small" themeColor="textSecondary">
            Server: {API_URL}
          </ThemedText>
        </ScrollView>

        <OptionSheet visible={areaSheet} title="Home area" onClose={() => setAreaSheet(false)}>
          <AreaPicker
            selected={prefs.homeArea}
            onSelect={(area) => {
              update({ homeArea: area });
              setAreaSheet(false);
            }}
          />
        </OptionSheet>
      </SafeAreaView>
    </ThemedView>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <ThemedText type="smallBold" accessibilityRole="header">
        {title}
      </ThemedText>
      {hint ? (
        <ThemedText type="small" themeColor="textSecondary">
          {hint}
        </ThemedText>
      ) : null}
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  content: { padding: Spacing.three, gap: Spacing.four, paddingBottom: Spacing.six },
  section: { gap: Spacing.two },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
});
