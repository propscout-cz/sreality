/* The Stats dashboard's vocabulary: the tree, the order, the labels, the units,
 * the title, the shelf rules and the default board. Every rule a component
 * renders is decided here, so these cases are the contract's SPA half. */
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./pgRead', () => ({ pgRead: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  apiPut: vi.fn(),
}));

import { pgRead } from './pgRead';
import { apiPut } from './api';
import { DEFAULT_FILTERS } from './filters';
import { PPM2_UNIT, PRICE_PERIOD_UNIT } from './measure';
import { STATS_REGISTRY, statsMeasure } from './statsRegistry.generated';
import { cell, pivotOf } from '@/components/stats/testPivot';
import {
  DEFAULT_CHARTS,
  DIMENSION_GROUPS,
  MAX_SERIES,
  NO_PLACES,
  cellKeyOf,
  cellTree,
  chartProblem,
  chartTitle,
  chipTarget,
  cohortChips,
  dimBlock,
  dropOutcome,
  fetchDashboard,
  flattenTree,
  keyLabel,
  ledgerCounts,
  measureUnit,
  moveRow,
  newChart,
  nodeLabel,
  normalizePivot,
  placeDim,
  placeNames,
  refusalText,
  removeDim,
  sanitizeChart,
  saveDashboard,
  seriesCount,
  seriesModel,
  sortKeys,
  viewBlock,
  type ChartSpec,
} from './stats';

const RENT = 'rent_monthly_czk_m2';
const SALE = 'sale_capital_czk_m2';
const LAND = 'land_capital_czk_m2';
const sp = (s: string) => s.replace(/\s/g, ' ');

const chart = (over: Partial<ChartSpec>): ChartSpec => ({
  id: 'c1',
  view: 'table',
  rows: [],
  cols: [],
  measure: 'ppm2',
  stat: 'median',
  ...over,
});

const OBCE = placeNames(
  pivotOf({
    places: [
      { dim: 'obec', key: '561380', name: 'Česká Lípa' },
      { dim: 'obec', key: '562971', name: 'Chomutov' },
      { dim: 'obec', key: '554481', name: 'Cheb' },
      { dim: 'obec', key: '582786', name: 'Brno' },
      { dim: 'obec', key: '586021', name: 'Hodonín' },
    ],
  }),
);

describe('sortKeys — the registry is the one order', () => {
  it('orders an enum by its options, unknown members after, alphabetically', () => {
    expect(sortKeys('disposition', ['3+kk', 'zzz', '1+kk', 'atyp-x', '2+1'], NO_PLACES)).toEqual([
      '1+kk', '2+1', '3+kk', 'atyp-x', 'zzz',
    ]);
    expect(sortKeys('condition', ['spatny', 'novostavba', 'projekt'], NO_PLACES)).toEqual([
      'projekt', 'novostavba', 'spatny',
    ]);
  });

  it('orders a boolean ano before ne', () => {
    expect(sortKeys('has_lift', ['ne', 'ano'], NO_PLACES)).toEqual(['ano', 'ne']);
  });

  it('orders bands by their lower bound, numerically', () => {
    expect(sortKeys('ppm2_band_s', ['100000', '95000', '5000'], NO_PLACES)).toEqual(['5000', '95000', '100000']);
  });

  it('orders places by their register name with Czech collation (Č after C, Ch after H)', () => {
    /* Code order would be Cheb, Česká Lípa, Chomutov, Brno, Hodonín: the label decides. */
    expect(sortKeys('obec', ['561380', '562971', '554481', '582786', '586021'], OBCE)).toEqual([
      '582786', '561380', '586021', '554481', '562971',
    ]);
  });
});

