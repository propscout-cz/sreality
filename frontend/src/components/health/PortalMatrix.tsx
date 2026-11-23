/* The Health page's spine (docs/design/health-dashboard.md § 5.3): nine portals answering to the
 * portal checks, worst portal first. A cell is the check's own verdict for that portal by the
 * harness's rule (lib/healthChecks pivotByPortal: the worst MEASURED cell, Not measured only when
 * nothing was); a non-OK cell says its word, value and arm. The row head expands the portal's arms,
 * each linking to its line in the check below. One CSS-responsive tree: below 768 px the rows
 * stack, OK cells and all-OK portals hide (`hidden md:…`). The anchors live in the check rows,
 * never here, so an id is never duplicated. No current portal cell at all (nothing measured
 * since the checks were declared, or every portal check timed out) reads Not measured, never a red. */
import { Fragment, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import StatusDot from '@/components/StatusDot';
import { Chevron } from '@/components/settings/SectionChrome';
import {
  STATUS_RANK,
  anchorId,
  cellsWorstFirst,
  pivotByPortal,
  tail,
  type HealthRow,
  type Status,
} from '@/lib/healthChecks';
import { portalShort } from '@/lib/portals';
import { fmtValue } from './CheckRow';

type Pivot = ReturnType<typeof pivotByPortal>;
type Entry = Pivot['cells'][string][string];

const NONE: Entry = { status: 'unknown', cell: null, measured: 0, notMeasured: 0 };

/* Worst portal first: its worst cell; on a tie, its worst ALARM cell (a standing gauge Problem
 * does not pin a portal above a live alarm), then more Problems, more Watch, more Not measured. */
function worstPortalsFirst({ columns, portals, cells }: Pivot): string[] {
  const score = (p: string) => {
    const of = (cols: HealthRow[]) => cols.map((c) => (cells[p]?.[c.check_key] ?? NONE).status);
    const all = of(columns);
    const alarms = of(columns.filter((c) => c.class === 'alarm'));
    const worst = (s: Status[]) => Math.min(STATUS_RANK.ok, ...s.map((x) => STATUS_RANK[x]));
    const count = (s: Status) => all.filter((x) => x === s).length;
    return [worst(all), worst(alarms), -count('fail'), -count('warn'), -count('unknown')];
  };
  const scored = new Map(portals.map((p) => [p, score(p)]));
  return [...portals].sort((a, b) => {
    const [sa, sb] = [scored.get(a) ?? [], scored.get(b) ?? []];
    for (let i = 0; i < sa.length; i++) if (sa[i] !== sb[i]) return sa[i] - sb[i];
    return 0;
  });
}

export default function PortalMatrix({ pivot }: { pivot: Pivot }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const order = useMemo(() => worstPortalsFirst(pivot), [pivot]);
  const { columns, cells } = pivot;
  if (!columns.length) return null;
  if (!order.length) {
    return (
      <p role="status" className="flex flex-wrap items-center gap-x-2 text-sm text-[var(--color-ink-2)]">
        <StatusDot status="unknown" />
        <span>· no portal check has a current portal cell</span>
      </p>
    );
  }
  const toggle = (p: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(p)) next.add(p);
      return next;
    });

  return (
    <table className="block w-full border-collapse text-sm md:table">
      <caption className="sr-only">Portals by check, worst portal first</caption>
      <thead className="hidden md:table-header-group">
        <tr>
          <th scope="col" className="w-40 py-1.5 pr-3 text-left text-[0.65rem] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]">
            Portal
          </th>
          {columns.map((col) => (
            <th key={col.check_key} scope="col" className="py-1.5 pr-3 text-left align-bottom font-normal">
              <span className="flex flex-wrap items-baseline gap-x-2">
                <Link
                  to={{ hash: anchorId(col.check_key) }}
                  className="text-[0.65rem] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-2)] underline decoration-[var(--color-rule-strong)] underline-offset-2"
                >
                  {col.label}
                </Link>
                {col.class === 'gauge' && <span className="text-[0.62rem] text-[var(--color-ink-3)]">gauge</span>}
                {col.effective_status !== 'ok' && <StatusDot status={col.effective_status} size="sm" word="sr-only" />}
              </span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="block md:table-row-group">
        {order.map((p) => {
          const row = columns.map((col) => ({ col, entry: cells[p]?.[col.check_key] ?? NONE }));
          const quiet = row.every(({ entry }) => entry.status === 'ok');
          const expanded = open.has(p);
          const detailId = `portal-arms-${p}`;
          return (
            <Fragment key={p}>
              <tr
                className={`${quiet ? 'hidden md:table-row' : 'block md:table-row'} border-t border-[var(--color-rule-soft)]`}
              >
                <th scope="row" className="block py-1.5 pr-3 text-left font-normal md:table-cell">
                  <button
                    type="button"
                    aria-expanded={expanded}
                    aria-controls={detailId}
                    onClick={() => toggle(p)}
                    className="flex items-center gap-1.5 text-sm text-[var(--color-ink)] max-md:min-h-11"
                  >
                    {/* The only cue that the head opens: ink-3 (3.9:1 light, 4.3:1 dark), not the
                        twisty's own ink-4 (2.0:1, 2.2:1). `!`: Tailwind emits ink-4 after ink-3. */}
                    <Chevron open={expanded} className="text-[var(--color-ink-3)]!" />
                    {portalShort(p)}
                  </button>
                </th>
                {row.map(({ col, entry }) => (
                  <MatrixCell key={col.check_key} label={col.label} portal={p} entry={entry} />
                ))}
              </tr>
              <tr id={detailId} className={expanded ? 'block md:table-row' : 'hidden'}>
                {expanded && (
                  <td colSpan={columns.length + 1} className="block pb-3 md:table-cell">
                    <Arms portal={p} columns={columns} />
                  </td>
                )}
              </tr>
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

/* One verdict. OK is a quiet dot (its word for screen readers only) that hides below 768 px; every
 * other status says its word, so colour is never the only cue (sage and brick read alike to a
 * red-green colour-blind eye). The full message is the cell's title and its line below. The arm
 * is named unless the column already is it (the Location Quality page's columns are arms). */
export function MatrixCell({ label, portal, entry }: { label: string; portal: string; entry: Entry }) {
  const ok = entry.status === 'ok';
  const value = entry.cell ? fmtValue(entry.cell.value, entry.cell.unit) : null;
  const arm = entry.cell ? tail(entry.cell.subject, portal) : null;
  const notMeasured = entry.notMeasured ? `${entry.notMeasured} not measured` : null;
  return (
    <td
      title={[entry.cell?.message, notMeasured].filter(Boolean).join(' · ') || undefined}
      className={`${ok ? 'hidden md:table-cell' : 'block md:table-cell'} py-1.5 pr-3 align-top`}
    >
      <span className="flex flex-wrap items-baseline gap-x-2 text-[0.78rem]">
        <span className="text-[0.65rem] uppercase tracking-[0.1em] text-[var(--color-ink-3)] md:hidden">{label}</span>
        <StatusDot status={entry.status} size="lg" word={ok ? 'sr-only' : 'visible'} />
        {!ok && entry.cell && (
          <span className="min-w-0 break-words text-[var(--color-ink)]">
            {value && <span className="font-mono tabular-nums">{value}</span>}
            {value && arm !== label && ' · '}
            {arm !== label && <span className="font-mono text-[0.72rem] text-[var(--color-ink-2)]">{arm}</span>}
          </span>
        )}
      </span>
    </td>
  );
}

/* A portal's arms per check: what is not OK, each linking to its line in the check below (which
 * holds the anchor, the facts and the trend), then how many were not measured and how many read OK. */
function Arms({ portal, columns }: { portal: string; columns: HealthRow[] }) {
  return (
    <div className="grid gap-x-6 gap-y-3 rounded-[var(--radius-sm)] bg-[var(--color-inset)] px-3 py-2.5 md:grid-cols-2 xl:grid-cols-3">
      {columns.map((col) => {
        const stale = col.effective_status === 'unknown';
        const mine = stale ? [] : cellsWorstFirst((col.details?.cells ?? []).filter((c) => c.portal === portal));
        const open = mine.filter((c) => c.status === 'fail' || c.status === 'warn');
        const unmeasured = mine.filter((c) => c.status === 'unknown').length;
        const ok = mine.length - open.length - unmeasured;
        return (
          <div key={col.check_key} className="min-w-0">
            <p className="text-[0.65rem] uppercase tracking-[0.14em] text-[var(--color-ink-3)]">{col.label}</p>
            <ul className="mt-1 space-y-1 text-[0.78rem]">
              {open.map((c) => (
                <li key={c.subject} className="flex flex-wrap items-baseline gap-x-2">
                  <StatusDot status={c.status} size="sm" />
                  <Link
                    to={{ hash: anchorId(col.check_key, c.subject) }}
                    title={c.message}
                    className="break-all font-mono text-[0.72rem] text-[var(--color-copper-2)] underline decoration-dotted underline-offset-2"
                  >
                    {tail(c.subject, portal)}
                  </Link>
                  {fmtValue(c.value, c.unit) && (
                    <span className="font-mono tabular-nums text-[var(--color-ink)]">{fmtValue(c.value, c.unit)}</span>
                  )}
                </li>
              ))}
            </ul>
            <p className="mt-1 text-[0.72rem] text-[var(--color-ink-2)]">
              {stale ? 'Not measured: the check has no current result' : `${unmeasured} not measured · ${ok} OK`}
            </p>
          </div>
        );
      })}
    </div>
  );
}
