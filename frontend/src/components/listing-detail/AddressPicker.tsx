/* The address picker (location W3b, ruling Q5): the admin's pencil on the property page's
 * address line. Own place search finds the place (usePlaceSuggest + SuggestPanel), and its
 * row IS one RÚIAN unit (an address point, a street or an obec; #2453); saving writes it as
 * operator claims on EVERY advert of the property,
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
import { PLACE_LEVEL_LABEL, type PlaceRow } from '@/lib/places';
import { propertyKeys } from '@/lib/mergedAdverts';

const NAME = 'Opravit adresu (RÚIAN)';
const SAVED_LEVELS: ReadonlyArray<string> = ['adresni_misto', 'ulice', 'obec'] satisfies ReadonlyArray<LocationPick['level']>;
const isSaved = (level: string): level is LocationPick['level'] => SAVED_LEVELS.includes(level);

/* The pick a row saves, or what to say instead. */
const savedPick = ({ level, id }: PlaceRow): LocationPick | string =>
  isSaved(level) ? { level, code: id } : 'Vyberte adresu, ulici nebo obec';

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
  const [row, setRow] = useState<PlaceRow | undefined>(undefined);
  const [notice, setNotice] = useState<string | null>(null);
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (pick: LocationPick | null) => submitLocationPick({ property_id: propertyId, pick }),
    onSuccess: ({ data }) => {
      void qc.invalidateQueries({ queryKey: propertyKeys.row(propertyId) });
      void qc.invalidateQueries({ queryKey: propertyLocationKeys.location(propertyId) });
      const said = outcome(data);
      if (said == null) onDone();
      else setNotice(said);
    },
  });

  const choose = (picked: PlaceRow) => {
    suggest.reset();
    setListOpen(false);
    setNotice(null);
    setRow(picked);
  };

  const pick = row === undefined ? null : savedPick(row);
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
            placeholder="Ulice a číslo, nebo obec…"
            className="w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]"
          />
        </Field>
        {listOpen && !suggest.unavailable && (
          <SuggestPanel
            isLoading={suggest.isLoading}
            tooShort={suggest.tooShort}
            matches={suggest.matches}
            onPick={choose}
          />
        )}
      </div>
      <p role="status" aria-live="polite" className="min-h-[1.25rem] text-[0.8rem] text-[var(--color-ink-2)]">
        {notice ??
          (suggest.unavailable ? 'Vyhledávání míst je teď nedostupné; zkuste psát dál.'
            : pick == null || typeof pick === 'string' ? pick : (
            <>
              <span className="mr-2 text-[0.6rem] tracking-[0.08em] uppercase text-[var(--color-ink-3)]">
                {PLACE_LEVEL_LABEL[pick.level]}
              </span>
              {[row?.name, row?.context].filter(Boolean).join(' · ')}
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
