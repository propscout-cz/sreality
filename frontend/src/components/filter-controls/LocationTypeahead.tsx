/* LocationTypeahead — the place box, on own place search (#2453).
 *
 * Rows come from `POST /places/suggest` (lib/places): kraje, okresy, towns, městské
 * části, části obce, streets and addresses, each row already a register unit.
 * Interaction contract matches the rest of filter-controls:
 *   value: DistrictChip[] | null  — selected chips
 *   onChange(next)                — null normalises to no constraint
 *
 * What you pick is what you filter: a row becomes the chip `{name, context, level, id}`
 * that every surface tests as `<level>_id = any(codes)` (lib/districtCodes), at once,
 * with no second call. A městská část has no code: its row's `places` are its quarters,
 * one ordinary část chip each (ruling 2026-11-02, #2380), so a URL, a preset and a
 * watchdog hold nothing new. An address adds its street's chip; one with no street adds
 * NO chip and says why under the input (ruling 2026-11-22). INCLUDE chips OR'd, then AND
 * NOT-(OR of EXCLUDE chips); the per-chip `−`/`+` button toggles `excluded`. The same
 * component renders on every surface with a location box.
 *
 * onPick fires once per pick with its row, so the Browse map can fly to it
 * (lib/places.flyToPlace) — also for a pick that adds no chip. The read (`usePlaceSuggest`)
 * and its list (`SuggestPanel`) are shared with the property page's address picker. */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { fetchPlaces, PLACE_LEVEL_LABEL, PLACES_UNAVAILABLE, type PlaceChipRow, type PlaceRow } from '@/lib/places';
import type { DistrictChip } from '@/lib/filters';
import { districtChipBadge, districtChipKey, isDistrictChip } from '@/lib/districtCodes';

const QUERY_DEBOUNCE_MS = 150;
const MIN_QUERY_LEN = 2;

/* Why a pick added no chip, shown under the input until the next keystroke or
 * pick. A refusal names the pick, so it is unambiguous. */
const PICK_NOTICE = {
  refused: (name: string) => `${name} can't be used as a filter — nothing was added`,
  duplicate: 'Already selected',
  expanded: (name: string, count: number) =>
    `${name} added as its ${count} ${count === 1 ? 'part' : 'parts'}`,
} as const;

const sameChip = (a: DistrictChip, b: DistrictChip): boolean =>
  districtChipKey(a) === districtChipKey(b);

const chipOf = ({ name, context, level, id }: PlaceChipRow) => ({ name, context, level, id });

/* The chips a row makes, or why it makes none: a městská část its quarters, an address
 * its street, any other row itself. */
const chipsOf = (row: PlaceRow): DistrictChip[] | string => {
  const units = row.level === 'momc' ? (row.places ?? []) : row.level === 'adresni_misto'
    ? (row.street ? [row.street] : []) : [row];
  const chips = units.map(chipOf).filter(isDistrictChip);
  return chips.length === units.length && chips.length > 0 ? chips : PICK_NOTICE.refused(row.name);
};

/* Own search's read behind a place box: the typed query, debounced, and its rows.
 * `unavailable` is the route's 503; the next keystroke asks again. */
export function usePlaceSuggest() {
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), QUERY_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);
  const suggestQ = useQuery({
    queryKey: ['places', 'suggest', debounced],
    queryFn: ({ signal }) => fetchPlaces(debounced, signal),
    enabled: debounced.length >= MIN_QUERY_LEN,
    staleTime: 60_000,
    retry: false,
  });
  return {
    query,
    setQuery,
    reset: () => {
      setQuery('');
      setDebounced('');
    },
    matches: suggestQ.data ?? [],
    isLoading: suggestQ.isFetching && debounced.length >= MIN_QUERY_LEN,
    tooShort: debounced.length > 0 && debounced.length < MIN_QUERY_LEN,
    unavailable: suggestQ.error instanceof Error && suggestQ.error.message === PLACES_UNAVAILABLE,
  };
}

