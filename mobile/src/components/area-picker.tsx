import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { StyleSheet, TextInput, View } from 'react-native';

import { api } from '@/api/client';
import { Chip } from '@/components/chip';
import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { titleCase } from '@/lib/filters';

type Props = { selected: string | null; onSelect: (planningArea: string) => void };

/**
 * Home area fallback when location is off: a postal code (resolved to its planning area by
 * the API and not stored) or a planning area picked from the list.
 */
export function AreaPicker({ selected, onSelect }: Props) {
  const theme = useTheme();
  const [postal, setPostal] = useState('');
  const areas = useQuery({ queryKey: ['areas'], queryFn: ({ signal }) => api.areas(signal), staleTime: Infinity });
  const lookup = useMutation({
    mutationFn: (code: string) => api.resolvePostal(code),
    onSuccess: (area) => onSelect(area.planning_area),
  });

  const byRegion = new Map<string, string[]>();
  for (const a of areas.data ?? []) byRegion.set(a.region, [...(byRegion.get(a.region) ?? []), a.planning_area]);

  return (
    <View style={styles.wrap}>
      <ThemedText type="smallBold">Postal code</ThemedText>
      <View style={styles.row}>
        <TextInput
          value={postal}
          onChangeText={(t) => setPostal(t.replace(/\D/g, '').slice(0, 6))}
          placeholder="e.g. 540123"
          placeholderTextColor={theme.textSecondary}
          keyboardType="number-pad"
          maxLength={6}
          accessibilityLabel="Postal code"
          style={[styles.input, { color: theme.text, borderColor: theme.border, backgroundColor: theme.backgroundElement }]}
        />
        <Chip
          label={lookup.isPending ? 'Finding…' : 'Use postal code'}
          selected
          onPress={() => postal.length === 6 && lookup.mutate(postal)}
        />
      </View>
      {lookup.isError ? (
        <ThemedText type="small" themeColor="textSecondary">
          {lookup.error.message}
        </ThemedText>
      ) : null}

      <ThemedText type="smallBold">Or pick your area</ThemedText>
      {areas.isError ? (
        <ThemedText type="small" themeColor="textSecondary">
          {areas.error.message}
        </ThemedText>
      ) : null}
      {[...byRegion.entries()].map(([region, list]) => (
        <View key={region} style={styles.region}>
          <ThemedText type="small" themeColor="textSecondary">
            {titleCase(region)}
          </ThemedText>
          <View style={styles.chips}>
            {list.map((area) => (
              <Chip key={area} label={titleCase(area)} selected={selected === area} onPress={() => onSelect(area)} />
            ))}
          </View>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: Spacing.two },
  row: { flexDirection: 'row', gap: Spacing.two, alignItems: 'center' },
  input: {
    flex: 1,
    borderWidth: 1,
    borderRadius: Spacing.two,
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.two,
    fontSize: 16,
  },
  region: { gap: Spacing.one, marginTop: Spacing.two },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
});
