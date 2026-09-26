import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useEffect, useState } from 'react';
import { useColorScheme } from 'react-native';

import { PrefsProvider } from '@/lib/prefs';

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const colorScheme = useColorScheme();
  const [queryClient] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 60_000, retry: 1 } } }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
        <PrefsProvider>
          <HideSplash />
          <Stack>
            <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
            <Stack.Screen name="(onboarding)" options={{ headerShown: false }} />
            <Stack.Screen name="event/[id]" options={{ title: 'Event', headerBackTitle: 'Back' }} />
          </Stack>
        </PrefsProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

/** Rendered once preferences have loaded (PrefsProvider renders nothing until then). */
function HideSplash() {
  useEffect(() => {
    void SplashScreen.hideAsync();
  }, []);
  return null;
}