describe('keyLabel — one label per key, steps by the payload basis', () => {
  it('labels a sale per-m² band with the sale step and unit', () => {
    expect(sp(keyLabel('ppm2_band_s', '5000', SALE, NO_PLACES))).toBe(sp(`5 000 – 10 000 ${PPM2_UNIT.sale}`));
    expect(sp(keyLabel('ppm2_band_l', '80000', SALE, NO_PLACES))).toBe(sp(`80 000 – 90 000 ${PPM2_UNIT.sale}`));
  });

  it('labels a rent per-m² band with the rent step and the monthly unit', () => {
    expect(sp(keyLabel('ppm2_band_s', '300', RENT, NO_PLACES))).toBe(sp(`300 – 325 ${PPM2_UNIT.rent}`));
    expect(sp(keyLabel('ppm2_band_l', '300', RENT, NO_PLACES))).toBe(sp(`300 – 350 ${PPM2_UNIT.rent}`));
  });

  it('labels a land band per m² of plot, on the sale step', () => {
    expect(sp(keyLabel('ppm2_band_s', '0', LAND, NO_PLACES))).toBe(sp(`0 – 5 000 ${PPM2_UNIT.land}`));
  });

  it('labels price bands by period', () => {
    expect(sp(keyLabel('price_band_m', '3000000', SALE, NO_PLACES))).toBe(sp(`3 000 000 – 3 500 000 ${PRICE_PERIOD_UNIT.capital}`));
    expect(sp(keyLabel('price_band_s', '15000', RENT, NO_PLACES))).toBe(sp(`15 000 – 16 000 ${PRICE_PERIOD_UNIT.monthly}`));
    expect(sp(keyLabel('price_band_l', '15000', RENT, NO_PLACES))).toBe(sp('15 000 – 20 000 Kč / měs'));
  });

  it('labels area bands with their one step whatever the basis', () => {
    expect(sp(keyLabel('area_band_10', '80', RENT, NO_PLACES))).toBe('80 – 90 m²');
    expect(sp(keyLabel('area_band_5', '80', SALE, NO_PLACES))).toBe('80 – 85 m²');
  });

  it('leaves a band without a basis unit-less — a gap, never a guess', () => {
    expect(sp(keyLabel('ppm2_band_s', '5000', null, NO_PLACES))).toBe('5 000 – 10 000');
  });

  it('labels enums by label_cs and unknown members by themselves', () => {
    expect(keyLabel('condition', 'po_rekonstrukci', SALE, NO_PLACES)).toBe('Po rekonstrukci');
    expect(keyLabel('has_balcony', 'ano', SALE, NO_PLACES)).toBe('Ano');
    expect(keyLabel('condition', 'legacy_spelling', SALE, NO_PLACES)).toBe('legacy_spelling');
  });
});

describe('places — a place key is a RÚIAN code, named by the payload', () => {
  const lhota = pivotOf({
    spec: { rows: ['obec'], cols: [], measure: 'ppm2' },
    places: [
      { dim: 'obec', key: '990500002', name: 'Lhota' },
      { dim: 'obec', key: '582786', name: 'Brno' },
      { dim: 'obec', key: '990500001', name: 'Lhota' },
      { dim: 'obec', key: '990500003', name: null },
    ],
    cells: [
      cell([], 60, 100),
      cell(['990500002'], 20, 90),
      cell(['582786'], 20, 120),
      cell(['990500001'], 15, 80),
      cell(['990500003'], 5, null),
    ],
  });
  const names = placeNames(lhota);

  it('labels a place key by its register name, per dimension', () => {
    expect(keyLabel('obec', '582786', SALE, names)).toBe('Brno');
    expect(keyLabel('okres', '582786', SALE, names)).toBe('582786');
    expect(nodeLabel({ keys: ['582786'], cells: new Map(), children: [] }, lhota.spec, SALE, names)).toBe('Brno');
  });

  it('keeps two places of one name as two rows, ordered by code', () => {
    const rows = flattenTree(cellTree(lhota.cells, lhota.spec, names).root);
    expect(rows.map((r) => r.keys.join('/'))).toEqual(['', '990500003', '582786', '990500001', '990500002']);
    expect(rows.map((r) => nodeLabel(r, lhota.spec, SALE, names))).toEqual([
      'Celkem', '990500003', 'Brno', 'Lhota', 'Lhota',
    ]);
  });

  it('shows the code when the register has no name for it', () => {
    expect(keyLabel('obec', '990500003', SALE, names)).toBe('990500003');
    expect(keyLabel('obec', '123456', SALE, names)).toBe('123456');
    expect(keyLabel('ulice', '990700001', SALE, NO_PLACES)).toBe('990700001');
  });

  it('names the x axis and the series of a line chart, in label order', () => {
    const p = pivotOf({
      spec: { rows: ['obec'], cols: ['kraj'], measure: 'ppm2' },
      places: [
        { dim: 'obec', key: '554481', name: 'Cheb' },
        { dim: 'obec', key: '582786', name: 'Brno' },
        { dim: 'kraj', key: '51', name: 'Karlovarský kraj' },
        { dim: 'kraj', key: '116', name: 'Jihomoravský kraj' },
      ],
      cells: [cell(['554481'], 20, 70, '51'), cell(['582786'], 20, 110, '116')],
    });
    const m = seriesModel(p, 'median');
    expect(m.data.map((d) => [d.x, d.label])).toEqual([['582786', 'Brno'], ['554481', 'Cheb']]);
    expect(m.series.map((s) => [s.key, s.label])).toEqual([
      ['116', 'Jihomoravský kraj'], ['51', 'Karlovarský kraj'],
    ]);
  });

  it('builds the lookup once per pivot', () => {
    expect(placeNames(lhota)).toBe(names);
  });
});

