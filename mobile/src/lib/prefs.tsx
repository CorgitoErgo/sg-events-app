/**
 * On-device preferences and saved events (AsyncStorage). Saved events keep the full event so
 * they show offline. Syncing to the server (/me/*) comes with anonymous device accounts.
 * The home area is a planning area, never a precise point (geo-proximity-sg skill, PDPA).
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import type { Event } from '@/api/schemas';
import type { RadiusKm } from '@/lib/filters';
import type { Clock } from '@/lib/time';

const KEY = 'sg-events:prefs:v1';

export type Prefs = {
  onboarded: boolean;
  categories: string[];
  radiusKm: RadiusKm;
  homeArea: string | null;
  clock: Clock;
  saved: Record<string, Event>;
};

export const DEFAULT_PREFS: Prefs = {
  onboarded: false,
  categories: [],
  radiusKm: 5,
  homeArea: null,
  clock: '24h',
  saved: {},
};

type PrefsContextValue = {
  prefs: Prefs;
  update: (patch: Partial<Prefs>) => void;
  toggleSaved: (event: Event) => void;
  isSaved: (id: number) => boolean;
  clearAll: () => Promise<void>;
};

const PrefsContext = createContext<PrefsContextValue | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Prefs | null>(null);

  useEffect(() => {
    AsyncStorage.getItem(KEY)
      .then((raw) => setPrefs(raw ? { ...DEFAULT_PREFS, ...JSON.parse(raw) } : DEFAULT_PREFS))
      .catch(() => setPrefs(DEFAULT_PREFS));
  }, []);

  const persist = useCallback((next: Prefs) => {
    setPrefs(next);
    AsyncStorage.setItem(KEY, JSON.stringify(next)).catch((err) => console.warn('Saving preferences failed', err));
  }, []);

  const value = useMemo<PrefsContextValue | null>(() => {
    if (!prefs) return null;
    return {
      prefs,
      update: (patch) => persist({ ...prefs, ...patch }),
      toggleSaved: (event) => {
        const saved = { ...prefs.saved };
        if (saved[event.id]) delete saved[event.id];
        else saved[event.id] = event;
        persist({ ...prefs, saved });
      },
      isSaved: (id) => Boolean(prefs.saved[id]),
      clearAll: async () => {
        await AsyncStorage.removeItem(KEY);
        setPrefs(DEFAULT_PREFS);
      },
    };
  }, [prefs, persist]);

  if (!value) return null; // the splash screen stays up until preferences load
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export function usePrefs(): PrefsContextValue {
  const ctx = useContext(PrefsContext);
  if (!ctx) throw new Error('usePrefs must be used inside PrefsProvider');
  return ctx;
}
