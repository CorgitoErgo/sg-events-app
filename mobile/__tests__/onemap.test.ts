import { ONEMAP_ATTRIBUTION, oneMapTileUrl } from '@/lib/onemap';

describe('OneMap basemap', () => {
  it('uses the official tiles, with the Night style in dark mode', () => {
    expect(oneMapTileUrl(false)).toBe('https://www.onemap.gov.sg/maps/tiles/Default/{z}/{x}/{y}.png');
    expect(oneMapTileUrl(true)).toBe('https://www.onemap.gov.sg/maps/tiles/Night/{z}/{x}/{y}.png');
  });

  it('carries the attribution OneMap requires', () => {
    expect(ONEMAP_ATTRIBUTION).toBe('OneMap © contributors | Singapore Land Authority');
  });
});