describe('measureUnit — from the payload basis only', () => {
  it('names price by period and per-m² by basis', () => {
    expect(measureUnit('price', SALE)).toBe(PRICE_PERIOD_UNIT.capital);
    expect(measureUnit('price', LAND)).toBe(PRICE_PERIOD_UNIT.capital);
    expect(measureUnit('price', RENT)).toBe(PRICE_PERIOD_UNIT.monthly);
    expect(measureUnit('ppm2', SALE)).toBe(PPM2_UNIT.sale);
    expect(measureUnit('ppm2', RENT)).toBe(PPM2_UNIT.rent);
    expect(measureUnit('ppm2', LAND)).toBe(PPM2_UNIT.land);
  });

  it('names area and time on market whatever the basis', () => {
    expect(measureUnit('area', RENT)).toBe('m²');
    expect(measureUnit('tom_days', null)).toBe('dní');
  });

  it('gives no unit to a money measure without a basis', () => {
    expect(measureUnit('price', null)).toBe('');
    expect(measureUnit('ppm2', null)).toBe('');
  });
});

describe('chartTitle — derived, never stored', () => {
  it('reads measure · rows › nested × column', () => {
    expect(chartTitle({ rows: ['disposition', 'condition'], cols: ['building_type'], measure: 'ppm2' })).toBe(
      'Cena za m² · Dispozice › Stav × Materiál',
    );
  });
  it('is the measure alone without dimensions', () => {
    expect(chartTitle({ rows: [], cols: [], measure: 'price' })).toBe('Cena');
    expect(chartTitle({ rows: [], cols: ['condition'], measure: 'area' })).toBe('Plocha · Stav');
  });

  it('names a band by its step in the payload basis\'s unit, never the other basis\'s', () => {
    const spec = { rows: ['price_band_m'], cols: ['ppm2_band_s'], measure: 'area' };
    expect(sp(chartTitle(spec, RENT))).toBe(
      sp(`Plocha · Cena po 2 500 ${PRICE_PERIOD_UNIT.monthly} × Cena za m² po 25 ${PPM2_UNIT.rent}`),
    );
    expect(sp(chartTitle(spec, SALE))).toBe(
      sp(`Plocha · Cena po 500 000 ${PRICE_PERIOD_UNIT.capital} × Cena za m² po 5 000 ${PPM2_UNIT.sale}`),
    );
    expect(sp(chartTitle({ rows: ['area_band_10'], cols: [], measure: 'price' }, RENT))).toBe('Cena · Plocha po 10 m²');
  });

  it('keeps the two-step label while the basis is unknown or mixed', () => {
    const spec = { rows: ['price_band_m'], cols: [], measure: 'tom_days' };
    expect(chartTitle(spec)).toBe(`Doba na trhu · ${STATS_REGISTRY.dimensions.find((d) => d.id === 'price_band_m')!.label_cs}`);
  });
});

