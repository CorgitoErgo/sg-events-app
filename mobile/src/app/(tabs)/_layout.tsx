import Ionicons from '@expo/vector-icons/Ionicons';
import { Redirect } from 'expo-router';
import { Tabs } from 'expo-router/js-tabs';

import { useTheme } from '@/hooks/use-theme';
import { usePrefs } from '@/lib/prefs';

export default function TabsLayout() {
  const { prefs } = usePrefs();
  const theme = useTheme();
  if (!prefs.onboarded) return <Redirect href="/categories" />;

  return (
    <Tabs screenOptions={{ headerShown: false, tabBarActiveTintColor: theme.primary }}>
      <Tabs.Screen
        name="index"
        options={{ title: 'Discover', tabBarIcon: ({ color, size }) => <Ionicons name="compass" color={color} size={size} /> }}
      />
      <Tabs.Screen
        name="ask"
        options={{ title: 'Ask', tabBarIcon: ({ color, size }) => <Ionicons name="chatbubbles" color={color} size={size} /> }}
      />
      <Tabs.Screen
        name="saved"
        options={{ title: 'Saved', tabBarIcon: ({ color, size }) => <Ionicons name="bookmark" color={color} size={size} /> }}
      />
      <Tabs.Screen
        name="settings"
        options={{ title: 'Settings', tabBarIcon: ({ color, size }) => <Ionicons name="settings" color={color} size={size} /> }}
      />
    </Tabs>
  );
}
