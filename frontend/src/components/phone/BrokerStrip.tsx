/* The phone property page's way to the broker, under the price (operator 2026-11-06):
 * the lead broker's name and office, "Zavolat" (the dialer, not a copy) and "Uložit
 * kontakt" (lib/vcard). The lead is the first of lib/brokers `propertyBrokers`, the
 * canonical ad's broker first, so it is the broker the Makléři section lists first;
 * more of them are one tap away there. Off the same batched read as that section (one
 * request). While the read is out it holds its height; a failed read, or a broker with
 * no number and no e-mail, draws nothing here: the Makléři section says what it knows. */

import { useMemo } from 'react';

import SaveContactButton from '@/components/SaveContactButton';
import { brokerContact, type ContactAbout } from '@/lib/brokerContact';
import { propertyBrokers, telHref } from '@/lib/brokers';
import { czPlural } from '@/lib/format';
import { useListingBrokers } from '@/lib/hydration';
import type { PropertySource } from '@/lib/types';

import BrokerAvatar from './BrokerAvatar';
import { ContactAddIcon, PhoneCallIcon } from './phoneIcons';

export const BROKERS_SECTION_ID = 'makleri';

const ACTION =
  'inline-flex h-11 items-center justify-center gap-2 rounded-[var(--radius-sm)] text-[15px] font-medium';
const PRIMARY = `${ACTION} bg-[var(--color-copper)] text-[var(--color-on-copper)] active:bg-[var(--color-copper-2)]`;
const SECONDARY = `${ACTION} border border-[var(--color-rule-strong)] text-[var(--color-ink)] active:bg-[var(--color-paper-2)]`;

export default function BrokerStrip({
  sources,
  sourcesFailed,
  canonicalId,
  about,
}: {
  sources: PropertySource[];
  sourcesFailed: boolean;
  canonicalId: number;
  about: ContactAbout;
}) {
  const ids = useMemo(() => sources.map((s) => s.id), [sources]);
  const read = useListingBrokers(ids);
  if (sourcesFailed || read.isError) return null;
  if (read.isPending || sources.length === 0) {
    return <div aria-hidden className="mx-4 mb-4 h-[6.5rem] rounded-[var(--radius-sm)] bg-[var(--color-paper-2)]" />;
  }

  const { brokers } = propertyBrokers(sources, read.brokers, canonicalId);
  const lead = brokers[0];
  const contact =
    lead &&
    brokerContact({
      brokerId: lead.broker_id,
      name: lead.broker_display_name,
      firm: lead.broker_firm_label,
      phone: lead.primary_phone,
      email: lead.primary_email,
      ads: lead.ads,
      about,
    });
  if (!lead || !contact) return null;
  const others = brokers.length - 1;

  return (
    <section aria-label="Makléř" className="px-4 pb-4">
      <div className="flex items-center gap-3">
        <BrokerAvatar name={lead.broker_display_name} />
        <div className="min-w-0">
          <p className="truncate text-[15px] text-[var(--color-ink)]">{lead.broker_display_name ?? 'Neznámý makléř'}</p>
          <p className="truncate text-[12px] text-[var(--color-ink-3)]">
            {lead.broker_firm_label ?? 'nezávislý / neznámá kancelář'}
          </p>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {lead.primary_phone ? (
          <a href={telHref(lead.primary_phone)} className={PRIMARY}>
            <PhoneCallIcon className="h-5 w-5" />
            Zavolat
          </a>
        ) : (
          <a href={`mailto:${lead.primary_email}`} className={PRIMARY}>
            Napsat e-mail
          </a>
        )}
        <SaveContactButton contact={contact} className={SECONDARY}>
          <ContactAddIcon className="h-5 w-5" />
          Uložit kontakt
        </SaveContactButton>
      </div>
      {others > 0 && (
        <a
          href={`#${BROKERS_SECTION_ID}`}
          onClick={(e) => {
            e.preventDefault();
            document.getElementById(BROKERS_SECTION_ID)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }}
          className="mt-2 inline-flex h-8 items-center text-[13px] font-medium text-[var(--color-copper)]"
        >
          {`+ ${others} ${czPlural(others, 'další makléř', 'další makléři', 'dalších makléřů')}`}
        </a>
      )}
    </section>
  );
}
