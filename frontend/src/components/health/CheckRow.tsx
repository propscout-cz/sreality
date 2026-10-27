/* One check of the registry, drawn the same way in every section of the Health page (docs/design/
 * health-dashboard.md § 5, "Every row"): the status word with its dot, the label, the value with its
 * unit, when it was measured (or since when it was not, and why), the check's own message, every
 * cell that is not OK, worst first, up to five (the rest, and every OK cell, behind "Show all"), the
 * incident line with Acknowledge, and the link to the page that owns the fix. An OK row is one muted
 * line plus any Not measured cells (on the phone, a note of its hidden OK cells). Nothing here judges: every status is the view's (`effective_status`) or the check's own
 * (`cell.status`), and every message is quoted as written. */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import StatusDot, { STATUS_WORD } from '@/components/StatusDot';
import {
  HEALTH_QUERY,
  acknowledge,
  anchorId,
  cellsWorstFirst,
  isOpen,
  isUnacknowledged,
  ownerTitle,
  type HealthCell,
  type HealthRow,
  type Status,
} from '@/lib/healthChecks';
import { timeLabelFull, valueAxisSpec } from '@/lib/chartAxis';
import { fmtAbsolute, fmtMeasuredPricePerM2, fmtRelative, fmtTime24 } from '@/lib/format';
import { ppm2BasisFromToken } from '@/lib/measure';

/* "Top five, then all" (design § 5): how many non-OK cells a row lists before "Show all". A
 * display count, not a threshold. */
const TOP_CELLS = 5;
/* A trend line needs two points; one point is a value, already on the line. */
const MIN_TREND_POINTS = 2;
/* A calendar day, to space the trend's points by date so a missing day shows as a gap. */
const DAY_MS = 86_400_000;

/* What the hash target wears: copper is "chosen or central" (system.md), a rule, never a shadow. */
const TARGET = 'border-l-2 border-[var(--color-copper)] bg-[var(--color-copper-soft)]';

/* English figures, as the checks write them ("1,897", "11.9"); a fraction keeps its magnitude
 * (0.04 h is not "0 h"). */
const ONE_DP = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const FINE = new Intl.NumberFormat('en-US', { maximumSignificantDigits: 2 });
const PERCENT = new Intl.NumberFormat('en-US', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 });

export function fmtNumber(v: number): string {
  const s = ONE_DP.format(v);
  return (s === '0' || s === '-0') && v !== 0 ? FINE.format(v) : s;
}

/* Value and unit: "3 cells", "44 h", "1 ad"; '%' and '×' sit tight ("11.9%", "1.4×"). At 1 the
 * unit's first plural word drops its 's' ("1 object absent", "1 failed run", "1 decision/h");
 * "USD", "min", "h", "s" and "pp" have none to drop. */
export function fmtValue(value: number | null | undefined, unit: string | null | undefined): string | null {
  if (value == null || !Number.isFinite(value)) return null;
  const n = fmtNumber(value);
  if (!unit) return n;
  if (unit === '%' || unit === '×') return n + unit;
  return `${n}\u00a0${value === 1 ? unit.replace(/\b(\w{2,})s\b/, '$1') : unit}`;
}

/* A clock time today, a date and time otherwise. */
export function fmtSince(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toDateString() === new Date().toDateString() ? fmtTime24(iso) : fmtAbsolute(iso);
}

const plural = (n: number, one: string, many: string) => `${n}\u00a0${n === 1 ? one : many}`;
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/* When the row was measured, or since when it was not and why (design § 5: "measured X ago" or
 * "not measured since T: reason"). A stale row names its stored verdict only as history. */
export function measuredLine(row: HealthRow): { text: string; title?: string } {
  if (row.effective_status !== 'unknown') {
    return { text: `measured ${fmtRelative(row.run_at)}`, title: fmtAbsolute(row.run_at) };
  }
  if (row.run_at == null) return { text: `not measured since ${fmtSince(row.declared_at)}: no result yet` };
  if (row.status != null && row.status !== 'unknown') {
    return {
      text: `not measured since ${fmtSince(row.last_measured_at ?? row.run_at)}: its newest result (${STATUS_WORD[row.status]}) is past its staleness budget`,
    };
  }
  return { text: `not measured since ${fmtSince(row.last_measured_at ?? row.declared_at)}` };
}

