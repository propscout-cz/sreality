/* Location quality — the `location_town_coverage` gauge's STORED cells (view
 * location_quality_by_source, migration 698, judged by scripts/checks/quality.py), read through
 * the Health page's own query (lib/healthChecks HEALTH_QUERY): the two pages show identical
 * numbers by construction, and Acknowledge patches the one cache entry. Ruling Q7: nothing is
 * measured twice and the page holds no threshold; every status is the check's, every value the
 * count or share that colours it. Admin-only (the health view returns no row to anyone else).
 * W3b moved corrections to the property page's address picker and deleted the inspector.
 *
 * Visual note: a class mix is single-hue horizontal bars — magnitude of one measure, so one
 * hue; identity color would be noise. */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Field } from '@/components/controls';
import ErrorBanner from '@/components/ErrorBanner';
import CheckRow, { fmtNumber, fmtValue, measuredLine } from '@/components/health/CheckRow';
import { MatrixCell } from '@/components/health/PortalMatrix';
import Panel from '@/components/Panel';
import Skeleton from '@/components/Skeleton';
import StatusDot from '@/components/StatusDot';
import { HEALTH_QUERY, HealthEmptyError, tail, verdictOf, type HealthCell, type HealthRow } from '@/lib/healthChecks';
import { portalShort } from '@/lib/portals';
import { LOCATION_SOURCES } from '../lib/locationQuality';

const CHECK_KEY = 'location_town_coverage';

