import type { Event } from '@/api/schemas';
import { mapHtml, mapPoints, parseMapMessage, safeJson } from '@/lib/leaflet-map';

const event = (id: number, location: Event['location']) => ({ id, location }) as Event;

describe('Leaflet map (Android)', () => {
  it('only maps events with a location', () => {
    expect(mapPoints([event(1, { lat: 1.39, lng: 103.9 }), event(2, null)])).toEqual([{ id: 1, lat: 1.39, lng: 103.9 }]);
  });

  it('builds a OneMap page limited to Singapore, with SRI-pinned Leaflet', () => {
    const html = mapHtml({ center: { lat: 1.39, lng: 103.9 }, zoom: 14, dark: true, userLocation: null });
    expect(html).toContain('https://www.onemap.gov.sg/maps/tiles/Night/{z}/{x}/{y}.png');
    expect(html).toContain('"bounds":[[1.144,103.535],[1.494,104.502]]');
    expect(html).toContain('integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="');
    expect(html).toContain('"user":null');
  });

  it('escapes data so a hostile value cannot close the script tag', () => {
    const hostile = '</script><img src=x onerror=alert(1)>&' + String.fromCharCode(0x2028);
    const json = safeJson({ title: hostile });
    expect(json).not.toMatch(new RegExp('[<>&\\u2028]'));
    expect(json).toContain('\\u003c/script\\u003e');
    expect(JSON.parse(json)).toEqual({ title: hostile });
  });

  it('accepts only well-formed messages from the page', () => {
    expect(parseMapMessage('{"type":"select","id":7}')).toEqual({ type: 'select', id: 7 });
    expect(parseMapMessage('{"type":"select","id":"7"}')).toBeNull();
    expect(parseMapMessage('{"type":"clear"}')).toEqual({ type: 'clear' });
    expect(parseMapMessage('{"type":"navigate","url":"x"}')).toBeNull();
    expect(parseMapMessage('not json')).toBeNull();
    expect(parseMapMessage('null')).toBeNull();
  });
});