describe('cellTree — children by key prefix, parent above children', () => {
  const spec = { rows: ['disposition', 'condition'], cols: [], measure: 'ppm2' };
  const cells = [
    cell([], 60, 100),
    cell(['2+kk'], 30, 110),
    cell(['1+kk'], 30, 90),
    cell(['2+kk', 'spatny'], 5, null),
    cell(['2+kk', 'novostavba'], 25, 120),
    cell(['1+kk', 'dobry'], 30, 90),
  ];

  it('nests every level under its prefix and orders each level by its dimension', () => {
    const rows = flattenTree(cellTree(cells, spec, NO_PLACES).root);
    expect(rows.map((r) => r.keys.join('/'))).toEqual([
      '', '1+kk', '1+kk/dobry', '2+kk', '2+kk/novostavba', '2+kk/spatny',
    ]);
    expect(rows.map((r) => nodeLabel(r, spec, SALE, NO_PLACES))).toEqual([
      'Celkem', '1+kk', 'Dobrý', '2+kk', 'Novostavba', 'Špatný',
    ]);
  });

  it('keeps a cell per column value and lists the columns in registry order', () => {
    const colSpec = { rows: ['disposition'], cols: ['has_lift'], measure: 'ppm2' };
    const tree = cellTree(
      [cell([], 20, 1, 'ne'), cell([], 20, 2, 'ano'), cell(['1+kk'], 20, 3, 'ne'), cell(['1+kk'], 12, 4, 'ano')],
      colSpec,
      NO_PLACES,
    );
    expect(tree.cols).toEqual(['ano', 'ne']);
    expect(tree.root.cells.get('ano')?.median).toBe(2);
    expect(tree.root.children[0].cells.get('ne')?.median).toBe(3);
  });

  it('keys React rows and cells by keys.join + col', () => {
    expect(cellKeyOf(['2+kk', 'dobry'], 'ano')).toBe('2+kk›dobry|ano');
    expect(cellKeyOf([], null)).toBe('|');
  });
});

describe('the ledger', () => {
  it('splits the sample by its leaf cells: behind a number vs under the minimum', () => {
    const p = pivotOf({
      spec: { rows: ['disposition'], cols: [], measure: 'ppm2' },
      cells: [cell([], 80, 100), cell(['1+kk'], 74, 90), cell(['2+kk'], 6, null)],
    });
    expect(ledgerCounts(p)).toEqual({ filtered: 100, sample: 80, excluded: 20, withStats: 74, underMin: 6 });
  });

  it('keeps the counts on a refusal and leaves the split unknown', () => {
    const p = pivotOf({ refused: 'mixed_category', cells: [], category_main: null });
    expect(ledgerCounts(p)).toMatchObject({ filtered: 100, sample: 80, withStats: null, underMin: null });
    expect(cohortChips(p)).toEqual(['více typů nemovitosti', 'prodej', 'aktivní']);
  });

  it('has nothing to count on a bad spec', () => {
    expect(ledgerCounts(normalizePivot({ refused: 'bad_spec', reason: 'x', cells: [] }, chart({})))).toBeNull();
  });

  it('names the cohort from the payload', () => {
    expect(cohortChips(pivotOf({}))).toEqual(['byty', 'prodej', 'aktivní']);
    expect(cohortChips(pivotOf({ status: 'inactive', category_type: 'pronajem' }))).toEqual([
      'byty', 'pronájem', 'neaktivní',
    ]);
  });
});

