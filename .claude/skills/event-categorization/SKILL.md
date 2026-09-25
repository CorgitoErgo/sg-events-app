---
name: event-categorization
description: The category taxonomy (career fairs, community events, volunteering, workshops, tech/startup, arts, family, etc.) and the classifier that assigns multi-label categories to Singapore events. Use this skill whenever adding or renaming a category, writing or tuning the classifier prompt, mapping a source's own tags to app categories, building the category picker in the app, or investigating why an event landed in the wrong category.
---

# Event Categorization

## Taxonomy

Multi-label: an event can hold up to 3 categories. IDs are stable slugs; labels and icons
live in the app so they can change without a migration. Keep this list in one place,
`backend/app/categories.py`, and expose it through `GET /categories` so the app never
hard-codes it.

| id | Label | Includes | Typical sources |
|---|---|---|---|
| `career_fair` | Career fairs & job fairs | job fairs, recruitment drives, career expos, hiring days | e2i, WSG, universities |
| `career_dev` | Career development | resume clinics, career talks, mentoring, industry sharing | universities, Luma |
| `networking` | Networking | mixers, meetups for professionals, founder nights | Luma, Eventbrite |
| `tech_startup` | Tech & startups | hackathons, demo days, dev meetups, AI talks | Luma, Meetup |
| `community` | Community events | CC events, block parties, neighbourhood carnivals, festive gatherings | onePA |
| `volunteering` | Volunteering | volunteer drives, donation drives, befriending, clean-ups | Tzu Chi, Giving.sg |
| `workshop_class` | Workshops & classes | courses, hands-on classes, skill workshops | onePA, Peatix, NLB |
| `talks` | Talks & seminars | public lectures, panels, book talks | universities, NLB |
| `arts_culture` | Arts & culture | exhibitions, theatre, heritage walks, cultural festivals | Esplanade, TIH |
| `music` | Music & performances | concerts, gigs, free performances | Esplanade, SISTIC |
| `family_kids` | Family & kids | children's programmes, school-holiday activities | NLB, Science Centre |
| `sports_fitness` | Sports & fitness | runs, group workouts, tournaments | ActiveSG, onePA |
| `nature_outdoors` | Nature & outdoors | guided walks, gardening, park events | NParks |
| `food_markets` | Food & markets | bazaars, flea markets, food festivals | TIH, news |
| `health_wellness` | Health & wellness | health screenings, mental wellness, mindfulness | onePA, community orgs |
| `faith_festivals` | Religious & festive | festival celebrations, open houses, religious community programmes | community sites |
| `education_open_house` | Open houses & education fairs | uni/poly open houses, education fairs | universities, polys |
| `youth` | Youth | youth programmes, youth centres, student-led events | Tzu Chi youth, youth groups |
| `seniors` | Seniors | active ageing programmes, senior-friendly classes | onePA |

Plus two boolean facets that are **not** categories: `is_free` and `is_online`.

## Classifier

Two stages, cheapest first.

**Stage 1: rules.** Map each source's own tags (`source_categories`) and strong keywords
to categories, e.g. "job fair", "career fair", "recruitment" → `career_fair`;
"volunteer" → `volunteering`; onePA "courses" → `workshop_class`. Keep the map in
`categories.py` (`SOURCE_TAG_MAP`, `KEYWORD_RULES`). If rules give ≥ 1 high-precision
match and the source is category-specific (e.g. e2i), stop here.

**Stage 2: Claude Haiku** for everything else, and to add secondary labels.

```python
tools = [{
  "name": "set_categories",
  "description": "Assign 1-3 categories to a Singapore event.",
  "input_schema": {
    "type": "object",
    "properties": {
      "categories": {"type": "array", "items": {"enum": CATEGORY_IDS},
                     "minItems": 1, "maxItems": 3},
      "audience": {"enum": ["public", "students_only", "members_only", "alumni"]},
      "confidence": {"enum": ["high", "medium", "low"]}
    },
    "required": ["categories", "audience", "confidence"]
  }
}]
# tool_choice={"type": "tool", "name": "set_categories"}, temperature=0
```

The system prompt should give the table above (id + includes), 4–6 labelled examples
covering the confusable pairs, and the rule "primary category first". Confusable pairs
worth examples: `career_fair` vs `career_dev` (is there an employer exhibition/hiring?),
`networking` vs `tech_startup`, `community` vs `faith_festivals`, `workshop_class` vs
`talks` (hands-on vs listening).

Input to the model: title, first 1,500 chars of description, organizer, source tags,
venue name. Batch with the Message Batches API for backfills; it's cheaper and there's no
latency need.

## Quality loop

- Keep `tests/fixtures/categorization_gold.jsonl` with ~150 hand-labelled SG events
  (aim for ≥ 5 per category). CI runs the classifier on it and reports per-category
  precision/recall; fail the build if `career_fair` precision drops below 0.9, since
  that's the flagship filter.
- Log user "wrong category" reports from the app into a `category_feedback` table and
  fold confirmed ones into the gold set.
- When adding a category: add to `categories.py`, add gold examples, re-run the
  classifier over active events (batch), and bump the app's category cache.
