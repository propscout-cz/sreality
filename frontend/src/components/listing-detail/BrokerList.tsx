/* Who is selling this property and how to reach them — on the overview tab, so
 * the contact is one glance from the photos (operator 2026-10-22).
 *
 * MS7 (lib/brokers.propertyBrokers), from one batched read the advert rows share.
 * A failed read says so with a retry, never "no broker" (the dark state that hid
 * the PostgREST revocation here for a month). Loading and "no broker" render
 * nothing, frame included: most properties have no attributed broker. */
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ROUTES } from '@/lib/routes';
import { propertyBrokers } from '@/lib/brokers';
import { useListingBrokers } from '@/lib/hydration';
import type { PropertySource } from '@/lib/types';
import BrokerContactCard from '@/components/BrokerContactCard';
import { SectionLabel } from '@/components/section';

export default function BrokerList({
  adverts,
  canonicalId,
  advertsFailed,
  onRetryAdverts,
  className,
}: {
  adverts: PropertySource[];
  canonicalId: number;
  advertsFailed: boolean;
  onRetryAdverts: () => void;
  /* The frame, drawn only when there is something to say. */
  className?: string;
}) {
  const ids = useMemo(() => adverts.map((a) => a.id), [adverts]);
  const read = useListingBrokers(ids);

  if (advertsFailed || read.isError) {
    return (
      <section aria-label="Makléř" className={className}>
        <SectionLabel>Makléř</SectionLabel>
        <p className="mt-3 text-sm text-[var(--color-brick)]">
          Makléře se nepodařilo načíst
          <button
            type="button"
            onClick={advertsFailed ? onRetryAdverts : read.refetch}
            className="ml-3 font-medium underline underline-offset-2 hover:no-underline"
          >
            Zkusit znovu
          </button>
        </p>
      </section>
    );
  }
  const { brokers, fromInactive } = propertyBrokers(adverts, read.brokers, canonicalId);
  if (brokers.length === 0) return null;

  return (
    <section aria-label={brokers.length > 1 ? 'Makléři' : 'Makléř'} className={className}>
      <SectionLabel>
        {brokers.length > 1 ? 'Makléři' : 'Makléř'}
        {fromInactive && (
          <span className="ml-2 normal-case tracking-normal font-normal text-[var(--color-ink-3)]">
            z neaktivních inzerátů
          </span>
        )}
      </SectionLabel>
      <ul className="mt-3 space-y-4">
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
            <BrokerContactCard broker={b} />
          </li>
        ))}
      </ul>
    </section>
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
