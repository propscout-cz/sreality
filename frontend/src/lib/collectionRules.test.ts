import { describe, expect, it } from 'vitest';
import { collectionLabel, isMine, shareAudience, sortCollections } from './collectionRules';

type Row = { id: number; name: string; monitoring_enabled: boolean; owner_email: string | null; shared_with: { email: string | null }[] };
const own = (over: Partial<Row> = {}): Row => ({
  id: 1, name: 'Šortlist', monitoring_enabled: false, owner_email: null, shared_with: [], ...over,
});
const sharedBy = (email: string, over: Partial<Row> = {}): Row =>
  own({ owner_email: email, shared_with: [{ email: null }], ...over });

describe('mine vs shared: one predicate, one label, one audience, one order', () => {
  it('isMine is owner_email == null', () => {
    expect(isMine(own())).toBe(true);
    expect(isMine(sharedBy('petr@example.cz'))).toBe(false);
    expect(isMine({ owner_email: null })).toBe(true);
  });

  it('collectionLabel names the owner of a shared collection only', () => {
    expect(collectionLabel(own({ name: 'monitoring' }))).toBe('monitoring');
    expect(collectionLabel(sharedBy('petr@example.cz', { name: 'monitoring' })))
      .toBe('monitoring · petr@example.cz');
  });

  it('shareAudience: my recipients and the owners of what is shared with me, sorted, once each', () => {
    const byId = new Map<number, Row>([
      [1, own({ id: 1, shared_with: [{ email: 'zuzana@example.cz' }, { email: 'adam@example.cz' }] })],
      [2, sharedBy('petr@example.cz', { id: 2 })],
      [3, sharedBy('adam@example.cz', { id: 3 })],
      [4, own({ id: 4 })],
    ]);
    expect(shareAudience([1, 2, 3, 4], byId)).toEqual([
      'adam@example.cz', 'petr@example.cz', 'zuzana@example.cz',
    ]);
    // An unshared collection, or an id the list does not know, has no audience.
    expect(shareAudience([4, 99], byId)).toEqual([]);
    expect(shareAudience([2], byId)).toEqual(['petr@example.cz']);
  });

  it('sortCollections puts monitored first, then by name, and never reorders its input', () => {
    const input = [own({ id: 1, name: 'B' }), own({ id: 2, name: 'A' }), own({ id: 3, name: 'C', monitoring_enabled: true })];
    expect(sortCollections(input).map((c) => c.id)).toEqual([3, 2, 1]);
    expect(input.map((c) => c.id)).toEqual([1, 2, 3]);
  });
});
