import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import ChartBuilder from './ChartBuilder';
import type { ChartSpec } from '@/lib/stats';

let latest: ChartSpec;

function Harness({ initial, categoryMain = 'byt' }: { initial: Partial<ChartSpec>; categoryMain?: string | null }) {
  const [draft, setDraft] = useState<ChartSpec>({
    id: 'c', view: 'table', rows: [], cols: [], measure: 'ppm2', stats: ['median'], ...initial,
  });
  latest = draft;
  return <ChartBuilder draft={draft} onChange={setDraft} categoryMain={categoryMain} />;
}

const rowsShelf = () => screen.getByRole('group', { name: 'Řádky' });
const colsShelf = () => screen.getByRole('group', { name: 'Sloupce' });
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
/* A segment of a value-well bar, found inside its named group by its own name. */
const segment = (group: string, name: string) =>
  within(screen.getByRole('group', { name: group })).getByRole('button', { name: `${group}: ${name}` });

describe('<ChartBuilder> — every drag has a click path', () => {
  it('a chip click appends to Řádky; a second click takes it back', () => {
    render(<Harness initial={{}} />);
    click('Přidat Dispozice do Řádků');
    click('Přidat Stav do Řádků');
    expect(latest.rows).toEqual(['disposition', 'condition']);
    expect(within(rowsShelf()).getByText('Dispozice')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Odebrat Dispozice z grafu' })).toHaveAttribute('aria-pressed', 'true');
    click('Odebrat Dispozice z grafu');
    expect(latest.rows).toEqual(['condition']);
  });

  it('a slot\'s arrows reorder, its × removes, and it moves across to Sloupce and back', () => {
    render(<Harness initial={{ rows: ['disposition', 'condition', 'kraj'] }} />);
    click('Posunout Kraj výš');
    expect(latest.rows).toEqual(['disposition', 'kraj', 'condition']);
    click('Posunout Dispozice níž');
    expect(latest.rows).toEqual(['kraj', 'disposition', 'condition']);
    expect(screen.getByRole('button', { name: 'Posunout Kraj výš' })).toBeDisabled();
    click('Odebrat Kraj');
    expect(latest.rows).toEqual(['disposition', 'condition']);
    click('Přesunout Stav do Sloupců');
    expect(latest).toMatchObject({ rows: ['disposition'], cols: ['condition'] });
    expect(within(colsShelf()).getByText('Stav')).toBeInTheDocument();
    click('Přesunout Stav do Řádků');
    expect(latest).toMatchObject({ rows: ['disposition', 'condition'], cols: [] });
  });

  it('nests the row slots: each level indented under the one before', () => {
    render(<Harness initial={{ rows: ['disposition', 'condition'] }} />);
    const items = within(rowsShelf()).getAllByRole('listitem') as HTMLElement[];
    expect(items.map((li) => li.style.marginLeft)).toEqual(['0rem', '0.75rem', '1.5rem']);
    expect(screen.getByText('2 z 6')).toBeInTheDocument();
  });

  it('line takes one row: the next chip goes to Sloupce, and a full board disables the rest', () => {
    render(<Harness initial={{ view: 'line' }} />);
    click('Přidat Dispozice do Řádků');
    click('Přidat Stav do Sloupců');
    expect(latest).toMatchObject({ rows: ['disposition'], cols: ['condition'] });
    expect(screen.getByRole('button', { name: 'Materiál — Řádky i Sloupce jsou plné' })).toBeDisabled();
    expect(screen.getByText('Řádky i Sloupce jsou plné — dimenzi nejdřív odeberte.')).toBeInTheDocument();
  });

  it('a box has no column, and a chart with a column cannot become a box', () => {
    const { unmount } = render(<Harness initial={{ view: 'box', rows: ['disposition'] }} />);
    expect(within(colsShelf()).getByText('Krabice nemá Sloupce — dimenze patří do Řádků.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Přesunout Dispozice do Sloupců' })).toBeNull();
    unmount();
    render(<Harness initial={{ rows: ['disposition', 'condition'], cols: ['kraj'] }} />);
    expect(segment('Zobrazení', 'Krabice')).toBeDisabled();
    expect(segment('Zobrazení', 'Čáry')).toBeDisabled();
    expect(screen.getByText(/Čáry: čáry a pruhy mají v Řádcích jen jednu dimenzi/)).toBeInTheDocument();
    expect(screen.getByText(/Krabice: krabice nemá Sloupce/)).toBeInTheDocument();
  });

  it('shows a dimension outside the cohort\'s category disabled, with the reason', () => {
    render(<Harness initial={{}} categoryMain="dum" />);
    expect(screen.getByRole('button', { name: 'Plocha po 5 m² — jen byty' })).toBeDisabled();
    expect(screen.getByText('Plocha po 5 m², Plocha po 10 m²: jen byty.')).toBeInTheDocument();
  });

  it('keeps a chosen chip clickable even where the category rules it out, so it can come back out', () => {
    render(<Harness initial={{ rows: ['area_band_5'] }} categoryMain="dum" />);
    expect(screen.getByRole('button', { name: 'Odebrat Plocha po 5 m² z grafu' })).toBeEnabled();
    click('Odebrat Plocha po 5 m² z grafu');
    expect(latest.rows).toEqual([]);
  });

  it('names the value group alone "Hodnota"; the view and statistic pickers have their own names', () => {
    render(<Harness initial={{ view: 'bar', rows: ['disposition'] }} />);
    const value = screen.getByRole('group', { name: 'Hodnota' });
    expect(within(value).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Doba na trhu', 'Plocha', 'Cena', 'Cena za m²',
    ]);
    expect(screen.getByRole('group', { name: 'Zobrazení' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Statistika · jedna nebo víc' })).toBeInTheDocument();
    expect(screen.queryByRole('region')).toBeNull();
  });

  it('picks the value, the view and — where statistics are drawn — the statistics', () => {
    render(<Harness initial={{ rows: ['disposition'] }} />);
    fireEvent.click(segment('Hodnota', 'Doba na trhu'));
    expect(latest.measure).toBe('tom_days');
    expect(screen.queryByRole('group', { name: /^Statistika/ })).toBeNull();
    fireEvent.click(segment('Zobrazení', 'Pruhy'));
    expect(latest.view).toBe('bar');
    expect(screen.getByRole('group', { name: /^Statistika/ })).toBeInTheDocument();
    click('Statistika: p75');
    expect(latest.stats).toEqual(['median', 'p75']);
  });

  it('takes several statistics at once, in the order they were picked, and never fewer than one', () => {
    render(<Harness initial={{ view: 'line', rows: ['disposition'] }} />);
    click('Statistika: p10');
    click('Statistika: p90');
    expect(latest.stats).toEqual(['median', 'p10', 'p90']);
    click('Statistika: medián');
    expect(latest.stats).toEqual(['p10', 'p90']);
    click('Statistika: p10');
    click('Statistika: p90');
    expect(latest.stats).toEqual(['p90']);
    expect(screen.getByRole('button', { name: 'Statistika: p90' })).toBeInTheDocument();
  });
});

/* Operator ruling 2026-10-27: a pick-one is the one joined bar (SegmentedBar).
 * Hodnota and Zobrazení each pick one; Statistika picks several and stays chips. */
describe('<ChartBuilder> — Hodnota and Zobrazení are the one joined bar', () => {
  it('draws each as one track of adjoining segments, the chosen one filled copper, no separate chips', () => {
    render(<Harness initial={{ view: 'bar', rows: ['disposition'] }} />);
    for (const [group, chosen] of [['Hodnota', 'Cena za m²'], ['Zobrazení', 'Pruhy']] as const) {
      const bar = screen.getByRole('group', { name: group });
      const track = bar.firstElementChild as HTMLElement;
      expect(track).toHaveClass('border', 'bg-[var(--color-paper-2)]');
      for (const b of within(bar).getAllByRole('button')) {
        expect(b.parentElement).toBe(track);
        expect(b).not.toHaveClass('border');
        expect(b).toHaveAttribute('aria-pressed', String(b.textContent === chosen));
      }
      expect(segment(group, chosen)).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
      expect(segment(group, chosen)).not.toHaveClass('bg-[var(--color-copper-soft)]');
    }
    /* Statistika picks several: still its own chips. */
    const stat = within(screen.getByRole('group', { name: 'Statistika · jedna nebo víc' })).getByRole('button', { name: 'Statistika: medián' });
    expect(stat).toHaveClass('border', 'bg-[var(--color-copper-soft)]');
  });

  it('keeps a view the shelves rule out as a disabled segment, its reason on hover and under the bar', () => {
    render(<Harness initial={{ rows: ['disposition'], cols: ['kraj'] }} />);
    const box = segment('Zobrazení', 'Krabice');
    expect(box).toBeDisabled();
    expect(box).toHaveAttribute('title', 'Krabice: krabice nemá Sloupce');
    expect(screen.getByText('Krabice: krabice nemá Sloupce')).toBeInTheDocument();
    expect(segment('Zobrazení', 'Pruhy')).toBeEnabled();
    expect(segment('Zobrazení', 'Pruhy')).not.toHaveAttribute('title');
  });

  /* The binding keeps aria names: each segment says which control it is, as the chips did. */
  it('names every segment by its group, "Hodnota: Cena za m²", "Zobrazení: Pruhy"', () => {
    render(<Harness initial={{ view: 'bar', rows: ['disposition'] }} />);
    expect(screen.getByRole('button', { name: 'Hodnota: Cena za m²' })).toHaveTextContent(/^Cena za m²$/);
    expect(screen.getByRole('button', { name: 'Hodnota: Doba na trhu' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Zobrazení: Pruhy' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Zobrazení: Tabulka' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByRole('button', { name: 'Pruhy' })).toBeNull();
  });

  it('a click on the lit segment sets the same value again', () => {
    const onChange = vi.fn();
    const draft: ChartSpec = { id: 'c', view: 'table', rows: [], cols: [], measure: 'ppm2', stats: ['median'] };
    render(<ChartBuilder draft={draft} onChange={onChange} categoryMain="byt" />);
    fireEvent.click(segment('Hodnota', 'Cena za m²'));
    expect(onChange).toHaveBeenLastCalledWith({ ...draft, measure: 'ppm2' });
    fireEvent.click(segment('Zobrazení', 'Tabulka'));
    expect(onChange).toHaveBeenLastCalledWith({ ...draft, view: 'table' });
  });
});

describe('<ChartBuilder> — the row grip sorts from the keyboard', () => {
  afterEach(() => vi.restoreAllMocks());

  const tick = () => act(() => new Promise<void>((r) => setTimeout(r, 0)));

  it('Space lifts a row, an arrow moves it, Space drops it, and the drop is narrated in Czech', async () => {
    /* jsdom lays nothing out: give each row slot a 40 px band and the shelf
     * their span, so dnd-kit's keyboard coordinates have somewhere to go (the
     * drag overlay starts on the first slot). A box, so Sloupce takes no drop. */
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const li = this.closest('li');
      const i = li?.parentElement ? [...li.parentElement.children].indexOf(li) : 0;
      const top = this.tagName === 'OL' ? 0 : i * 40;
      const height = this.tagName === 'OL' ? 160 : 32;
      return { x: 0, y: top, top, left: 0, width: 200, height, bottom: top + height, right: 200, toJSON: () => ({}) } as DOMRect;
    });
    render(<Harness initial={{ view: 'box', rows: ['disposition', 'condition', 'kraj'] }} />);
    const grip = screen.getByRole('button', { name: 'Přesunout Dispozice (úroveň 1)' });
    expect(grip).toHaveAttribute('aria-roledescription', 'řaditelná dimenze');
    grip.focus();
    fireEvent.keyDown(grip, { code: 'Space', key: ' ' });
    await tick();
    fireEvent.keyDown(document, { code: 'ArrowDown', key: 'ArrowDown' });
    await tick();
    fireEvent.keyDown(document, { code: 'Space', key: ' ' });
    await tick();
    expect(latest.rows).toEqual(['condition', 'disposition', 'kraj']);
    expect(screen.getByText('Položeno: Dispozice do Řádků.')).toBeInTheDocument();
  });
});
