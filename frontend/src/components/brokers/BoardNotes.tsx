/* The sentences a broker board carries around its rows (docs/design/broker-board.md):
 * what it leaves out, how many properties stand behind it, the market every
 * speed is read against, why speed is refused, and what speed means. Browse's
 * Makléři tab and the phone's Makléři screen draw these same ones, each in its
 * own type size (`className`). */

import { filterLabel } from '@/components/phone/filterRows';
import { boardIgnores, entryWindow, type BoardMeta } from '@/lib/brokerBoard';
import { CHART_TZ } from '@/lib/chartAxis';
import type { ListingFilters } from '@/lib/filters';
import { czPlural, fmtCount, fmtPct } from '@/lib/format';
import { portalLabel } from '@/lib/portals';

export const sharePct = (share: number): string => fmtPct(share * 100, { digits: 0 });

const Num = ({ n }: { n: number }) => <span className="font-mono tabular-nums">{fmtCount(n)}</span>;
const csDate = (d: Date) => d.toLocaleDateString('cs-CZ', { timeZone: CHART_TZ });
/* "Bazoš", "Bazoš a Maxima", "Bazoš, Bezrealitky a Maxima". */
const csList = (xs: string[]) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} a ${xs[xs.length - 1]}`);

/* The set lenses the board cannot judge (lib/brokerBoard boardIgnores); nothing when none is set. */
export function IgnoredNote({ filters, className }: { filters: ListingFilters; className?: string }) {
  const ignored = boardIgnores(filters);
  if (ignored.length === 0) return null;
  return <p className={className}>Žebříček makléřů nebere v úvahu: {ignored.join(', ')}.</p>;
}

/* Who stands behind the rows: the cohort, the share of it no broker is known
 * for, and the portals in it that publish no broker. Dismissed properties are
 * counted (a market read), which Browse hides unless asked. */
export function CoverageLine({ meta, filters, className }: { meta: BoardMeta; filters: ListingFilters; className?: string }) {
  const uncovered = meta.uncovered_portals.map((p) => portalLabel(p) ?? p);
  const cohort = meta.cohort_properties;
  return (
    <p className={className}>
      Ve výběru <Num n={cohort} /> {czPlural(cohort, 'nemovitost', 'nemovitosti', 'nemovitostí')}
      {filters.showDismissed ? '' : ' včetně skrytých'}, makléře známe u <Num n={meta.attributed_properties} />
      {meta.unattributed_share != null && meta.unattributed_share > 0 && (
        <>
          {' '}(bez makléře <span className="font-mono tabular-nums">{sharePct(meta.unattributed_share)}</span>)
        </>
      )}
      {uncovered.length > 0 && (
        <>
          ; {csList(uncovered)} {uncovered.length > 1 ? 'makléře neuvádějí' : 'makléře neuvádí'}
        </>
      )}
      .
    </p>
  );
}

/* A horizon no spell can have been followed through yet (entryWindow `opens`). */
const NotYet = ({ meta, opens }: { meta: BoardMeta; opens: Date }) => (
  <>
    Pro <Num n={meta.horizon_days} /> dní zatím nelze nic změřit: první odpovědi od {csDate(opens)}.
  </>
);

/* The benchmark: every attributed (property, broker) pair of the cohort, never narrowed by the row options. */
export function MarketLine({ meta, className }: { meta: BoardMeta; className?: string }) {
  if (meta.speed_refused) return null;
  const { opens } = entryWindow(meta);
  if (opens) {
    return (
      <p className={className}>
        <NotYet meta={meta} opens={opens} />
      </p>
    );
  }
  const m = meta.market;
  return (
    <p className={className}>
      Celý výběr:{' '}
      {m.share == null ? (
        <>
          sledováno <Num n={m.n} />, podíl se ukazuje od <Num n={meta.min_n} />
        </>
      ) : (
        <>
          <Num n={m.exited} /> z <Num n={m.n} /> opustilo trh do <Num n={meta.horizon_days} /> dní,{' '}
          <span className="font-mono tabular-nums">{sharePct(m.share)}</span>
        </>
      )}
      {meta.unknown > 0 && (
        <>
          {' '}· nezjištěno <Num n={meta.unknown} />
        </>
      )}
      .
    </p>
  );
}

/* Speed refused for this cohort, in one sentence naming the filters to change
 * (metadata.speed_refused_by) in the sidebar's words; volume stands. */
export function RefusalLine({ meta, className }: { meta: BoardMeta; className?: string }) {
  if (!meta.speed_refused) return null;
  const named = csList([...new Set(meta.speed_refused_by.map(filterLabel))].map((l) => `„${l}“`));
  return (
    <p role="note" className={className}>
      Rychlost se pro tento výběr nepočítá:{' '}
      {meta.speed_refused === 'outcome_selected'
        ? `výběr třídí podle výsledku. Vraťte na výchozí: ${named}.`
        : `míchá typy nemovitostí nebo nabídky. Zvolte jen jednu hodnotu: ${named}.`}
    </p>
  );
}

/* What "k z n" means: the label's caveat, the entry window from E0 and the follow-up margin M. */
export function SpeedLegend({ meta, className }: { meta: BoardMeta; className?: string }) {
  const { from, to, opens } = entryWindow(meta);
  const n = meta.horizon_days;
  return (
    <p className={className}>
      „Opustilo trh do <Num n={n} /> dní“ znamená prodáno nebo staženo, ne nutně prodej.{' '}
      {opens ? (
        <NotYet meta={meta} opens={opens} />
      ) : (
        <>
          „k z n“: z n nemovitostí, které makléř začal inzerovat od {csDate(from)} do {csDate(to)} (
          <Num n={n} /> dní a <Num n={meta.margin_days} /> dní rezerva), jich k opustilo trh do <Num n={n} /> dní,
          dokud je inzeroval.
        </>
      )}{' '}
      Podíl od n ≥ <Num n={meta.min_n} />; řazení podle rychlosti bere dolní mez Wilsonova intervalu (95 %).
      Nezjištěno: vedeno jako aktivní, ale dlouho neviděno; do n se nepočítá.
    </p>
  );
}
