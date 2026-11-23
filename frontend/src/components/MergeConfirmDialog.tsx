/* The last look before a merge (operator ruling 2026-11-23): a merge moves
 * EVERY ad of the properties it names, so each letter shows the whole property
 * it will make, ad by ad with its photos, grouped by the property the ad sits
 * on today. Nothing is decided here that the bar did not already state: the
 * dialog reads, and its one button sends the same letters. */
import { useId, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import Dialog, { DialogClose } from '@/components/Dialog';
import ErrorBanner from '@/components/ErrorBanner';
import Spinner from '@/components/Spinner';
import MemberGrid from '@/components/autodedup/MemberGrid';
import { PHOTOS_PER_ADVERT, memberFromListing } from '@/components/autodedup/memberFromListing';
import { fmtCount } from '@/lib/format';
import { useListingPhotos } from '@/lib/hydration/useCardHydration';
import { inzeratu, mergedAdvertsKeys } from '@/lib/mergedAdverts';
import { fetchListingsForPropertyIds } from '@/lib/queries';
import { ROUTES } from '@/lib/routes';
import type { ListingPublic } from '@/lib/types';
import type { MergeMode } from '@/lib/useMergeMode';

const BTN = 'px-3 py-1.5 text-sm rounded-[var(--radius-sm)] transition-colors disabled:opacity-50';

export default function MergeConfirmDialog({
  groups,
  merging,
  onConfirm,
  onClose,
}: {
  /* The letters that merge: each held by two properties or more. */
  groups: MergeMode['groups'];
  merging: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const propertyIds = useMemo(
    () => [...new Set(groups.flatMap(([, ids]) => ids))].sort((a, b) => a - b),
    [groups],
  );
  const ads = useQuery<ListingPublic[], Error>({
    queryKey: mergedAdvertsKeys.ofProperties(propertyIds),
    queryFn: ({ signal }) => fetchListingsForPropertyIds(propertyIds, { signal }),
    enabled: propertyIds.length > 0,
    staleTime: 30_000,
  });
  const byProperty = useMemo(() => {
    const out = new Map<number, ListingPublic[]>();
    for (const ad of ads.data ?? []) {
      if (ad.property_id == null) continue;
      out.set(ad.property_id, [...(out.get(ad.property_id) ?? []), ad]);
    }
    return out;
  }, [ads.data]);
  const adIds = useMemo(() => (ads.data ?? []).map((ad) => ad.id), [ads.data]);
  const { photos } = useListingPhotos(adIds, PHOTOS_PER_ADVERT);

  return (
    <Dialog
      open
      onClose={merging ? () => {} : onClose}
      labelledBy={titleId}
      className="w-full max-w-6xl flex flex-col"
    >
      <header className="flex items-baseline justify-between gap-4 px-6 pt-5 pb-3">
        <div>
          <h2
            id={titleId}
            className="text-[1.35rem] leading-tight"
            style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
          >
            Zkontrolujte, co sloučení spojí
          </h2>
          <p className="mt-1 text-sm text-[var(--color-ink-2)] max-w-[52rem]">
            Sloučení přesune všechny inzeráty vybraných nemovitostí, i ty neaktivní. Každé písmeno
            níže bude jedna nemovitost.
          </p>
        </div>
        <DialogClose onClick={onClose} disabled={merging} label="Zavřít" className="-mr-1" />
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto px-6 pb-4 space-y-6">
        {ads.error && <ErrorBanner message={ads.error.message} onRetry={() => ads.refetch()} />}
        {ads.isPending && <Spinner />}
        {ads.data &&
          groups.map(([letter, ids]) => {
            const total = ids.reduce((n, id) => n + (byProperty.get(id)?.length ?? 0), 0);
            return (
              <section key={letter} aria-label={`Písmeno ${letter}`} className="space-y-3">
                <h3 className="flex flex-wrap items-baseline gap-x-3 border-b border-[var(--color-rule)] pb-1">
                  <span className="font-mono text-base font-medium text-[var(--color-copper-2)]">
                    {letter}
                  </span>
                  <span className="text-sm text-[var(--color-ink-2)] tabular-nums">
                    z {fmtCount(ids.length)} nemovitostí jedna · {fmtCount(total)} {inzeratu(total)}
                  </span>
                </h3>
                {ids.map((id) => {
                  const rows = byProperty.get(id) ?? [];
                  return (
                    <div key={id} className="space-y-2">
                      <p className="text-[0.75rem] text-[var(--color-ink-3)] tabular-nums">
                        <Link
                          to={ROUTES.property.build({ propertyId: id })}
                          target="_blank"
                          rel="noopener"
                          className="font-mono text-[var(--color-ink-2)] underline decoration-[var(--color-rule-strong)] hover:text-[var(--color-ink)]"
                        >
                          #{id}
                        </Link>{' '}
                        · {fmtCount(rows.length)} {inzeratu(rows.length)}
                      </p>
                      <MemberGrid
                        unfolded
                        members={rows.map((ad) =>
                          memberFromListing(ad.id, ad, ad, photos.get(ad.id) ?? []),
                        )}
                      />
                    </div>
                  );
                })}
              </section>
            );
          })}
      </div>

      <footer className="flex items-center justify-end gap-2 border-t border-[var(--color-rule)] px-6 py-3">
        <button
          type="button"
          onClick={onClose}
          disabled={merging}
          className={`${BTN} border border-[var(--color-rule)] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] hover:border-[var(--color-rule-strong)]`}
        >
          Zpět
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={merging || !ads.data}
          className={`${BTN} bg-[var(--color-copper)] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)]`}
        >
          {merging ? 'Slučuji…' : 'Sloučit'}
        </button>
      </footer>
    </Dialog>
  );
}
