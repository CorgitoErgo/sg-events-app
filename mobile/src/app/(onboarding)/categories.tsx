import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { api } from '@/api/client';
import { CategoryGrid } from '@/components/category-grid';
import { Chip } from '@/components/chip';
import { EmptyState } from '@/components/empty-state';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { usePrefs } from '@/lib/prefs';

export default function OnboardingCategories() {
  const router = useRouter();
  const { prefs, update } = usePrefs();
  const [selected, setSelected] = useState<string[]>(prefs.categories);
  const categories = useQuery({ queryKey: ['categories'], queryFn: ({ signal }) => api.categories(signal) });

  return (
    <ThemedView style={styles.fill}>
      <SafeAreaView style={styles.fill}>
        <ScrollView contentContainerStyle={styles.content}>
          <ThemedText type="subtitle" accessibilityRole="header">
            What are you into?
          </ThemedText>
          <ThemedText themeColor="textSecondary">
            Pick a few categories. You can change them any time in Settings.
          </ThemedText>
          {categories.isPending ? <ActivityIndicator /> : null}
          {categories.isError ? (
            <EmptyState message={categories.error.message} actionLabel="Retry" onAction={() => categories.refetch()} />
          ) : null}
          {categories.data ? (
            <CategoryGrid categories={categories.data} selected={selected} onChange={setSelected} />
          ) : null}
        </ScrollView>
        <View style={styles.footer}>
          <Chip label="Skip" onPress={() => router.push('/location')} />
          <Chip
            label={selected.length ? `Continue with ${selected.length}` : 'Continue'}
            selected
            onPress={() => {
              update({ categories: selected });
              router.push('/location');
            }}
          />
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
