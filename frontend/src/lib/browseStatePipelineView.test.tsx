/* The Pipeline chip loads a VIEW, and both halves of that have to hold in the
 * URL adapter — the one Browse actually runs on:
 *   1. the cohort is reset to neutral (the shipped bug: the chip ANDed the
 *      scope onto byt+pronájem, hiding 44 of 45 deals), and
 *   2. any active preset is deselected IN THE SAME WRITE. Two writes against
 *      one searchParams snapshot would clobber each other, and a preset left
 *      marked active over replaced filters reads as edited, which this chip
 *      must never cause.
 * The sidebar's Reset holds to the same second half (resetFilters).
 */

import { describe, expect, it } from 'vitest';
import { act, render } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';

import { useUrlBrowseState, type BrowseViewState } from './browseState';
import { DEFAULT_FILTERS, fromSearchParams, pipelineViewFilters } from './filters';

function Harness({
  onReady,
}: {
  onReady: (s: BrowseViewState, search: string, back: () => void) => void;
}) {
  const state = useUrlBrowseState();
  const loc = useLocation();
  const nav = useNavigate();
  onReady(state, loc.search, () => nav(-1));
  return null;
}

function mount(initial: string) {
  let state!: BrowseViewState;
  let search = '';
  let back = () => {};
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Harness
        onReady={(s, q, b) => {
          state = s;
          search = q;
          back = b;
        }}
      />
    </MemoryRouter>,
  );
  return {
    get state() { return state; },
    get search() { return search; },
    back: () => back(),
    get filters() { return fromSearchParams(new URLSearchParams(search)); },
  };
}

const STAGES = [
  { id: 1, is_terminal: false },
  { id: 2, is_terminal: false },
  { id: 9, is_terminal: true },
];

describe('loadPipelineView (URL adapter)', () => {
  it('replaces a narrowed cohort with the neutral pipeline view', () => {
    const h = mount('/browse?cat=byt&deal=pronajem&price_max=5000000&disp=2%2Bkk');
    act(() => h.state.loadPipelineView([]));
    expect(h.filters).toEqual(pipelineViewFilters([]));
    expect(h.filters.pipeline).toEqual({ set: 'in', in: [], out: [] });
    expect(h.filters.categoryMain).toEqual([]);
    expect(h.filters.categoryType).toBeNull();
    expect(h.filters.priceMax).toBeNull();
  });

  it('scopes to the live stages only (operator 2026-11-01)', () => {
    const h = mount('/browse?cat=byt');
    act(() => h.state.loadPipelineView(STAGES));
    expect(h.filters.pipeline).toEqual({ set: null, in: [1, 2], out: [] });
    expect(new URLSearchParams(h.search).get('pipeline')).toBe('1,2');
  });

  it('deselects the active preset in the same write', () => {
    const h = mount('/browse?preset=abc-123&cat=dum');
    expect(h.state.activePresetId).toBe('abc-123');
    act(() => h.state.loadPipelineView([]));
    expect(new URLSearchParams(h.search).has('preset')).toBe(false);
    expect(h.state.activePresetId).toBeNull();
  });

  it('keeps the map-overlay knobs (they are not cohort filters)', () => {
    const h = mount('/browse?cat=byt&rentmap=1&colorby=overall&tab=stats');
    act(() => h.state.loadPipelineView([]));
    const sp = new URLSearchParams(h.search);
    expect(sp.get('rentmap')).toBe('1');
    expect(sp.get('colorby')).toBe('overall');
    expect(sp.get('tab')).toBe('stats');
  });

  it('is undoable — the load pushes a history entry rather than replacing', () => {
    const h = mount('/browse?cat=dum&deal=prodej');
    act(() => h.state.loadPipelineView([]));
    expect(h.filters.pipeline).not.toBeNull();
    act(() => h.back());
    // Back is the undo for "the chip wiped my filters" — it only works because
    // the load PUSHES a history entry instead of replacing the current one.
    expect(h.filters.categoryMain).toEqual(['dum']);
    expect(h.filters.pipeline).toBeNull();
  });
});

describe('resetFilters (URL adapter)', () => {
  it('writes the default filters and deselects the active preset in the same write', () => {
    const h = mount('/browse?preset=abc-123&cat=dum&price_max=5000000&sort=price_czk&tab=stats');
    act(() => h.state.resetFilters());
    const sp = new URLSearchParams(h.search);
    expect(sp.has('preset')).toBe(false);
    expect(h.state.activePresetId).toBeNull();
    expect(h.filters).toEqual(DEFAULT_FILTERS);
    expect(sp.get('sort')).toBe('price_czk');
    expect(sp.get('tab')).toBe('stats');
  });

  it('is undoable — Back brings the preset and its filters back', () => {
    const h = mount('/browse?preset=abc-123&cat=dum');
    act(() => h.state.resetFilters());
    act(() => h.back());
    expect(h.state.activePresetId).toBe('abc-123');
    expect(h.filters.categoryMain).toEqual(['dum']);
  });
});

/* Operator ruling 2026-10-22 (reversing 2026-10-19): a preset click applies
 * every filter the preset stores, the lenses included — so a preset saved
 * without them (every preset before the ruling) turns them off. */
describe('loadPreset applies the lenses it stores', () => {
  it('turns off the curated sets, the broker and the dismissed reveal a preset does not store', () => {
    const h = mount(
      '/browse?cat=dum&pipeline_excluded=any&collections=4&tags=1,2&broker=7&dismissed=show',
    );
    expect(h.filters.pipeline).toEqual({ set: 'out', in: [], out: [] });
    act(() => h.state.loadPreset({
      id: 'vys',
      name: 'Vysočany byty',
      filter_spec: { filters: { categoryMain: ['byt'], categoryType: 'prodej', dispositions: ['2+kk'] }, sort: null },
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
    } as never));
    expect(h.filters.categoryMain).toEqual(['byt']);
    expect(h.filters.dispositions).toEqual(['2+kk']);
    expect(h.filters.pipeline).toBeNull();
    expect(h.filters.collections).toBeNull();
    expect(h.filters.tags).toBeNull();
    expect(h.filters.brokerId).toBeNull();
    expect(h.filters.showDismissed).toBe(false);
    expect(h.state.activePresetId).toBe('vys');
  });

  it('applies the curated sets, the broker and the dismissed reveal a preset stores', () => {
    const h = mount('/browse?cat=dum');
    act(() => h.state.loadPreset({
      id: 'deals',
      name: 'Moje dealy',
      filter_spec: {
        filters: {
          categoryMain: ['byt'],
          categoryType: 'prodej',
          pipeline: { set: 'out', in: [], out: [] },
          collections: { set: 'out', in: [], out: [] },
          tags: { set: null, in: [1, 2], out: [] },
          brokerId: 7,
          showDismissed: true,
        },
        sort: null,
      },
      created_at: '2026-10-22T00:00:00Z',
      updated_at: '2026-10-22T00:00:00Z',
    } as never));
    expect(h.filters.pipeline).toEqual({ set: 'out', in: [], out: [] });
    expect(h.filters.collections).toEqual({ set: 'out', in: [], out: [] });
    expect(h.filters.tags).toEqual({ set: null, in: [1, 2], out: [] });
    expect(h.filters.brokerId).toBe(7);
    expect(h.filters.showDismissed).toBe(true);
    expect(h.state.activePresetId).toBe('deals');
  });
});