export default function CheckRow({ row, target }: { row: HealthRow; target?: { subject?: string } }) {
  const st = row.effective_status;
  const key = row.check_key;
  // Read Not measured by the view (stale, or nothing measured), the row draws nothing as a current
  // OK. A stale row's cells are its last verdicts: all withheld. One that measured nothing keeps its
  // non-OK cells, never an OK one nor its stored value: health_liveness() stores 'unknown' over OK
  // cells and keeps its value (migration 633), so the stored status alone never decides.
  const unmeasured = st === 'unknown';
  const stale = unmeasured && row.status != null && row.status !== 'unknown';
  const legacy = row.details != null && !Array.isArray(row.details.cells);
  const cells = useMemo(() => {
    const all = stale ? [] : cellsWorstFirst(row.details?.cells ?? []);
    return unmeasured ? all.filter((c) => c.status !== 'ok') : all;
  }, [stale, unmeasured, row.details]);
  const top = cells.filter((c) => c.status !== 'ok').slice(0, TOP_CELLS);
  const targetCell = target?.subject == null ? undefined : cells.find((c) => c.subject === target.subject);
  // A link to a cell past the top five (or to an OK one) opens the list until the operator closes it.
  const [showAll, setShowAll] = useState<boolean | null>(null);
  const expanded = showAll ?? (targetCell != null && !top.includes(targetCell));
  const listed = expanded ? cells : top;
  const notMeasured = cells.filter((c) => c.status === 'unknown').length;
  // Below 768 px OK cells hide (CellLine). When every cell "Show all" adds is OK, the phone gets no
  // button that could show nothing: the note saying where they are stands in for it.
  const okCells = cells.filter((c) => c.status === 'ok');
  const onlyOkBehind = cells.length > top.length && cells.length - top.length === okCells.length;
  const hiddenOk = expanded ? okCells.filter((c) => c !== targetCell).length : onlyOkBehind ? okCells.length : 0;
  const quiet = st === 'ok';
  const message = !quiet && !stale ? row.details?.message : undefined;
  const open = row.incidents.filter(isOpen);
  const when = measuredLine(row);
  const stateCell = cells.find((c) => c.subject === 'state' && typeof c.facts.state === 'string');
  const rowTargeted = target != null && targetCell == null;
  // The value: a count of the drawn cells at the row's status for a 'cells' row ("2 cells" under
  // Problem), the stored value for any other unit (Watchers' minutes, drifted files), withheld
  // while the row reads Not measured.
  const value = legacy
    ? null
    : row.unit === 'cells'
      ? cells.length ? plural(cells.filter((c) => c.status === st).length, 'cell', 'cells') : null
      : unmeasured ? null : fmtValue(row.value, row.unit);
  const listId = `cells-${key}`;
  const seen = new Set<string>();

  // tabIndex -1: a #key link lands focus on the row, Acknowledge hands it to the heading.
  return (
    <article
      id={anchorId(key)}
      tabIndex={-1}
      aria-labelledby={`check-${key}`}
      className={`scroll-mt-20 py-3 ${rowTargeted ? `${TARGET} px-2` : ''}`}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
        <StatusDot status={st} />
        <h3
          id={`check-${key}`}
          tabIndex={-1}
          className={`text-[0.95rem] leading-snug ${quiet ? 'font-medium text-[var(--color-ink-2)]' : ''}`}
        >
          {row.label}
        </h3>
        {row.class === 'gauge' && (
          <span className="rounded-[var(--radius-xs)] border border-[var(--color-rule)] px-1 text-[0.62rem] uppercase tracking-[0.1em] text-[var(--color-ink-3)]">
            gauge
          </span>
        )}
        {stateCell && (
          <span
            title={stateCell.message}
            className="rounded-[var(--radius-xs)] border border-[var(--color-rule)] bg-[var(--color-paper)] px-1.5 py-0.5 font-mono text-[0.68rem] text-[var(--color-ink-2)]"
          >
            {String(stateCell.facts.state)}
          </span>
        )}
        {value && <span className="font-mono tabular-nums text-[0.8rem] text-[var(--color-ink)]">{value}</span>}
        {notMeasured > 0 && st !== 'unknown' && (
          <span className="font-mono tabular-nums text-[0.75rem] text-[var(--color-ink-2)]">
            {notMeasured}&nbsp;not measured
          </span>
        )}
        <span className="font-mono text-[0.72rem] tabular-nums text-[var(--color-ink-2)]" title={when.title}>
          {when.text}
        </span>
        <span className="ml-auto flex flex-wrap items-baseline gap-x-3">
          {cells.length > top.length && (
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls={listId}
              onClick={() => setShowAll(!expanded)}
              className={`${onlyOkBehind ? 'hidden md:inline-flex' : 'max-md:inline-flex max-md:min-h-11 max-md:items-center'} text-[0.75rem] text-[var(--color-ink-2)] underline decoration-[var(--color-rule-strong)] underline-offset-2 hover:text-[var(--color-ink)]`}
            >
              {expanded ? 'Show fewer' : `Show all ${plural(cells.length, 'cell', 'cells')}`}
            </button>
          )}
          {row.owner_path && (
            <Link
              to={row.owner_path}
              className="text-[0.75rem] text-[var(--color-copper-2)] underline decoration-dotted underline-offset-2"
            >
              {ownerTitle(row.owner_path)} <span aria-hidden>→</span>
            </Link>
          )}
        </span>
      </div>

      {message && <Message text={message} />}

      {cells.length > 0 && (
        <ul id={listId} className={listed.length ? 'mt-2 space-y-1.5' : hiddenOk ? 'max-md:mt-1' : ''}>
          {listed.map((c, i) => {
            const first = !seen.has(c.subject);
            seen.add(c.subject);
            return <CellLine key={`${i}:${c.subject}`} checkKey={key} cell={c} anchor={first} targeted={c === targetCell} />;
          })}
          {hiddenOk > 0 && (
            <li className="text-[0.72rem] text-[var(--color-ink-2)] md:hidden">
              {plural(hiddenOk, 'OK cell is', 'OK cells are')} listed on a wider screen.
            </li>
          )}
        </ul>
      )}

      {open.length > 0 && <IncidentLine checkKey={key} incidents={row.incidents} />}
    </article>
  );
}

