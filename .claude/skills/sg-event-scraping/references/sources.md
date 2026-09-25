# Singapore Event Sources

Starting notes for each source. Site structures, terms and APIs change, so **verify each
item live before building** (open the page, check robots.txt and terms, inspect the
Network tab) and update this file with what you find. Items marked ⚠ need a decision
from the user before scraping.

## Contents
1. Summary table
2. Aggregators & ticketing (Eventbrite, Luma, Peatix, Meetup, SISTIC)
3. Government & national (visitsingapore; STB TIH discontinued; onePA, NLB, NParks, Esplanade)
4. Careers (e2i, WSG, university career centres)
5. Universities & polytechnics
6. News (CNA, Straits Times)
7. Community, religious & non-profit (Tzu Chi, Giving.sg, others)
8. Template for new sources

---

## 1. Summary table

| Source | Best method | Cadence | Main categories | Notes |
|---|---|---|---|---|
| ~~STB TIH~~ | Discontinued 31 Jul 2025 | n/a | n/a | API and portal gone; no successor found (2026-09-25) |
| visitsingapore.com | JSON-LD / HTML | daily | arts, festivals, family, food | robots allows; ⚠ read terms first. Now the main route to TIH's old coverage |
| Eventbrite (eventbrite.sg) | API for known orgs; ⚠ listing pages | 6 h | workshops, networking, career | Public search API was shut down in 2019–2020 |
| Luma (luma.com) | ICS: city feed + org calendars ✅ built | 6 h | tech, startup, networking, arts | Big for SG tech/startup scene; see verified notes below |
| Peatix | JSON-LD on event pages | 6 h | community, workshops | Widely used by SG community groups |
| Meetup | ⚠ API needs OAuth; check terms | 6 h | tech, hobbies, social | |
| onePA | Public JSON endpoints behind the site, else HTML | daily | community, classes, seniors, family | People's Association community clubs island-wide |
| NLB | Event listing pages / feeds | daily | talks, kids, workshops | Library branches everywhere, great for proximity |
| NParks | Event listing pages | daily | nature, family, volunteering | |
| Esplanade | What's On pages, JSON-LD | daily | arts, music, free performances | |
| e2i / WSG | Event pages | daily | career fairs, job fairs | Core for the "career fair" category |
| Universities | ICS/RSS if offered, else HTML | daily | career fairs, talks, open houses | Career portals are login-only: skip |
| CNA | RSS → LLM extraction | hourly | discovery of large events | Store link + own summary only |
| Straits Times | RSS headlines → LLM extraction | hourly | discovery | Paywall: never fetch paywalled bodies |
| Tzu Chi Singapore | HTML → LLM extraction | daily | volunteering, youth, community | Often bilingual EN/ZH |
| Giving.sg | ⚠ check terms/API | daily | volunteering | |

---

## 2. Aggregators & ticketing

### Eventbrite (eventbrite.sg)
- The public `/v3/events/search/` endpoint was removed (announced for Dec 2019, fully off
  by Feb 2020). Remaining API routes: event by ID, events by venue, events by
  organization. Useful once you know organiser IDs (e.g. SG universities, co-working
  spaces, community groups that publish there).
- Event detail pages embed schema.org JSON-LD Events.
- ⚠ Listing/discovery pages: read Eventbrite's current terms on automated access before
  crawling. If not permitted, build an organiser allowlist and use the API instead.
- Online events: `eventAttendanceMode` = OnlineEventAttendanceMode → `is_online=True`.

### Luma (luma.com, formerly lu.ma)
- Verified on: 2026-09-25 (adapter: `backend/scrapers/luma.py`)
- robots.txt: luma.com and api.lu.ma allow SGEventsBot for `/singapore` and `/ics/get`;
  no crawl-delay (we use 2 s).
- Terms: no scraping clause, but Acceptable Use says "You must not access the Service by
  any means other than our publicly supported interfaces." iCal subscription is a
  documented feature (help.luma.com/p/ical-syncing), so **ICS only; never crawl HTML**.
- Method: ICS.
  - City feed: `https://api.lu.ma/ics/get?entity=discover&id=discplace-mUbtdfNjfWaLQ72`
    ("What's Happening in Singapore", ~45 upcoming events, all with GEO).
  - Org calendars: `https://api.lu.ma/ics/get?entity=calendar&id=cal-…`. The list is in
    `CALENDARS` in the adapter. It was seeded from calendars featured on luma.com/singapore;
    individuals' "Personal" calendars are excluded on purpose.
- Cadence: 6 h. No ETag/Last-Modified, so every run is a full fetch (feeds are ~50 KB).
- Quirks:
  - `STATUS` is always `TENTATIVE`, so ignore it except `CANCELLED`.
  - Times are UTC; all-day events use DATE values with an exclusive `DTEND`.
  - `DESCRIPTION` = "Get up-to-date information at: <url>", then an optional "Address:"
    block, the body (truncated with "…"), then "Hosted by …".
  - About half of events hide the address ("Check event page for more details.") but still
    carry `GEO`.
  - Calendar feeds include past events and other cities (London, Shanghai, SF, Tokyo).
    Drop events outside the SG bounding box, and events with no `GEO` that never mention
    Singapore.
  - Calendar-listed external events have a bit.ly `LOCATION` and no address.
  - `ORGANIZER` CN is sometimes an individual's display name (the published host).
