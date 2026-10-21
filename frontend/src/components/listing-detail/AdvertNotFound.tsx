/* A portal advert we hold no row for — most often a pasted link to an ad the
 * scrapers have not reached yet. ONE state for both shells (pages/PropertyDetail
 * frames it): it says so in Czech, offers "Přidat do databáze" (the new-property
 * dialog prefilled with the pasted URL; admins only, components/NewPropertyProvider)
 * and, when the paste carried the URL, the portal's own page (same window: an
 * installed iOS app opens it in its in-app Safari sheet). Its own chunk: a rare
 * page, kept out of the first visit. */

import { useNewPropertyDialog } from '@/components/NewPropertyProvider';
import { portalLabel } from '@/lib/portals';

export default function AdvertNotFound({
  source,
  nativeId,
  pastedUrl,
  heading: Heading,
}: {
  source: string;
  nativeId: string;
  pastedUrl: string | null;
  heading: 'h1' | 'h2';
}) {
  const newProperty = useNewPropertyDialog();
  const portal = portalLabel(source) ?? source;
  return (
    <>
      <p className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--color-ink-3)]">
        Nenalezeno
      </p>
      <Heading className="mt-2 font-display text-[22px] font-medium leading-tight tracking-[-0.01em] text-[var(--color-ink)]">
        Tento inzerát v databázi nemáme.
      </Heading>
      <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[14px] text-[var(--color-ink-2)]">
        <span className="inline-flex h-5 items-center rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] px-1 text-[11px]">
          {portal}
        </span>
        <span className="break-all font-mono text-[12px] tabular-nums">{nativeId}</span>
      </p>
      <p className="mt-3 text-[13px] leading-relaxed text-[var(--color-ink-3)]">
        Buď je nový a k nám ještě nedorazil, nebo ho portál stáhl dřív, než jsme ho přečetli.
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2">
        {newProperty && (
          <button
            type="button"
            onClick={() => newProperty.open(pastedUrl ? { url: pastedUrl } : {})}
            className="inline-flex h-11 items-center rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-4 text-[14px] font-medium text-white hover:bg-[var(--color-copper-2)] active:bg-[var(--color-copper-2)] transition-colors"
          >
            Přidat do databáze
          </button>
        )}
        {pastedUrl && (
          <a
            href={pastedUrl}
            className="inline-flex h-11 items-center gap-1 text-[15px] font-medium text-[var(--color-copper)]"
          >
            Otevřít na portálu {portal}
            <span aria-hidden>↗</span>
          </a>
        )}
      </div>
    </>
  );
}