/* The check's own sentence, quoted as written; two lines until opened. The toggle shows only
 * when the clamp actually hides text (measured in the browser, so no length guess); its name
 * says what it opens and keeps its visible word ("more", "less"), so a voice command still finds it. */
function Message({ text }: { text: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setOverflows(el.scrollHeight > el.clientHeight);
    measure();
    if (typeof ResizeObserver !== 'function') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text]);
  return (
    <div className="mt-1 flex items-start gap-2">
      <p
        ref={ref}
        className={`min-w-0 flex-1 whitespace-pre-line break-words text-[0.8rem] leading-relaxed text-[var(--color-ink-2)] ${open ? '' : 'line-clamp-2'}`}
      >
        {text}
      </p>
      {(open || overflows) && (
        <button
          type="button"
          aria-expanded={open}
          aria-label={open ? 'Show less of the message' : 'Show more of the message'}
          onClick={() => setOpen(!open)}
          className="shrink-0 text-[0.75rem] text-[var(--color-ink-2)] underline decoration-[var(--color-rule-strong)] underline-offset-2 max-md:inline-flex max-md:min-h-11 max-md:items-center"
        >
          {open ? 'less' : 'more'}
        </button>
      )}
    </div>
  );
}

/* One cell: its word and dot, subject, value, message, and the facts worth a glance. An OK cell
 * hides below 768 px unless the link targets it. Only the first line of a subject carries its
 * anchor (and takes focus when a link lands on it), so a repeated subject never duplicates an id
 * (each worker heartbeat is its own `worker/<name>` subject since migration 675). */
function CellLine({
  checkKey,
  cell,
  anchor,
  targeted,
}: {
  checkKey: string;
  cell: HealthCell;
  anchor: boolean;
  targeted: boolean;
}) {
  const value = fmtValue(cell.value, cell.unit);
  const show = cell.status === 'ok' && !targeted ? 'hidden md:flex' : 'flex';
  return (
    <li
      id={anchor ? anchorId(checkKey, cell.subject) : undefined}
      tabIndex={anchor ? -1 : undefined}
      className={`${show} scroll-mt-20 flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[0.8rem] ${targeted ? `${TARGET} px-2 py-1` : ''}`}
    >
      <StatusDot status={cell.status} size="sm" />
      <span className="break-all font-mono text-[0.72rem] text-[var(--color-ink-2)]">{cell.subject}</span>
      {value && <span className="font-mono tabular-nums text-[var(--color-ink)]">{value}</span>}
      <span className="min-w-0 flex-1 basis-64 break-words text-[var(--color-ink-2)]">{cell.message}</span>
      <Facts facts={cell.facts} unit={cell.unit} />
    </li>
  );
}

/* The chosen facts (W8 contract): share and lower bound are FRACTIONS, shown as percent; a per-m²
 * figure only with the basis that names its unit (rule 23, lib/measure.ts), else not at all; a
 * gauge cell's "worse than 7 d ago" when `worse_7d` is true (what the strip's "Gauges worse than
 * last week" counts); a workflow's run link; a gauge's trend. Everything else stays in the message. */
