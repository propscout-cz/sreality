import { describe, expect, it } from 'vitest';

import { dispatchTarget } from './dispatchTarget';

const row = (over: Partial<Parameters<typeof dispatchTarget>[0]>) => ({
  source_kind: 'watchdog' as const,
  property_id: null,
  collection_id: null,
  share_kind: null,
  share_ref: null,
  ...over,
});

describe('dispatchTarget', () => {
  it('opens the property of a watchdog or collection-monitor row, whatever its portal', () => {
    expect(dispatchTarget(row({ property_id: 999 }))).toBe('/property/999');
    expect(dispatchTarget(row({ source_kind: 'collection_monitor', property_id: 7, collection_id: 42 }))).toBe(
      '/property/7',
    );
  });

  it('opens a share by what it shares', () => {
    expect(dispatchTarget(row({ source_kind: 'share', share_kind: 'collection', share_ref: '42', collection_id: 42 }))).toBe(
      '/collection/42',
    );
    expect(dispatchTarget(row({ source_kind: 'share', share_kind: 'pipeline', share_ref: 'b0b' }))).toBe('/pipeline');
    expect(
      dispatchTarget(row({ source_kind: 'share', share_kind: 'dashboard', share_ref: '6f1c2d3e-0000-4000-8000-000000000001' })),
    ).toBe('/browse?tab=stats&dashboard=6f1c2d3e-0000-4000-8000-000000000001');
  });

  it('stays on the feed for a row with nothing to open', () => {
    expect(dispatchTarget(row({}))).toBe('/notifications');
    expect(dispatchTarget(row({ source_kind: 'share' }))).toBe('/notifications');
    expect(dispatchTarget(row({ source_kind: 'share', share_kind: 'dashboard' }))).toBe('/notifications');
    expect(dispatchTarget(row({ source_kind: 'share', share_kind: 'collection' }))).toBe('/notifications');
  });
});
