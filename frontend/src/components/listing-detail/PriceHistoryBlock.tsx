/* The property page's price history: the property's span (first / last seen,
 * days on market), the canonical advert's lineage of price moves, every advert's
 * own line on the chart and each dated move. Shared by the desktop page and the
 * phone layout (#1767), which passes its own label. */
import { Suspense, useMemo, useState } from 'react';

import ErrorBoundary from '@/components/ErrorBoundary';
import Panel from '@/components/Panel';
import { SectionLabel } from '@/components/section';
import { timeLabelFull } from '@/lib/chartAxis';
import { fmtAbsolute, fmtCzk, fmtPct, fmtShortDate } from '@/lib/format';
import { lazyChunk } from '@/lib/lazyChunk';
import { buildPriceSeries, priceChangeEvents, type PriceAdvert } from '@/lib/priceHistory';
import type { PropertyPublic } from '@/lib/queries';
import type { ListingSnapshotPublic } from '@/lib/types';

const PriceLineChart = lazyChunk(
  () => import('@/components/listing-detail/PriceLineChart'),
);

/* One rule per field (decision 18): the span (first / last seen, days on market)
   is the property's, any advert active; the price-change tiles are the canonical
   advert's lineage (MS10: its same-portal predecessors, one step per handover),
   the same `price_change_count` / `total_price_change_pct` Browse filters on. The
   chart and the dated moves are every advert's own (MS9, MS10); a step drawn
   there never spans two adverts. */
export default function PriceHistoryBlock({
  property,
  adverts,
  snapshots,
  label = 'Price history',
}: {
  property: PropertyPublic;
  adverts: PriceAdvert[];
  snapshots: ListingSnapshotPublic[];
  label?: string;
}) {
  // Date.now() is captured once at mount (not per render) and threaded into the
  // pure helpers so they stay deterministic. A per-render `now` gave `series` a
  // new reference on every staggered query resolution, re-rendering
  // PriceLineChart mid-measure and tripping recharts' #310 crash.
  const [now] = useState(() => Date.now());
  const series = useMemo(
    () => buildPriceSeries(adverts, snapshots, now),
    [adverts, snapshots, now],
  );
  // Dated price moves, from the same series the chart draws: the exact day and
  // size of each step (readable even if the chart itself fails to render).
  const changes = useMemo(() => priceChangeEvents(series), [series]);

  /* A section card (C1–C10); the span's labels are L4 (C8). */
  return (
    <Panel title={label}>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
        <Stat
          label="First seen"
          value={fmtShortDate(property.first_seen_at)}
          title={fmtAbsolute(property.first_seen_at)}
        />
        <Stat
          label="Last seen"
          value={property.is_active ? 'now' : fmtShortDate(property.last_seen_at)}
          title={fmtAbsolute(property.last_seen_at)}
        />
        <Stat label="Days on market" value={String(property.tom_days ?? '—')} mono />
        <Stat
          label="Price changes"
          value={String(property.price_change_count ?? 0)}
          title="Změny ceny hlavního inzerátu a jeho předchůdců na stejném portálu, včetně kroku při každém novém vložení; seznam pod grafem ukazuje změny všech inzerátů"
          mono
        />
        <Stat
          label="Price change"
          value={fmtPct(property.total_price_change_pct, { signed: true })}
          mono
          pct={property.total_price_change_pct}
        />
      </div>

      {series.length > 0 && (
        <div className="pt-1">
          <ErrorBoundary
            label="price-chart"
            fallback={
              <div className="flex h-[230px] items-center justify-center rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule)] text-sm text-[var(--color-ink-3)]">
                Price chart unavailable
              </div>
            }
          >
            <Suspense
              fallback={
                <div className="h-[230px] rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)]" />
              }
            >
              <PriceLineChart series={series} />
            </Suspense>
          </ErrorBoundary>
        </div>
      )}

      {changes.length > 0 && (
        <ul aria-label="Price moves" className="flex flex-wrap gap-x-5 gap-y-1.5">
          {changes.map((c) => (
            <li
              key={`${c.seriesId}-${c.t}`}
              /* Wraps between its parts, never inside one, so a phone-width
                 column keeps each date and price on one line. */
              className="flex flex-wrap items-center gap-x-2 gap-y-0.5 whitespace-nowrap text-[0.78rem] tabular-nums"
            >
              <span className="text-[var(--color-ink-2)]">{c.label}</span>
              <span className="font-mono text-[var(--color-ink-3)]">
                {timeLabelFull(c.t, 'day')}
              </span>
              <span className="font-mono text-[var(--color-ink-3)]">{fmtCzk(c.from)}</span>
              <span className="text-[var(--color-ink-4)]">→</span>
              <span className="font-mono text-[var(--color-ink)]">{fmtCzk(c.to)}</span>
              <span
                className="font-mono"
                style={{ color: c.pct > 0 ? 'var(--color-brick)' : 'var(--color-sage)' }}
              >
                {fmtPct(c.pct, { signed: true })}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Stat({
  label,
  value,
  title,
  mono,
  pct,
}: {
  label: string;
  value: string;
  title?: string;
  mono?: boolean;
  pct?: number | null;
}) {
  const color =
    pct == null || pct === 0
      ? undefined
      : pct > 0
        ? 'var(--color-brick)'
        : 'var(--color-sage)';
  return (
    <div>
      <SectionLabel>{label}</SectionLabel>
      <p
        className={['mt-1 text-sm text-[var(--color-ink)]', mono ? 'font-mono tabular-nums' : ''].join(' ')}
        title={title}
        style={color ? { color } : undefined}
      >
        {value}
      </p>
    </div>
  );
}
