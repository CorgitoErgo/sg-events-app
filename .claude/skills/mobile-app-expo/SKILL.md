---
name: mobile-app-expo
description: How to build the SG events mobile app with Expo (React Native + TypeScript) — screens, category picker and subscriptions, location permission and "near me" search, map/list views, event detail and calendar export, the Ask (RAG chat) screen with tappable event citations, push notifications for new matching events, and the API client. Use this skill for any work in the mobile/ folder, UI or UX questions, iOS/Android permissions, maps, notifications, or wiring the app to the FastAPI backend.
---

# Mobile App (Expo)

## Setup

```bash
npx create-expo-app@latest mobile --template   # choose the TypeScript + Expo Router template
cd mobile
npx expo install expo-location react-native-maps expo-notifications expo-calendar \
  expo-secure-store expo-linking
npm i @tanstack/react-query zod date-fns date-fns-tz
```

Use a development build (`npx expo run:ios` / `run:android` or EAS) once maps and
notifications are involved; Expo Go has limits for push notifications.

API base URL from `app.config.ts` `extra.apiUrl`; never hard-code. Validate every API
response with `zod` schemas mirrored from the backend's Pydantic models (generate them
from the FastAPI OpenAPI spec with `openapi-typescript` to keep them in sync).

## Screens (Expo Router)

```
app/
  (onboarding)/categories.tsx   # pick categories on first launch
  (onboarding)/location.tsx     # permission rationale + postal-code fallback
  (tabs)/index.tsx              # Discover: list/map toggle, filter chips
  (tabs)/ask.tsx                # RAG chat
  (tabs)/saved.tsx              # saved events + saved searches
  (tabs)/settings.tsx           # categories, radius, notifications, home area
  event/[id].tsx                # detail
```

**Categories picker.** Fetch `GET /categories`, show as a grid of chips with icons;
multi-select, persist locally and to `POST /me/subscriptions`. Career fairs and
community events should be visible without scrolling.

**Discover.** Filter chips row: categories, date (Today / This weekend / Next 7 days /
Custom), radius (1 / 3 / 5 / 10 km / Anywhere), Free only, Include online.
List cards show title, date/time in SGT, venue + distance ("1.2 km · Sengkang"),
price badge, category icon. Map view uses `react-native-maps` with marker clustering;
tapping a marker opens a bottom sheet card. Paginate with `useInfiniteQuery`.

**Event detail.** Full info, source attribution ("From onePA", "Also on Eventbrite"),
buttons: Register (opens `registration_url`), Directions (opens Google/Apple Maps with
coordinates), Add to calendar (`expo-calendar`), Save, Share, "Wrong category?" report.
For `confidence='low'` show "Details from a news report; check the source".

**Ask.** Chat UI calling `POST /ask` with streaming. Render `[E1842]` citations as inline
event cards (look up from `cited_event_ids`). Show `applied_filters` as editable chips
above the answer; editing a chip re-runs the query with that filter overriding the
parser. Offer starter prompts: "Career fairs near me this month", "Free weekend
activities for kids", "Volunteering this Saturday".

## Location

```ts
import * as Location from "expo-location";

const { status } = await Location.requestForegroundPermissionsAsync();
if (status === "granted") {
  const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
  // send pos.coords.latitude/longitude with each search request only
} else {
  // fallback: ask for postal code or planning area; backend geocodes via OneMap
}
```

- Foreground permission only; the app doesn't need background location. Background
  location invites store-review scrutiny and isn't needed for search.
- Write clear permission strings in `app.config.ts` (`NSLocationWhenInUseUsageDescription`:
  "Used to show events near you. Your location isn't stored.").
- Cache the last position for 10 minutes to avoid repeated GPS calls.

## Notifications

`expo-notifications` → get the Expo push token → `POST /me/push-token`. The backend
digest job (see `rag-pipeline`) sends "New events matching your categories". Deep-link
notifications to `event/[id]` or to Discover with the saved search applied. Let users set
digest frequency and quiet hours in Settings.

## Identity & data

Start with anonymous device accounts (server-issued ID in `expo-secure-store`) so
onboarding is frictionless; add sign-in later for multi-device sync. Provide
"Delete my data" in Settings (PDPA), which calls `DELETE /me`.

## Quality bar

- Dates always rendered in `Asia/Singapore` with `date-fns-tz`, regardless of device
  timezone (travellers, emulators).
- Empty states that suggest a fix ("No career fairs within 3 km this weekend. Try
  10 km?").
- Accessibility: labels on icon-only buttons, dynamic type, colour contrast on category
  chips.
- Offline: TanStack Query persistence so saved events show without network.
- Tests: Jest + React Native Testing Library for filter-chip logic and citation parsing;
  one Maestro or Detox smoke flow (onboard → pick career fairs → see results).
