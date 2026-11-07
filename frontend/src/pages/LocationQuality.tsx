/* Location quality — the `location_town_coverage` gauge's STORED cells (view
 * location_quality_by_source, migration 698, judged by scripts/checks/quality.py), read through
 * the Health page's own query (lib/healthChecks HEALTH_QUERY): the two pages show identical
 * numbers by construction, and Acknowledge patches the one cache entry. Ruling Q7: nothing is
 * measured twice and the page holds no threshold; every status is the check's, every value the
 * count or share that colours it. Admin-only (the health view returns no row to anyone else).
 * W3b moved corrections to the property page's address picker and deleted the inspector.
 * W6-c (migration 733): the town cell's stored position grid replaced the grain bars, and every
 * number in it opens its ads (components/location/PositionAudit); the portal and the open cell
 * live in the URL (?portal=&kind=&grain=).
 *
 * Visual note: a class mix is single-hue horizontal bars — magnitude of one measure, so one
 * hue; identity color would be noise. */

import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
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
import { KindStrip, NoAnswerEntry, PositionList, PositionsGrid } from '@/components/location/PositionAudit';
import {
  KIND_LABEL,
  listFromParams,
  paramsForList,
  POSITION_GRAINS,
  positionGrid,
  type PositionCell,
  type PositionListSpec,
} from '@/lib/positionAudit';

const CHECK_KEY = 'location_town_coverage';

/* A cell's counts by class (`facts.disputed`) as bars, in the matrix's figures. */
function BarList({ counts }: { counts: unknown }) {
  const rows = Object.entries(counts && typeof counts === 'object' ? counts : {})
    .filter((e): e is [string, number] => typeof e[1] === 'number');
  const total = rows.reduce((a, [, n]) => a + n, 0);
  if (rows.length === 0) {
    return <p className="text-sm text-[var(--color-ink-3)]">No rows.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {rows.map(([value, n]) => {
        const pct = total ? (100 * n) / total : 0;
        return (
          <li key={value} className="flex items-center gap-2">
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
          </li>
        );
      })}
    </ul>
  );
}

/* ---------- the property waterfall (ruling Q18) ---------- */

/* Every published property once, at its location ad's finest level: the no-town steps are the served cells themselves
 * (Health's figures, by their own names), then served/town's stored `grains` coarse to fine in the position grid's
 * words. Only stored counts, never a remainder; null when the reading stored no waterfall. */
function waterfall(fleet: HealthCell[]): Record<string, number> | null {
  const town = fleet.find((c) => c.subject === 'served/town');
  const withheld = fleet.find((c) => c.subject === 'served/town_withheld');
  const grains = town?.facts.grains as Record<string, number> | undefined;
  if (!town || !withheld || !grains) return null;
  return {
    [KIND_LABEL.foreign.label]: Number(town.facts.foreign),
    [town.subject]: Number(town.value),
    [withheld.subject]: Number(withheld.value),
    ...Object.fromEntries(
      POSITION_GRAINS.filter((g) => g.key in grains).reverse().map((g) => [`ends at ${g.label}`, grains[g.key]]),
    ),
  };
}

function PropertyWaterfall({ fleet }: { fleet: HealthCell[] }) {
  const steps = waterfall(fleet);
  return (
    <Panel title="Property waterfall">
      <div data-testid="property-waterfall">
        <p className="mb-3 text-sm text-[var(--color-ink-2)]">
          Published properties, each counted once at its location ad’s finest level. Published, not all active: a young
          property counts once it is published. Browse also applies the consumer location rule, so its count can differ.
        </p>
        {steps ? (
          <BarList counts={steps} />
        ) : (
          <p className="text-sm text-[var(--color-ink-3)]">The gauge has stored no waterfall in this reading.</p>
        )}
      </div>
    </Panel>
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

function Scorecard({
  row,
  source,
  list,
  onSource,
  onList,
}: {
  row: HealthRow;
  source: string;
  list: PositionListSpec | null;
  onSource: (s: string) => void;
  onList: (portal: string, list: PositionListSpec | null) => void;
}) {
  const cell = list?.mode === 'cell' ? list.cell : null;
  const onCell = (portal: string, next: PositionCell) => onList(portal, { mode: 'cell', cell: next });
  const { portals, columns, at, fleet } = useMemo(() => pivot(row.details?.cells ?? []), [row.details]);
  /* Every verdict is Health's (`verdictOf`); a stale row's facts are history, withheld as on Health. */
  const current = row.effective_status !== 'unknown';
  const positions = current ? at[source]?.town?.facts.positions : undefined;
  const disputed = current ? at[source]?.outside?.facts.disputed : undefined;
  const grids = current
    ? portals.map((p) => [p, positionGrid(at[p]?.town?.facts.positions)] as const)
    : [];
  return (
    <>
      {current ? <PropertyWaterfall fleet={fleet} /> : null}
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
      {current ? (
        <PositionsGrid
          portal={source}
          grid={positions === undefined ? undefined : positionGrid(positions)}
          chosen={cell}
          onOpen={(c) => onCell(source, c)}
        />
      ) : null}
      {cell ? (
        <PositionList key={`${source}|${cell.kind}|${cell.grain ?? ''}`} portal={source} spec={{ mode: 'cell', cell }}
          readingAt={row.run_at} onClose={() => onList(source, null)} />
      ) : null}
      <KindStrip grids={grids} portal={source} chosen={cell} onOpen={onCell} />
      <NoAnswerEntry open={list?.mode === 'answerless'}
        onOpen={() => onList(source, list?.mode === 'answerless' ? null : { mode: 'answerless' })} />
      {list?.mode === 'answerless' ? (
        <PositionList key="answerless" portal={source} spec={list} readingAt={null} onClose={() => onList(source, null)} />
      ) : null}
      {disputed ? (
        <Panel title={`Disputed classes · ${portalShort(source)}`}>
          <BarList counts={disputed} />
        </Panel>
      ) : null}
    </>
  );
}

/* ---------- page ---------- */

export default function LocationQuality() {
  /* The portal and the open cell are the URL's (?portal=&kind=&grain=), so a cell is a link. */
  const [params, setParams] = useSearchParams();
  const source = params.get('portal') ?? 'bezrealitky';
  const list = listFromParams(params);
  const openList = (portal: string, next: PositionListSpec | null) => setParams(paramsForList(portal, next));
  const setSource = (portal: string) => openList(portal, null);
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
            <Scorecard row={row} source={source} list={list} onSource={setSource} onList={openList} />
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