- Fixture files: `backend/tests/fixtures/luma/` (city feed, B71 calendar, synthetic edge cases).
- Event pages carry JSON-LD and a `__NEXT_DATA__` blob, but per the terms above, don't use them.
- Luma has an official API, but it is for calendars you manage; don't assume it covers
  discovery.

### Peatix
- Event pages include JSON-LD. Venue strings often include Singapore postal codes.

### Meetup
- ⚠ The API requires OAuth and has usage terms; confirm eligibility before building.

### SISTIC
- Ticketed shows. Check terms; may overlap with Esplanade/visitsingapore, so rely on dedup.

## 3. Government & national

### STB Tourism Information & Services Hub (TIH): discontinued
- Verified on: 2026-09-25. STB discontinued TIH on 31 July 2025; its APIs and data are no
  longer available. `tih.stb.gov.sg`, `tih-dev.stb.gov.sg` and `api.stb.gov.sg` no longer
  resolve, and the developer.tech.gov.sg product page returns 404. No successor API found.

### visitsingapore.com
- "What's happening"/events section. robots.txt allows
  `/whats-happening/all-happenings/` (checked 2026-09-25). ⚠ Terms not yet reviewed.
  Check for JSON-LD first.

### onePA (onepa.gov.sg)
- People's Association: events and courses at community clubs across all
  constituencies. Very strong for community, seniors, family and neighbourhood proximity.
- The site is a JS front-end; inspect the Network tab for the public JSON it loads and
  use that (with polite rate limits) instead of Playwright where possible.
- Listings usually name a CC ("Sengkang CC"): geocode the CC once and cache it.
- Distinguish **events** (one-off) from **courses** (multi-session classes). Map courses to
  `workshop_class` and keep the schedule as `sessions`.

### NLB, NParks, Esplanade, Science Centre, Gardens by the Bay, ActiveSG
- Mostly public event listings with stable venues; ideal for proximity search.
- Pre-seed a `venues` table with their branch/venue coordinates to avoid repeat geocoding.

## 4. Careers

### e2i (NTUC Employment and Employability Institute) and Workforce Singapore
- Run island-wide career/job fairs, often at community clubs and malls. Main source for
  `career_fair` alongside universities.
- Check whether pages carry JSON-LD; otherwise HTML + LLM extraction.

### University career centres
- NUS Centre for Future-ready Graduates, NTU Career & Attachment Office, SMU career
  centre, etc. Their public pages list career fairs; the student portals (e.g.
  TalentConnect, Careers@NTU) are **login-only: do not scrape**. Mark such events
  `audience="students_only"` when the page says so.

## 5. Universities & polytechnics

NUS, NTU, SMU, SUTD, SIT, SUSS, and the five polytechnics (NP, SP, TP, RP, NYP).
- Look for ICS/RSS on each events page first; many university CMSs expose one.
- Useful categories: career fairs, public lectures/talks, open houses (seasonal, around
  admissions), arts performances.
- Tag `audience` (public / students_only / alumni) because many events aren't public.

## 6. News

### CNA (channelnewsasia.com)
- Use its RSS feeds (find the feeds page) for Singapore/lifestyle sections.
- Pipeline: RSS item → cheap keyword/Haiku pre-filter "does this mention an upcoming
  public event in SG with a date?" → fetch article (if allowed) → LLM extraction →
  store event facts + article URL. Do not store article text.

### The Straits Times (straitstimes.com)
- Many articles are paywalled. Use RSS titles/summaries only; never fetch or store
  paywalled bodies. Weekend "things to do" style pieces are the richest discovery
  inputs; extract facts, link out, and prefer re-finding the event at its primary source
  (organiser page) for full details.
- News-derived events get `confidence` lowered until matched to a primary source.

## 7. Community, religious & non-profit

### Tzu Chi Singapore (Tzu Chi Humanistic Youth Centre, etc.)
- Announcements are often bilingual (English/Chinese), sometimes posters/images only.
- HTML → LLM extraction with language detection; set `language` and keep both titles
  if present (`title`, `title_alt`).
- Image-only posters: optional Claude vision extraction; mark `confidence="low"`.

### Other community sources to evaluate
Giving.sg (volunteering), SG Cares, self-help groups (CDAC, Mendaki, SINDA, Eurasian
Association), religious organisations (MUIS mosques, temples, churches) for festivals
and community programmes, ground-up youth groups, co-working spaces with public
calendars. Many publish on Instagram/Telegram/Facebook only; those platforms prohibit
scraping, so offer an "organiser submission" form in the app instead.

---

## 8. Template for new sources

```
### <Source name> (<domain>)
- Verified on: YYYY-MM-DD
- robots.txt: allowed paths / crawl-delay
- Terms: automated access permitted? (quote section + link)
- Method: API | ICS | RSS | JSON-LD | JSON endpoint | HTML | Playwright | LLM
- Entry URLs:
- Cadence:
- Category hints:
- Quirks:
- Fixture files:
```
