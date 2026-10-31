/* The address picker (location W3b, ruling Q5): the admin's pencil on the property page's
 * address line. Browse's place typeahead finds the place (usePlaceSuggest + SuggestPanel);
 * `/maps/resolve` with the address-point flag binds it to ONE RÚIAN unit (an address point, a
 * street or an obec); saving writes it as operator claims on EVERY advert of the property,
 * which outrank the portals' words. "Vrátit údaje z inzerátů" takes the pick back. The page's
 * label, map and city tiles re-read `properties_public` (a live view) at once; Browse follows
 * at its next rebuild. Desktop only, lazy, admin only. */

import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import AnchoredPopover from '@/components/AnchoredPopover';
import ErrorBanner from '@/components/ErrorBanner';
import { Field } from '@/components/controls';
import { PencilIcon } from '@/components/icons';
import { SuggestPanel, usePlaceSuggest } from '@/components/filter-controls/LocationTypeahead';
import { propertyLocationKeys } from '@/components/phone/propertyLocation';
import { submitLocationPick, type LocationPick, type PickResult } from '@/lib/locationQuality';
import { resolveSuggestion, type LocationResolution, type MapySuggestion } from '@/lib/maps';
import { propertyKeys } from '@/lib/mergedAdverts';

const NAME = 'Opravit adresu (RÚIAN)';
const BADGE: Record<string, string> = { adresni_misto: 'Adresní místo', ulice: 'Ulice', obec: 'Obec' };

/* The bound pick, or what to say instead. `res` null = the resolve call failed. */
function bound(res: LocationResolution | null): LocationPick | string {
  if (res == null) return 'Místo se nepodařilo ověřit — zkuste to znovu';
  if (res.level == null || !(res.level in BADGE)) return 'Vyberte adresu, ulici nebo obec';
  if (res.id == null) return 'V RÚIAN nelze jednoznačně najít';
  return { level: res.level as LocationPick['level'], code: res.id };
}

/* What a save leaves to say; null = done, the popover closes. */
function outcome({ resolved, held, location }: PickResult): string | null {
  if (!resolved) return 'Uloženo; poloha se přepočítá během několika minut.';
  if (held === false) {
    return `Uloženo, ale poloha je stále „${location?.label ?? '—'}“ — údaje portálu (PSČ, kód adresy z portálu) převážily; zvolte konkrétní adresu.`;
  }
  return null;
}

/* `label`: the address line as it reads now; `adCount`: the adverts a save writes. */
type Props = { propertyId: number; label: string | null; adCount: number };

export default function AddressPicker(props: Props) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        ref={anchor}
        type="button"
        aria-label={NAME}
        title={NAME}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="ml-1 inline-flex h-6 w-6 items-center justify-center rounded-[var(--radius-sm)] align-middle text-[var(--color-ink-3)] hover:bg-[var(--color-paper-3)] hover:text-[var(--color-ink)] transition-colors"
      >
        <PencilIcon className="h-3.5 w-3.5" />
      </button>
      {open && (
        <AnchoredPopover anchorRef={anchor} onClose={() => setOpen(false)} ariaLabel={NAME} className="w-[22rem] p-3">
          <Picker {...props} onDone={() => setOpen(false)} />
        </AnchoredPopover>
      )}
    </>
  );
}

/* Mounted per opening, so every opening starts with an empty box. */
function Picker({ propertyId, label, adCount, onDone }: Props & { onDone: () => void }) {
  const suggest = usePlaceSuggest();
  const [listOpen, setListOpen] = useState(false);
  /* undefined: nothing picked yet; null: the resolve failed. Only the latest pick lands. */
  const [res, setRes] = useState<LocationResolution | null | undefined>(undefined);
  const [resolving, setResolving] = useState(false);
  const latest = useRef(0);
  const [notice, setNotice] = useState<string | null>(null);
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (pick: LocationPick | null) => submitLocationPick({ property_id: propertyId, pick }),
    onSuccess: ({ data }) => {
      void qc.invalidateQueries({ queryKey: propertyKeys.row(propertyId) });
      void qc.invalidateQueries({ queryKey: propertyLocationKeys.location(propertyId) });
      void qc.invalidateQueries({ queryKey: propertyLocationKeys.staticMap(propertyId) });
      const said = outcome(data);
      if (said == null) onDone();
      else setNotice(said);
    },
  });

  const choose = async (s: MapySuggestion) => {
    const n = ++latest.current;
    suggest.reset();
    setListOpen(false);
    setNotice(null);
    setResolving(true);
    const answer = await resolveSuggestion(s, { addressPoint: true }).catch(() => null);
    if (n !== latest.current) return;
    setResolving(false);
    setRes(answer);
  };

  const pick = resolving || res === undefined ? null : bound(res);
  const quiet = 'px-2.5 py-1.5 text-[0.8rem] text-[var(--color-ink-3)] hover:text-[var(--color-ink)] disabled:opacity-50';
  const primary =
    'px-3 py-1.5 text-[0.8rem] rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] disabled:opacity-40 transition-colors';

  return (
    <div className="space-y-2 text-sm">
      <p className="text-[0.75rem] text-[var(--color-ink-3)]">{label ?? '—'}</p>
      <div className="relative">
        <Field label="Adresa, ulice nebo obec" as="control">
          <input
            type="text"
            value={suggest.query}
            onChange={(e) => {
              suggest.setQuery(e.target.value);
              setListOpen(true);
            }}
            onFocus={() => setListOpen(true)}
            disabled={suggest.notConfigured}
            placeholder="Ulice a číslo, nebo obec…"
            className="w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] disabled:opacity-60"
          />
        </Field>
        {listOpen && !suggest.notConfigured && (
          <SuggestPanel
            isLoading={suggest.isLoading}
            tooShort={suggest.tooShort}
            matches={suggest.matches}
            onPick={(s) => void choose(s)}
          />
        )}
      </div>
      <p role="status" aria-live="polite" className="min-h-[1.25rem] text-[0.8rem] text-[var(--color-ink-2)]">
        {notice ??
          (resolving ? 'Ověřuji v RÚIAN…' : pick == null || typeof pick === 'string' ? pick : (
            <>
              <span className="mr-2 text-[0.6rem] tracking-[0.08em] uppercase text-[var(--color-ink-3)]">
                {BADGE[pick.level]}
              </span>
              {[res?.name, res?.context].filter(Boolean).join(' · ')}
            </>
          ))}
      </p>
      {save.error ? <ErrorBanner flush message={save.error.message} /> : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" onClick={() => save.mutate(null)} disabled={save.isPending} className={quiet}>
          Vrátit údaje z inzerátů
        </button>
        {pick != null && typeof pick !== 'string' && (
          <button type="button" onClick={() => save.mutate(pick)} disabled={save.isPending} className={primary}>
            {save.isPending ? 'Ukládám…' : `Uložit pro všechny inzeráty${adCount > 0 ? ` (${adCount})` : ''}`}
          </button>
        )}
      </div>
    </div>
  );
}
