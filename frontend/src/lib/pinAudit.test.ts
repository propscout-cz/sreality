/* The list read that carries W7-b's state (migration 518).
 *
 * Every list read is scoped to exactly one state, because a total over both
 * answers no question anyone asked; the first page alone carries the exact count
 * of the filtered list (W1 deleted the summary RPC's reader and the nav badge).
 *
 * The stub records the PostgREST chain instead of hitting a server: every method
 * returns the same recorder and the recorder is thenable, so the builder's real
 * shape (which method is called with what) is what the assertions read.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

type Call = [string, unknown[]];

const stub = vi.hoisted(() => {
  const calls: Call[] = [];
  const target = {
    then: (resolve: (v: unknown) => void) =>
      resolve({ data: [], error: null, count: 7 }),
  };
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
  EMPTY_PIN_AUDIT_FILTERS,
  fetchPinAuditPage,
} from '@/lib/pinAudit';

const eqCalls = (): Call[] => stub.calls.filter(([m]) => m === 'eq');
const inCalls = (): Call[] => stub.calls.filter(([m]) => m === 'in');

describe('pinAudit reads', () => {
  beforeEach(() => {
    stub.calls.length = 0;
  });

  it('counts the filtered list exactly on its first page only', async () => {
    const sort = { field: 'last_seen_at', direction: 'desc' } as const;
    const first = await fetchPinAuditPage(EMPTY_PIN_AUDIT_FILTERS, sort, null);
    expect(first.total).toBe(7);
    expect(stub.calls.find(([m]) => m === 'select')?.[1][1]).toEqual({ count: 'exact' });
    stub.calls.length = 0;
    const next = await fetchPinAuditPage(EMPTY_PIN_AUDIT_FILTERS, sort, { value: '2026-10-31', id: 1 });
    expect(next.total).toBeNull();
    expect(stub.calls.find(([m]) => m === 'select')?.[1][1]).toBeUndefined();
  });

  it('defaults the page to the issue, not to the waiting room', () => {
    expect(EMPTY_PIN_AUDIT_FILTERS.state).toBe('unresolved');
  });

  it('scopes every list read to one state', async () => {
    await fetchPinAuditPage(
      { ...EMPTY_PIN_AUDIT_FILTERS, state: 'pending' },
      { field: 'last_seen_at', direction: 'desc' },
      null,
    );
    expect(eqCalls()).toContainEqual(['eq', ['state', 'pending']]);
  });

  it('never carries the quality buckets into pending — there is no verdict yet', async () => {
    await fetchPinAuditPage(
      {
        ...EMPTY_PIN_AUDIT_FILTERS,
        state: 'pending',
        qualities: ['active_no_claims'],
      },
      { field: 'last_seen_at', direction: 'desc' },
      null,
    );
    expect(inCalls().some(([, args]) => args[0] === 'quality')).toBe(false);
  });

  it('does send them under unresolved', async () => {
    await fetchPinAuditPage(
      { ...EMPTY_PIN_AUDIT_FILTERS, qualities: ['active_no_claims'] },
      { field: 'last_seen_at', direction: 'desc' },
      null,
    );
    expect(inCalls()).toContainEqual(['in', ['quality', ['active_no_claims']]]);
  });
});
