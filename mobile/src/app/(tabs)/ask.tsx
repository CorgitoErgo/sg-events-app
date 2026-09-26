import Ionicons from '@expo/vector-icons/Ionicons';
import { useQuery } from '@tanstack/react-query';
import { formatInTimeZone } from 'date-fns-tz';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { api, askStream, type AskRequest } from '@/api/client';
import type { AskAppliedFilters, Event } from '@/api/schemas';
import { Chip } from '@/components/chip';
import { EventCard } from '@/components/event-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { splitCitations } from '@/lib/citations';
import { titleCase, type When, WHEN_OPTIONS } from '@/lib/filters';
import { useDeviceLocation } from '@/lib/location';
import { usePrefs } from '@/lib/prefs';
import { SG_TZ } from '@/lib/time';

const STARTERS = ['Career fairs near me this month', 'Free weekend activities for kids', 'Volunteering this Saturday'];
const RADIUS_CYCLE = [1, 3, 5, 10];

type Overrides = NonNullable<AskRequest['filters']>;

type Turn = {
  id: number;
  question: string;
  overrides: Overrides;
  status: 'streaming' | 'done' | 'error';
  text: string;
  events: Event[];
  filters: AskAppliedFilters | null;
  notes: string[];
  error?: string;
};

export default function Ask() {
  const { prefs } = usePrefs();
  const { coords } = useDeviceLocation();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const abort = useRef<AbortController | null>(null);
  const nextId = useRef(1);
  const scroll = useRef<ScrollView>(null);
  const categories = useQuery({ queryKey: ['categories'], queryFn: ({ signal }) => api.categories(signal), staleTime: 24 * 3600_000 });
  const labels = Object.fromEntries((categories.data ?? []).map((c) => [c.id, c.label]));

  useEffect(() => () => abort.current?.abort(), []);

  const patch = (id: number, change: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...(typeof change === 'function' ? change(t) : change) } : t)));

  const send = (question: string, overrides: Overrides = {}) => {
    const q = question.trim();
    if (q.length < 2) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    const id = nextId.current++;
    setTurns((all) => [
      ...all,
      { id, question: q, overrides, status: 'streaming', text: '', events: [], filters: null, notes: [] },
    ]);
    setInput('');
    const body: AskRequest = { q, time_format: prefs.clock, filters: overrides };
    if (coords) Object.assign(body, { lat: coords.lat, lng: coords.lng });
    askStream(
      body,
      {
        onMeta: (meta) => patch(id, { events: meta.events, filters: meta.applied_filters, notes: meta.notes }),
        onDelta: (text) => patch(id, (t) => ({ text: t.text + text })),
        onDone: (done) => patch(id, { text: done.answer_text, status: 'done' }),
      },
      controller.signal,
    )
      .then(() => patch(id, (t) => (t.status === 'streaming' ? { status: 'done' } : {})))
      .catch((err: Error) => {
        if (err.name !== 'AbortError') patch(id, { status: 'error', error: err.message });
      });
  };

  return (
    <ThemedView style={styles.fill}>
      <SafeAreaView edges={['top']} style={styles.fill}>
        <KeyboardAvoidingView style={styles.fill} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView
            ref={scroll}
            contentContainerStyle={styles.content}
            onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })}
            keyboardShouldPersistTaps="handled">
            <ThemedText type="subtitle" accessibilityRole="header">
              Ask
            </ThemedText>
            {turns.length === 0 ? (
              <View style={styles.starters}>
                <ThemedText themeColor="textSecondary">Ask about events in plain words. For example:</ThemedText>
                {STARTERS.map((s) => (
                  <Chip key={s} label={s} icon="sparkles" onPress={() => send(s)} />
                ))}
              </View>
            ) : null}
            {turns.map((turn) => (
              <TurnView key={turn.id} turn={turn} labels={labels} onRerun={(o) => send(turn.question, o)} />
            ))}
          </ScrollView>
          <Composer value={input} onChange={setInput} onSend={() => send(input)} />
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ThemedView>
  );
}