/* A cell's counts by class (`facts.grains`, `facts.disputed`) as bars, in the matrix's figures. */
function BarList({ counts }: { counts: unknown }) {
  const rows = Object.entries(counts && typeof counts === 'object' ? counts : {})
    .filter((e): e is [string, number] => typeof e[1] === 'number');
  const total = rows.reduce((a, [, n]) => a + n, 0);
  if (rows.length === 0) {
    return <p className="text-sm text-[var(--color-ink-3)]">No rows.</p>;
  }
  return (
    <div className="space-y-1.5">
      {rows.map(([value, n]) => {
        const pct = total ? (100 * n) / total : 0;
        return (
          <div key={value} className="flex items-center gap-2">
            <span className="w-44 shrink-0 truncate text-sm text-[var(--color-ink-2)]">
              {value}
            </span>
            <span className="h-2 flex-1 rounded-full bg-[var(--color-rule-soft)]">
              <span
                className="block h-2 rounded-full bg-[var(--color-copper)]"
                style={{ width: `${Math.max(pct, pct > 0 ? 1 : 0)}%` }}
              />
            </span>
            <span className="text-right font-mono tabular-nums text-[0.78rem]">
              {fmtNumber(n)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- the scorecard ---------- */

/* Rows = the portals the cells name, columns = their arms (Health's `tail`), both in the gauge's
 * own order (no per-key table here). Fleet cells (portal null) are the strip. */
function pivot(cells: HealthCell[]) {
  const portals: string[] = [];
  const columns: string[] = [];
  const at: Record<string, Record<string, HealthCell>> = {};
  const fleet: HealthCell[] = [];
  for (const c of cells) {
    if (c.portal == null) {
      fleet.push(c);
      continue;
    }
    const arm = tail(c.subject, c.portal);
    if (!at[c.portal]) {
      at[c.portal] = {};
      portals.push(c.portal);
    }
    if (!columns.includes(arm)) columns.push(arm);
    at[c.portal][arm] = c;
  }
  return { portals, columns, at, fleet };
}

function Scorecard({ row, source, onSource }: { row: HealthRow; source: string; onSource: (s: string) => void }) {
  const { portals, columns, at, fleet } = useMemo(() => pivot(row.details?.cells ?? []), [row.details]);
  /* Every verdict is Health's (`verdictOf`); a stale row's facts are history, withheld as on Health. */
  const current = row.effective_status !== 'unknown';
  const grains = current ? at[source]?.street?.facts.grains : undefined;
  const disputed = current ? at[source]?.outside?.facts.disputed : undefined;
  return (
    <>
      <Panel title="By portal">
        <div className="overflow-x-auto">
          <table className="block w-full border-collapse text-sm md:table">
            <caption className="sr-only">Location quality by portal; a row picks the source of the panels below</caption>
            <thead className="hidden md:table-header-group">
              <tr className="text-left text-[0.65rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)]">
                <th scope="col" className="py-1.5 pr-3 font-medium">Portal</th>
                {columns.map((c) => (
                  <th key={c} scope="col" className="py-1.5 pr-3 font-medium">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody className="block md:table-row-group">
              {portals.map((p) => (
                <tr
                  key={p}
                  data-testid={`location-matrix-${p}`}
                  onClick={() => onSource(p)}
                  className={`block md:table-row border-t border-[var(--color-rule-soft)] cursor-pointer hover:bg-[var(--color-copper-soft)] ${p === source ? 'bg-[var(--color-copper-soft)]' : ''}`}
                >
                  <th scope="row" className="block py-1.5 pr-3 text-left font-normal md:table-cell">
                    {/* The keyboard path to the row's click: it bubbles there. */}
                    <button type="button" aria-pressed={p === source} className="text-sm text-[var(--color-ink)]">
                      {portalShort(p)}
                    </button>
                  </th>
                  {columns.map((c) => (
                    <MatrixCell key={c} label={c} portal={p} entry={verdictOf(row, at[p][c] ? [at[p][c]] : [])} />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {fleet.length > 0 && (
          <ul
            data-testid="location-fleet"
            className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 border-t border-[var(--color-rule-soft)] pt-3 text-[0.78rem]"
          >
            {fleet.map((c) => {
              const v = verdictOf(row, [c]);
              const value = v.cell ? fmtValue(v.cell.value, v.cell.unit) : null;
              return (
                <li key={c.subject} title={v.cell?.message} className="flex items-baseline gap-x-2">
                  <span className="font-mono text-[0.72rem] text-[var(--color-ink-2)]">{c.subject}</span>
                  <StatusDot status={v.status} size="sm" />
                  {value && <span className="font-mono tabular-nums text-[var(--color-ink)]">{value}</span>}
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
      {grains || disputed ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title={`Grain · ${portalShort(source)}`}>
            <BarList counts={grains} />
          </Panel>
          <Panel title={`Disputed classes · ${portalShort(source)}`}>
            <BarList counts={disputed} />
          </Panel>
        </div>
      ) : null}
    </>
  );
}

/* ---------- page ---------- */

export default function LocationQuality() {
  const [source, setSource] = useState<string>('bezrealitky');
  /* The Health page's own entry (same key, same poll): identical numbers by construction. */
  const health = useQuery({
    ...HEALTH_QUERY,
    select: (rows: HealthRow[]) => rows.find((r) => r.check_key === CHECK_KEY) ?? null,
  });
  const row = health.data;
  const when = row ? measuredLine(row) : null;

  return (
    <div className="px-6 pt-5 pb-8 max-w-screen-2xl mx-auto">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl leading-tight">Location quality</h1>
          <p className="text-sm text-[var(--color-ink-2)]">
            The stored cells of <span className="font-mono">{CHECK_KEY}</span>, as Health shows them
            {when ? <> · <span title={when.title}>{when.text}</span></> : null}
          </p>
        </div>
        <Field label="Source" as="control">
          <select
            className="rounded-[var(--radius-xs)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-2 py-1.5 text-sm"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          >
            {LOCATION_SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
      </header>

      <div className="mt-4 space-y-4">
        {health.isPending ? (
          <Skeleton height={280} />
        ) : row === undefined ? (
          <ErrorBanner
            title={health.error instanceof HealthEmptyError ? 'Empty read:' : 'Failed to load:'}
            message={health.error?.message ?? 'The health view did not answer.'}
            onRetry={() => void health.refetch()}
            retryLabel="Retry"
          />
        ) : row ? (
          <>
            <Scorecard row={row} source={source} onSource={setSource} />
            <Panel title="As on Health">
              <CheckRow row={{ ...row, owner_path: '' }} />
            </Panel>
          </>
        ) : (
          <p className="text-sm text-[var(--color-ink-3)]">
            The health read holds no <span className="font-mono">{CHECK_KEY}</span> check.
          </p>
        )}
      </div>
    </div>
  );
}