describe('normalizePivot', () => {
  it('reads jsonb numerics and the echoed spec', () => {
    const p = normalizePivot(
      {
        spec: { rows: ['disposition'], cols: [], measure: 'price' },
        filtered: '12', sample: 10, excluded: 2, basis: SALE, min_n: 10, refused: null,
        cells: [{ keys: ['1+kk'], col: null, n: 10, mean: '5.5', median: 5, p10: 1, p25: 2, p75: 7, p90: 9 }],
      },
      { rows: [], cols: [], measure: 'ppm2' },
    );
    expect(p.spec).toEqual({ rows: ['disposition'], cols: [], measure: 'price' });
    expect(p.filtered).toBe(12);
    expect(p.cells[0].mean).toBe(5.5);
    expect(p.places).toEqual([]);
  });

  it('reads the place names, a missing name as null, and drops a malformed entry', () => {
    const p = normalizePivot(
      {
        spec: { rows: ['obec'], cols: [], measure: 'price' },
        places: [
          { dim: 'obec', key: '582786', name: 'Brno' },
          { dim: 'obec', key: '990500003', name: null },
          { dim: 'obec', key: 582786, name: 'Brno' },
          null,
        ],
        cells: [],
      },
      { rows: [], cols: [], measure: 'ppm2' },
    );
    expect(p.places).toEqual([
      { dim: 'obec', key: '582786', name: 'Brno' },
      { dim: 'obec', key: '990500003', name: null },
    ]);
  });
});

describe('the shelf rules (the registry\'s, in the UI)', () => {
  it('a chip click fills Řádky, then Sloupce, then nothing', () => {
    expect(chipTarget(chart({}))).toBe('rows');
    expect(chipTarget(chart({ view: 'line', rows: ['disposition'] }))).toBe('cols');
    expect(chipTarget(chart({ view: 'line', rows: ['disposition'], cols: ['condition'] }))).toBeNull();
    expect(chipTarget(chart({ view: 'box', rows: ['a', 'b', 'c', 'd', 'e', 'f'] }))).toBeNull();
  });

  it('places a dimension once, moving it rather than copying it', () => {
    const c = placeDim(chart({ rows: ['disposition', 'condition'] }), 'disposition', 'cols');
    expect(c.rows).toEqual(['condition']);
    expect(c.cols).toEqual(['disposition']);
    expect(placeDim(c, 'condition', 'cols')).toMatchObject({ rows: ['disposition'], cols: ['condition'] });
  });

  it('refuses a seventh row, a column on a box, a second row on a line', () => {
    const six = chart({ rows: ['disposition', 'condition', 'kraj', 'okres', 'obec', 'ulice'] });
    expect(placeDim(six, 'has_lift', 'rows')).toBe(six);
    const box = chart({ view: 'box', rows: ['disposition'] });
    expect(placeDim(box, 'condition', 'cols')).toBe(box);
    const line = chart({ view: 'line', rows: ['disposition'] });
    expect(placeDim(line, 'condition', 'rows')).toBe(line);
  });

  it('inserts at an index, removes, and reorders', () => {
    const c = placeDim(chart({ rows: ['disposition', 'condition'] }), 'kraj', 'rows', 1);
    expect(c.rows).toEqual(['disposition', 'kraj', 'condition']);
    expect(moveRow(c, 2, 0).rows).toEqual(['condition', 'disposition', 'kraj']);
    expect(removeDim(c, 'kraj').rows).toEqual(['disposition', 'condition']);
  });

  it('says why a view cannot take the shelves, and why a chart cannot be saved', () => {
    expect(viewBlock({ rows: ['a', 'b'], cols: [], measure: 'ppm2' }, 'line')).toMatch(/jednu dimenzi/);
    expect(viewBlock({ rows: ['a'], cols: ['b'], measure: 'ppm2' }, 'box')).toMatch(/krabice/);
    expect(viewBlock({ rows: ['a'], cols: ['b'], measure: 'ppm2' }, 'bar')).toBeNull();
    expect(chartProblem(chart({ view: 'bar', rows: [] }))).toMatch(/osa x/);
    expect(chartProblem(chart({ view: 'table', rows: [] }))).toBeNull();
  });

  it('disables a byty-only dimension off byty, with the reason', () => {
    expect(dimBlock('area_band_5', 'dum')).toBe('jen byty');
    expect(dimBlock('area_band_5', 'byt')).toBeNull();
    expect(dimBlock('area_band_5', null)).toBeNull();
    expect(dimBlock('disposition', 'dum')).toBeNull();
  });

  it('drops a chip or a slot where it lands, and says when the view refuses it', () => {
    const c = chart({ rows: ['disposition', 'condition', 'kraj'] });
    /* A chosen palette chip moves like its slot: down onto kraj lands AT kraj. */
    expect(dropOutcome(c, 'disposition', { shelf: 'rows', at: 'kraj' }).next.rows).toEqual([
      'condition', 'kraj', 'disposition',
    ]);
    expect(dropOutcome(c, 'kraj', { shelf: 'rows', at: 'disposition' }).next.rows).toEqual([
      'kraj', 'disposition', 'condition',
    ]);
    expect(dropOutcome(c, 'has_lift', { shelf: 'rows', at: 'condition' }).next.rows).toEqual([
      'disposition', 'has_lift', 'condition', 'kraj',
    ]);
    expect(dropOutcome(c, 'has_lift', { shelf: 'cols' })).toMatchObject({ refused: false, next: { cols: ['has_lift'] } });
    const box = chart({ view: 'box', rows: ['disposition'] });
    expect(dropOutcome(box, 'condition', { shelf: 'cols' })).toEqual({ next: box, refused: true });
    const six = chart({ rows: ['disposition', 'condition', 'kraj', 'okres', 'obec', 'ulice'] });
    expect(dropOutcome(six, 'has_lift', { shelf: 'rows' }).refused).toBe(true);
    expect(dropOutcome(six, 'ulice', { shelf: 'rows', at: 'ulice' }).refused).toBe(false);
  });

  it('groups every registry dimension exactly once in the palette', () => {
    const grouped = DIMENSION_GROUPS.flatMap((g) => g.dims);
    expect([...grouped].sort()).toEqual(STATS_REGISTRY.dimensions.map((d) => d.id).sort());
    expect(new Set(grouped).size).toBe(grouped.length);
    expect(DIMENSION_GROUPS.map((g) => g.label)).toEqual(['Vlastnost', 'Cena', 'Místo', 'Vybavení']);
  });
});