export function LocationTypeahead({
  label,
  value,
  onChange,
  onPick,
}: {
  /* REQUIRED. The caption the user sees above/beside this field. A wrapping
   * Field/Section is role="group" — ancestor context, never this input's
   * name — and the placeholder self-changes once a chip exists. */
  label: string;
  value: ReadonlyArray<DistrictChip> | null;
  onChange: (next: DistrictChip[] | null) => void;
  /* Fires once per picked row: the Browse map flies there whether or not the
   * pick becomes a chip. A row with no point is still emitted; the receiver
   * no-ops on it (lib/places.flyToPlace). */
  onPick?: (row: PlaceRow) => void;
}) {
  const selected = useMemo(() => value ?? [], [value]);

  /* Every edit applies to the LATEST chips — refreshed every render, and set
   * synchronously by emit — never the ones a click closed over. */
  const latest = useRef<ReadonlyArray<DistrictChip>>(selected);
  latest.current = selected;

  const suggest = usePlaceSuggest();
  const { unavailable } = suggest;
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<{ text: string; done?: boolean } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const emit = (next: ReadonlyArray<DistrictChip>) => {
    latest.current = next;
    onChange(next.length === 0 ? null : [...next]);
  };

  /* Dedupe by the unit: a re-pick changes nothing (an excluded chip stays excluded). */
  const add = (row: PlaceRow) => {
    onPick?.(row);
    suggest.reset();
    const chips = chipsOf(row);
    if (typeof chips === 'string') {
      setNotice({ text: chips });
      return;
    }
    const fresh = chips.filter((chip) => !latest.current.some((c) => sameChip(c, chip)));
    if (fresh.length === 0) {
      setNotice({ text: PICK_NOTICE.duplicate });
      return;
    }
    emit([...latest.current, ...fresh]);
    setNotice(row.level === 'momc' ? { text: PICK_NOTICE.expanded(row.name, chips.length), done: true } : null);
  };

  const remove = (chip: DistrictChip) => {
    emit(latest.current.filter((c) => !sameChip(c, chip)));
  };

  const toggleExclude = (chip: DistrictChip) => {
    emit(
      latest.current.map((c) =>
        sameChip(c, chip) ? { ...c, excluded: !(c.excluded === true) } : c,
      ),
    );
  };

  return (
    <div>
      <div ref={ref} className="relative">
        <input
          type="text"
          aria-label={label}
          value={suggest.query}
          onChange={(e) => {
            suggest.setQuery(e.target.value);
            setNotice(null);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder={
            selected.length === 0 ? 'Type a city, district, or street…' : 'Add another…'
          }
          className="w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] disabled:opacity-60"
        />
        {open && !unavailable && (
          <SuggestPanel
            isLoading={suggest.isLoading}
            tooShort={suggest.tooShort}
            matches={suggest.matches}
            onPick={add}
          />
        )}
      </div>

      {/* Always mounted, so a screen reader hears a notice that appears. */}
      <p
        role="status"
        aria-live="polite"
        className={
          notice
            ? `mt-1 text-[0.7rem] ${notice.done ? 'text-[var(--color-ink-3)]' : 'text-[var(--color-brick)]'}`
            : 'sr-only'
        }
      >
        {notice?.text}
      </p>

      {unavailable && (
        <p className="mt-1 text-[0.7rem] text-[var(--color-ink-4)]">
          Place search is unavailable right now. Keep typing to try again; places
          already selected keep filtering.
        </p>
      )}

      {selected.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {selected.map((chip) => {
            const label = chip.context
              ? `${chip.name} · ${chip.context}`
              : chip.name;
            const key = districtChipKey(chip);
            const badge = districtChipBadge(chip);
            const excluded = chip.excluded === true;
            /* Excluded chips read as a negative filter: brick (the
             * inactive/failures semantic colour) + a leading minus. The
             * −/+ button toggles the mode; × removes the chip. */
            const palette = excluded
              ? 'bg-[var(--color-brick-soft)] text-[var(--color-brick)]'
              : 'bg-[var(--color-copper-soft)] text-[var(--color-copper)]';
            return (
              <li key={key}>
                <span
                  className={`inline-flex items-center gap-1 pl-1 pr-1 py-0.5 text-xs rounded-[var(--radius-sm)] ${palette}`}
                >
                  <button
                    type="button"
                    onClick={() => toggleExclude(chip)}
                    className="inline-flex h-4 w-4 items-center justify-center rounded-[var(--radius-xs)] font-semibold leading-none opacity-70 hover:opacity-100 transition-opacity"
                    aria-label={`${excluded ? 'Include' : 'Exclude'} ${label} (${badge})`}
                    title={excluded ? 'Click to include' : 'Click to exclude'}
                  >
                    {excluded ? '+' : '−'}
                  </button>
                  <span className="px-0.5">{excluded ? `− ${label}` : label}</span>
                  <span
                    className="shrink-0 text-[0.6rem] tracking-[0.08em] uppercase opacity-70"
                    title={`${badge} · RÚIAN ${chip.id}`}
                  >
                    {badge}
                  </span>
                  <button
                    type="button"
                    onClick={() => remove(chip)}
                    className="inline-flex h-4 w-4 items-center justify-center rounded-[var(--radius-xs)] leading-none opacity-60 hover:opacity-100 transition-opacity"
                    aria-label={`Remove ${label} (${badge})`}
                    title="Remove"
                  >
                    ×
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function SuggestPanel({
  isLoading,
  tooShort,
  matches,
  onPick,
}: {
  isLoading: boolean;
  tooShort: boolean;
  matches: ReadonlyArray<PlaceRow>;
  onPick: (row: PlaceRow) => void;
}) {
  if (tooShort) {
    return (
      <div className="absolute z-20 mt-1 w-full rounded-[var(--radius-md)] bg-[var(--color-paper-3)] border border-[var(--color-rule-strong)] shadow-[0_4px_16px_rgba(0,0,0,0.06)] px-3 py-2 text-[0.75rem] text-[var(--color-ink-4)]">
        Type at least 2 characters…
      </div>
    );
  }
  if (isLoading) {
    return (
      <div className="absolute z-20 mt-1 w-full rounded-[var(--radius-md)] bg-[var(--color-paper-3)] border border-[var(--color-rule-strong)] shadow-[0_4px_16px_rgba(0,0,0,0.06)] px-3 py-2 text-[0.75rem] text-[var(--color-ink-4)]">
        Searching…
      </div>
    );
  }
  if (matches.length === 0) return null;
  return (
    <ul
      role="listbox"
      className="absolute z-20 mt-1 w-full max-h-72 overflow-y-auto rounded-[var(--radius-md)] bg-[var(--color-paper-3)] border border-[var(--color-rule-strong)] shadow-[0_4px_16px_rgba(0,0,0,0.06)] py-1"
    >
      {matches.map((row) => (
        <li key={`${row.level}-${row.id}`}>
          <button
            type="button"
            onClick={() => onPick(row)}
            className="w-full flex items-center justify-between gap-3 px-3 py-1.5 text-left hover:bg-[var(--color-copper-soft)]"
          >
            <span className="min-w-0 flex-1 truncate">
              <span className="block text-sm text-[var(--color-ink)] truncate">{row.name}</span>
              {row.context ? (
                <span className="block text-[0.7rem] text-[var(--color-ink-3)] truncate">{row.context}</span>
              ) : null}
            </span>
            <span className="shrink-0 text-[0.65rem] tracking-[0.12em] uppercase text-[var(--color-ink-3)] border border-[var(--color-rule)] rounded-[var(--radius-xs)] px-1.5 py-0.5">
              {PLACE_LEVEL_LABEL[row.level]}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
