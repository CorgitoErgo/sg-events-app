/**
 * zod schemas for every API response, mirroring the backend's Pydantic models.
 * Each is checked against the types generated from the OpenAPI spec (`npm run gen:api`),
 * so `tsc` fails if the backend and the app drift apart.
 */
import { z } from 'zod';

import type { components } from './generated';

type S = components['schemas'];

export const VenueSchema = z.object({
  name: z.string(),
  address: z.string().nullable(),
  postal_code: z.string().nullable(),
  planning_area: z.string().nullable(),
  region: z.string().nullable(),
}) satisfies z.ZodType<S['VenueOut']>;

export const LocationSchema = z.object({ lat: z.number(), lng: z.number() }) satisfies z.ZodType<S['Location']>;

export const SourceSchema = z.object({ source_id: z.string(), url: z.string() }) satisfies z.ZodType<
  S['SourceOut']
>;

export const EventSchema = z.object({
  id: z.number().int(),
  title: z.string(),
  summary: z.string().nullable(),
  starts_at: z.string(),
  ends_at: z.string().nullable(),
  all_day: z.boolean(),
  is_online: z.boolean(),
  is_free: z.boolean().nullable(),
  price_min_sgd: z.number().nullable(),
  price_max_sgd: z.number().nullable(),
  categories: z.array(z.string()),
  audience: z.string(),
  organizer: z.string().nullable(),
  image_url: z.string().nullable(),
  registration_url: z.string().nullable(),
  confidence: z.string(),
  status: z.string(),
  venue: VenueSchema.nullable(),
  location: LocationSchema.nullable(),
  distance_m: z.number().nullable(),
  sources: z.array(SourceSchema),
}) satisfies z.ZodType<S['EventOut']>;

export const EventDetailSchema = EventSchema.extend({
  title_alt: z.string().nullable(),
  description: z.string().nullable(),
  language: z.array(z.string()).nullable(),
  sessions: z.array(z.object({ starts_at: z.string(), ends_at: z.string().nullable() })),
  first_seen_at: z.string(),
  last_seen_at: z.string(),
}) satisfies z.ZodType<S['EventDetail']>;

export const AppliedFiltersSchema = z.object({
  categories: z.array(z.string()),
  date_from: z.string(),
  date_to: z.string(),
  when: z.string().nullable(),
  lat: z.number().nullable(),
  lng: z.number().nullable(),
  radius_km: z.number().nullable(),
  area: z.string().nullable(),
  region: z.string().nullable(),
  free: z.boolean().nullable(),
  online: z.string(),
  include_restricted: z.boolean(),
  sort: z.string(),
}) satisfies z.ZodType<S['AppliedFilters']>;

export const EventsPageSchema = z.object({
  items: z.array(EventSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  next_offset: z.number().int().nullable(),
  filters: AppliedFiltersSchema,
}) satisfies z.ZodType<S['EventsPage']>;

export const CategorySchema = z.object({ id: z.string(), label: z.string(), includes: z.string() });
export const CategoriesSchema = z.array(CategorySchema);

export const AreaSchema = z.object({ planning_area: z.string(), region: z.string() }) satisfies z.ZodType<
  S['AreaOut']
>;
export const AreasSchema = z.array(AreaSchema);

export const AskAppliedFiltersSchema = z.object({
  semantic_query: z.string(),
  categories: z.array(z.string()),
  date_from: z.string(),
  date_to: z.string(),
  place: z.string().nullable(),
  lat: z.number().nullable(),
  lng: z.number().nullable(),
  radius_km: z.number().nullable(),
  area: z.string().nullable(),
  region: z.string().nullable(),
  free: z.boolean().nullable(),
  online: z.string(),
  include_restricted: z.boolean(),
}) satisfies z.ZodType<S['AskAppliedFilters']>;

export const AskResponseSchema = z.object({
  answer_text: z.string(),
  cited_event_ids: z.array(z.number().int()),
  events: z.array(EventSchema),
  applied_filters: AskAppliedFiltersSchema,
  parser: z.enum(['llm', 'rules']),
  answered_by: z.enum(['claude', 'template']),
  notes: z.array(z.string()),
}) satisfies z.ZodType<S['AskResponse']>;

/** SSE "meta" and "done" payloads from POST /ask with stream: true. */
export const AskMetaSchema = z.object({
  applied_filters: AskAppliedFiltersSchema,
  events: z.array(EventSchema),
  parser: z.enum(['llm', 'rules']),
  notes: z.array(z.string()),
});
export const AskDoneSchema = z.object({
  answer_text: z.string(),
  cited_event_ids: z.array(z.number().int()),
  answered_by: z.enum(['claude', 'template']),
});

export type Event = z.infer<typeof EventSchema>;
export type EventDetail = z.infer<typeof EventDetailSchema>;
export type EventsPage = z.infer<typeof EventsPageSchema>;
export type Category = z.infer<typeof CategorySchema>;
export type Area = z.infer<typeof AreaSchema>;
export type AskAppliedFilters = z.infer<typeof AskAppliedFiltersSchema>;
export type AskResponse = z.infer<typeof AskResponseSchema>;
export type AskMeta = z.infer<typeof AskMetaSchema>;
export type AskDone = z.infer<typeof AskDoneSchema>;