function Facts({ facts, unit }: { facts: Record<string, unknown>; unit: string | null }) {
  const share = num(facts.share);
  const lower = num(facts.lower_bound);
  const median = num(facts.median);
  const basis = ppm2BasisFromToken(typeof facts.price_per_m2_basis === 'string' ? facts.price_per_m2_basis : null);
  const run = typeof facts.run_url === 'string' && facts.run_url.startsWith('https://') ? facts.run_url : null;
  const series = trendSeries(facts.trend);
  const parts = [
    share != null ? `share ${PERCENT.format(share)}` : null,
    lower != null ? `lower bound ${PERCENT.format(lower)}` : null,
    median != null && basis != null && basis !== 'mixed' ? `median ${fmtMeasuredPricePerM2(median, basis)}` : null,
    facts.worse_7d === true ? 'worse than 7\u00a0d ago' : null,
  ].filter((p): p is string => p != null);
  if (!parts.length && !run && series.length < MIN_TREND_POINTS) return null;
  return (
    <span className="flex flex-wrap items-center gap-x-3 font-mono tabular-nums text-[0.72rem] text-[var(--color-ink-2)]">
      {parts.map((p) => <span key={p}>{p}</span>)}
      {run && (
        <a href={run} target="_blank" rel="noreferrer" className="text-[var(--color-copper)] underline-offset-2 hover:underline">
          run <span aria-hidden>↗</span>
        </a>
      )}
      {series.length >= MIN_TREND_POINTS && <Trend series={series} unit={unit} />}
    </span>
  );
}

type Point = { day: string; at: number; value: number; status: Status };

/* A gauge cell's `facts.trend` ([[Prague day, value, status], …], GLOSSARY "Trend"): the points
 * with a number, oldest first. */
function trendSeries(points: unknown): Point[] {
  return (Array.isArray(points) ? points : [])
    .filter((p): p is [string, number, string] => Array.isArray(p) && typeof p[0] === 'string' && num(p[1]) != null)
    .map(([day, value, status]): Point => ({
      day,
      at: Date.parse(`${day}T00:00:00Z`),
      value,
      status: status === 'ok' || status === 'warn' || status === 'fail' ? status : 'unknown',
    }))
    .filter((p) => !Number.isNaN(p.at))
    .sort((a, b) => a.at - b.at);
}

const TREND_DOT: Record<Status, string> = {
  ok: 'fill-[var(--color-sage)]',
  warn: 'fill-[var(--color-ochre)]',
  fail: 'fill-[var(--color-brick)]',
  unknown: 'fill-[var(--color-ink-3)]',
};

/* The sparkline: a line through the measured days, dashed across a day with no measurement
 * (dashed = absent), the newest point's dot in its status. The value domain comes from
 * lib/chartAxis like every axis in the app; the days are spaced by date. */
function Trend({ series, unit }: { series: Point[]; unit: string | null }) {
  const values = series.map((p) => p.value);
  const { domain: [lo, hi] } = valueAxisSpec([Math.min(...values), Math.max(...values)], { targetTicks: 2 });
  const first = series[0];
  const last = series[series.length - 1];
  const span = (last.at - first.at) / DAY_MS;
  const x = (p: Point) => 2 + ((p.at - first.at) / DAY_MS / span) * 116;
  const y = (p: Point) => 18 - ((p.value - lo) / (hi - lo)) * 16;
  const label =
    `Trend, ${series.length} measured days from ${timeLabelFull(first.day, 'day')} to ${timeLabelFull(last.day, 'day')}: ` +
    `${fmtValue(first.value, unit)} to ${fmtValue(last.value, unit)}`;
  return (
    <svg role="img" aria-label={label} width="120" height="20" viewBox="0 0 120 20" className="shrink-0">
      <title>{label}</title>
      {series.slice(1).map((p, i) => {
        const prev = series[i];
        const gap = (p.at - prev.at) / DAY_MS !== 1;
        return (
          <line
            key={p.day}
            x1={x(prev)}
            y1={y(prev)}
            x2={x(p)}
            y2={y(p)}
            strokeWidth="1.25"
            strokeDasharray={gap ? '2 2' : undefined}
            className={gap ? 'stroke-[var(--color-rule-strong)]' : 'stroke-[var(--color-ink-3)]'}
          />
        );
      })}
      <circle cx={x(last)} cy={y(last)} r="2.25" className={TREND_DOT[last.status]} />
    </svg>
  );
}

