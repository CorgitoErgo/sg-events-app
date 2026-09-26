import { fetch as streamingFetch } from 'expo/fetch';
import type { z } from 'zod';

import { type QueryParams, toQueryString } from '@/lib/filters';

import { API_URL } from './config';
import {
  AreaSchema,
  AreasSchema,
  AskDoneSchema,
  AskMetaSchema,
  AskResponseSchema,
  CategoriesSchema,
  EventDetailSchema,
  EventsPageSchema,
  type AskDone,
  type AskMeta,
} from './schemas';
import { SseParser } from './sse';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
  }
}

const OFFLINE = "Can't reach the events server. Check your connection and try again.";

async function request<T>(path: string, schema: z.ZodType<T>, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, { ...init, signal, headers: { Accept: 'application/json', ...init?.headers } });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new ApiError(OFFLINE);
  }
  if (!resp.ok) throw new ApiError(await errorMessage(resp), resp.status);
  const parsed = schema.safeParse(await resp.json());
  if (!parsed.success) {
    console.warn(`Unexpected response from ${path}`, parsed.error.issues.slice(0, 3));
    throw new ApiError('The server sent something unexpected. Please update the app.');
  }
  return parsed.data;
}

async function errorMessage(resp: Response): Promise<string> {
  try {
    const body = await resp.json();
    if (typeof body?.detail === 'string') return body.detail;
    if (Array.isArray(body?.detail) && body.detail[0]?.msg) return String(body.detail[0].msg);
  } catch {
    // not JSON
  }
  return `Request failed (${resp.status})`;
}

export const api = {
  categories: (signal?: AbortSignal) => request('/categories', CategoriesSchema, undefined, signal),

  areas: (signal?: AbortSignal) => request('/areas', AreasSchema, undefined, signal),

  resolvePostal: (postal: string) => request(`/areas/resolve${toQueryString({ postal })}`, AreaSchema),

  events: (params: QueryParams, offset: number, signal?: AbortSignal) =>
    request(`/events${toQueryString({ ...params, offset, limit: 20 })}`, EventsPageSchema, undefined, signal),

  event: (id: number, signal?: AbortSignal) => request(`/events/${id}`, EventDetailSchema, undefined, signal),

  ask: (body: AskRequest, signal?: AbortSignal) =>
    request(
      '/ask',
      AskResponseSchema,
      { method: 'POST', body: JSON.stringify({ ...body, stream: false }), headers: { 'Content-Type': 'application/json' } },
      signal,
    ),
};

export type AskRequest = {
  q: string;
  lat?: number;
  lng?: number;
  time_format?: '24h' | '12h';
  filters?: {
    categories?: string[];
    when?: 'today' | 'weekend' | 'week' | 'month';
    radius_km?: number;
    area?: string;
    free?: boolean;
    online?: 'include' | 'exclude' | 'only';
  };
};

export type AskStreamHandlers = {
  onMeta: (meta: AskMeta) => void;
  onDelta: (text: string) => void;
  onDone: (done: AskDone) => void;
};

/** POST /ask with streaming (Server-Sent Events via expo/fetch). Resolves when the stream ends. */
export async function askStream(body: AskRequest, handlers: AskStreamHandlers, signal?: AbortSignal): Promise<void> {
  let resp;
  try {
    resp = await streamingFetch(`${API_URL}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ ...body, stream: true }),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new ApiError(OFFLINE);
  }
  if (!resp.ok) throw new ApiError(await errorMessage(resp as unknown as Response), resp.status);

  const parser = new SseParser();
  const dispatch = (chunk: string) => {
    for (const msg of parser.push(chunk)) {
      const data = JSON.parse(msg.data);
      if (msg.event === 'meta') handlers.onMeta(AskMetaSchema.parse(data));
      else if (msg.event === 'delta') handlers.onDelta(String(data.text ?? ''));
      else if (msg.event === 'done') handlers.onDone(AskDoneSchema.parse(data));
    }
  };

  if (!resp.body) {
    dispatch(await resp.text());
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    dispatch(decoder.decode(value, { stream: true }));
  }
  dispatch(decoder.decode() + '\n\n');
}
