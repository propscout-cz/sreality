/* The position audit's list read and grid vocabulary (W6-c, migration 733).
 *
 * The stub records the PostgREST chain instead of hitting a server (as lib/pinAudit.test does): every method returns
 * the same recorder and the recorder is thenable, so the builder's real shape is what the assertions read. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

type Call = [string, unknown[]];

const stub = vi.hoisted(() => {
  const calls: Call[] = [];
  const target = { then: (resolve: (v: unknown) => void) => resolve({ data: [], error: null, count: 12 }) };
  const proxy: Record<string, unknown> = new Proxy(target, {
    get(t, prop) {
      if (prop === 'then') return t.then;
      return (...args: unknown[]) => {
        calls.push([String(prop), args]);
        return proxy;
      };
    },
  }) as unknown as Record<string, unknown>;
  return { calls, proxy };
});

vi.mock('@/lib/supabase', () => ({
  supabase: {
    from: (relation: string) => {
      stub.calls.push(['from', [relation]]);
      return stub.proxy;
    },
  },
  isSupabaseConfigured: () => true,
}));

import {
  cellFromParams,
  fetchNoAnswerCount,
  fetchPositionAuditPage,
  gridGrains,
  gridKinds,
  listFilters,
  listFromParams,
  paramsForCell,
  paramsForList,
  positionGrid,
  precisionWords,
} from '@/lib/positionAudit';

const eqs = (): Call[] => stub.calls.filter(([m]) => m === 'eq');
const ins = (): Call[] => stub.calls.filter(([m]) => m === 'in');
const sort = { field: 'last_seen_at', direction: 'desc' } as const;
const cell = (kind: string, grain: string | null) => ({ mode: 'cell', cell: { kind, grain } }) as const;

describe('positionAudit list read', () => {
  beforeEach(() => {
    stub.calls.length = 0;
  });

  it("reads a cell as the reading does: its cohort, kind and grain, counted exactly on the first page only", async () => {
    const first = await fetchPositionAuditPage(listFilters('idnes', cell('own_pin', 'street')), sort, null);
    expect(stub.calls[0]).toEqual(['from', ['location_position_audit_public']]);
    expect(first.total).toBe(12);
    expect(eqs()).toEqual([['eq', ['in_reading', true]], ['eq', ['source', 'idnes']],
      ['eq', ['position_kind', 'own_pin']], ['eq', ['grain', 'street']]]);
    expect(stub.calls.find(([m]) => m === 'limit')?.[1]).toEqual([100]);
    stub.calls.length = 0;
    const next = await fetchPositionAuditPage(listFilters('idnes', cell('own_pin', null)), sort, { value: '2026-11-01', id: 9 });
    expect(next.total).toBeNull();
    expect(eqs().some(([, [col]]) => col === 'grain')).toBe(false);
  });

  it('sends the evidence and sibling facts only where the list holds none rows, and every kind sends no kind', async () => {
    const filters = { ...listFilters('bazos', cell('all', 'street')), evidence: 'withheld', sibling: 'yes' } as const;
    await fetchPositionAuditPage(filters, sort, null);
    expect(eqs().map(([, [col]]) => col)).toEqual(['in_reading', 'source', 'grain']);
    stub.calls.length = 0;
    await fetchPositionAuditPage({ ...filters, spec: cell('none', null), categories: ['byt'] }, sort, null);
    expect(eqs()).toEqual([
      ['eq', ['in_reading', true]], ['eq', ['source', 'bazos']], ['eq', ['position_kind', 'none']],
      ['eq', ['town_evidence', false]], ['eq', ['sibling_placed', true]]]);
    expect(ins()).toEqual([['in', ['category_main', ['byt']]]]);
  });

  it('reads the no-answer list off the snapshot in every status, by its own portal and status chips', async () => {
    const filters = listFilters('bazos', { mode: 'answerless' });
    expect(filters.status).toBe('all');
    await fetchPositionAuditPage(filters, sort, null);
    expect(eqs()).toEqual([['eq', ['snapshot_kind', 'none']]]);
    stub.calls.length = 0;
    await fetchPositionAuditPage({ ...filters, portals: ['remax', 'idnes'], status: 'delisted', evidence: 'unresolved' }, sort, null);
    expect(eqs()).toEqual([['eq', ['snapshot_kind', 'none']], ['eq', ['is_active', false]], ['eq', ['town_evidence', true]]]);
    expect(ins()).toEqual([['in', ['source', ['remax', 'idnes']]]]);
  });

  it("counts the no-answer entry's list exactly, with no rows", async () => {
    expect(await fetchNoAnswerCount()).toBe(12);
    expect(stub.calls.find(([m]) => m === 'select')?.[1]).toEqual(['listing_id', { count: 'exact', head: true }]);
    expect(eqs()).toEqual([['eq', ['snapshot_kind', 'none']]]);
  });
});

describe('positionAudit grid and cell', () => {
  it('reads the stored fact defensively and keeps the rare grains out until they hold a row', () => {
    const grid = positionGrid({ door: { address: 3 }, own_pin: { kraj: 0, street: 'x' }, odd: 7 });
    expect(grid).toEqual({ door: { address: 3 }, own_pin: { kraj: 0 } });
    expect(gridGrains(grid)).toEqual(['address', 'street', 'quarter', 'obec', 'okres', 'country', 'unknown']);
    expect(gridGrains({ ...grid, unit_point: { kraj: 1 }, x: { mystery: 2 } })).toEqual(
      ['address', 'street', 'quarter', 'obec', 'okres', 'kraj', 'country', 'unknown', 'mystery']);
    expect(gridKinds({ unclassified: { street: 1 }, zzz: { street: 1 } }).slice(-2)).toEqual(['unclassified', 'zzz']);
  });

  it('words the precision as the map does, never a second rule', () => {
    expect(precisionWords(100)).toBe('Přesná poloha');
    expect(precisionWords(90)).toBe('Přesná poloha');
    expect(precisionWords(60)).toBe('Přibližná poloha');
    expect(precisionWords(null)).toBe('Přesnost polohy neznámá');
  });

  it('round-trips the list through the URL, one list at a time', () => {
    const params = new URLSearchParams(paramsForCell('remax', { kind: 'town_point', grain: 'obec' }));
    expect(params.toString()).toBe('portal=remax&kind=town_point&grain=obec');
    expect(cellFromParams(params)).toEqual({ kind: 'town_point', grain: 'obec' });
    expect(cellFromParams(new URLSearchParams(paramsForCell('remax', null)))).toBeNull();
    expect(cellFromParams(new URLSearchParams('kind=all'))).toEqual({ kind: 'all', grain: null });
    const entry = new URLSearchParams(paramsForList('remax', { mode: 'answerless' }));
    expect(entry.toString()).toBe('portal=remax&answerless=1');
    expect(listFromParams(entry)).toEqual({ mode: 'answerless' });
    expect(cellFromParams(entry)).toBeNull();
  });
});