/* The incident line (design § 5: "since 06:40 · 2 notices · not acknowledged") and the one write
 * on the page. Acknowledge is per CHECK (the route refuses a subject): it stops the reminders of
 * every open incident of this check; each still closes only on a measured OK. A success patches
 * the read before it refetches, so a failed refetch never shows them unacknowledged again; once
 * the patched row is drawn, focus lands on its heading (the button left with it), and the status
 * region, mounted before it speaks, says how many the server acknowledged. */
function IncidentLine({ checkKey, incidents }: { checkKey: string; incidents: HealthRow['incidents'] }) {
  const qc = useQueryClient();
  const [said, setSaid] = useState('');
  const focusHeading = useRef(false);
  const ack = useMutation({
    mutationFn: () => acknowledge(checkKey),
    onSuccess: (n) => {
      const key = HEALTH_QUERY.queryKey;
      const mine = qc.getQueryData<HealthRow[]>(key)?.find((r) => r.check_key === checkKey);
      // Focus moves only when the patch takes the button away; a poll may have shown them acknowledged already.
      focusHeading.current = mine?.incidents.some(isUnacknowledged) ?? false;
      setSaid(`Acknowledged ${plural(n, 'incident', 'incidents')}`);
      const at = new Date().toISOString();
      // The patch keeps the read's own time: it is not a new read, and the header's "read X ago" must not say so.
      qc.setQueryData<HealthRow[]>(
        key,
        (rows) =>
          rows?.map((r) =>
            r.check_key !== checkKey
              ? r
              : { ...r, incidents: r.incidents.map((i) => (isUnacknowledged(i) ? { ...i, acknowledged_at: at } : i)) },
          ),
        { updatedAt: qc.getQueryState(key)?.dataUpdatedAt },
      );
      return qc.invalidateQueries({ queryKey: key });
    },
    // Its own onError keeps lib/mutationCache's global toast away: the failure is said here.
    onError: () => undefined,
  });
  const open = incidents.filter(isOpen);
  const waiting = open.filter(isUnacknowledged).length;
  useEffect(() => {
    if (!focusHeading.current || waiting) return;
    focusHeading.current = false;
    // Only when the button took the focus with it (it fell to <body>); never pulled from elsewhere.
    if (document.activeElement == null || document.activeElement === document.body) {
      document.getElementById(`check-${checkKey}`)?.focus();
    }
  }, [waiting, checkKey]);
  const oldest = open.reduce((a, b) => (Date.parse(b.first_seen_at) < Date.parse(a.first_seen_at) ? b : a));
  const notices = open.reduce((n, i) => n + (i.notice_count ?? 0), 0);
  const acked = open.map((i) => i.acknowledged_at).filter((t): t is string => t != null).sort();
  const state =
    waiting === open.length
      ? 'not acknowledged'
      : waiting === 0
        ? `acknowledged ${fmtSince(acked[acked.length - 1])}`
        : `${open.length - waiting} of ${open.length} acknowledged`;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.75rem] text-[var(--color-ink-2)]">
      <span className="font-mono text-[0.72rem] tabular-nums">
        {plural(open.length, 'open incident', 'open incidents')} · since {fmtSince(oldest.first_seen_at)} ·{' '}
        {plural(notices, 'notice', 'notices')} · {state}
      </span>
      {/* Pending: aria-disabled, clicks ignored; `disabled` would drop the focus it holds. */}
      {waiting > 0 && (
        <button
          type="button"
          aria-disabled={ack.isPending || undefined}
          onClick={() => {
            if (ack.isPending) return;
            setSaid('');
            ack.mutate();
          }}
          title={`Stops the reminders and the canary e-mail for this check's ${plural(waiting, 'open incident', 'open incidents')}; each still closes on a measured OK.`}
          className="rounded-[var(--radius-xs)] border border-[var(--color-copper)] px-3 py-1 text-[0.8rem] text-[var(--color-copper)] hover:bg-[var(--color-copper-soft)] aria-disabled:cursor-wait max-md:h-11"
        >
          {ack.isPending ? 'Acknowledging…' : 'Acknowledge'}
        </button>
      )}
      {waiting > 0 && ack.isError && (
        <span role="alert" className="text-[0.75rem] text-[var(--color-brick)]">
          Acknowledge failed: {ack.error.message}
        </span>
      )}
      <span role="status" aria-live="polite" className="sr-only">
        {said}
      </span>
    </div>
  );
}
