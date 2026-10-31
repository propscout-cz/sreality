/* Location quality — the `location_town_coverage` gauge's STORED cells (view
 * location_quality_by_source, migration 698, judged by scripts/checks/quality.py), read through
 * the Health page's own query (lib/healthChecks HEALTH_QUERY): the two pages show identical
 * numbers by construction, and Acknowledge patches the one cache entry. Ruling Q7: nothing is
 * measured twice and the page holds no threshold; every status is the check's, every value the
 * count or share that colours it. Admin-only (the health view returns no row to anyone else).
 * Below the scorecard, the listing inspector and operator corrections over the admin-gated
 * `/location/*` API (detail by id).
 *
 * Visual note: a class mix is single-hue horizontal bars — magnitude of one measure, so one
 * hue; identity color would be noise. */

import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Field } from '@/components/controls';
import ErrorBanner from '@/components/ErrorBanner';
import CheckRow, { fmtValue, measuredLine } from '@/components/health/CheckRow';
import Panel, { PanelGroup } from '@/components/Panel';
import Skeleton from '@/components/Skeleton';
import Stat from '@/components/Stat';
import StatusDot from '@/components/StatusDot';
import { HEALTH_QUERY, HealthEmptyError, type HealthCell, type HealthRow } from '@/lib/healthChecks';
import { portalShort } from '@/lib/portals';
import {
  fetchInspector, fetchInspectorByNative, submitCorrection,
  LOCATION_SOURCES,
  type CorrectionResult, type Inspector,
} from '../lib/locationQuality';
import { fmtCount, fmtRelative } from '../lib/format';

const CHECK_KEY = 'location_town_coverage';

/* A cell's counts by class (`facts.grains`, `facts.disputed`) as bars. */
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
              {fmtCount(n)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- the scorecard ---------- */

/* Rows = the portals the cells name, columns = the subject after `${portal}/`, both in the
 * gauge's own order (no per-key table here); a subject naming no arm (an older result's shape)
 * is the portal's town. Fleet cells (portal null) are the strip. */
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
    const arm = c.subject.startsWith(`${c.portal}/`) ? c.subject.slice(c.portal.length + 1) : 'town';
    if (!at[c.portal]) {
      at[c.portal] = {};
      portals.push(c.portal);
    }
    if (!columns.includes(arm)) columns.push(arm);
    at[c.portal][arm] = c;
  }
  return { portals, columns, at, fleet };
}

/* One verdict, the check's own; Not measured while the view reads the row Not measured (its
 * stored cells are history then, as on Health). OK says its word to screen readers only. */
function Verdict({ cell, measured }: { cell: HealthCell | undefined; measured: boolean }) {
  const status = measured && cell ? cell.status : 'unknown';
  const value = measured && cell ? fmtValue(cell.value, cell.unit) : null;
  return (
    <span className="flex flex-wrap items-baseline gap-x-2" title={measured ? cell?.message : undefined}>
      <StatusDot status={status} size="lg" word={status === 'ok' ? 'sr-only' : 'visible'} />
      {value && <span className="font-mono tabular-nums text-[var(--color-ink)]">{value}</span>}
    </span>
  );
}

