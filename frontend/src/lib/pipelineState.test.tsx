import { describe, expect, it } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { usePipelineViewState, type PipelineViewState } from './pipelineState';

/* Renders the hook and exposes both its state and the live URL, so each test
 * asserts the round-trip rather than the hook's internals. */
function Harness({ onReady }: { onReady: (s: PipelineViewState) => void }) {
  const state = usePipelineViewState();
  const loc = useLocation();
  onReady(state);
  return (
    <div>
      <span data-testid="search">{loc.search}</span>
      <span data-testid="status">{state.status}</span>
      <span data-testid="types">{[...state.types].join(',')}</span>
      <span data-testid="collections">{JSON.stringify(state.scopes.collections)}</span>
      <span data-testid="tags">{JSON.stringify(state.scopes.tags)}</span>
      <span data-testid="sort">{`${state.sort.field}:${state.sort.direction}`}</span>
      <span data-testid="districts">{state.districts.map((d) => d.name).join('|')}</span>
    </div>
  );
}

function mount(initial = '/pipeline') {
  let state!: PipelineViewState;
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Harness onReady={(s) => (state = s)} />
    </MemoryRouter>,
  );
  return {
    get state() {
      return state;
    },
    search: () => screen.getByTestId('search').textContent,
    read: (id: string) => screen.getByTestId(id).textContent,
  };
}

describe('usePipelineViewState', () => {
  it('defaults to a clean URL and the manual sort', () => {
    const h = mount();
    expect(h.search()).toBe('');
    expect(h.read('sort')).toBe('board_position:asc');
    expect(h.read('status')).toBe('any');
  });

  it('round-trips the sort through the URL', () => {
    const h = mount();
    act(() => h.state.setSort({ field: 'added_at', direction: 'desc' }));
    expect(h.search()).toBe('?sort=-added_at');
    expect(h.read('sort')).toBe('added_at:desc');
  });

  it('omits the param again when the sort returns to the default', () => {
    const h = mount('/pipeline?sort=-price_czk');
    expect(h.read('sort')).toBe('price_czk:desc');
    act(() => h.state.setSort({ field: 'board_position', direction: 'asc' }));
    expect(h.search()).toBe('');
  });

  it('spells the manual sort "manual", not "board_position"', () => {
    const h = mount('/pipeline?sort=manual');
    expect(h.read('sort')).toBe('board_position:asc');
  });

  it('falls back to the default for an unknown sort token', () => {
    const h = mount('/pipeline?sort=nonsense');
    expect(h.read('sort')).toBe('board_position:asc');
  });

  /* The chips emit the WHOLE next selection, so the hook takes an array and
   * owns nothing about which chip moved. */
  it('round-trips the type chips', () => {
    const h = mount();
    act(() => h.state.setTypes(['byt', 'dum']));
    expect(h.search()).toBe('?cat=byt%2Cdum');
    expect(h.read('types')).toBe('byt,dum');
    act(() => h.state.setTypes(['dum']));
    expect(h.read('types')).toBe('dum');
    act(() => h.state.setTypes([]));
    expect(h.search()).toBe('');
  });

  /* `collections` / `collections_excluded` are Browse's own spelling and
   * encoding (lib/setScope, ONE codec), not a second one for the board. */
  it('round-trips the collection scope and omits the params when off', () => {
    const h = mount();
    act(() => h.state.setScope('collections', { set: null, in: [7, 9], out: [] }));
    expect(h.search()).toBe('?collections=7%2C9');
    expect(h.read('collections')).toBe('{"set":null,"in":[7,9],"out":[]}');
    act(() => h.state.setScope('collections', { set: 'out', in: [], out: [] }));
    expect(h.search()).toBe('?collections_excluded=any');
    act(() => h.state.setScope('collections', null));
    expect(h.search()).toBe('');
  });

  /* `tags` / `tags_excluded` are Browse's two sides of the tag rule. */
  it('round-trips both sides of the tag scope and omits each param when empty', () => {
    const h = mount();
    act(() => h.state.setScope('tags', { set: null, in: [5], out: [7, 9] }));
    expect(h.search()).toBe('?tags=5&tags_excluded=7%2C9');
    expect(h.read('tags')).toBe('{"set":null,"in":[5],"out":[7,9]}');
    act(() => h.state.setScope('tags', { set: null, in: [], out: [9] }));
    expect(h.search()).toBe('?tags_excluded=9');
    act(() => h.state.setScope('tags', null));
    expect(h.search()).toBe('');
  });

  it('clears every cohort param in one write, keeping the sort', () => {
    const h = mount(
      '/pipeline?status=active&cat=byt&collections=7&collections_excluded=8&tags=5&tags_excluded=9&districts=Beroun&districts_lvl=obec&districts_id=531057&sort=-added_at',
    );
    act(() => h.state.reset());
    expect(h.search()).toBe('?sort=-added_at');
    expect(h.read('status')).toBe('any');
    expect(h.read('types')).toBe('');
    expect(h.read('collections')).toBe('null');
    expect(h.read('tags')).toBe('null');
    expect(h.read('districts')).toBe('');
  });

  it('round-trips status', () => {
    const h = mount();
    act(() => h.state.setStatus('inactive'));
    expect(h.search()).toBe('?status=inactive');
    act(() => h.state.setStatus('any'));
    expect(h.search()).toBe('');
  });

  /* Districts use Browse's five-param CSV family, so a chip's admin id and
   * level survive a reload — the district chip contract keys on the stable
   * admin ID, not the name. */
  it('round-trips district chips including level and id', () => {
    const h = mount();
    act(() =>
      h.state.setDistricts([
        { name: 'Beroun', context: null, level: 'obec', id: 531057 },
      ]),
    );
    expect(h.search()).toContain('districts=Beroun');
    expect(h.search()).toContain('districts_lvl=obec');
    expect(h.search()).toContain('districts_id=531057');
    expect(h.read('districts')).toBe('Beroun');
  });

  it('clears the whole district param family, leaving no stale level behind', () => {
    const h = mount(
      '/pipeline?districts=Beroun&districts_lvl=obec&districts_id=531057',
    );
    expect(h.read('districts')).toBe('Beroun');
    act(() => h.state.setDistricts([]));
    expect(h.search()).toBe('');
  });

  it('keeps unrelated params intact', () => {
    const h = mount('/pipeline?status=active&collections=7');
    act(() => h.state.setSort({ field: 'price_czk', direction: 'asc' }));
    expect(h.search()).toContain('status=active');
    expect(h.search()).toContain('collections=7');
    expect(h.search()).toContain('sort=price_czk');
  });
});
