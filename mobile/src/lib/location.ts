/**
 * Foreground location only, requested with a rationale screen first. Coordinates are sent
 * with each search and never stored; the last fix is cached for 10 minutes to avoid
 * repeated GPS calls (mobile-app-expo skill).
 */
import * as Location from 'expo-location';
import { useCallback, useEffect, useState } from 'react';
import { Linking } from 'react-native';

const CACHE_MS = 10 * 60 * 1000;
let cached: { lat: number; lng: number; at: number } | null = null;

export type Coords = { lat: number; lng: number };
export type PermissionState = 'unknown' | 'granted' | 'denied' | 'undetermined';

async function currentCoords(): Promise<Coords | null> {
  if (cached && Date.now() - cached.at < CACHE_MS) return cached;
  try {
    const last = await Location.getLastKnownPositionAsync({ maxAge: CACHE_MS });
    const pos = last ?? (await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }));
    cached = { lat: pos.coords.latitude, lng: pos.coords.longitude, at: Date.now() };
    return cached;
  } catch {
    return null; // location services off, or no fix yet
  }
}

export function useDeviceLocation() {
  const [permission, setPermission] = useState<PermissionState>('unknown');
  const [coords, setCoords] = useState<Coords | null>(cached);

  const refresh = useCallback(async () => {
    const { status } = await Location.getForegroundPermissionsAsync();
    setPermission(status as PermissionState);
    if (status === 'granted') setCoords(await currentCoords());
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /** Ask for permission (after the rationale screen). Returns whether it was granted. */
  const request = useCallback(async () => {
    const { status, canAskAgain } = await Location.requestForegroundPermissionsAsync();
    setPermission(status as PermissionState);
    if (status === 'granted') {
      setCoords(await currentCoords());
      return true;
    }
    if (!canAskAgain) await Linking.openSettings();
    return false;
  }, []);

  return { permission, coords, request, refresh };
}
