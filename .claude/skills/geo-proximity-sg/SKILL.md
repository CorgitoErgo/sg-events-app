---
name: geo-proximity-sg
description: Location and proximity search for Singapore events — geocoding venues, postal codes, community clubs and MRT stations with OneMap (Singapore Land Authority), PostGIS radius queries, distance sorting, planning areas/regions, and handling the user's device location privately. Use this skill whenever the task involves "near me", radius or distance filters, maps, geocoding addresses, postal codes, SVY21 coordinates, planning areas like Sengkang or Tampines, or any location bug.
---

# Geo Proximity (Singapore)

## Why OneMap

OneMap is SLA's national map API: free, built for Singapore addresses, understands
6-digit postal codes, HDB blocks, MRT stations, CCs and buildings, and returns WGS84
lat/lng plus SVY21 X/Y. Use it for all geocoding; don't pay for Google Geocoding unless
OneMap misses something.

**Verify the current endpoints in the OneMap API docs before coding** (the domain moved
from `developers.onemap.sg` to `www.onemap.gov.sg` and some endpoints now need a token).
At time of writing:

- Token: `POST https://www.onemap.gov.sg/api/auth/post/getToken` with
  `{email, password}` → JWT valid ~3 days. Cache it and refresh before expiry.
- Search/geocode: `GET /api/common/elastic/search?searchVal=<q>&returnGeom=Y&getAddrDetails=Y&pageNum=1`
- Reverse geocode: `GET /api/public/revgeocode?location=<lat>,<lng>&buffer=40`
- Planning area for a point: `GET /api/public/popapi/getPlanningarea?latitude=..&longitude=..`

Wrap these in `backend/pipeline/geo/onemap.py` with a token manager, 5 req/s limit, and
retries. Note the API has historically returned both `LONGITUDE` and a misspelt
`LONGTITUDE` key; read whichever is present.

## Geocoding venues (pipeline step)

Order of attempts, stopping at the first success:

1. Coordinates from the source (JSON-LD `geo`, Luma data) → validate only.
2. `venues` table cache hit on normalized name or postal code.
3. Postal code regex `\b(?:Singapore\s*)?(\d{6})\b` → OneMap search on the postal code
   (most precise).
4. Full address → OneMap search.
5. Venue name alone ("Our Tampines Hub", "Sengkang CC") → OneMap search; accept only if
   the result's `SEARCHVAL`/`BUILDING` fuzzy-matches the name (`token_set_ratio >= 80`).
6. Give up: leave `geom` null, set `confidence='medium'`, and surface the event only in
   non-proximity views. Log it for a manual `venues` fix.

**Sanity check** every point: Singapore bounding box roughly lat 1.15–1.48,
lng 103.59–104.10. Outside it → reject (common cause: swapped lat/lng).

Pre-seed `venues` with fixed, frequently used places: all CCs (onePA), NLB branches,
university campuses, Esplanade, Expo, Suntec, Marina Bay Sands, major malls. This
removes most repeat geocoding.

After geocoding, fetch the planning area and store `planning_area` and `region` on the
venue so users can filter "events in Sengkang" or "North-East" without a radius.

## Proximity query

```sql
-- :lat, :lng from the device or a geocoded place; :radius_m e.g. 5000
SELECT e.*,
       ST_Distance(e.geom, ST_MakePoint(:lng, :lat)::geography) AS distance_m
FROM events e
WHERE e.status = 'active'
  AND coalesce(e.ends_at, e.starts_at) >= now()
  AND e.starts_at < :to_ts
  AND e.categories && :categories::text[]
  AND e.geom IS NOT NULL
  AND ST_DWithin(e.geom, ST_MakePoint(:lng, :lat)::geography, :radius_m)
ORDER BY distance_m, e.starts_at
LIMIT :limit OFFSET :offset;
```

`ST_MakePoint` takes **(lng, lat)**. `ST_DWithin` on `geography` uses metres and the GiST
index. Online events are excluded from radius results but shown in a separate "Online"
section.

Radius presets for the app: 1, 3, 5 (default), 10 km, and "Anywhere in SG". Singapore
is ~50 km across, so 10 km already covers a large slice of the island.

Sort options: distance, soonest, or a blended score
`0.6 * (1 - distance/radius) + 0.4 * (1 - hours_until_start / window_hours)`.

## Place-name queries

"Career fairs near Sengkang MRT" or "near 540123": geocode the place text with OneMap,
then run the same radius query. Recognise planning-area names ("in Tampines") and filter
by `planning_area` instead of radius when the user says "in" rather than "near".

## User location privacy (PDPA)

- The app sends coordinates per request only; the backend never stores raw user
  locations. Log requests with coordinates rounded to 2 decimal places (~1 km) at most.
- Saved "home area" is stored as a planning area or postal-sector (first two digits), not a
  precise point, unless the user explicitly saves an address.
- Explain in the app's permission rationale why location is needed and offer a
  postal-code/area fallback when permission is denied.

## Optional later: travel time

For "within 20 min by public transport", OneMap routing (needs token) can compute
transit times, but it's per origin-destination pair. Do it only for the top ~20 results
after the radius filter, and cache by (origin geohash6, venue_id).

## Tests

Fixtures of real OneMap responses (postal code, building name, no-result, multi-result)
in `tests/fixtures/onemap/`. Unit-test the bounding-box check, lat/lng order, and the
fuzzy acceptance rule. Never hit the live API in CI.
