import { useEffect, useMemo, useRef, useState } from 'react';
import { Linking, StyleSheet, View } from 'react-native';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { mapHtml, parseMapMessage, safeJson, type MapPoint } from '@/lib/leaflet-map';

type Props = {
  points: MapPoint[];
  center: { lat: number; lng: number };
  zoom: number;
  dark: boolean;
  userLocation: { lat: number; lng: number } | null;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
};

/** Leaflet + OneMap tiles in a WebView (the Android map; see lib/leaflet-map.ts). */
export function OneMapWebView({ points, center, zoom, dark, userLocation, selectedId, onSelect }: Props) {
  const web = useRef<WebView>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  // Built once per theme: a new page would reset the view. Later changes are injected.
  const [initial] = useState({ center, zoom, userLocation });
  const html = useMemo(() => mapHtml({ ...initial, dark }), [initial, dark]);

  useEffect(() => {
    if (ready) web.current?.injectJavaScript(`window.setMarkers(${safeJson(points)}); true;`);
  }, [ready, points]);

  useEffect(() => {
    if (ready) web.current?.injectJavaScript(`window.select(${safeJson(selectedId)}); true;`);
  }, [ready, selectedId]);

  const onMessage = (e: WebViewMessageEvent) => {
    const msg = parseMapMessage(e.nativeEvent.data);
    if (msg?.type === 'ready') setReady(true);
    else if (msg?.type === 'select') onSelect(msg.id);
    else if (msg?.type === 'clear') onSelect(null);
    else if (msg?.type === 'error') setFailed(msg.message);
  };

  if (failed) {
    return (
      <View style={styles.failed}>
        <ThemedText>The map couldn&apos;t load ({failed}). Check your internet connection, or use the list.</ThemedText>
      </View>
    );
  }
  return (
    <WebView
      ref={web}
      style={StyleSheet.absoluteFill}
      source={{ html }}
      key={dark ? 'dark' : 'light'}
      originWhitelist={['*']}
      onLoadStart={() => setReady(false)}
      onMessage={onMessage}
      onError={(e) => setFailed(e.nativeEvent.description || 'page error')}
      // The page never navigates; anything else opens in the browser.
      onShouldStartLoadWithRequest={(req) => {
        if (req.url.startsWith('about:') || req.url.startsWith('data:')) return true;
        void Linking.openURL(req.url);
        return false;
      }}
      setSupportMultipleWindows={false}
      overScrollMode="never"
      accessibilityLabel={`Map with ${points.length} events`}
    />
  );
}

const styles = StyleSheet.create({
  failed: { flex: 1, justifyContent: 'center', padding: Spacing.four },
});