function Scorecard({ row, source, onSource }: { row: HealthRow; source: string; onSource: (s: string) => void }) {
  const measured = row.effective_status !== 'unknown';
  const { portals, columns, at, fleet } = useMemo(() => pivot(row.details?.cells ?? []), [row.details]);
  const grains = at[source]?.street?.facts.grains;
  const disputed = at[source]?.outside?.facts.disputed;
  return (
    <>
      <Panel title="By portal">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Location quality by portal; a row picks the inspector&apos;s source</caption>
            <thead>
              <tr className="text-left text-[0.65rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)]">
                <th scope="col" className="py-1.5 pr-3 font-medium">Portal</th>
                {columns.map((c) => (
                  <th key={c} scope="col" className="py-1.5 pr-3 font-medium">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {portals.map((p) => (
                <tr
                  key={p}
                  data-testid={`location-matrix-${p}`}
                  onClick={() => onSource(p)}
                  className={`border-t border-[var(--color-rule-soft)] cursor-pointer hover:bg-[var(--color-copper-soft)] ${p === source ? 'bg-[var(--color-copper-soft)]' : ''}`}
                >
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                    {/* The keyboard path to the row's click: it bubbles there. */}
                    <button type="button" aria-pressed={p === source} className="text-sm text-[var(--color-ink)]">
                      {portalShort(p)}
                    </button>
                  </th>
                  {columns.map((c) => (
                    <td key={c} className="py-1.5 pr-3 align-top text-[0.78rem]">
                      <Verdict cell={at[p][c]} measured={measured} />
                    </td>
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
            {fleet.map((c) => (
              <li key={c.subject} className="flex items-baseline gap-x-2">
                <span className="font-mono text-[0.72rem] text-[var(--color-ink-2)]">{c.subject}</span>
                <Verdict cell={c} measured={measured} />
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {measured && (grains || disputed) ? (
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

/* ---------- inspector + corrections ---------- */

const CORRECTABLE = [
  'address_point_id', 'street_name', 'house_number_cp', 'house_number_co',
  'psc', 'obec_name', 'cast_obce_name', 'okres_name',
] as const;

function InspectorSection({ source }: { source: string }) {
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState<string | null>(null);
  const inspector = useQuery({
    queryKey: ['location', 'inspector', source, submitted],
    queryFn: () =>
      /^\d+$/.test(submitted!.trim())
        ? fetchInspector(submitted!)
        : fetchInspectorByNative(source, submitted!),
    enabled: submitted != null && submitted.trim() !== '',
  });
  const ins = inspector.data?.data ?? null;
  return (
    <Panel title="Listing inspector (read-your-writes)">
      <form
        className="flex items-end gap-2 max-w-md"
        onSubmit={(e) => { e.preventDefault(); setSubmitted(query); }}
      >
        {/* The submit button stays OUTSIDE the Field: anything inside the
          * <label> would join the input's accessible name and fire label
          * activation. */}
        <Field label="Listing or native id" as="control" className="flex-1">
          <input
            className="w-full rounded-[var(--radius-xs)] border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-sm"
            placeholder={`listing id, or ${source} native id`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </Field>
        <button
          type="submit"
          className="rounded-[var(--radius-xs)] border border-[var(--color-copper)] px-3 py-1.5 text-sm text-[var(--color-copper)] hover:bg-[var(--color-copper-soft)]"
        >
          Inspect
        </button>
      </form>
      {inspector.error ? <ErrorBanner message={inspector.error.message} /> : null}
      {ins ? <InspectorBody ins={ins} onCorrected={() => inspector.refetch()} /> : null}
    </Panel>
  );
}

function InspectorBody({ ins, onCorrected }: { ins: Inspector; onCorrected: () => void }) {
  const p = ins.projection ?? {};
  const axis = (k: string) => String(p[k] ?? '∅');
  /* The answer table's four read axes. There is no stored display label: the
   * row carries its parts and the reader composes them. */
  const AXES: [string, string][] = [
    ['granularity', 'granularity'], ['match_confidence', 'confidence'],
    ['uncertainty_radius_m', 'radius m'], ['country_status', 'country'],
    ['resolver_version', 'resolver'], ['registry_version', 'registry'],
  ];
  const label = [
    [p.street_name, p.house_number_cp].filter(Boolean).join(' '),
    p.obec_name,
  ].filter(Boolean).join(', ');
  return (
    <div className="mt-4 space-y-4">
      <div>
        <div className="text-sm">
          <span className="font-mono text-[0.8rem]">#{ins.listing_id}</span>{' '}
          <span className="text-[var(--color-ink-2)]">{label || '∅'}</span>
          {p.disputed ? (
            <span className="ml-2 text-[0.7rem] text-[var(--color-brick)]">
              DISPUTED · {String(p.disputed)}
            </span>
          ) : null}
        </div>
        <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          {AXES.map(([k, label_]) => <Stat key={k} label={label_} value={axis(k)} />)}
        </div>
        <div className="mt-1 text-[0.68rem] text-[var(--color-ink-4)]">
          street {axis('street_name')} · čp {axis('house_number_cp')} · čo {axis('house_number_co')} ·
          obec {axis('obec_name')} · PSČ {axis('psc')} · kód ADM {axis('ruian_adm_kod')} ·
          resolved {axis('resolved_at')}
        </div>
      </div>

      <CorrectionForm listingId={ins.listing_id} onDone={onCorrected} />

      <details>
        <summary className="cursor-pointer text-sm text-[var(--color-ink-2)]">
          Claims ({ins.claims.length})
        </summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[0.65rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)]">
                <th className="py-1 pr-3 font-medium">Claim</th>
                <th className="py-1 pr-3 font-medium">Value</th>
                <th className="py-1 pr-3 font-medium">Method</th>
                <th className="py-1 pr-3 font-medium">Licence</th>
                <th className="py-1 font-medium">Observed</th>
              </tr>
            </thead>
            <tbody>
              {ins.claims.map((c) => (
                <tr key={c.id} className="border-t border-[var(--color-rule-soft)]">
                  <td className="py-1 pr-3">{c.claim_type}</td>
                  <td className="py-1 pr-3 font-mono text-[0.75rem]">{c.value_text ?? c.value_num ?? '—'}</td>
                  <td className="py-1 pr-3 font-mono text-[0.75rem]">{c.extraction_method}</td>
                  <td className="py-1 pr-3">{c.licence_class}</td>
                  <td className="py-1 text-[var(--color-ink-3)]">{fmtRelative(c.first_observed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function CorrectionForm({ listingId, onDone }: { listingId: number; onDone: () => void }) {
  const [claimType, setClaimType] = useState<string>('street_name');
  const [value, setValue] = useState('');
  const [note, setNote] = useState('');
  const [result, setResult] = useState<CorrectionResult | null>(null);
  const mut = useMutation({
    mutationFn: () =>
      submitCorrection({ listing_id: listingId, claim_type: claimType, value_text: value, note: note || undefined }),
    onSuccess: (res) => { setResult(res.data); setValue(''); onDone(); },
  });
  const input = 'rounded-[var(--radius-xs)] border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-sm';
  return (
    /* A group inside the inspector card (C7): an L3 title, no box of its own. */
    <PanelGroup
      title="Operator correction"
      accessory={<span className="text-xs text-[var(--color-ink-3)]">appends a claim, resolves immediately</span>}
    >
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => { e.preventDefault(); if (value.trim()) mut.mutate(); }}
      >
        <Field label="Claim type" as="control">
          <select className={input} value={claimType} onChange={(e) => setClaimType(e.target.value)}>
            {CORRECTABLE.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="Corrected value" as="control" className="flex-1 min-w-40">
          <input
            className={`${input} w-full`}
            placeholder={claimType === 'address_point_id' ? 'kód ADM (digits)' : 'corrected value'}
            value={value} onChange={(e) => setValue(e.target.value)}
          />
        </Field>
        <Field label="Note" as="control" className="min-w-32">
          <input
            className={`${input} w-full`} placeholder="note (optional)"
            value={note} onChange={(e) => setNote(e.target.value)}
          />
        </Field>
        <button
          type="submit" disabled={mut.isPending || !value.trim()}
          className="rounded-[var(--radius-xs)] bg-[var(--color-copper)] px-3 py-1.5 text-sm text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] disabled:opacity-50"
        >
          {mut.isPending ? 'Applying…' : 'Correct'}
        </button>
      </form>
      {mut.error ? <ErrorBanner message={mut.error.message} /> : null}
      {result ? (
        <div className="mt-2 text-[0.75rem] text-[var(--color-ink-2)]">
          {result.restatement ? 'Restated an existing claim' : 'Claim appended'} ·{' '}
          {result.resolved ? 'resolved synchronously' : 'queued for the next drain (≤15 min)'}
          {result.registry_echo ? (
            <span> · registry: {String(result.registry_echo.street ?? '')} {String(result.registry_echo.cislo_domovni ?? '')}
              {result.registry_echo.cislo_orientacni ? `/${result.registry_echo.cislo_orientacni}` : ''},{' '}
              {String(result.registry_echo.obec ?? '')} {String(result.registry_echo.psc ?? '')}</span>
          ) : null}
          {result.projection ? (
            <span> · now: {String(result.projection.granularity)} / {String(result.projection.street_name ?? '∅')}</span>
          ) : null}
        </div>
      ) : null}
    </PanelGroup>
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

        <InspectorSection source={source} />
      </div>
    </div>
  );
}
