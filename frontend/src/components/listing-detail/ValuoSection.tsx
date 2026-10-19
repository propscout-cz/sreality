/* The property page's Valuo section (#valuo): a paid outside valuation, after our estimates and never
 * inside them (rule 12), for every signed-in user (operator ruling 2026-10-19). The card flips into its
 * editor in place (system.md), drawn from the API's `fields` alone, every value tagged with where it came
 * from. The phone reads the latest answer only: no editor, no button (#1760 Q10). */
import { useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';

import ErrorBanner from '@/components/ErrorBanner';
import Skeleton from '@/components/Skeleton';
import Spinner from '@/components/Spinner';
import { CollapsibleGroup, DecimalField, Field, Segmented } from '@/components/controls';
import EstimateCard, { CardRow } from '@/components/estimation/EstimateCard';
import RangeStrip from '@/components/estimation/RangeStrip';
import RunHistory, { type HistoryColumn } from '@/components/estimation/RunHistory';
import { RunStatusChip } from '@/components/estimation/RunPanel';
import { SingleSelectDropdown } from '@/components/filter-controls/SingleSelectDropdown';
import { InfoHint } from '@/components/settings/SectionChrome';
import { Hairline, SectionLabel } from '@/components/section';
import { ApiError } from '@/lib/api';
import { czPlural, fmtArea, fmtCount, fmtCzk, fmtDateSlash, fmtDistanceM, fmtMeasuredPricePerM2, fmtPct, fmtTime24 } from '@/lib/format';
import { ppm2BasisFromToken } from '@/lib/measure';
import { mfShape } from '@/lib/mfReference';
import { estimationKeys, fetchEstimationsForListings, propertyRunIds, type PropertyPublic } from '@/lib/queries';
import type { EstimationRun } from '@/lib/types';
import {
  LEASE_PERIOD, missingRequired, optionLabel, useRunValuo, useValuoPage, withEdit,
  type Provenance, type Valuation, type ValuoField, type ValuoPage, type ValuoRun, type ValuoValue, type ValuoValues,
} from '@/lib/valuo';

const NONE = 'Nevyplněno';
const INK3 = 'text-[var(--color-ink-3)]';
const QUIET = 'px-2 py-1 text-[0.8rem] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] disabled:opacity-50';
const LINK = 'text-[0.8rem] text-[var(--color-copper)] hover:underline underline-offset-2';
const GRID = 'grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(11rem,1fr))]';
const ABSENT = '[&_input]:border-dashed [&_select]:border-dashed [&_button]:border-dashed'; // dashed = nothing sent
const Period = ({ on }: { on: boolean }) => (on ? <span className={`ml-1 text-[0.7rem] ${INK3}`}>{LEASE_PERIOD}</span> : null);
const isLease = (v: Valuation) => ppm2BasisFromToken(v.ppm2_basis) === 'rent';
const ENVIRONMENT: Record<Valuation['environment'], string> = { sandbox: 'sandbox', production: 'ostré' };

export default function ValuoSection({ property, listingIds, readOnly = false, bare = false }: {
  property: PropertyPublic;
  listingIds: number[];
  readOnly?: boolean;
  bare?: boolean;
}) {
  const pageQ = useValuoPage(property.property_id);
  const runIds = useMemo(() => propertyRunIds(listingIds), [listingIds]);
  // Our runs as the estimations block above holds them: a cache read, never a fetch of ours.
  const runsQ = useQuery({
    queryKey: estimationKeys.byListing(runIds), queryFn: () => fetchEstimationsForListings(runIds), enabled: false,
  });
  const [pickedId, setPickedId] = useState<number | null>(null);
  const [editing, setEditing] = useState<boolean | null>(null);
  const [reusedAt, setReusedAt] = useState<string | null>(null);
  const page = pageQ.data ?? null;
  const history = page?.history ?? [];
  if (readOnly && !history.some((v) => v.result)) return null;

  const shown = readOnly
    ? (history.find((v) => v.result) ?? null)
    : (history.find((v) => v.id === pickedId) ?? history[0] ?? null);
  const canRun = !readOnly && page != null && page.refusal == null;
  const open = canRun && (editing ?? (history.length === 0 && page.blocking.length > 0));
  const pick = (id: number, reused: string | null = null) => {
    setPickedId(id);
    setEditing(false);
    setReusedAt(reused);
  };
  const done = ({ reused, valuation: v }: ValuoRun) => pick(v.id, reused ? v.created_at : null);
  const card = (v: Valuation) => (
    <ValuoCard valuation={v} page={page} property={property} runs={runsQ.data?.data ?? []}
      onEdit={canRun ? () => setEditing(true) : undefined} />
  );

  let body: ReactNode = null;
  const err = pageQ.error;
  if (pageQ.isLoading) body = <Skeleton height={140} />;
  // A 4xx (no account, no such property) answers the same on every retry: a refusal sentence, not an error.
  else if (err instanceof ApiError && err.kind === 'http' && err.status < 500) body = <p className={`text-sm ${INK3}`}>{err.message}</p>;
  else if (err) body = <ErrorBanner message={err.message} onRetry={() => void pageQ.refetch()} />;
  else if (page) {
    body = (
      <div className="space-y-3">
        {page.refusal && !readOnly && <p className={`text-sm ${INK3}`}>{page.refusal.message}</p>}
        {reusedAt && (
          <p className={`text-[0.75rem] ${INK3}`}>
            Stejný dotaz {fmtDateSlash(reusedAt) === fmtDateSlash(new Date().toISOString()) ? 'dnes' : `dne ${fmtDateSlash(reusedAt)}`}{' '}
            už proběhl ({fmtTime24(reusedAt)}) — zobrazuji uložené ocenění.
          </p>
        )}
        {open ? (
          <ValuoEditor propertyId={property.property_id} page={page} ignored={shown?.ignored_ids ?? []}
            onCancel={() => setEditing(false)} onDone={done} />
        ) : shown ? card(shown) : !page.refusal && (
          <EstimateCard tone="absent" eyebrow="Ocenění Valuo" className="flex flex-col items-start gap-2">
            <p className={`text-sm ${INK3}`}>Zatím bez ocenění</p>
            {canRun && <button type="button" onClick={() => setEditing(true)} className={LINK}>Ocenit přes Valuo →</button>}
          </EstimateCard>
        )}
      </div>
    );
  }

  return (
    <>
      {!bare && <Hairline />}
      <section id="valuo" className="scroll-mt-6">
        <div className="flex items-baseline justify-between gap-3">
          <SectionLabel>Valuo</SectionLabel>
          <div className="flex items-baseline gap-3 text-[0.7rem] text-[var(--color-ink-3)]">
            {history.length > 0 && <p className="tracking-wide font-mono tabular-nums">{history.length} ocenění</p>}
            {(open || !shown ? page?.environment : shown.environment) === 'sandbox' && (
              <span className="px-1.5 border border-[var(--color-rule)] rounded-[var(--radius-xs)] text-[0.58rem] tracking-[0.18em] uppercase">
                Sandbox
              </span>
            )}
          </div>
        </div>
        <div className="mt-4">{body}</div>
        {!readOnly && page && history.length > 1 && (
          <RunHistory title="Všechna ocenění" rows={history} columns={HISTORY_COLUMNS}
            selectedId={shown?.id ?? null} onPick={(id) => pick(id)} when="Kdy" viewing="zobrazeno" />
        )}
      </section>
    </>
  );
}

function ValuoCard({ valuation: v, page, property, runs, onEdit }: {
  valuation: Valuation;
  page: ValuoPage | null;
  property: PropertyPublic;
  runs: EstimationRun[];
  onEdit?: () => void;
}) {
  const basis = ppm2BasisFromToken(v.ppm2_basis);
  const lease = basis === 'rent';
  const perM2 = (n: number | null) => fmtMeasuredPricePerM2(n, basis);
  const r = v.result;
  const aside = onEdit && <button type="button" onClick={onEdit} className={LINK}>Nové ocenění →</button>;
  const eyebrow = `${lease ? 'Odhad nájmu' : 'Odhad ceny'} · Valuo`;
  if (r == null) {
    return (
      <EstimateCard eyebrow={eyebrow} aside={aside}>
        {v.error ? <ErrorBanner title="Ocenění selhalo:" message={v.error.message} />
          : <p className={`mt-2 flex items-center gap-2 text-sm ${INK3}`}><Spinner /> Valuo počítá…</p>}
      </EstimateCard>
    );
  }
  // The ask compares only when the request's deal type IS the ad's own (an auction is a sale by assumption).
  const ask = page?.provenance.kind?.class === 'ad' && v.request.kind === page.defaults.kind ? property.price_czk : null;
  const ours = runs.find((run) => run.status === 'success' && (run.estimate_kind ?? 'rent') === (lease ? 'rent' : 'sale'));
  const mf = mfShape(property.mf_reference_rent);
  const marks = [
    { label: 'nabídka', value: ask },
    { label: 'náš odhad', value: ours && (lease ? ours.estimated_monthly_rent_czk : ours.estimated_sale_price_czk) },
    { label: 'MF', value: lease && mf.kind === 'value' ? mf.ref.monthly_rent_czk : null },
  ].filter((m): m is { label: string; value: number } => m.value != null);
  const delta = ask != null && r.avg_price ? ((ask - r.avg_price) / r.avg_price) * 100 : null;
  const flags = Array.isArray(r.flags) && r.flags.length > 0 ? r.flags.map((f) => (typeof f === 'string' ? f : JSON.stringify(f))).join(', ') : null;
  const rows: [string, string | null][] = [
    ['Za m² (min–max)', r.min_price_m2 != null && r.max_price_m2 != null ? `${fmtCount(Math.round(r.min_price_m2))}–${perM2(r.max_price_m2)}` : null],
    ['Směr. odchylka Valuo', r.std_price_m2 != null ? `± ${perM2(r.std_price_m2)}` : null],
    ['Plocha výpočtu', r.calc_area != null ? fmtArea(r.calc_area) : null],
    ['Okruh srovnání', r.distance != null ? fmtDistanceM(r.distance) : null], // metres: unverified until the probe
    ['Ke dni', typeof v.request.as_of === 'string' ? fmtDateSlash(v.request.as_of) : null],
    ['Příznaky', flags],
  ];
  // Said only when there is something to say: a point coarser than a street, a parameter the answer did not use.
  const notes = [['Poloha', v.provenance.place?.note], ['Valuo ignorovalo', v.ignored_labels.join(', ')]].filter((n): n is [string, string] => !!n[1]);
  return (
    <EstimateCard eyebrow={eyebrow} aside={aside}>
      <div className="mt-1 flex items-baseline justify-between gap-3">
        <span className="text-lg font-medium font-mono tabular-nums">{fmtCzk(r.avg_price)}<Period on={lease} /></span>
        <span className={`text-[0.72rem] font-mono tabular-nums ${INK3}`}>{perM2(r.avg_price_m2)}</span>
      </div>
      {r.min_price != null && r.max_price != null && r.avg_price != null && (
        <div className="mt-3">
          <RangeStrip range={{ low: r.min_price, mid: r.avg_price, high: r.max_price }}
            caption="min · průměr · max (Valuo)" format={fmtCzk} marks={marks} />
        </div>
      )}
      {delta != null && (
        <p className="mt-2 text-[0.8rem] text-[var(--color-ink-2)]">
          {`Nabídka je o ${fmtPct(Math.abs(delta))} ${delta >= 0 ? 'nad' : 'pod'} průměrem Valuo.`}
        </p>
      )}
      <dl className="mt-2 space-y-0.5 text-[0.72rem] tabular-nums">
        {rows.map(([label, value]) => (
          <CardRow key={label} label={label}>
            {value ?? <span className={`px-1.5 border border-dashed border-[var(--color-rule-strong)] rounded-[var(--radius-xs)] ${INK3}`}>—</span>}
          </CardRow>
        ))}
        {notes.map(([label, text]) => <CardRow key={label} label={label}>{text}</CardRow>)}
      </dl>
      <p className="mt-1.5 text-[0.58rem] text-[var(--color-ink-4)]">Valuo API · počet srovnání Valuo neuvádí</p>
    </EstimateCard>
  );
}

const HISTORY_COLUMNS: HistoryColumn<Valuation>[] = [
  { header: 'Druh', className: `text-[0.8rem] ${INK3}`, cell: (v) => v.kind_label },
  {
    header: 'Průměr', right: true, className: 'font-mono tabular-nums text-[var(--color-ink)]',
    cell: (v) => <>{fmtCzk(v.result?.avg_price)}<Period on={v.result != null && isLease(v)} /></>,
  },
  // Each cell names its own unit (rule 23): a sale row and a lease row sit 300x apart.
  {
    header: 'Za m²', right: true, className: 'font-mono tabular-nums text-[var(--color-ink-2)]',
    cell: (v) => fmtMeasuredPricePerM2(v.result?.avg_price_m2, ppm2BasisFromToken(v.ppm2_basis)),
  },
  { header: 'Stav', cell: (v) => <RunStatusChip status={v.status} /> },
  { header: 'Prostředí', className: `text-[0.65rem] tracking-[0.14em] uppercase ${INK3}`, cell: (v) => ENVIRONMENT[v.environment] },
];

function ValuoEditor({ propertyId, page, ignored, onCancel, onDone }: {
  propertyId: number;
  page: ValuoPage;
  ignored: string[];
  onCancel: () => void;
  onDone: (run: ValuoRun) => void;
}) {
  const run = useRunValuo(propertyId);
  const [edits, setEdits] = useState<ValuoValues>({});
  const [failed, setFailed] = useState<string | null>(null);
  const values = { ...page.defaults, ...edits };
  const missing = missingRequired(page.fields, values);
  const busy = run.isPending;
  const submit = () => {
    setFailed(null);
    // A failed paid call is a stored row too: the form stays open to try again.
    run.mutate(edits, { onSuccess: (res) => (res.valuation.error ? setFailed(res.valuation.error.message) : onDone(res)) });
  };
  const param = (f: ValuoField) => (
    <Param key={f.name} field={f} value={values[f.name] ?? null} ignored={ignored.includes(f.name)}
      provenance={f.name in edits ? { class: 'user', note: null } : page.provenance[f.name]}
      onChange={(v) => setEdits((e) => withEdit(e, page.defaults, f.name, v))} />
  );
  const more = page.fields.filter((f) => f.group === 'more');
  const assumed = more.filter((f) => !(f.name in edits) && page.provenance[f.name]?.class === 'assumed').length;
  const counts = `${more.filter((f) => values[f.name] != null).length} vyplněno · ${assumed} ${czPlural(assumed, 'předpoklad', 'předpoklady', 'předpokladů')}`;
  const aside = (
    <div className="flex flex-wrap items-center justify-end gap-1">
      <button type="button" onClick={() => setEdits({})} disabled={busy || Object.keys(edits).length === 0} className={QUIET}>
        Obnovit výchozí
      </button>
      <button type="button" onClick={onCancel} disabled={busy} className={QUIET}>Zrušit</button>
      <button type="button" onClick={submit} disabled={busy || missing.length > 0}
        className="rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-2.5 py-1 text-[0.8rem] text-white hover:bg-[var(--color-copper-2)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
        Ocenit přes Valuo
      </button>
    </div>
  );
  return (
    <EstimateCard eyebrow="Parametry ocenění" aside={aside}>
      <p className="mt-1 text-sm text-[var(--color-ink-2)]">
        {[page.subject.label, page.subject.location?.label].filter(Boolean).join(' · ')}
      </p>
      <fieldset disabled={busy} className="mt-4 min-w-0 border-0 p-0 m-0 disabled:opacity-60">
        <div className={GRID}>{page.fields.filter((f) => f.group === 'basic').map(param)}</div>
        {more.length > 0 && (
          <div className="mt-5">
            <CollapsibleGroup title={`Další parametry · ${counts}`} active={more.some((f) => f.name in edits)}>
              <div className={GRID}>{more.map(param)}</div>
            </CollapsibleGroup>
          </div>
        )}
      </fieldset>
      {missing.length > 0 && <p className={`mt-3 text-[0.75rem] ${INK3}`}>Chybí povinné parametry: {missing.join(', ')}.</p>}
      {busy && <p className={`mt-3 flex items-center gap-2 text-sm ${INK3}`}><Spinner /> Valuo počítá…</p>}
      {failed != null && <ErrorBanner title="Ocenění selhalo:" message={failed} />}
      {run.error && <ErrorBanner title="Nelze ocenit:" message={run.error.message} />}
    </EstimateCard>
  );
}

/* Where a value came from, after its caption; brick marks one Valuo's answer did not use. */
const TAGS: Record<Provenance['class'], [string, string]> = {
  ad: ['z inzerátu', INK3], property: ['z inzerátu', INK3], inferred: ['odvozeno', INK3],
  assumed: ['předpoklad', 'text-[var(--color-ink-2)]'], missing: ['chybí', INK3], user: ['upraveno', 'text-[var(--color-copper)]'],
};
const BOOL = [{ value: null, label: 'neuvedeno' }, { value: 'ano', label: 'ano' }, { value: 'ne', label: 'ne' }] as const;

function Param({ field: f, value, provenance, ignored, onChange }: {
  field: ValuoField;
  value: ValuoValue;
  provenance: Provenance | undefined;
  ignored: boolean;
  onChange: (v: ValuoValue) => void;
}) {
  const [word, tone] = TAGS[provenance?.class ?? 'missing'];
  const text = value == null ? null : String(value);
  const control =
    f.fixed || f.type === 'text' ? (
      <p className="py-1.5 text-sm text-[var(--color-ink-2)]">{f.type === 'enum' ? optionLabel([f], f.name, value) : (text ?? '—')}</p>
    ) : f.type === 'enum' && f.options.length <= 3 ? (
      <Segmented label={f.label} value={text} onChange={onChange}
        options={f.required ? f.options : [{ value: null, label: NONE }, ...f.options]} />
    ) : f.type === 'enum' ? (
      <SingleSelectDropdown label={f.label} value={text} options={f.options} onChange={onChange} placeholder={NONE} />
    ) : f.type === 'boolean' ? (
      <Segmented label={f.label} options={BOOL} value={value == null ? null : value ? 'ano' : 'ne'}
        onChange={(v) => onChange(v == null ? null : v === 'ano')} />
    ) : f.type === 'date' ? (
      // Never cleared: a request without its date would meet another day's answer in the dedupe.
      <input type="date" aria-label={f.label} value={text ?? ''} onChange={(e) => e.target.value && onChange(e.target.value)}
        className="w-full px-2 py-1.5 text-sm font-mono tabular-nums rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)]" />
    ) : (
      <DecimalField label={f.label} value={typeof value === 'number' ? value : null} onChange={onChange}
        suffix={f.unit ?? undefined} integer={f.type === 'integer'} />
    );
  const tag = (
    <span className="inline-flex items-center gap-1.5 text-[0.58rem] tracking-[0.16em] uppercase">
      <span className={tone}>{word}</span>
      {provenance?.note && <InfoHint text={provenance.note} className="[&>svg]:h-3 [&>svg]:w-3" />}
      {ignored && <span className="text-[var(--color-brick)]">Valuo ignorovalo</span>}
    </span>
  );
  return (
    <Field label={f.label} aside={tag}>
      <div className={value == null && !f.fixed ? ABSENT : undefined}>{control}</div>
    </Field>
  );
}
