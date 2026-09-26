import { StyleSheet, View } from 'react-native';

import type { Category } from '@/api/schemas';
import { Chip } from '@/components/chip';
import { categoryIcon } from '@/components/category-icon';
import { Spacing } from '@/constants/theme';

type Props = {
  categories: Category[];
  selected: string[];
  onChange: (next: string[]) => void;
};

/** Multi-select category chips, in the API's order (career fairs and community come early). */
export function CategoryGrid({ categories, selected, onChange }: Props) {
  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((c) => c !== id) : [...selected, id]);
  return (
    <View style={styles.grid}>
      {categories.map((c) => (
        <Chip
          key={c.id}
          label={c.label}
          icon={categoryIcon(c.id)}
          selected={selected.includes(c.id)}
          onPress={() => toggle(c.id)}
          accessibilityHint={c.includes}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
});
