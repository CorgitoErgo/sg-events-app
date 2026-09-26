/** Schemas and the SSE parser against real responses captured from the API (src/api/__fixtures__). */
import { readFileSync } from 'fs';
import { join } from 'path';

import {
  AskDoneSchema,
  AskMetaSchema,
  AskResponseSchema,
  CategoriesSchema,
  EventDetailSchema,
  EventsPageSchema,
} from '@/api/schemas';
import { SseParser } from '@/api/sse';

const fixture = (name: string) => readFileSync(join(__dirname, '..', 'src', 'api', '__fixtures__', name), 'utf8');

describe('schemas accept real API responses', () => {
  it.each([
    ['events-page.json', EventsPageSchema],
    ['event-detail.json', EventDetailSchema],
    ['ask-response.json', AskResponseSchema],
    ['categories.json', CategoriesSchema],
  ])('%s', (name, schema) => {
    expect(schema.safeParse(JSON.parse(fixture(name))).success).toBe(true);
  });

  it('rejects a response missing required fields', () => {
    const page = JSON.parse(fixture('events-page.json'));
    delete page.items[0].starts_at;
    expect(EventsPageSchema.safeParse(page).success).toBe(false);
  });
});

describe('SseParser', () => {
  const stream = fixture('ask-stream.txt');

  it('parses the real /ask stream into meta, delta and done', () => {
    const messages = new SseParser().push(stream);
    expect(messages.map((m) => m.event)).toEqual(['meta', 'delta', 'done']);
    expect(AskMetaSchema.parse(JSON.parse(messages[0].data)).events.length).toBeGreaterThan(0);
    const done = AskDoneSchema.parse(JSON.parse(messages[2].data));
    expect(done.cited_event_ids.length).toBeGreaterThan(0);
  });

  it.each([1, 7, 64])('gives the same result when the stream arrives in %i-character chunks', (size) => {
    const parser = new SseParser();
    const events: string[] = [];
    for (let i = 0; i < stream.length; i += size) events.push(...parser.push(stream.slice(i, i + size)).map((m) => m.event));
    expect(events).toEqual(['meta', 'delta', 'done']);
  });

  it('handles CRLF line endings, comments and multi-line data', () => {
    const messages = new SseParser().push(': keep-alive\r\nevent: delta\r\ndata: a\r\ndata: b\r\n\r\n');
    expect(messages).toEqual([{ event: 'delta', data: 'a\nb' }]);
  });
});