describe('sanitizeChart', () => {
  it('keeps a valid chart and defaults its stat', () => {
    expect(sanitizeChart({ id: 'x', view: 'box', rows: ['disposition'], cols: [], measure: 'ppm2' })).toEqual(
      chart({ id: 'x', view: 'box', rows: ['disposition'] }),
    );
  });
  it('drops what the registry would refuse', () => {
    expect(sanitizeChart({ id: 'x', view: 'box', rows: [], cols: ['condition'], measure: 'ppm2' })).toBeNull();
    expect(sanitizeChart({ id: 'x', view: 'line', rows: [], cols: [], measure: 'ppm2' })).toBeNull();
    expect(sanitizeChart({ id: 'x', view: 'table', rows: ['gone'], cols: [], measure: 'ppm2' })).toBeNull();
    expect(sanitizeChart({ id: 'x', view: 'table', rows: ['kraj', 'kraj'], cols: [], measure: 'ppm2' })).toBeNull();
    expect(sanitizeChart({ id: '', view: 'table', rows: [], cols: [], measure: 'ppm2' })).toBeNull();
  });
});

describe('seriesModel — x is the one row dimension, series the column values', () => {
  it('orders x and series by the registry and leaves a gap for a cell under the minimum', () => {
    const p = pivotOf({
      spec: { rows: ['disposition'], cols: ['has_lift'], measure: 'ppm2' },
      cells: [
        cell(['2+kk'], 20, 200, 'ne'),
        cell(['1+kk'], 20, 100, 'ano'),
        cell(['1+kk'], 4, null, 'ne'),
        cell(['2+kk'], 20, 210, 'ano'),
      ],
    });
    const m = seriesModel(p, 'median');
    expect(m.series.map((s) => s.label)).toEqual(['Ano', 'Ne']);
    expect(m.data.map((d) => d.label)).toEqual(['1+kk', '2+kk']);
    expect(m.data[0]).toMatchObject({ s0: 100, s1: null });
    expect(m.gaps).toBe(true);
    expect(seriesCount(p)).toBe(2);
  });

  it('counts the series a column would draw, one without a column', () => {
    const many = pivotOf({
      spec: { rows: ['disposition'], cols: ['obec'], measure: 'ppm2' },
      cells: Array.from({ length: MAX_SERIES + 1 }, (_, i) => cell(['1+kk'], 20, 100, String(582786 + i))),
    });
    expect(seriesCount(many)).toBe(MAX_SERIES + 1);
    expect(seriesCount(pivotOf({ spec: { rows: ['disposition'], cols: [], measure: 'ppm2' } }))).toBe(1);
  });
});

