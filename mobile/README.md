# SG Events: mobile app

Expo (SDK 57) + TypeScript + Expo Router, TanStack Query, zod. See the `mobile-app-expo`
skill for the design; this file is how to run it.

## Run it on your phone (Expo Go)

1. Start the API so the phone can reach it (from `backend/`):

   ```powershell
   uv run uvicorn app.main:app --host 0.0.0.0 --port 9000
   ```

   Windows may ask to allow Python through the firewall: allow it on private networks.

2. Point the app at your PC's LAN address (phone and PC on the same Wi-Fi):

   ```powershell
   Copy-Item .env.example .env.local   # edit EXPO_PUBLIC_API_URL if your IP differs
   ```

3. Start Expo and scan the QR code with Expo Go:

   ```powershell
   npm start
   ```

Settings shows the server address the app is using.

## Checks

```powershell
npm test            # Jest: filters, SGT times, citations, SSE parsing, schemas vs real API responses, EventCard
npm run typecheck   # tsc, including zod schemas vs the generated API types
npm run gen:api     # after backend model changes: regenerate src/api/generated.ts, then typecheck
```

`maestro test .maestro/onboarding-to-results.yaml` runs the smoke flow on a development build.

## Notes

- Dates always render in Asia/Singapore, whatever the device time zone.
- Location is foreground-only and sent per request, never stored. Without it, the app uses
  a home planning area (from a postal code or a list); only the area is kept.
- Categories, settings and saved events live on the device (saved events work offline).
  Server sync and push notifications come with device accounts (`/me/*`).
- Maps: Expo Go works as is. A development build for Android needs a Google Maps API key
  (`android.config.googleMaps.apiKey` in `app.config.ts`).
- "Add to calendar" uses `expo-calendar/legacy` on purpose: the system add-event form works
  the same on iOS and Android without calendar permission.
