/* FilterForm — registry-driven widget dispatch.
 *
 * Focused on the routing decisions FilterForm makes:
 *   - filters declared for the requested agenda render; others don't
 *   - includeOnly narrows the set, with min/max pair auto-inclusion
 *   - tri-state filters render as TriRow (any/yes/no)
 *   - pill_group filters and on/off filters render as the one joined bar
 *   - paired ranges with full bounds render the slider; without
 *     bounds, they render paired inputs
 *   - customWidgets overrides the dispatcher
 *
 * Skips the composite LOCATION control — it pulls maplibre-gl, which
 * doesn't initialise cleanly in jsdom and isn't part of the FilterForm
 * dispatch logic anyway.
 */

import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { FilterForm } from './FilterForm';

const CHOSEN = ['bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]'];

/* The one joined bar (SegmentedBar): every segment in one track, none of them a
 * bordered chip of its own. */
function expectJoined(buttons: HTMLElement[]) {
  const track = buttons[0].parentElement!;
  for (const b of buttons) {
    expect(b.parentElement).toBe(track);
    expect(b).not.toHaveClass('border');
  }
  expect(track).toHaveClass('border', 'border-[var(--color-rule)]', 'bg-[var(--color-paper-2)]');
}

describe('<FilterForm>', () => {
  it('draws the City quality group when grouped, as the Watchdog editor does', () => {
    render(
      <FilterForm
        scope="watchdog"
        state={{}}
        onChange={vi.fn()}
        includeOnly={['near_pop_5km_min']}
      />,
    );
    expect(screen.getByText('City quality')).toBeInTheDocument();
  });

  it('renders filters declared for the scope', () => {
    render(
      <FilterForm
        scope="browse"
        state={{}}
        onChange={vi.fn()}
        includeOnly={['category_type']}
        flat
      />,
    );
    // category_type is a PILL_GROUP with the four registry options;
    // at least one of them should appear as a button. (category_main is now
    // a multiselect scoped off the browse agenda, so it's no longer the
    // representative pill_group here.)
    expect(screen.getByRole('button', { name: 'Pronájem' })).toBeInTheDocument();
  });

  it('honours the includeOnly slice', () => {
    render(
      <FilterForm
        scope="browse"
        state={{}}
        onChange={vi.fn()}
        includeOnly={['category_type']}
        flat
      />,
    );
    // dispositions has 10 pills; none should render when excluded.
    expect(screen.queryByRole('button', { name: '2+kk' })).not.toBeInTheDocument();
  });

  it('auto-pairs min and max siblings on a slider filter', () => {
    render(
      <FilterForm
        scope="browse"
        state={{ min_garden_area: 100, max_garden_area: 800 }}
        onChange={vi.fn()}
        includeOnly={['min_garden_area']}    // only one half listed
        flat
      />,
    );
    // garden area keeps the dual-thumb slider (range_slider + full
    // bounds). The slider exposes two range inputs; if pairing fired,
    // both exist.
    const sliders = screen.getAllByRole('slider');
    expect(sliders).toHaveLength(2);
  });

  it('renders a range_inputs filter as paired number inputs, not a slider', () => {
    render(
      <FilterForm
        scope="browse"
        state={{ min_price_czk: 5000, max_price_czk: 15_000 }}
        onChange={vi.fn()}
        includeOnly={['min_price_czk']}
        labels={{ min_price_czk: 'Price' }}
        flat
      />,
    );
    // price opts into range_inputs, so it renders plain number inputs
    // (NumberCell = type=text inputMode=numeric → role "textbox") even
    // though it still carries min/max/step bounds metadata.
    expect(screen.queryAllByRole('slider')).toHaveLength(0);
    expect(screen.getAllByRole('textbox').length).toBeGreaterThanOrEqual(2);
  });

  it("renders pill_group filters as <button aria-pressed>", () => {
    const onChange = vi.fn();
    render(
      <FilterForm
        scope="browse"
        state={{ category_type: 'pronajem' }}
        onChange={onChange}
        includeOnly={['category_type']}
        flat
      />,
    );
    const rent = screen.getByRole('button', { name: 'Pronájem' });
    expect(rent).toHaveAttribute('aria-pressed', 'true');
    const sale = screen.getByRole('button', { name: 'Prodej' });
    fireEvent.click(sale);
    // Single-filter updates ship as one-element arrays — the batching
    // shape is uniform across single + paired emissions so the parent
    // can always apply them atomically.
    expect(onChange).toHaveBeenCalledWith([
      { id: 'category_type', value: 'prodej' },
    ]);
  });

  /* "Listing for" has no Vše (ruling 2026-10-20) and is the one joined bar
   * (ruling 2026-10-27): no deal lights nothing, and the lit deal releases. */
  it('draws a nullable pill_group as the joined bar that lights nothing and releases', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <FilterForm
        scope="browse"
        state={{ category_type: null }}
        onChange={onChange}
        includeOnly={['category_type']}
        labels={{ category_type: 'Listing for' }}
        flat
      />,
    );
    const bar = screen.getByRole('group', { name: 'Listing for' });
    const deals = within(bar).getAllByRole('button');
    expect(deals.map((b) => b.textContent)).toEqual(['Pronájem', 'Prodej', 'Dražba', 'Podíl']);
    expectJoined(deals);
    for (const b of deals) expect(b).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', { name: 'Prodej' }));
    expect(onChange).toHaveBeenLastCalledWith([{ id: 'category_type', value: 'prodej' }]);

    rerender(
      <FilterForm
        scope="browse"
        state={{ category_type: 'prodej' }}
        onChange={onChange}
        includeOnly={['category_type']}
        labels={{ category_type: 'Listing for' }}
        flat
      />,
    );
    expect(screen.getByRole('button', { name: 'Prodej' })).toHaveClass(...CHOSEN);
    fireEvent.click(screen.getByRole('button', { name: 'Prodej' }));
    expect(onChange).toHaveBeenLastCalledWith([{ id: 'category_type', value: null }]);
  });

  /* The phone Filtry sheet drew Status through this form: a tap on the lit chip
   * stored status null, and the live count showed inactive properties only. */
  it('never empties a pick-one that is not nullable: a click on the lit segment sets it again', () => {
    const onChange = vi.fn();
    render(
      <FilterForm
        scope="browse"
        state={{ status: 'active' }}
        onChange={onChange}
        includeOnly={['status']}
        labels={{ status: 'Status' }}
        flat
      />,
    );
    const statuses = within(screen.getByRole('group', { name: 'Status' })).getAllByRole('button');
    expect(statuses.map((b) => b.textContent)).toEqual(['Vše', 'Aktivní', 'Neaktivní']);
    expectJoined(statuses);
    expect(screen.getByRole('button', { name: 'Aktivní' })).toHaveClass(...CHOSEN);
    fireEvent.click(screen.getByRole('button', { name: 'Aktivní' }));
    expect(onChange).toHaveBeenLastCalledWith([{ id: 'status', value: 'active' }]);
  });

  /* A lone on/off is the two-segment bar "<off> | <label>", as the sidebar's
   * "All ads | Seen at birth" always was (ruling 2026-10-27). */
  it('draws an on/off filter as the two-segment bar, off first, named by its label', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <FilterForm
        scope="browse"
        state={{ with_estimates: false }}
        onChange={onChange}
        includeOnly={['with_estimates']}
        labels={{ with_estimates: 'With estimates' }}
        flat
      />,
    );
    const bar = screen.getByRole('group', { name: 'With estimates' });
    const segs = within(bar).getAllByRole('button');
    expect(segs.map((b) => b.textContent)).toEqual(['All properties', 'With estimates']);
    expectJoined(segs);
    expect(segs[0]).toHaveAttribute('aria-pressed', 'true');
    expect(segs[0]).toHaveClass(...CHOSEN);
    fireEvent.click(segs[1]);
    expect(onChange).toHaveBeenLastCalledWith([{ id: 'with_estimates', value: true }]);

    rerender(
      <FilterForm
        scope="browse"
        state={{ with_estimates: true }}
        onChange={onChange}
        includeOnly={['with_estimates']}
        labels={{ with_estimates: 'With estimates' }}
        flat
      />,
    );
    expect(within(bar).getByRole('button', { name: 'With estimates' })).toHaveClass(...CHOSEN);
    fireEvent.click(within(bar).getByRole('button', { name: 'All properties' }));
    expect(onChange).toHaveBeenLastCalledWith([{ id: 'with_estimates', value: false }]);
  });

  /* The Watchdog editor and the phone sheet draw these through the form; each
   * names its off side in the sidebar's words. */
  it('names each on/off filter\'s off side', () => {
    render(
      <FilterForm
        scope="browse"
        state={{}}
        onChange={vi.fn()}
        includeOnly={['tom_observed_only', 'include_no_price']}
        labels={{ tom_observed_only: 'Seen at birth', include_no_price: 'Include unpriced' }}
        flat
      />,
    );
    const born = screen.getByRole('group', { name: 'Seen at birth' });
    expect(within(born).getAllByRole('button').map((b) => b.textContent)).toEqual(['All ads', 'Seen at birth']);
    const unpriced = screen.getByRole('group', { name: 'Include unpriced' });
    expect(within(unpriced).getAllByRole('button').map((b) => b.textContent)).toEqual(['Priced only', 'Include unpriced']);
  });

  /* The bars are fill bars: equal columns, so at the sidebar's 240px floor
   * ("Listing for": Pronájem) or in a phone-wide Watchdog editor (its long
   * "Include listings without a price") a label that has no room left ends in an
   * ellipsis inside its own segment instead of running into the next one. */
  it('keeps every segment label inside its own segment, its name whole', () => {
    render(
      <FilterForm
        scope="watchdog"
        state={{ category_type: 'pronajem' }}
        onChange={vi.fn()}
        includeOnly={['include_no_price', 'category_type']}
        labels={{ include_no_price: 'Include listings without a price', category_type: 'Deal' }}
        flat
      />,
    );
    const unpriced = screen.getByRole('group', { name: 'Include listings without a price' });
    const deal = screen.getAllByRole('button', { name: /Pronájem/ })[0].parentElement!;
    for (const bar of [unpriced, deal]) {
      expect(bar.style.gridTemplateColumns).toMatch(/^repeat\(\d, minmax\(0, 1fr\)\)$/);
      for (const b of within(bar).getAllByRole('button')) {
        for (const c of ['min-w-0', 'overflow-hidden', 'text-ellipsis', 'whitespace-nowrap']) expect(b).toHaveClass(c);
      }
    }
    expect(within(unpriced).getByRole('button', { name: 'Include listings without a price' })).toBeInTheDocument();
  });

  it('renders tristate filters as the TriRow control', () => {
    const onChange = vi.fn();
    render(
      <FilterForm
        scope="browse"
        state={{ has_balcony: null }}
        onChange={onChange}
        includeOnly={['has_balcony']}
        labels={{ has_balcony: 'Balcony' }}
        flat
      />,
    );
    expect(screen.getByText('Balcony')).toBeInTheDocument();
    // TriRow exposes "any", "yes", "no" buttons.
    const yes = screen.getByRole('button', { name: 'yes' });
    fireEvent.click(yes);
    expect(onChange).toHaveBeenCalledWith([
      { id: 'has_balcony', value: true },
    ]);
  });

  it('batches paired range updates so min and max apply atomically', () => {
    // The original bug: dragging the lo thumb fired onChange(min, lo)
    // followed immediately by onChange(max, oldMax), and a non-
    // functional setter like Browse's URL writer saw both calls
    // against the same stale filters — second call won, lo never
    // changed. The batched shape sends both updates in one array so
    // the parent reduces them together.
    const onChange = vi.fn();
    render(
      <FilterForm
        scope="browse"
        state={{ min_garden_area: 1000, max_garden_area: 3000 }}
        onChange={onChange}
        includeOnly={['min_garden_area']}
        flat
      />,
    );
    const sliders = screen.getAllByRole('slider') as HTMLInputElement[];
    fireEvent.change(sliders[0], { target: { value: '2000' } });
    expect(onChange).toHaveBeenCalledTimes(1);
    const lastCall = onChange.mock.calls.at(-1)![0] as Array<{
      id: string; value: unknown;
    }>;
    expect(lastCall).toHaveLength(2);
    expect(lastCall.map((u) => u.id).sort()).toEqual([
      'max_garden_area', 'min_garden_area',
    ]);
  });

  it('routes a custom widget through the customWidgets prop', () => {
    const CustomWidget = vi.fn(() => <div>custom-rendered</div>);
    render(
      <FilterForm
        scope="browse"
        state={{ tags: null }}
        onChange={vi.fn()}
        includeOnly={['tags']}
        customWidgets={{ tags: CustomWidget }}
        flat
      />,
    );
    expect(screen.getByText('custom-rendered')).toBeInTheDocument();
    expect(CustomWidget).toHaveBeenCalled();
  });

  /* Stateful harness mirroring how Browse / WatchdogEdit feed updates
   * back into `state` — required to expose controlled-input round trips. */
  function StatefulNumberHarness({ id, seen = [] }: { id: string; seen?: unknown[] }) {
    const [state, setState] = useState<Record<string, unknown>>({ [id]: null });
    return (
      <FilterForm
        scope="browse"
        state={state}
        onChange={(updates) => {
          seen.push(...updates.map((u) => u.value));
          setState((prev) => {
            const next = { ...prev };
            for (const u of updates) next[u.id] = u.value;
            return next;
          });
        }}
        includeOnly={[id]}
        labels={{ [id]: 'signed' }}
        flat
      />
    );
  }

  it('keeps a typed leading minus alive in signed number inputs', () => {
    // Regression: the old parse-or-drop handler wiped the '-' keystroke
    // (controlled input restores prior value on a no-emit), so typing
    // "-10" into total_price_change_pct stored +10 — the opposite cohort.
    render(<StatefulNumberHarness id="total_price_change_pct" />);
    const input = screen.getByRole('textbox', { name: 'signed' });
    fireEvent.change(input, { target: { value: '-' } });
    expect(input).toHaveValue('-');
    fireEvent.change(input, { target: { value: '-1' } });
    fireEvent.change(input, { target: { value: '-10' } });
    expect(input).toHaveValue('-10');
  });

  it('keeps a mid-typing decimal point alive in float number inputs', () => {
    render(<StatefulNumberHarness id="total_price_change_pct" />);
    const input = screen.getByRole('textbox', { name: 'signed' });
    fireEvent.change(input, { target: { value: '7' } });
    fireEvent.change(input, { target: { value: '7.' } });
    expect(input).toHaveValue('7.');
    fireEvent.change(input, { target: { value: '7.5' } });
    expect(input).toHaveValue('7.5');
  });

  it('reads a decimal comma, and emits only a value the filter does not hold yet', () => {
    const seen: unknown[] = [];
    render(<StatefulNumberHarness id="total_price_change_pct" seen={seen} />);
    const input = screen.getByRole('textbox', { name: 'signed' });
    for (const text of ['7', '7,', '7,5', '7,50', '']) {
      fireEvent.change(input, { target: { value: text } });
      expect(input).toHaveValue(text);
    }
    expect(seen).toEqual([7, 7.5, null]);
  });

  it('truncates an integer filter, a comma like a point, and keeps the typed text on blur', () => {
    const seen: unknown[] = [];
    render(<StatefulNumberHarness id="price_change_count_min" seen={seen} />);
    const input = screen.getByRole('textbox', { name: 'signed' });
    fireEvent.change(input, { target: { value: '2,7' } });
    expect(seen).toEqual([2]);
    expect(input).toHaveValue('2,7');
    fireEvent.blur(input);
    expect(input).toHaveValue('2,7');
  });
});