function TurnView({
  turn,
  labels,
  onRerun,
}: {
  turn: Turn;
  labels: Record<string, string>;
  onRerun: (overrides: Overrides) => void;
}) {
  const theme = useTheme();
  const router = useRouter();
  const { prefs } = usePrefs();
  const byId = new Map(turn.events.map((e) => [e.id, e]));
  const segments = splitCitations(turn.text, new Set(byId.keys()));
  const open = (id: number) => router.push({ pathname: '/event/[id]', params: { id: String(id) } });

  return (
    <View style={styles.turn}>
      <View style={[styles.question, { backgroundColor: theme.primary }]}>
        <ThemedText style={{ color: theme.onPrimary }}>{turn.question}</ThemedText>
      </View>
      {turn.filters ? (
        <FilterChips filters={turn.filters} overrides={turn.overrides} labels={labels} onRerun={onRerun} />
      ) : null}
      {segments.map((seg, i) =>
        seg.type === 'text' ? (
          <ThemedText key={i}>{seg.text.trim()}</ThemedText>
        ) : (
          <EventCard key={i} event={byId.get(seg.id)!} clock={prefs.clock} compact onPress={() => open(seg.id)} />
        ),
      )}
      {turn.status === 'streaming' && !turn.text ? <ActivityIndicator accessibilityLabel="Finding events" /> : null}
      {turn.status === 'error' ? (
        <ThemedText themeColor="textSecondary">{turn.error}</ThemedText>
      ) : null}
      {turn.notes.map((note) => (
        <ThemedText key={note} type="small" themeColor="textSecondary">
          {note}
        </ThemedText>
      ))}
    </View>
  );
}

/** Applied filters as editable chips: editing one re-asks with that filter overriding the parser. */
function FilterChips({
  filters,
  overrides,
  labels,
  onRerun,
}: {
  filters: AskAppliedFilters;
  overrides: Overrides;
  labels: Record<string, string>;
  onRerun: (overrides: Overrides) => void;
}) {
  const nextWhen = (): When => {
    const current = overrides.when ? WHEN_OPTIONS.indexOf(overrides.when) : -1;
    return WHEN_OPTIONS[(current + 1) % WHEN_OPTIONS.length];
  };
  const last = new Date(new Date(filters.date_to).getTime() - 1);
  const dates = `${formatInTimeZone(filters.date_from, SG_TZ, 'EEE d MMM')} – ${formatInTimeZone(last, SG_TZ, 'EEE d MMM')}`;
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
      {filters.categories.map((c) => (
        <Chip
          key={c}
          label={`${labels[c] ?? c} ✕`}
          accessibilityHint="Remove this category and ask again"
          onPress={() => onRerun({ ...overrides, categories: filters.categories.filter((x) => x !== c) })}
        />
      ))}
      <Chip label={dates} icon="calendar" accessibilityHint="Change the dates and ask again" onPress={() => onRerun({ ...overrides, when: nextWhen() })} />
      {filters.radius_km != null ? (
        <Chip
          label={`${filters.radius_km} km${filters.place ? ` of ${filters.place}` : ''}`}
          icon="locate"
          accessibilityHint="Change the distance and ask again"
          onPress={() =>
            onRerun({ ...overrides, radius_km: RADIUS_CYCLE[(RADIUS_CYCLE.indexOf(filters.radius_km!) + 1) % RADIUS_CYCLE.length] })
          }
        />
      ) : null}
      {filters.area ? <Chip label={`In ${titleCase(filters.area)}`} icon="location" /> : null}
      {filters.free ? (
        <Chip label="Free ✕" accessibilityHint="Include paid events and ask again" onPress={() => onRerun({ ...overrides, free: false })} />
      ) : null}
    </ScrollView>
  );
}

function Composer({ value, onChange, onSend }: { value: string; onChange: (t: string) => void; onSend: () => void }) {
  const theme = useTheme();
  return (
    <View style={[styles.composer, { borderTopColor: theme.border, backgroundColor: theme.background }]}>
      <TextInput
        value={value}
        onChangeText={onChange}
        placeholder="e.g. free career fairs near Sengkang this weekend"
        placeholderTextColor={theme.textSecondary}
        returnKeyType="send"
        onSubmitEditing={onSend}
        maxLength={500}
        accessibilityLabel="Your question"
        style={[styles.input, { color: theme.text, backgroundColor: theme.backgroundElement }]}
      />
      <Pressable
        onPress={onSend}
        accessibilityRole="button"
        accessibilityLabel="Send"
        hitSlop={8}
        style={[styles.send, { backgroundColor: theme.primary }]}>
        <Ionicons name="arrow-up" size={20} color={theme.onPrimary} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  content: { padding: Spacing.three, gap: Spacing.four },
  starters: { gap: Spacing.two, alignItems: 'flex-start' },
  turn: { gap: Spacing.two },
  question: { alignSelf: 'flex-end', maxWidth: '85%', padding: Spacing.three, borderRadius: Spacing.three },
  chips: { gap: Spacing.two },
  composer: {
    flexDirection: 'row',
    gap: Spacing.two,
    padding: Spacing.two,
    borderTopWidth: StyleSheet.hairlineWidth,
    alignItems: 'center',
  },
  input: { flex: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two, fontSize: 16 },
  send: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
});
