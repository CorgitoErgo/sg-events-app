import type { ExpoConfig } from 'expo/config';

// The API address comes from EXPO_PUBLIC_API_URL (set it in mobile/.env.local, e.g.
// http://192.168.0.4:9000 so a phone on the same Wi-Fi can reach the dev server).
const apiUrl = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:9000';

const config: ExpoConfig = {
  name: 'SG Events',
  slug: 'sg-events',
  version: '0.1.0',
  orientation: 'portrait',
  icon: './assets/images/icon.png',
  scheme: 'sgevents',
  userInterfaceStyle: 'automatic',
  ios: {
    icon: './assets/expo.icon',
    bundleIdentifier: 'com.corgitoergo.sgevents',
  },
  android: {
    package: 'com.corgitoergo.sgevents',
    adaptiveIcon: {
      backgroundColor: '#E6F4FE',
      foregroundImage: './assets/images/android-icon-foreground.png',
      backgroundImage: './assets/images/android-icon-background.png',
      monochromeImage: './assets/images/android-icon-monochrome.png',
    },
    predictiveBackGestureEnabled: false,
  },
  web: {
    output: 'static',
    favicon: './assets/images/favicon.png',
  },
  plugins: [
    'expo-router',
    [
      'expo-splash-screen',
      {
        backgroundColor: '#208AEF',
        image: './assets/images/splash-icon.png',
        imageWidth: 76,
      },
    ],
    [
      // Foreground only: search doesn't need background location (mobile-app-expo skill).
      'expo-location',
      {
        locationWhenInUsePermission: "Used to show events near you. Your location isn't stored.",
        locationAlwaysAndWhenInUsePermission: false,
        locationAlwaysPermission: false,
        motionUsagePermission: false,
        isIosBackgroundLocationEnabled: false,
        isAndroidBackgroundLocationEnabled: false,
      },
    ],
    [
      'expo-calendar',
      {
        writeOnlyAccess: true,
        writeOnlyCalendarPermission: 'Used to add events you choose to your calendar.',
        calendarPermission: false,
        remindersPermission: false,
      },
    ],
  ],
  experiments: {
    typedRoutes: true,
    reactCompiler: true,
  },
  extra: {
    apiUrl,
  },
};

export default config;
