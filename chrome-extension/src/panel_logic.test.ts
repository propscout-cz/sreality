/* `npm test` (node --test). The listing panel's rules, apart from its DOM. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CHIPS_MAX, FOLD_BELOW_PX, STEP_BAR_MAX, chartShape, cutChips, factChips, fmtDate, portalNote, stageControl,
  startsFolded, storedFold, tabMarkList, type FactFormat, type FactRow,
} from './panel_logic.ts';

test('the sheet starts folded below 1280 px whatever was stored, else as last left', () => {
  assert.equal(FOLD_BELOW_PX, 1280);
  for (const stored of [null, false, true]) assert.equal(startsFolded(stored, 1279), true);
  assert.equal(startsFolded(null, 1280), false, 'never chose: open');
  assert.equal(startsFolded(false, 1440), false);
  assert.equal(startsFolded(true, 1440), true);
});

test('the stored fold wins; the old panel\'s "minimized" counts only until one is stored', () => {
  assert.equal(storedFold({}), null, 'never chose');
  assert.equal(storedFold({ panelFolded: false, panelMinimized: true }), false);
  assert.equal(storedFold({ panelFolded: true }), true);
  assert.equal(storedFold({ panelMinimized: true }), true, 'the old choice, read once');
  assert.equal(storedFold({ panelMinimized: false }), null, 'an old "open" is no choice');
  assert.equal(storedFold({ panelFolded: 'yes' }), null, 'only a boolean is a choice');
});

test('the folded tab marks the stage, a price DROP only, and MF, each only when there', () => {
  const drop = (pct: number) => `drop ${pct}`;
  assert.deepEqual(tabMarkList('S', -4.3, 'MF', drop), ['S', 'drop -4.3', 'MF']);
  assert.deepEqual(tabMarkList('S', 2.1, 'MF', drop), ['S', 'MF'], 'a rise is no news on a tab');
  assert.deepEqual(tabMarkList('S', 0, null, drop), ['S']);
  assert.deepEqual(tabMarkList(null, null, null, drop), []);
  assert.deepEqual(tabMarkList(null, -1, null, drop), ['drop -1']);
});

const FMT = {
  floor: (f: number, t: number | null) => ({ text: `${f}/${t}`, title: `${f} of ${t}` }),
  label: (col: string, slug: string) => `${col}:${slug}`,
  plot: (m2: number) => `${m2} m² pozemku`,
};
const texts = (row: FactRow, fmt: FactFormat): string[] => factChips(row, fmt).map((f) => f.text);
const BASE: FactRow = {
  category_main: 'byt', floor: 4, total_floors: 6, condition: 'velmi_dobry', ownership: 'osobni',
  property_subtype: 'rodinny', plot_area_m2: 905,
};

test('each property type shows its own facts, in its order, values only', () => {
  assert.deepEqual(texts(BASE, FMT), ['4/6', 'condition:velmi_dobry', 'ownership:osobni']);
  assert.deepEqual(texts({ ...BASE, category_main: 'dum' }, FMT),
    ['905 m² pozemku', 'condition:velmi_dobry', 'subtype:rodinny']);
  assert.deepEqual(texts({ ...BASE, category_main: 'komercni' }, FMT),
    ['subtype:rodinny', '4/6', 'condition:velmi_dobry']);
  assert.deepEqual(texts({ ...BASE, category_main: 'pozemek' }, FMT), ['ownership:osobni'],
    'a plot: ownership only (its area is the title)');
  assert.deepEqual(texts({ ...BASE, category_main: 'ostatni' }, FMT), []);
});

test('a missing fact is left out, never drawn as a dash', () => {
  const bare = { ...BASE, floor: null, condition: null, ownership: '' };
  assert.deepEqual(texts(bare, FMT), []);
  assert.deepEqual(texts({ ...BASE, category_main: 'dum', plot_area_m2: null }, FMT),
    ['condition:velmi_dobry', 'subtype:rodinny']);
  // Ground floor is a floor (0), not a missing one.
  assert.deepEqual(texts({ ...BASE, floor: 0, condition: null, ownership: null }, FMT), ['0/6']);
  // A formatter that has no label leaves the chip out.
  assert.deepEqual(texts(BASE, { ...FMT, label: () => null }), ['4/6']);
});

test('the floor chip keeps its formatter\'s tooltip; a label chip has none', () => {
  assert.deepEqual(factChips(BASE, FMT).slice(0, 2),
    [{ text: '4/6', title: '4 of 6' }, { text: 'condition:velmi_dobry' }]);
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

const FRAME = { width: 312, height: 116, left: 28, right: 60, top: 12, bottom: 20, labelCh: 6 };
const DAY = 86_400_000;
type Pt = { t: number; price: number };
const adTrack = (id: number, label: string, points: Pt[], endT: number) => ({ id, label, points, endT });

test('a track is a step line held flat between moves, dotted where the price moved', () => {
  const shape = chartShape(
    [adTrack(1, 'Sreality', [{ t: 0, price: 100 }, { t: 10 * DAY, price: 100 }, { t: 20 * DAY, price: 90 }], 40 * DAY)],
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
    adTrack(1, 'Sreality', [{ t: 0, price: 100 }], 40 * DAY),
    adTrack(2, 'iDNES', [{ t: 5 * DAY, price: 99 }], 40 * DAY),
    adTrack(3, 'Bezrealitky', [{ t: 2 * DAY, price: 95 }], 30 * DAY),
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
    adTrack(1, 'Sreality', [{ t: 0, price: 50 }], 10 * DAY),
    adTrack(2, 'iDNES', [], 10 * DAY),
  ], FRAME, [0, 10 * DAY], [45, 55]);
  assert.equal(shape.tracks.length, 1);
  assert.equal(shape.tracks[0].d, 'M28 54H252');
  assert.deepEqual(shape.tracks[0].steps, []);
});

test('an ended track\'s label crossing a line moves below its start, then right of the plot', () => {
  // Canonical flat at 95 (y 33); the ended track starts at 93 (y 41.4), so "above" sits on the canonical line.
  let shape = chartShape([
    adTrack(1, 'Sreality', [{ t: 0, price: 95 }], 40 * DAY),
    adTrack(3, 'iDNES', [{ t: 5 * DAY, price: 93 }], 20 * DAY),
  ], FRAME, [0, 40 * DAY], [80, 100]);
  let ended = shape.tracks.find((t) => t.id === 3);
  assert.deepEqual(shape.labels.find((l) => l.id === 3), { id: 3, x: (ended?.start.x ?? 0) + 4, y: 54.4 }, 'below the start');
  // The canonical steps down through both spots (a rise at 10 days, a run at 91): the gutter.
  shape = chartShape([
    adTrack(1, 'Sreality', [{ t: 0, price: 95 }, { t: 10 * DAY, price: 91 }], 40 * DAY),
    adTrack(3, 'Bezrealitky', [{ t: 5 * DAY, price: 93 }], 20 * DAY),
  ], FRAME, [0, 40 * DAY], [80, 100]);
  ended = shape.tracks.find((t) => t.id === 3);
  const gutter = shape.labels.filter((l) => l.x === 259).sort((a, b) => a.y - b.y);
  assert.deepEqual(gutter.map((l) => l.id), [3, 1], 'right of the plot, at its end');
  assert.equal(gutter[0].y, (ended?.end.y ?? 0) + 4);
  assert.ok(gutter[1].y - gutter[0].y >= 12, 'never on another label');
});

test('an ended track starting at the top is labelled below its start', () => {
  const shape = chartShape([adTrack(3, 'iDNES', [{ t: 2 * DAY, price: 100 }], 20 * DAY)], FRAME, [0, 40 * DAY], [80, 100]);
  const [t] = shape.tracks;
  assert.deepEqual(shape.labels, [{ id: 3, x: t.start.x + 4, y: t.start.y + 13 }]);
});

test('right-hand labels crowded at the bottom shift up into the plot, 12 px apart', () => {
  const shape = chartShape(
    [1, 2, 3, 4, 5, 6].map((id) => adTrack(id, `Portál ${id}`, [{ t: 0, price: 80 }], 40 * DAY)),
    FRAME, [0, 40 * DAY], [80, 100],
  );
  const ys = shape.labels.map((l) => l.y).sort((a, b) => a - b);
  assert.equal(ys.length, 6);
  for (const y of ys) assert.ok(y >= FRAME.top + 8 && y <= FRAME.height - FRAME.bottom + 4, `in the plot: ${y}`);
  for (let i = 1; i < ys.length; i++) assert.ok(ys[i] - ys[i - 1] >= 12, `apart: ${ys[i - 1]} ${ys[i]}`);
});

test('a day prints as d. m. yyyy; nothing or garbage prints nothing', () => {
  // Midday local time: the same calendar day on any clock.
  assert.equal(fmtDate(new Date(2026, 9, 24, 12).getTime()), '24. 10. 2026');
  assert.equal(fmtDate(null), '');
  assert.equal(fmtDate('not a date'), '');
});
