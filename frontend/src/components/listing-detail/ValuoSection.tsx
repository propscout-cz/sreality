/* The property page's Valuo section (#valuo): a paid outside valuation, after our estimates and never
 * inside them (rule 12), for every signed-in user (operator ruling 2026-10-19). The card flips into its
 * editor in place (system.md), drawn from the API's `fields` alone, every value tagged with where it came
 * from. The phone reads the latest answer only: no editor, no button (#1760 Q10). */
import { Suspense, useId, useState, type ReactNode } from 'react';

import ErrorBanner from '@/components/ErrorBanner';
import Skeleton from '@/components/Skeleton';
import Spinner from '@/components/Spinner';
import { CollapsibleGroup, DecimalField, Field, Segmented, TextBox } from '@/components/controls';
import EstimateCard, { CardRow, PerMonth } from '@/components/estimation/EstimateCard';
import RangeStrip from '@/components/estimation/RangeStrip';
import RunHistory from '@/components/estimation/RunHistory';
import { RunStatusChip } from '@/components/estimation/RunPanel';
import { SingleSelectDropdown } from '@/components/filter-controls/SingleSelectDropdown';
import { InfoHint } from '@/components/settings/SectionChrome';
import { Hairline, SectionLabel } from '@/components/section';
import { Th, cellClass, type Column } from '@/components/table';
import { ApiError } from '@/lib/api';
import {
  czPlural, fmtArea, fmtCount, fmtCzk, fmtDateSlash, fmtDistanceM, fmtMeasuredPricePerM2, fmtPct, fmtTime24, fmtTomDays,
} from '@/lib/format';
import { lazyChunk } from '@/lib/lazyChunk';
import { ppm2BasisFromToken } from '@/lib/measure';
import { mfShape } from '@/lib/mfReference';
import { useEstimationRuns, type PropertyPublic } from '@/lib/queries';
import { bareImage, type EstimationRun } from '@/lib/types';
import {
  BAND_FROM, MIN_KEPT, band, formErrors, isOwnAd, missingRequired, optionLabel, recalc, recalcBase, reliability, shownPerM2,
  shownPrice, useNewValuation, useValuoPage, withEdit,
  type NewValuation, type Provenance, type RecalcBase, type Valuation, type ValuoField, type ValuoIssue, type ValuoPage, type ValuoRecord,
  type ValuoValue, type ValuoValues,
} from '@/lib/valuo';

const ImageLightbox = lazyChunk(() => import('@/components/ImageLightbox'));

const NONE = 'Nevyplněno';
const INK3 = 'text-[var(--color-ink-3)]';
const QUIET = 'px-2 py-1 text-[0.8rem] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] disabled:opacity-50';
const LINK = 'text-[0.8rem] text-[var(--color-copper)] hover:underline underline-offset-2';
/* A field = a caption track over a control track (subgrid): a wrapped caption never lowers its control. pb-4 spaces the
 * rows, -mb-4 takes the last back; a lone field keeps one track, the place (free text) takes two (coordinates fit). */
const GRID = 'grid gap-x-4 -mb-4 [grid-template-columns:repeat(auto-fill,minmax(11rem,1fr))]';
const CELL = 'row-span-2 grid grid-cols-1 grid-rows-subgrid pb-4 [&>:first-child]:self-end';
/* Valuo's average, rounded as shown, the exact figure in the title; a lease is monthly (production call 2026-10-21). */
const Price = ({ value, lease, className }: { value: number | null; lease: boolean; className?: string }) => (
  <span className={className} title={value == null ? undefined : `${fmtCzk(value)}${lease ? ' /měs' : ''}`}>
    {fmtCzk(value == null ? null : shownPrice(value, lease))}{lease && <PerMonth />}
  </span>
);
const isLease = (v: Valuation) => ppm2BasisFromToken(v.ppm2_basis) === 'rent';
/* Its per-m² average the same way, naming its own unit (rule 23): a sale row and a lease row sit 300x apart. */
const PerM2 = ({ v, className }: { v: Valuation; className?: string }) => {
  const n = v.result?.avg_price_m2 ?? null;
  const fmt = (x: number | null) => fmtMeasuredPricePerM2(x, ppm2BasisFromToken(v.ppm2_basis));
  return <span className={className} title={n == null ? undefined : fmt(n)}>{fmt(n == null ? null : shownPerM2(n, isLease(v)))}</span>;
};
const ENVIRONMENT: Record<Valuation['environment'], string> = { sandbox: 'sandbox', production: 'ostré' };
const BRICK = 'text-[0.75rem] text-[var(--color-brick)]';

