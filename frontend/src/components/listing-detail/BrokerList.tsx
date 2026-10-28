/* Who is selling this property, how to reach them, and the number each of their
 * ads prints for callers — on the overview tab, so the contact is one glance from
 * the photos (operator 2026-10-22; the ad reference 2026-10-23).
 *
 * MS7 (lib/brokers.propertyBrokers), from one batched read the advert rows share.
 * A failed read says so with a retry, never "no broker" (the dark state that hid
 * the PostgREST revocation here for a month). A section card (system.md § Section
 * cards): with no attributed broker it stays, count 0, and says so (C10); while
 * the read is out it claims nothing either way. */
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ROUTES } from '@/lib/routes';
import { propertyBrokers } from '@/lib/brokers';
import { useListingBrokers } from '@/lib/hydration';
import type { PropertySource } from '@/lib/types';
import BrokerContactCard from '@/components/BrokerContactCard';
import Panel, { PanelEmpty } from '@/components/Panel';

export default function BrokerList({
  adverts,
  canonicalId,
  advertsFailed,
  onRetryAdverts,
}: {
  adverts: PropertySource[];
  canonicalId: number;
  advertsFailed: boolean;
  onRetryAdverts: () => void;
}) {
  const ids = useMemo(() => adverts.map((a) => a.id), [adverts]);
  const read = useListingBrokers(ids);

  if (advertsFailed || read.isError) {
    return (
      <Panel title="Makléř" className="min-w-0">
        <p className="text-sm text-[var(--color-brick)]">
          Makléře se nepodařilo načíst
          <button
            type="button"
            onClick={advertsFailed ? onRetryAdverts : read.refetch}
            className="ml-3 font-medium underline underline-offset-2 hover:no-underline"
          >
            Zkusit znovu
          </button>
        </p>
      </Panel>
    );
  }
  /* A property always has an advert: an empty list is one not read yet. */
  if (read.isPending || adverts.length === 0) {
    return (
      <Panel title="Makléř" className="min-w-0">
        <p className="text-sm text-[var(--color-ink-3)]">Načítám…</p>
      </Panel>
    );
  }
  const { brokers, fromInactive } = propertyBrokers(adverts, read.brokers, canonicalId);
  if (brokers.length === 0) {
    return (
      <Panel title="Makléř" count={0} className="min-w-0">
        <PanelEmpty>Makléř není uveden.</PanelEmpty>
      </Panel>
    );
  }

  return (
    <Panel
      title={brokers.length > 1 ? 'Makléři' : 'Makléř'}
      count={brokers.length}
      note={fromInactive ? 'z neaktivních inzerátů' : undefined}
      className="min-w-0"
    >
      <ul className="space-y-4">
        {brokers.map((b) => (
          <li key={b.broker_id} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
            <div className="min-w-0">
              <Link
                to={ROUTES.brokerDetail.build({ id: b.broker_id })}
                title={b.broker_display_name ?? undefined}
                className="inline-flex items-center gap-1.5 text-[1.05rem] leading-tight text-[var(--color-ink)] hover:text-[var(--color-copper-2)] transition-colors"
              >
                <span className="truncate">{b.broker_display_name ?? 'Neznámý makléř'}</span>
                <OutArrow />
              </Link>
              <p className="mt-1 text-sm text-[var(--color-ink-3)]">
                {b.broker_firm_label ?? 'nezávislý / neznámá kancelář'}
              </p>
            </div>
            <BrokerContactCard broker={b} ads={b.ads} bare />
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function OutArrow() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
      <line x1="1" y1="9" x2="8.5" y2="1.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
      <polyline
        points="3.5,1.5 8.5,1.5 8.5,6.5"
        stroke="currentColor"
        strokeWidth="1.25"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
