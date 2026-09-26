import { StyleSheet, View } from 'react-native';

import { Chip } from '@/components/chip';
import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';

type Props = { message: string; actionLabel?: string; onAction?: () => void };

/** An empty or error state that always suggests what to do next. */
export function EmptyState({ message, actionLabel, onAction }: Props) {
  return (
    <View style={styles.wrap} accessibilityLiveRegion="polite">
      <ThemedText style={styles.message} themeColor="textSecondary">
        {message}
      </ThemedText>
      {actionLabel && onAction ? <Chip label={actionLabel} onPress={onAction} selected /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { alignItems: 'center', gap: Spacing.three, padding: Spacing.five },
  message: { textAlign: 'center' },
});