export default function ValuoSection({ property, listingIds, adUrls = [], readOnly = false, bare = false }: {
  property: PropertyPublic;
  listingIds: number[];
  adUrls?: readonly (string | null)[]; // every ad of the property, delisted ones too: Valuo's own-ad match runs over them
  readOnly?: boolean;
  bare?: boolean;
}) {
  const pageQ = useValuoPage(property.property_id);
  const runsQ = useEstimationRuns(listingIds); // the estimations block's own query: one fetch for both
  const [pickedId, setPickedId] = useState<number | null>(null);
  const [editing, setEditing] = useState<boolean | null>(null);
  const [reusedAt, setReusedAt] = useState<string | null>(null);
  const page = pageQ.data ?? null;
  const history = page?.history ?? [];
  if (readOnly && !history.some((v) => v.result)) return null;

  const shown = readOnly
    ? (history.find((v) => v.result) ?? null)
    : (history.find((v) => v.id === pickedId) ?? history[0] ?? null);
  const canValue = !readOnly && page != null && page.refusal == null;
  const open = canValue && (editing ?? (history.length === 0 && page.blocking.length > 0));
  const pick = (id: number, reused: string | null = null) => {
    setPickedId(id);
    setEditing(false);
    setReusedAt(reused);
  };
  const done = ({ reused, valuation: v }: NewValuation) => pick(v.id, reused ? v.created_at : null);

  let body: ReactNode = null;
  const err = pageQ.error;
  if (pageQ.isLoading) body = <Skeleton height={140} />;
  // A 4xx (no account, no such property) answers the same on every retry: a refusal sentence, not an error.
  else if (err instanceof ApiError && err.kind === 'http' && err.status < 500) body = <p className={`text-sm ${INK3}`}>{err.message}</p>;
  else if (err) body = <ErrorBanner title="Ocenění se nepodařilo načíst:" message={err.message} onRetry={() => void pageQ.refetch()} />;
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
          <ValuoEditor propertyId={property.property_id} page={page} refused={formErrors(shown)}
            onCancel={() => setEditing(false)} onDone={done} />
        ) : shown ? (
          <>
            <ValuoCard valuation={shown} page={page} property={property} runs={runsQ.data?.data ?? []}
              onEdit={canValue ? () => setEditing(true) : undefined} />
            {!readOnly && <Comparables key={shown.id} valuation={shown} fields={page.fields} adUrls={adUrls} />}
          </>
        ) : !page.refusal && (
          <EstimateCard tone="absent" eyebrow="Ocenění Valuo" className="flex flex-col items-start gap-2">
            <p className={`text-sm ${INK3}`}>Zatím bez ocenění</p>
            {canValue && <button type="button" onClick={() => setEditing(true)} className={LINK}>Ocenit přes Valuo →</button>}
          </EstimateCard>
        )}
      </div>
    );
  }

  return (
    <>
      {!bare && <Hairline />}
      <section id="valuo" className="scroll-mt-28">
        <div className="flex items-baseline justify-between gap-3">
          <SectionLabel>Valuo</SectionLabel>
          {(open || !shown ? page?.environment : shown.environment) === 'sandbox' && (
            <span className={`px-1.5 border border-[var(--color-rule)] rounded-[var(--radius-xs)] text-[0.58rem] tracking-[0.18em] uppercase ${INK3}`}>
              Sandbox
            </span>
          )}
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
        {v.error ? <Failure valuation={v} /> : <p className={`mt-2 flex items-center gap-2 text-sm ${INK3}`}><Spinner /> Valuo počítá…</p>}
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
  // Against the exact average, never the rounded one on screen.
  const delta = ask != null && r.avg_price ? ((ask - r.avg_price) / r.avg_price) * 100 : null;
  const range = band(v, 'range_price');
  const rangeM2 = band(v, 'range_price_m2');
  const flags = Array.isArray(r.flags) ? r.flags.map((f) => (typeof f === 'string' ? f : JSON.stringify(f))).join(', ') : null;
  const m2 = ([low, high]: number[]) => `${fmtCount(low)}–${perM2(high)}`; // the unit once, after the high end
  const rows: [string, ReactNode][] = [
    ['Za m² (pásmo)', rangeM2 && <span title={m2(rangeM2.map(Math.round))}>{m2(rangeM2.map((n) => shownPerM2(n, lease)))}</span>],
    ['Směr. odchylka Valuo', r.std_price_m2 != null ? `± ${perM2(r.std_price_m2)}` : null],
    ['Plocha výpočtu', r.calc_area != null ? fmtArea(r.calc_area) : null],
    ['Ke dni', typeof v.request.as_of === 'string' ? fmtDateSlash(v.request.as_of) : null],
  ];
  // An empty list is an answer (Valuo raised none), so no row; only a missing one is the dashed gap.
  if (flags !== '') rows.push(['Příznaky', flags]);
  // Said only when there is something to say: what the point sent stands for (the place the user typed, as typed).
  const place = v.provenance.place?.class === 'user' ? v.request.place : v.provenance.place?.note;
  const reach = v.summary ? reliability(v.summary) : []; // rows from before v3 carry no summary
  return (
    <EstimateCard eyebrow={eyebrow} aside={aside}>
      <div className="mt-1 flex items-baseline justify-between gap-3">
        <Price value={r.avg_price} lease={lease} className="text-lg font-medium font-mono tabular-nums" />
        <PerM2 v={v} className={`text-[0.72rem] font-mono tabular-nums ${INK3}`} />
      </div>
      {reach.length > 0 && <p className="mt-0.5 text-[0.72rem] font-mono tabular-nums text-[var(--color-ink-2)]">{reach.join('\u00a0· ')}</p>}
      {v.summary?.not_enough && (
        // Ochre lives in the dot only: ochre text fails contrast on paper (system.md).
        <p className="mt-1 flex items-center gap-1.5 text-[0.72rem] text-[var(--color-ink-2)]">
          <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-ochre)]" />
          Valuo nenašlo dost srovnatelných nemovitostí ani v maximálním okruhu.
        </p>
      )}
      {range && r.avg_price != null && (
        <div className="mt-3">
          <RangeStrip range={{ low: shownPrice(range[0], lease), mid: r.avg_price, high: shownPrice(range[1], lease) }}
            caption="pásmo spolehlivosti (Valuo)" format={fmtCzk} marks={marks} />
        </div>
      )}
      {delta != null && (
        <p className="mt-2 text-[0.8rem] text-[var(--color-ink-2)]">
          {`Nabídka je o ${fmtPct(Math.abs(delta))} ${delta >= 0 ? 'nad' : 'pod'} průměrem Valuo.`}
        </p>
      )}
      <dl className="mt-2 space-y-0.5 text-[0.72rem] font-mono tabular-nums">
        {rows.map(([label, value]) => (
          <CardRow key={label} label={label}>
            {value ?? <span className={`px-1.5 border border-dashed border-[var(--color-rule-strong)] rounded-[var(--radius-xs)] ${INK3}`}>—</span>}
          </CardRow>
        ))}
        {typeof place === 'string' && place !== '' && <CardRow label="Poloha">{place}</CardRow>}
      </dl>
    </EstimateCard>
  );
}

