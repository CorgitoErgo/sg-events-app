import { useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';

import type { Category } from '@/api/schemas';
import { CategoryGrid } from '@/components/category-grid';
import { Chip } from '@/components/chip';
import { OptionSheet } from '@/components/option-sheet';
import { Spacing } from '@/constants/theme';
import {
  type DiscoverFilters,
  RADIUS_OPTIONS,
  radiusLabel,
  type SearchPlace,
  titleCase,
  WHEN_LABELS,
  WHEN_OPTIONS,
} from '@/lib/filters';

type Props = {
  filters: DiscoverFilters;
  onChange: (patch: Partial<DiscoverFilters>) => void;
  place: SearchPlace;
  categories: Category[];
  onSetLocation: () => void;
};

export function FilterBar({ filters, onChange, place, categories, onSetLocation }: Props) {
  const [sheet, setSheet] = useState<'categories' | 'radius' | null>(null);
  const labels = Object.fromEntries(categories.map((c) => [c.id, c.label]));
  const categoryLabel =
    filters.categories.length === 0
      ? 'All categories'
      : filters.categories.length === 1
        ? (labels[filters.categories[0]] ?? '1 category')
        : `${labels[filters.categories[0]] ?? 'Categories'} +${filters.categories.length - 1}`;

  return (
    <View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
        <Chip
          label={categoryLabel}
          icon="options"
          selected={filters.categories.length > 0}
          onPress={() => setSheet('categories')}
          accessibilityHint="Choose categories"
        />
        {place.kind === 'coords' ? (
          <Chip
            label={radiusLabel(filters.radiusKm)}
            icon="locate"
            selected={filters.radiusKm != null}
            onPress={() => setSheet('radius')}
            accessibilityHint="Choose how far to search"
          />
        ) : place.kind === 'area' ? (
          <Chip label={`In ${titleCase(place.area)}`} icon="location" selected onPress={onSetLocation} />
        ) : (
          <Chip label="Near me" icon="navigate" onPress={onSetLocation} accessibilityHint="Use your location" />
        )}
        {WHEN_OPTIONS.map((w) => (
          <Chip key={w} label={WHEN_LABELS[w]} selected={filters.when === w} onPress={() => onChange({ when: w })} />
        ))}
        <Chip label="Free" icon="pricetag" selected={filters.free} onPress={() => onChange({ free: !filters.free })} />
        <Chip
          label="Online"
          icon="laptop"
          selected={filters.includeOnline}
          onPress={() => onChange({ includeOnline: !filters.includeOnline })}
          accessibilityHint="Include online events"
        />
      </ScrollView>

      <OptionSheet visible={sheet === 'categories'} title="Categories" onClose={() => setSheet(null)}>
        <CategoryGrid categories={categories} selected={filters.categories} onChange={(c) => onChange({ categories: c })} />
      </OptionSheet>
      <OptionSheet visible={sheet === 'radius'} title="Distance" onClose={() => setSheet(null)}>
        <View style={styles.wrap}>
          {RADIUS_OPTIONS.map((r) => (
            <Chip
              key={String(r)}
              label={radiusLabel(r)}
              selected={filters.radiusKm === r}
              onPress={() => {
                onChange({ radiusKm: r });
                setSheet(null);
              }}
            />
          ))}
        </View>
      </OptionSheet>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { gap: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
});
