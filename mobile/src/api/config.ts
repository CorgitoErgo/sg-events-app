import Constants from 'expo-constants';

/** Base URL of the SG Events API, from app.config.ts `extra.apiUrl`. Never hard-coded. */
export const API_URL: string = String(Constants.expoConfig?.extra?.apiUrl ?? 'http://localhost:9000').replace(
  /\/+$/,
  '',
);