describe('refusals and defaults', () => {
  it('has one Czech sentence per refusal', () => {
    expect(refusalText('mixed_category')).toMatch(/jeden typ nemovitosti a jeden typ nabídky/);
    expect(refusalText('mixed_status')).toMatch(/jen aktivní, nebo jen neaktivní/);
    expect(sp(refusalText('too_many_cells'))).toMatch(/2 000 buněk/);
    expect(refusalText('empty')).toMatch(/žádné nemovitosti/);
    expect(refusalText('bad_spec')).toMatch(/neplatné/);
  });

  it('seeds the four default charts', () => {
    expect(DEFAULT_CHARTS.map(({ view, rows, measure }) => ({ view, rows, measure }))).toEqual([
      { view: 'table', rows: [], measure: 'price' },
      { view: 'table', rows: [], measure: 'ppm2' },
      { view: 'box', rows: ['disposition'], measure: 'ppm2' },
      { view: 'box', rows: ['condition'], measure: 'ppm2' },
    ]);
    expect(DEFAULT_CHARTS.every((c) => sanitizeChart(c) != null)).toBe(true);
    expect(sanitizeChart(newChart())).not.toBeNull();
  });

  it('seeds only charts that answer on the default filter, and on any category', () => {
    /* Status 'any' mixes live and delisted properties: a turnover seed would open
     * every new account's board on a mixed_status refusal. */
    expect(DEFAULT_FILTERS.status).toBe('any');
    for (const c of DEFAULT_CHARTS) {
      expect(statsMeasure(c.measure)?.single_status, c.id).toBe(false);
      /* No category-limited dimension (area bands): switching the filter off
       * byty must not empty a seed. */
      for (const d of [...c.rows, ...c.cols]) {
        expect(STATS_REGISTRY.dimensions.find((x) => x.id === d)?.categories ?? null, `${c.id} ${d}`).toBeNull();
      }
    }
  });
});

describe('the board', () => {
  afterEach(() => vi.mocked(pgRead).mockReset());

  it('shows the defaults when the account has no row', async () => {
    vi.mocked(pgRead).mockResolvedValueOnce({ data: null, count: null });
    expect(await fetchDashboard()).toEqual(DEFAULT_CHARTS);
  });

  it('reads the saved list, an empty one included', async () => {
    vi.mocked(pgRead).mockResolvedValueOnce({ data: { charts: [chart({ id: 'mine' })] }, count: null });
    expect((await fetchDashboard()).map((c) => c.id)).toEqual(['mine']);
    vi.mocked(pgRead).mockResolvedValueOnce({ data: { charts: [] }, count: null });
    expect(await fetchDashboard()).toEqual([]);
  });

  it('saves the whole list through PUT /stats/dashboard', async () => {
    vi.mocked(apiPut).mockResolvedValueOnce({ charts: [chart({ id: 'a' })] });
    const out = await saveDashboard([chart({ id: 'a' })]);
    expect(apiPut).toHaveBeenCalledWith('/stats/dashboard', { charts: [chart({ id: 'a' })] });
    expect(out.map((c) => c.id)).toEqual(['a']);
  });
});
