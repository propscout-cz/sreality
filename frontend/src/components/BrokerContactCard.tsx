import { useRef } from 'react';
import {
  contactState,
  prettyPhone,
  telHref,
  type BrokerContactFields,
  type ContactState,
} from '@/lib/brokers';
import { COPY_WORDS, copyText, type CopyWords } from '@/lib/clipboard';
import { portalBadge } from '@/lib/portals';
import type { PropertySource } from '@/lib/types';

/* An ad as the card names it: its portal, and the number it prints for callers. */
export type CardAd = Pick<PropertySource, 'id' | 'source' | 'source_url' | 'ad_reference'>;

/* The broker's two reachable channels and, under them, the AD REFERENCE of each ad this
   broker advertises (GLOSSARY: the number the ad prints for callers, kept verbatim) — ONE
   card, drawn by /brokers/:id (no ads) and by the property page's broker list on both
   shells, so the same broker reads identically everywhere. Typed on BrokerContactFields,
   not BrokerPublic: the masked pair is all the card consumes. The property page's rows
   always carry the values; /brokers/:id sends has_* in their place to a non-admin. An ad
   that prints no number reads "<portál> —": no other number is ever put in its place. */
export default function BrokerContactCard({
  broker,
  ads = [],
  bare = false,
  className,
}: {
  broker: BrokerContactFields;
  ads?: ReadonlyArray<CardAd>;
  /* Inside a section card: no frame of its own (no box in a box, system.md § Section cards). */
  bare?: boolean;
  className?: string;
}) {
  return (
    <div
      className={[
        bare
          ? 'min-w-[15rem]'
          : 'border border-[var(--color-rule)] rounded-[var(--radius-md)] bg-[var(--color-paper-3)] px-4 py-3 min-w-[15rem]',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <p className="text-[0.6rem] tracking-[0.16em] uppercase text-[var(--color-ink-3)]">
        Kontakt pro oslovení
      </p>
      <div className="mt-2 space-y-1.5">
        <ContactRow
          label="telefon"
          state={contactState(broker.primary_phone, broker.has_phone)}
          format={prettyPhone}
          href={telHref}
          words={COPY_WORDS.phone}
        />
        <ContactRow
          label="e-mail"
          state={contactState(broker.primary_email, broker.has_email)}
          href={(v) => `mailto:${v}`}
          words={COPY_WORDS.email}
        />
        {ads.map((ad) => {
          const portal = portalBadge(ad.source, ad.source_url);
          return (
            <ContactRow
              key={ad.id}
              label={portal}
              prefix={portal}
              state={ad.ad_reference ? { state: 'value', value: ad.ad_reference } : { state: 'none' }}
              words={COPY_WORDS.reference}
              copyLabel={`kopírovat číslo zakázky (${portal})`}
              title={
                ad.ad_reference
                  ? `Číslo zakázky z inzerátu na ${portal}`
                  : 'Číslo zakázky není k dispozici.'
              }
            />
          );
        })}
      </div>
    </div>
  );
}

function ContactRow({
  label,
  prefix,
  state,
  format = (v) => v,
  href,
  words,
  copyLabel,
  title,
}: {
  label: string;
  /* Drawn before the value: the portal an ad reference belongs to. */
  prefix?: string;
  state: ContactState;
  format?: (v: string) => string;
  href?: (v: string) => string;
  words: CopyWords;
  copyLabel?: string;
  title?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  if (state.state !== 'value') {
    // "masked" means a contact IS on file but this session may not see it —
    // rendering it as the empty dash would claim the broker has none.
    const masked = state.state === 'masked';
    return (
      <p
        className="text-sm text-[var(--color-ink-4)] font-[family-name:var(--font-mono)]"
        title={masked ? 'Kontakt je viditelný jen pro administrátory.' : title}
      >
        {label} {masked ? '· kontakt na vyžádání' : '—'}
      </p>
    );
  }
  const shown = format(state.value);
  return (
    <div className="flex items-center justify-between gap-3" title={title}>
      <span className="min-w-0 flex items-baseline gap-2">
        {prefix && (
          <span className="shrink-0 text-[0.7rem] text-[var(--color-ink-3)]">{prefix}</span>
        )}
        <span ref={ref} className="min-w-0 truncate text-sm font-[family-name:var(--font-mono)]">
          {href ? (
            <a
              href={href(state.value)}
              className="text-[var(--color-copper-2)] hover:underline underline-offset-2"
            >
              {shown}
            </a>
          ) : (
            <span className="text-[var(--color-ink)] [user-select:all]">{shown}</span>
          )}
        </span>
      </span>
      <button
        type="button"
        onClick={() => void copyText(shown, ref.current, words)}
        aria-label={copyLabel ?? `kopírovat ${label}`}
        className="shrink-0 py-1.5 md:py-0 text-[0.6rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)] hover:text-[var(--color-ink)] transition-colors"
      >
        kopírovat
      </button>
    </div>
  );
}
