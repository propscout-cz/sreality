/* `npm test` (node --test). The listing panel's rules, apart from its DOM. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CHIPS_MAX, FOLD_BELOW_PX, STEP_BAR_MAX, chartShape, cutChips, factChips, fmtDate, portalNote, stageControl,
  startsFolded, type FactRow,
} from './panel_logic.ts';

test('the sheet starts folded below 1280 px whatever was stored, else as last left', () => {
  assert.equal(FOLD_BELOW_PX, 1280);
  for (const stored of [null, false, true]) assert.equal(startsFolded(stored, 1279), true);
  assert.equal(startsFolded(null, 1280), false, 'never chose: open');
  assert.equal(startsFolded(false, 1440), false);
  assert.equal(startsFolded(true, 1440), true);
});

const FMT = {
  floor: (f: number, t: number | null) => `${f}/${t}`,
  label: (col: string, slug: string) => `${col}:${slug}`,
  plot: (m2: number) => `${m2} m² pozemku`,
};
const BASE: FactRow = {
  category_main: 'byt', floor: 4, total_floors: 6, condition: 'velmi_dobry', ownership: 'osobni',
  property_subtype: 'rodinny', plot_area_m2: 905,
};

test('each property type shows its own facts, in its order, values only', () => {
  assert.deepEqual(factChips(BASE, FMT), ['4/6', 'condition:velmi_dobry', 'ownership:osobni']);
  assert.deepEqual(factChips({ ...BASE, category_main: 'dum' }, FMT),
    ['905 m² pozemku', 'condition:velmi_dobry', 'subtype:rodinny']);
  assert.deepEqual(factChips({ ...BASE, category_main: 'komercni' }, FMT),
    ['subtype:rodinny', '4/6', 'condition:velmi_dobry']);
  assert.deepEqual(factChips({ ...BASE, category_main: 'pozemek' }, FMT), ['ownership:osobni'],
    'a plot: ownership only (its area is the title)');
  assert.deepEqual(factChips({ ...BASE, category_main: 'ostatni' }, FMT), []);
});

test('a missing fact is left out, never drawn as a dash', () => {
  const bare = { ...BASE, floor: null, condition: null, ownership: '' };
  assert.deepEqual(factChips(bare, FMT), []);
  assert.deepEqual(factChips({ ...BASE, category_main: 'dum', plot_area_m2: null }, FMT),
    ['condition:velmi_dobry', 'subtype:rodinny']);
  // Ground floor is a floor (0), not a missing one.
  assert.deepEqual(factChips({ ...BASE, floor: 0, condition: null, ownership: null }, FMT), ['0/6']);
  // A formatter that has no label leaves the chip out.
  assert.deepEqual(factChips(BASE, { ...FMT, label: () => null }), ['4/6']);
});

test('a chip row shows three, then +N for the rest', () => {
  assert.equal(CHIPS_MAX, 3);
  assert.deepEqual(cutChips(['a', 'b']), { shown: ['a', 'b'], more: 0 });
  assert.deepEqual(cutChips(['a', 'b', 'c']), { shown: ['a', 'b', 'c'], more: 0 });
  assert.deepEqual(cutChips(['a', 'b', 'c', 'd', 'e']), { shown: ['a', 'b', 'c'], more: 2 });
});

test('up to four open stages draw a step bar; more fall back to the dropdown', () => {
  assert.equal(STEP_BAR_MAX, 4);
  const open = (n: number) => Array.from({ length: n }, () => ({ is_terminal: false }));
  const closed = [{ is_terminal: true }, { is_terminal: true }, { is_terminal: true }];
  assert.equal(stageControl([...open(4), ...closed]), 'bar', 'closed stages do not count');
  assert.equal(stageControl(open(5)), 'dropdown');
  assert.equal(stageControl([]), 'bar');
});

test('the price note counts portals and the ads taken down', () => {
  assert.deepEqual(portalNote([]), { portals: 0, withdrawn: 0 });
  assert.deepEqual(portalNote([
    { source: 'sreality', is_active: true },
    { source: 'sreality', is_active: false },
    { source: 'idnes', is_active: true },
    { source: 'bezrealitky', is_active: false },
  ]), { portals: 3, withdrawn: 2 }, 'two ads on one portal are one portal');
});

const FRAME = { width: 312, height: 116, left: 28, right: 60, top: 12, bottom: 20 };
const DAY = 86_400_000;

test('a track is a step line held flat between moves, dotted where the price moved', () => {
  const shape = chartShape(
    [{ id: 1, points: [{ t: 0, price: 100 }, { t: 10 * DAY, price: 100 }, { t: 20 * DAY, price: 90 }], endT: 40 * DAY }],
    FRAME, [0, 40 * DAY], [80, 100],
  );
  const [track] = shape.tracks;
  assert.equal(shape.x(0), 28);
  assert.equal(shape.x(40 * DAY), 252);
  assert.equal(shape.y(100), 12);
  assert.equal(shape.y(80), 96);
  // An unchanged observation adds no vertical; the move to 90 does.
  assert.equal(track.d, 'M28 12H84H140V54H252');
  assert.deepEqual(track.steps, [{ x: 140, y: 54 }]);
  assert.deepEqual(track.end, { x: 252, y: 54 });
  assert.equal(track.area, 'M28 12H84H140V54H252V96H28Z');
  assert.deepEqual(shape.labels, [{ id: 1, x: 259, y: 58 }]);
});

test('labels: right of the plot for a live track, never overlapping; above the start for one that ended', () => {
  const shape = chartShape([
    { id: 1, points: [{ t: 0, price: 100 }], endT: 40 * DAY },
    { id: 2, points: [{ t: 5 * DAY, price: 99 }], endT: 40 * DAY },
    { id: 3, points: [{ t: 2 * DAY, price: 95 }], endT: 30 * DAY },
  ], FRAME, [0, 40 * DAY], [80, 100]);
  const byId = new Map(shape.labels.map((l) => [l.id, l]));
  assert.equal(byId.get(1)?.x, 259);
  assert.equal(byId.get(2)?.x, 259);
  assert.ok((byId.get(2)?.y ?? 0) - (byId.get(1)?.y ?? 0) >= 12, 'pushed apart');
  const ended = shape.tracks.find((t) => t.id === 3);
  assert.deepEqual(byId.get(3), { id: 3, x: (ended?.start.x ?? 0) + 4, y: (ended?.start.y ?? 0) - 5 });
  assert.equal(ended?.end.x, shape.x(30 * DAY));
});

test('a flat single-ad history is one line across; a track with no point draws nothing', () => {
  const shape = chartShape([
    { id: 1, points: [{ t: 0, price: 50 }], endT: 10 * DAY },
    { id: 2, points: [], endT: 10 * DAY },
  ], FRAME, [0, 10 * DAY], [45, 55]);
  assert.equal(shape.tracks.length, 1);
  assert.equal(shape.tracks[0].d, 'M28 54H252');
  assert.deepEqual(shape.tracks[0].steps, []);
});

test('a day prints as d. m. yyyy; nothing or garbage prints nothing', () => {
  assert.equal(fmtDate('2026-10-24T08:00:00Z'), '24. 10. 2026');
  assert.equal(fmtDate(null), '');
  assert.equal(fmtDate('not a date'), '');
});