const HISTORY_COLUMNS: Column<Valuation>[] = [
  { header: 'Druh', className: `text-[0.8rem] ${INK3}`, cell: (v) => v.kind_label ?? '—' },
  {
    header: 'Průměr', align: 'right', className: 'font-mono tabular-nums text-[var(--color-ink)]',
    cell: (v) => <Price value={v.result?.avg_price ?? null} lease={v.result != null && isLease(v)} />,
  },
  { header: 'Za m²', align: 'right', className: 'font-mono tabular-nums text-[var(--color-ink-2)]', cell: (v) => <PerM2 v={v} /> },
  { header: 'Stav', cell: (v) => <RunStatusChip status={v.status} /> },
  { header: 'Prostředí', className: `text-[0.65rem] tracking-[0.14em] uppercase ${INK3}`, cell: (v) => ENVIRONMENT[v.environment] },
];

/* Only a web address becomes a link: the URL is Valuo's, not ours. */
const webUrl = (u: string | null | undefined) => (u && /^https?:\/\//i.test(u) ? u : null);
const NO_PHOTO = 'block h-[3.375rem] w-[4.5rem] rounded-[var(--radius-xs)] border border-dashed border-[var(--color-rule-strong)]';
const NUM = 'font-mono tabular-nums whitespace-nowrap';
/* A shade on the box's right edge while columns sit past it (below ~1024 px): the paper cover rides with the content
 * (local) and hides the edge's shade (scroll) once the last column is in view, or when nothing overflows. */
const SCROLL_HINT = '[background:linear-gradient(var(--color-paper),var(--color-paper))_right/1.5rem_100%_no-repeat_local,linear-gradient(to_left,var(--color-rule-strong),transparent)_right/1rem_100%_no-repeat_scroll]';
const TAG = `block font-sans text-[0.58rem] tracking-[0.16em] uppercase ${INK3}`;
const SOLD = <span title="Realizovaná prodejní cena (ČÚZK / realitní kanceláře)" className={TAG}>prodáno</span>;
const OWN = <span title="Inzerát této nemovitosti: Valuo ho započítalo mezi srovnání" className={TAG}>tento inzerát</span>;

/* Our figure over the comparables left ticked, beside Valuo's card and labelled ours. It keeps one height in every state
 * (a sentence, a figure, a figure with its band) so the table below never jumps under the cursor. */
function Recalculation({ valuation: v, base, kept, count, onReset }: {
  valuation: Valuation; base: RecalcBase; kept: readonly ValuoRecord[]; count: number; onReset: (() => void) | undefined;
}) {
  const basis = ppm2BasisFromToken(v.ppm2_basis);
  const lease = isLease(v);
  const mine = kept.length >= MIN_KEPT ? recalc(kept) : null;
  const delta = mine ? (mine.avg / base.ref - 1) * 100 : 0;
  const [low, high] = mine ? [mine.avg - mine.std, mine.avg + mine.std] : [0, 0];
  const shown = (perM2: number) => shownPrice(Math.round(perM2 * base.area), lease);
  return (
    <EstimateCard tone="ours" eyebrow={<>Náš přepočet · <span className="font-mono tabular-nums tracking-normal">{kept.length} z {count}</span> srovnání</>} className="mb-4 min-h-[8.5rem]"
      aside={<button type="button" onClick={onReset} disabled={!onReset} className={QUIET}>Vrátit výchozí</button>}>
      {mine == null ? (
        <p className={`mt-2 text-sm ${INK3}`}>Pro přepočet ponechte alespoň <span className="font-mono tabular-nums">{MIN_KEPT}</span> srovnání</p>
      ) : (
        <div aria-live="polite">
          <div className="mt-1 flex items-baseline justify-between gap-3">
            <Price value={Math.round(mine.avg * base.area)} lease={lease} className="text-lg font-medium font-mono tabular-nums" />
            <span title={fmtMeasuredPricePerM2(mine.avg, basis)} className={`text-[0.72rem] font-mono tabular-nums ${INK3}`}>
              {fmtMeasuredPricePerM2(shownPerM2(mine.avg, lease), basis)}
            </span>
          </div>
          <p className="mt-0.5 text-[0.8rem] text-[var(--color-ink-2)]">
            {Math.abs(delta) < 0.05 ? 'stejně jako Valuo' : `o ${fmtPct(Math.abs(delta))} ${delta > 0 ? 'nad' : 'pod'} Valuo`}
            {kept.length < BAND_FROM && <span title="Z méně než pěti srovnání, bez pásma" className={`ml-2 text-[0.58rem] tracking-[0.16em] uppercase ${INK3}`}>orientační</span>}
          </p>
          {kept.length >= BAND_FROM && (
            <dl className="mt-1 text-[0.72rem] font-mono tabular-nums">
              <CardRow label="pásmo">
                <span title={`Průměr ± jedna směrodatná odchylka srovnání; za m²: ${fmtCount(low)}–${fmtMeasuredPricePerM2(high, basis)}`}>
                  {fmtCount(shown(low))}–{fmtCzk(shown(high))}{lease && <PerMonth />}
                </span>
              </CardRow>
            </dl>
          )}
        </div>
      )}
    </EstimateCard>
  );
}

/* Valuo's comparables under its card, in Valuo's order; every per-m² figure names its own unit (rule 23). Fits a 1024 px
 * page without a sideways scroll, so a tag sits under its figure, never beside it; the phone card leaves them out. While
 * our recalculation is offered each row has a tick: page state only, forgotten by a reload or another history row. */
function Comparables({ valuation: v, fields, adUrls }: {
  valuation: Valuation; fields: readonly ValuoField[]; adUrls: readonly (string | null)[];
}) {
  const [opened, setOpened] = useState<ValuoRecord | null>(null);
  const [broken, setBroken] = useState<ReadonlySet<number>>(() => new Set());
  const [dropped, setDropped] = useState<ReadonlySet<number>>(() => new Set());
  const records = v.records ?? [];
  if (records.length === 0) return null;
  const base = recalcBase(v);
  const basis = ppm2BasisFromToken(v.ppm2_basis);
  const lease = basis === 'rent';
  const czk = (n: number | null) => <>{fmtCzk(n)}{lease && n != null && <PerMonth />}</>;
  const toggle = (i: number) => setDropped((d) => { const next = new Set(d); if (!next.delete(i)) next.add(i); return next; });
  // The advert detail's shape is unseen in production yet: only a string reads as an address.
  const address = (r: ValuoRecord) => (typeof r.address === 'string' && r.address !== '' ? r.address : r.place);
  const photo = (r: ValuoRecord, i: number) => (r.photos?.[0] && !broken.has(i) ? (
    <button type="button" onClick={() => setOpened(r)}
      aria-label={`Fotografie (${r.photos?.length ?? 0}): ${address(r) ?? 'srovnání'}`}
      className="block h-[3.375rem] w-[4.5rem] overflow-hidden rounded-[var(--radius-xs)] bg-[var(--color-inset)]">
      <img src={r.photos[0].thumb} alt={address(r) ?? 'Fotka srovnání'} loading="lazy" decoding="async"
        onError={() => setBroken((b) => new Set(b).add(i))} className="block h-full w-full object-cover" />
    </button>
  ) : <span aria-hidden className={NO_PHOTO} />);
  const columns: Column<ValuoRecord>[] = [
    { header: 'Foto', cell: (r, i) => (base == null ? photo(r, i) : (
      <div className="flex items-center gap-2">
        <input type="checkbox" checked={!dropped.has(i)} onChange={() => toggle(i)}
          aria-label={`Zahrnout do přepočtu: ${address(r) ?? 'srovnání'}`} className="h-4 w-4 shrink-0 accent-[var(--color-copper)]" />
        {photo(r, i)}
      </div>
    )) },
    { header: 'Adresa', className: 'text-[var(--color-ink)]', cell: (r) => {
      const href = webUrl(r.advert_url);
      const text = address(r) ?? (href ? 'Inzerát' : '—');
      const link = href ? <a href={href} target="_blank" rel="noopener noreferrer" className="hover:underline underline-offset-2">{text}</a> : text;
      return <><span title={text} className="w-[12rem] line-clamp-2">{link}</span>{base && isOwnAd(r, adUrls) && OWN}</>;
    } },
    { header: 'Velikost', className: 'whitespace-nowrap font-mono tabular-nums text-[var(--color-ink-2)]', cell: (r) => (
      // 4ch holds every layout Valuo names ("2+kk", "Jiná"), so the area starts at one x down the column.
      <>{r.local_type != null && <><span className="inline-block min-w-[5ch]">{optionLabel(fields, 'local_type', r.local_type)}</span></>}{fmtArea(r.calc_area)}</>
    ) },
    { header: 'Cena', align: 'right', className: `${NUM} text-[var(--color-ink)]`, cell: (r) => (
      <>{czk(r.price)}<span className={`block text-[0.7rem] ${INK3}`}>{fmtMeasuredPricePerM2(r.price_m2, basis)}</span>{r.sale_price && SOLD}</>
    ) },
    { header: 'Upravená', align: 'right', className: `${NUM} text-[var(--color-ink)]`, cell: (r) => (
      <>{czk(r.adjusted_price)}{base && <span className={`block text-[0.7rem] ${INK3}`}>{fmtMeasuredPricePerM2(r.adjusted_price_m2, basis)}</span>}</>
    ) },
    { header: 'Vzdálenost', align: 'right', className: `${NUM} text-[var(--color-ink-2)]`, cell: (r) => fmtDistanceM(r.distance) },
    { header: 'Shoda', align: 'right', className: `${NUM} text-[var(--color-ink-2)]`, cell: (r) => fmtPct(r.score_prct, { digits: 0 }) },
    { header: 'V\u00a0inzerci', align: 'right', className: `${NUM} text-[var(--color-ink-2)]`, cell: (r) => fmtTomDays(r.days) },
  ];
  return (
    <div className="pt-3">
      {base && <Recalculation valuation={v} base={base} kept={records.filter((_, i) => !dropped.has(i))} count={records.length}
        onReset={dropped.size > 0 ? () => setDropped(new Set()) : undefined} />}
      <SectionLabel>
        Srovnávací nemovitosti · <span className="font-mono tabular-nums tracking-normal">{records.length}</span>
      </SectionLabel>
      {base && <p className={`mt-1 text-[0.8rem] ${INK3}`}>Odškrtněte srovnání a přepočítáme odhad z ostatních.</p>}
      <div className={`mt-3 min-w-0 overflow-x-auto rounded-[var(--radius-md)] border border-[var(--color-rule)] ${SCROLL_HINT}`}>
        <table aria-label="Srovnávací nemovitosti Valuo" className="w-full text-sm">
          <thead className="bg-[var(--color-paper-2)] border-b border-[var(--color-rule)]">
            <tr>{columns.map((c) => <Th key={c.header} size="xs" align={c.align}>{c.header}</Th>)}</tr>
          </thead>
          <tbody>
            {records.map((r, i) => (
              <tr key={`${r.id}:${i}`} className="border-b border-[var(--color-rule-soft)] last:border-b-0">
                {columns.map((c) => <td key={c.header} className={cellClass(c, 'px-3 py-2 align-top')}>{c.cell(r, i)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {opened?.photos && opened.photos.length > 0 && (
        <Suspense fallback={null}>
          {/* The viewer has no caption prop: its under-photo bar says which comparable this is. */}
          <ImageLightbox images={opened.photos.map((p, i) => bareImage(i, null, p.full))} startIndex={0} onClose={() => setOpened(null)}
            actionsAt={() => (<p className="text-center"><span className="inline-block px-2 py-0.5 rounded-[var(--radius-xs)] bg-[var(--color-paper)] text-sm text-[var(--color-ink)]">
              {address(opened) ?? 'Srovnání'} · <span className="font-mono tabular-nums">{czk(opened.price)}</span></span></p>)} />
        </Suspense>
      )}
    </div>
  );
}

function ValuoEditor({ propertyId, page, refused, onCancel, onDone }: {
  propertyId: number;
  page: ValuoPage;
  refused: ValuoIssue[]; // what Valuo refused on the row on screen, until this form's own answer replaces it
  onCancel: () => void;
  onDone: (answer: NewValuation) => void;
}) {
  const valuate = useNewValuation(propertyId);
  const [edits, setEdits] = useState<ValuoValues>({});
  const [failed, setFailed] = useState<Valuation | null>(null);
  const values = { ...page.defaults, ...edits };
  const missing = missingRequired(page.fields, values);
  const busy = valuate.isPending;
  const submit = () => {
    setFailed(null);
    // A failed paid call is a stored row too: the form stays open to try again.
    valuate.mutate(edits, { onSuccess: (res) => (res.valuation.error ? setFailed(res.valuation) : onDone(res)) });
  };
  const issues = failed ? formErrors(failed) : refused;
  const said = (name: string) => issues.filter((i) => i.field === name).map((i) => i.message);
  // An issue naming no parameter on this form still reads, under the banner.
  const loose = issues.filter((i) => i.field == null || !page.fields.some((f) => f.name === i.field));
  const param = (f: ValuoField) => (
    <Param key={f.name} field={f} value={values[f.name] ?? null} issues={said(f.name)}
      provenance={f.name in edits ? { class: 'user', note: null } : page.provenance[f.name]}
      onChange={(v) => setEdits((e) => withEdit(e, page.defaults, f.name, v))} />
  );
  const more = page.fields.filter((f) => f.group === 'more');
  const assumed = more.filter((f) => !(f.name in edits) && page.provenance[f.name]?.class === 'assumed').length;
  // A refused parameter inside the folded group is counted on its band, so folding never hides it.
  const wrong = more.filter((f) => said(f.name).length > 0).length;
  const counts = [
    `${more.filter((f) => values[f.name] != null).length} vyplněno`,
    `${assumed} ${czPlural(assumed, 'předpoklad', 'předpoklady', 'předpokladů')}`,
    wrong > 0 && `${wrong} ${czPlural(wrong, 'chyba', 'chyby', 'chyb')}`,
  ].filter(Boolean).join(' · ');
  const aside = (
    <div className="flex flex-wrap items-center justify-end gap-1">
      <button type="button" onClick={() => setEdits({})} disabled={busy || Object.keys(edits).length === 0} className={QUIET}>
        Obnovit výchozí
      </button>
      <button type="button" onClick={onCancel} disabled={busy} className={QUIET}>Zrušit</button>
      <button type="button" onClick={submit} disabled={busy || missing.length > 0}
        className="rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-2.5 py-1 text-[0.8rem] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
        Ocenit přes Valuo
      </button>
    </div>
  );
  return (
    <EstimateCard eyebrow="Parametry ocenění" aside={aside}>
      <p className="mt-1 text-sm text-[var(--color-ink-2)]">
        {[page.subject.label, page.subject.location?.label].filter(Boolean).join(' · ')}
      </p>
      {missing.length > 0 && <p className={`mt-3 text-[0.75rem] ${INK3}`}>Chybí povinné parametry: {missing.join(', ')}.</p>}
      {busy && <p className={`mt-3 flex items-center gap-2 text-sm ${INK3}`}><Spinner /> Valuo počítá…</p>}
      {failed && <Failure valuation={failed} />}
      {loose.map((i, k) => <p key={k} className={`mt-1.5 ${BRICK}`}>{i.field ? `${i.field}: ${i.message}` : i.message}</p>)}
      {valuate.error && <ErrorBanner title="Nelze ocenit:" message={valuate.error.message} />}
      <fieldset disabled={busy} className="mt-4 min-w-0 border-0 p-0 m-0 disabled:opacity-60">
        <div className={GRID}>{page.fields.filter((f) => f.group === 'basic').map(param)}</div>
        {more.length > 0 && (
          <div className="mt-5">
            <CollapsibleGroup title={`Další parametry · ${counts}`} active={more.some((f) => f.name in edits)} defaultOpen={wrong > 0}>
              <div className={GRID}>{more.map(param)}</div>
            </CollapsibleGroup>
          </div>
        )}
      </fieldset>
    </EstimateCard>
  );
}

/* A failed row: the API's Czech sentence once, no prefix (each says what failed); for an engine refusal, Valuo's own
 * words (the API appends them after ": ") as Valuo's, and the place that was sent. */
function Failure({ valuation: { error: e, request } }: { valuation: Valuation }) {
  const engine = e?.code === 'calculation';
  const at = engine ? e.message.indexOf(': ') : -1;
  const note = 'mt-1 text-[0.75rem] text-[var(--color-ink-2)]';
  return e && (
    <>
      <ErrorBanner title={null} message={at < 0 ? e.message : `${e.message.slice(0, at)}.`} />
      {at >= 0 && <p className={note}>Valuo: „{e.message.slice(at + 2)}“</p>}
      {engine && typeof request.place === 'string' && (
        <p className={note}>Odeslaná poloha: <span className="font-mono tabular-nums">{request.place}</span></p>)}
    </>
  );
}

/* Where a value came from, after its caption. */
const TAGS: Record<Provenance['class'], [string, string]> = {
  ad: ['z inzerátu', INK3], property: ['z inzerátu', INK3], inferred: ['odvozeno', INK3],
  assumed: ['předpoklad', 'text-[var(--color-ink-2)]'], missing: ['chybí', INK3], user: ['upraveno', 'text-[var(--color-copper)]'],
};
const BOOL = [{ value: null, label: 'neuvedeno' }, { value: 'ano', label: 'ano' }, { value: 'ne', label: 'ne' }] as const;

function Param({ field: f, value, provenance, issues, onChange }: {
  field: ValuoField;
  value: ValuoValue;
  provenance: Provenance | undefined;
  issues: string[];
  onChange: (v: ValuoValue) => void;
}) {
  const [word, tone] = TAGS[provenance?.class ?? 'missing'];
  const text = value == null ? null : String(value);
  const issuesId = useId();
  const invalid = issues.length > 0 ? { 'aria-invalid': true, 'aria-describedby': issuesId } : {};
  const box = value == null ? 'absent' : 'present';
  const control =
    f.fixed ? (
      <p className="py-1.5 text-sm text-[var(--color-ink-2)]">{optionLabel([f], f.name, value)}</p>
    ) : f.type === 'text' ? (
      // An empty box is no value: a required one then holds the call.
      <TextBox type="text" box={box} label={f.label} value={text ?? ''} maxLength={f.max_length ?? undefined} {...invalid}
        onChange={(e) => onChange(e.target.value.trim() === '' ? null : e.target.value)} />
    ) : f.type === 'enum' && f.options.length <= 3 ? (
      <Segmented label={f.label} value={text} onChange={onChange} aria={invalid} box={box}
        options={f.required ? f.options : [{ value: null, label: NONE }, ...f.options]} />
    ) : f.type === 'enum' ? (
      <SingleSelectDropdown label={f.label} value={text} options={f.options} onChange={onChange} placeholder={NONE} aria={invalid} box={box} />
    ) : f.type === 'boolean' ? (
      <Segmented label={f.label} options={BOOL} value={value == null ? null : value ? 'ano' : 'ne'} aria={invalid} box={box}
        onChange={(v) => onChange(v == null ? null : v === 'ano')} />
    ) : f.type === 'date' ? (
      // Never cleared: a request without its date would meet another day's answer in the dedupe.
      <TextBox type="date" box={box} label={f.label} value={text ?? ''} {...invalid}
        onChange={(e) => e.target.value && onChange(e.target.value)} />
    ) : (
      <DecimalField label={f.label} value={typeof value === 'number' ? value : null} onChange={onChange}
        suffix={f.unit ?? undefined} integer={f.type === 'integer'} aria={invalid} box={box} />
    );
  return (
    <Field label={f.label} className={f.type === 'text' ? `${CELL} col-span-2` : CELL} aside={
      <span className="inline-flex items-center gap-1.5 text-[0.58rem] tracking-[0.16em] uppercase">
        <span className={tone}>{word}</span>
        {provenance?.note && <InfoHint text={provenance.note} className="[&>svg]:h-3 [&>svg]:w-3" />}
      </span>
    }>
      {control}
      {issues.length > 0 && <div id={issuesId}>{issues.map((m, k) => <p key={k} className={`mt-1 ${BRICK}`}>Valuo: „{m}“</p>)}</div>}
    </Field>
  );
}
