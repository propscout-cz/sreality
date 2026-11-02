/* LocationTypeahead — Mapy.cz-powered place autocomplete.
 *
 * Suggestions come from the FastAPI proxy at /maps/suggest (which keeps the
 * MAPY_CZ_API_KEY server-side): kraje, okresy, towns, quarters, streets and
 * addresses. Interaction contract matches the rest of filter-controls:
 *   value: DistrictChip[] | null  — selected chips
 *   onChange(next)                — null normalises to no constraint
 *
 * What you pick is what you filter: `/maps/resolve` answers ONE RÚIAN unit at
 * the picked level (kraj, okres, obec, část obce, ulice — an address is its
 * street) with the register's name, and the pick becomes the chip
 * `{name, context, level, id}` that every surface tests as
 * `<level>_id = any(codes)` (lib/districtCodes). A pick the server cannot
 * bind to one unit adds NO chip and says why under the input; no Mapy string is
 * ever stored. INCLUDE chips OR'd, then AND NOT-(OR of EXCLUDE chips); the per-chip
 * `−`/`+` button toggles `excluded`. The same component renders on every surface
 * with a location box.
 *
 * onPick fires once per pick, before it resolves, so the Browse map can fly to
 * the picked place — also for a pick that adds no chip. The suggest read
 * (`usePlaceSuggest`) and its list (`SuggestPanel`) are shared with the
 * property page's address picker (location W3b). */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import {
  fetchSuggest,
  resolveSuggestion,
  type LocationResolution,
  type MapySuggestion,
  SUGGEST_NOT_CONFIGURED,
  typeBadge,
} from '@/lib/maps';
import type { DistrictChip } from '@/lib/filters';
import { districtChipBadge, districtChipKey, isDistrictChip } from '@/lib/districtCodes';

const QUERY_DEBOUNCE_MS = 150;
const MIN_QUERY_LEN = 2;

/* Why a pick added no chip, shown under the input until the next keystroke or
 * pick. A refusal names the pick, so a notice that lands late is unambiguous. */
const PICK_NOTICE = {
  refused: (name: string) => `${name} can't be used as a filter — nothing was added`,
  country: "A country can't be used as a filter",
  failed: "Couldn't verify this place — try again",
  duplicate: 'Already selected',
} as const;

const sameChip = (a: DistrictChip, b: DistrictChip): boolean =>
  districtChipKey(a) === districtChipKey(b);

/* The chip a resolve answer makes, or why it makes none. `res` null = the
 * resolve call threw, which says nothing about the place. */
const chipFromResolve = (
  pick: MapySuggestion,
  res: LocationResolution | null,
): DistrictChip | string => {
  if (res == null) return PICK_NOTICE.failed;
  if (res.level == null && pick.type === 'regional.country') return PICK_NOTICE.country;
  const chip = { name: res.name, context: res.context, level: res.level, id: res.id };
  return isDistrictChip(chip) ? chip : PICK_NOTICE.refused(pick.name);
};

/* The Mapy suggest read behind a place box: the typed query, debounced, and its answer. */
export function usePlaceSuggest() {
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), QUERY_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);
  const suggestQ = useQuery({
    queryKey: ['maps', 'suggest', debounced],
    queryFn: ({ signal }) => fetchSuggest(debounced, signal),
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
    notConfigured:
      suggestQ.error instanceof Error && suggestQ.error.message === SUGGEST_NOT_CONFIGURED,
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
  /* Fires once per picked suggestion, before it resolves: the Browse map flies
   * there whether or not the pick becomes a chip. Picks without a `position`
   * are still emitted; the receiver should no-op when it is missing. */
  onPick?: (s: MapySuggestion) => void;
}) {
  const selected = useMemo(() => value ?? [], [value]);

  /* A pick resolves asynchronously, so every edit applies to the LATEST chips —
   * refreshed every render, and set synchronously by emit — through the latest
   * onChange, never the ones the click closed over. */
  const latest = useRef<ReadonlyArray<DistrictChip>>(selected);
  latest.current = selected;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const suggest = usePlaceSuggest();
  const { notConfigured } = suggest;
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(0);
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
    onChangeRef.current(next.length === 0 ? null : [...next]);
  };

  /* The map flies at once; the chip waits for `/maps/resolve`. Dedupe runs
   * after resolving, because identity is the resolved unit — a re-pick changes
   * nothing (an excluded chip stays excluded). */
  const add = async (s: MapySuggestion) => {
    onPick?.(s);
    suggest.reset();
    setNotice(null);
    setPending((n) => n + 1);
    const res = await resolveSuggestion(s).catch(() => null);
    if (!alive.current) return;
    setPending((n) => n - 1);
    const chip = chipFromResolve(s, res);
    if (typeof chip === 'string') setNotice(chip);
    else if (latest.current.some((c) => sameChip(c, chip))) setNotice(PICK_NOTICE.duplicate);
    else emit([...latest.current, chip]);
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
    <div aria-busy={pending > 0}>
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
          disabled={notConfigured}
          className="w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] disabled:opacity-60"
        />
        {open && !notConfigured && (
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
        className={notice ? 'mt-1 text-[0.7rem] text-[var(--color-brick)]' : 'sr-only'}
      >
        {notice}
      </p>

      {notConfigured && (
        <p className="mt-1 text-[0.7rem] text-[var(--color-ink-4)]">
          Place search is unavailable — geocoding isn&apos;t configured for this
          deploy, or Mapy.cz isn&apos;t answering. Reload to try again; places
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
  matches: ReadonlyArray<MapySuggestion>;
  onPick: (s: MapySuggestion) => void;
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
      {matches.map((s, idx) => (
        <li key={`${s.name}-${idx}`}>
          <button
            type="button"
            onClick={() => onPick(s)}
            className="w-full flex items-center justify-between gap-3 px-3 py-1.5 text-left hover:bg-[var(--color-copper-soft)]"
          >
            <span className="min-w-0 flex-1 truncate">
              {/* Mapy.cz puts the place name in `name` (e.g. "Ostrava")
                * and the regional context in `location`
                * (e.g. "okres Ostrava-město, Moravskoslezský kraj").
                * `label` is the type-in-Czech ("Obec", "Vesnice") which
                * the type badge already shows. Display name + location
                * stacked so the dropdown reads as a real address list. */}
              <span className="block text-sm text-[var(--color-ink)] truncate">
                {s.name}
              </span>
              {s.location ? (
                <span className="block text-[0.7rem] text-[var(--color-ink-3)] truncate">
                  {s.location}
                </span>
              ) : null}
            </span>
            <span className="shrink-0 text-[0.65rem] tracking-[0.12em] uppercase text-[var(--color-ink-3)] border border-[var(--color-rule)] rounded-[var(--radius-xs)] px-1.5 py-0.5">
              {typeBadge(s.type)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
