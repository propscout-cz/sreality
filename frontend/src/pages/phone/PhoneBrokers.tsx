/* Makléři on the phone (#1769, #1760 Q12): a reduced leaderboard — place,
 * category and offer, metric — plus the name search, over the desktop page's
 * own reads in lib/brokers.ts (`fetchBrokerLeaderboard`, `searchBrokersByName`,
 * both on the caller's JWT, contacts masked server-side).
 *
 * The roll is a ruled notebook page: the rank sits in the left margin behind a
 * hairline, like line numbers, the person and the firm in the body, the ranked
 * figure in mono at the right edge. Typing a name swaps the roll for the search
 * results in place (a dropdown has no room on a phone, and the keyboard covers
 * half the screen); clearing the field brings the roll back. */

import { useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';

import BottomSheet from '@/components/phone/BottomSheet';
import DayRule from '@/components/phone/DayRule';
import { RollEmpty } from '@/components/phone/RollParts';
import { CHIP, CHIP_DIVIDER, CHIP_MD, CHIP_ROW, CHIP_SM, chipState } from '@/components/phone/chips';
import { ChevronIcon, ClearIcon, PinIcon, SearchIcon } from '@/components/phone/phoneIcons';
import { LocationTypeahead } from '@/components/filter-controls/LocationTypeahead';
import {
  LEADER_METRIC_OPTIONS,
  chipsToGeoArrays,
  fetchBrokerLeaderboard,
  searchBrokersByName,
  type BrokerLeaderRow,
  type LeaderMetric,
} from '@/lib/brokers';
import type { DistrictChip } from '@/lib/filters';
import { czPlural, fmtCount } from '@/lib/format';
import { ROUTES } from '@/lib/routes';
import { useDebouncedTerm } from '@/lib/useDebouncedTerm';

/* The phone's category and offer pills: the two the operator works (Q12). A
 * pressed pill tapped again lets go — no constraint, the desktop's "Vše". */
const CATEGORIES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'byt', label: 'Byty' },
  { value: 'dum', label: 'Domy' },
];
const OFFERS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'prodej', label: 'Prodej' },
  { value: 'pronajem', label: 'Pronájem' },
];
const LIMIT = 100;

const CATEGORY_WORD: Record<string, string> = { byt: 'byty', dum: 'domy' };
const OFFER_WORD: Record<string, string> = { prodej: 'prodej', pronajem: 'pronájem' };

/* The chips that actually scope the board: coded includes at the three levels
 * the leaderboard takes (`chipsToGeoArrays` skips a city-quarter chip). */
export const scopingChips = (chips: ReadonlyArray<DistrictChip>): DistrictChip[] =>
  chips.filter((d) => d.id != null && !d.excluded && d.level !== 'cast_obce');

/* "Celá ČR", "Praha", "Praha +2" — short enough for a chip and a rule. */
export function placeLabelOf(chips: ReadonlyArray<DistrictChip>): string {
  const scoped = scopingChips(chips);
  if (scoped.length === 0) return 'Celá ČR';
  return scoped.length === 1 ? scoped[0].name : `${scoped[0].name} +${scoped.length - 1}`;
}

/* The ranked figure and its noun: active properties, or every ad ever. */
export function metricFigure(r: BrokerLeaderRow, metric: LeaderMetric): { value: number; unit: string } {
  if (metric === 'listing_count') {
    return { value: r.listing_count, unit: czPlural(r.listing_count, 'inzerát', 'inzeráty', 'inzerátů') };
  }
  return {
    value: r.active_property_count,
    unit: czPlural(r.active_property_count, 'nemovitost', 'nemovitosti', 'nemovitostí'),
  };
}

export default function PhoneBrokers() {
  const [q, debounced, setQ] = useDebouncedTerm();
  const searching = q.trim().length > 0;

  return (
    <div className="pt-3">
      <SearchField value={q} onChange={setQ} />
      {searching ? <SearchResults typed={q.trim()} term={debounced} /> : <Leaderboard />}
    </div>
  );
}

function SearchField({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div className="px-4">
      <div className="relative">
        <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-[var(--color-ink-3)]" />
        <input
          ref={ref}
          type="search"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-label="Hledat makléře podle jména"
          placeholder="Hledat makléře podle jména"
          autoComplete="off"
          enterKeyHint="search"
          className="h-11 w-full rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] pl-10 pr-11 text-[16px] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] [&::-webkit-search-cancel-button]:hidden"
        />
        {value && (
          <button
            type="button"
            onClick={() => {
              onChange('');
              ref.current?.focus();
            }}
            aria-label="Vymazat hledání"
            className="absolute right-0 top-0 flex h-11 w-11 items-center justify-center text-[var(--color-ink-3)] active:text-[var(--color-ink)]"
          >
            <ClearIcon className="h-[18px] w-[18px]" />
          </button>
        )}
      </div>
    </div>
  );
}

function SearchResults({ typed, term }: { typed: string; term: string }) {
  const resultsQ = useQuery({
    queryKey: ['broker-name-search', term],
    queryFn: () => searchBrokersByName(term),
    enabled: term.length >= 2,
    staleTime: 60_000,
  });
  const results = resultsQ.data ?? [];

  let body: ReactNode;
  if (typed.length < 2) {
    body = <Hint>Napište aspoň dvě písmena jména.</Hint>;
  } else if (term.length < 2 || resultsQ.isPending) {
    body = (
      <Hint>
        <span role="status">Hledám…</span>
      </Hint>
    );
  } else if (resultsQ.isError) {
    /* A failed search is not an empty one. */
    body = (
      <RollEmpty
        title="Hledání selhalo."
        detail={(resultsQ.error as Error).message}
        action={{ label: 'Zkusit znovu', onClick: () => void resultsQ.refetch() }}
      />
    );
  } else if (results.length === 0) {
    body = <Hint>Nikdo takový — zkuste jiný tvar jména.</Hint>;
  } else {
    body = (
      <ol aria-label="Nalezení makléři" className="border-b border-[var(--color-rule)]">
        {results.map((b) => (
          <li key={b.broker_id}>
            {/* The CZ-scoped count is what the search orders by (migration 396). */}
            <BrokerRow
              id={b.broker_id}
              lead={<Initials name={b.display_name} />}
              name={b.display_name}
              firm={b.firm_name ?? b.firm_domain}
              value={b.cz_active_property_count}
              unit={czPlural(b.cz_active_property_count, 'nemovitost', 'nemovitosti', 'nemovitostí')}
            />
          </li>
        ))}
      </ol>
    );
  }

  return (
    <div className="mt-1">
      <DayRule label="Hledání" />
      {body}
    </div>
  );
}

function Hint({ children }: { children: ReactNode }) {
  return <p className="px-4 pb-3 text-[13px] text-[var(--color-ink-3)]">{children}</p>;
}

function Leaderboard() {
  const [districts, setDistricts] = useState<DistrictChip[]>([]);
  const [categoryMain, setCategoryMain] = useState<string | null>('byt');
  const [categoryType, setCategoryType] = useState<string | null>('prodej');
  const [metric, setMetric] = useState<LeaderMetric>('active_property_count');
  const [placeOpen, setPlaceOpen] = useState(false);

  const geo = useMemo(() => chipsToGeoArrays(districts), [districts]);
  const placeLabel = placeLabelOf(districts);

  const boardQ = useQuery<{ rows: BrokerLeaderRow[]; caption: string }>({
    queryKey: [
      'broker-leaderboard-phone',
      geo.regionIds, geo.okresIds, geo.obecIds,
      categoryMain, categoryType, metric, LIMIT,
    ],
    /* The caption travels with the rows it was fetched under, so the previous
     * filter's rows (kept on screen while the next load runs) are never labelled
     * with the new filter's words. */
    queryFn: ({ signal }) => {
      const caption = [placeLabel, categoryMain && CATEGORY_WORD[categoryMain], categoryType && OFFER_WORD[categoryType]]
        .filter(Boolean)
        .join(' · ');
      return fetchBrokerLeaderboard({ ...geo, categoryMain, categoryType, metric, limit: LIMIT }, signal).then(
        (rows) => ({ rows, caption }),
      );
    },
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
  const rows = boardQ.data?.rows ?? [];

  const pill = (on: boolean) => `${CHIP} ${CHIP_MD} ${chipState(on)}`;
  const placeOn = scopingChips(districts).length > 0;

  return (
    <>
      <div className={`${CHIP_ROW} mt-3`} role="group" aria-label="Výběr">
        <button
          type="button"
          onClick={() => setPlaceOpen(true)}
          aria-haspopup="dialog"
          aria-label={`Místo: ${placeLabel}`}
          className={`${pill(placeOn)} max-w-[60vw]`}
        >
          <PinIcon className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">{placeLabel}</span>
        </button>
        <span aria-hidden className={CHIP_DIVIDER} />
        {CATEGORIES.map((c) => (
          <button
            key={c.value}
            type="button"
            aria-pressed={categoryMain === c.value}
            onClick={() => setCategoryMain(categoryMain === c.value ? null : c.value)}
            className={pill(categoryMain === c.value)}
          >
            {c.label}
          </button>
        ))}
        <span aria-hidden className={CHIP_DIVIDER} />
        {OFFERS.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={categoryType === o.value}
            onClick={() => setCategoryType(categoryType === o.value ? null : o.value)}
            className={pill(categoryType === o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
      <div className={`${CHIP_ROW} mt-2 items-center`} role="group" aria-label="Řadit dle">
        <span className="shrink-0 text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]">
          Řadit
        </span>
        {LEADER_METRIC_OPTIONS.map((m) => (
          <button
            key={m.value}
            type="button"
            aria-pressed={metric === m.value}
            onClick={() => setMetric(m.value)}
            className={`${CHIP} ${CHIP_SM} ${chipState(metric === m.value)}`}
          >
            {m.label}
          </button>
        ))}
      </div>

      <DayRule label={boardQ.data?.caption ?? placeLabel} />

      {boardQ.isPending ? (
        <RowsSkeleton />
      ) : boardQ.isError ? (
        <RollEmpty
          title="Žebříček se nepodařilo načíst."
          detail={boardQ.error.message}
          action={{ label: 'Zkusit znovu', onClick: () => void boardQ.refetch() }}
        />
      ) : rows.length === 0 ? (
        <RollEmpty
          title="Pro tento výběr nikdo."
          detail="Zkuste jiné místo, nebo typ či nabídku pusťte."
        />
      ) : (
        <ol
          aria-label="Žebříček makléřů"
          aria-busy={boardQ.isPlaceholderData || undefined}
          className={`border-b border-[var(--color-rule)] transition-opacity ${boardQ.isPlaceholderData ? 'opacity-60' : ''}`}
        >
          {rows.map((r, i) => {
            const fig = metricFigure(r, metric);
            return (
              <li key={r.broker_id}>
                <BrokerRow
                  id={r.broker_id}
                  lead={
                    <span className="font-mono text-[12px] tabular-nums text-[var(--color-ink-3)]">{i + 1}</span>
                  }
                  name={r.display_name}
                  firm={r.firm_name ?? r.firm_domain}
                  value={fig.value}
                  unit={fig.unit}
                />
              </li>
            );
          })}
        </ol>
      )}
      {!boardQ.isPending && !boardQ.isError && rows.length > 0 && (
        <p className="py-5 text-center text-[0.75rem] tabular-nums text-[var(--color-ink-4)]">
          {rows.length >= LIMIT ? `Prvních ${fmtCount(LIMIT)} makléřů.` : 'Konec žebříčku.'}
        </p>
      )}

      {placeOpen && (
        <BottomSheet label="Místo" onClose={() => setPlaceOpen(false)}>
          <div className="min-h-[70dvh] px-4 pt-3">
            <LocationTypeahead label="Místo" value={districts} onChange={(next) => setDistricts(next ?? [])} />
            <p className="mt-3 text-[12px] text-[var(--color-ink-3)]">
              Kraj, okres nebo obec; bez místa platí celá ČR. Městská část se v žebříčku zatím nepočítá.
            </p>
          </div>
        </BottomSheet>
      )}
    </>
  );
}

/* One line of the ledger: the margin (rank or initials), the person and the
 * firm, the figure. */
function BrokerRow({
  id,
  lead,
  name,
  firm,
  value,
  unit,
}: {
  id: number;
  lead: ReactNode;
  name: string | null;
  firm: string | null;
  value: number;
  unit: string;
}) {
  return (
    <Link
      to={ROUTES.brokerDetail.build({ id })}
      className="flex min-h-16 items-stretch border-t border-[var(--color-rule)] active:bg-[var(--color-paper-2)]"
    >
      <span className="flex w-12 shrink-0 items-center justify-center border-r border-[var(--color-rule-soft)]">
        {lead}
      </span>
      <span className="flex min-w-0 flex-1 items-center gap-3 py-2.5 pl-3 pr-3">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px] text-[var(--color-ink)]">{name ?? 'Neznámý makléř'}</span>
          <span className="mt-0.5 block truncate text-[12px] text-[var(--color-ink-3)]">
            {firm ?? 'nezávislý / neznámá kancelář'}
          </span>
        </span>
        <span className="shrink-0 text-right">
          <span className="block font-mono text-[15px] tabular-nums text-[var(--color-ink)]">{fmtCount(value)}</span>
          <span className="block text-[10px] text-[var(--color-ink-3)]">{unit}</span>
        </span>
        <ChevronIcon className="h-4 w-4 shrink-0 text-[var(--color-ink-4)]" />
      </span>
    </Link>
  );
}

/* "Jana Kubíčková" → "JK", "Ing. Petr Novák, MBA" → "PN": the avatar, in
 * Fraunces. Degrees (a word with a dot, anything after a comma) are not the
 * person's letters. */
export function brokerInitials(name: string | null | undefined): string {
  const words = (name ?? '')
    .split(',')[0]
    .trim()
    .split(/\s+/)
    .filter((w) => w && !w.includes('.'));
  if (words.length === 0) return '?';
  const first = words[0][0] ?? '';
  const last = words.length > 1 ? (words[words.length - 1][0] ?? '') : '';
  return (first + last).toLocaleUpperCase('cs-CZ');
}

export function Initials({ name, size = 'sm' }: { name: string | null | undefined; size?: 'sm' | 'lg' }) {
  return (
    <span
      aria-hidden
      className={[
        'flex shrink-0 items-center justify-center rounded-full border border-[var(--color-rule-strong)] font-display text-[var(--color-ink-2)]',
        size === 'lg' ? 'h-12 w-12 text-[17px]' : 'h-8 w-8 text-[12px]',
      ].join(' ')}
    >
      {brokerInitials(name)}
    </span>
  );
}

function RowsSkeleton() {
  return (
    <>
      <p className="sr-only" role="status">
        Načítám žebříček…
      </p>
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          aria-hidden
          className="flex min-h-16 items-stretch border-t border-[var(--color-rule)] last:border-b"
        >
          <span className="w-12 shrink-0 border-r border-[var(--color-rule-soft)]" />
          <span className="flex flex-1 flex-col justify-center gap-2 px-3">
            <span className="h-4 w-40 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
            <span className="h-3 w-28 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          </span>
        </div>
      ))}
    </>
  );
}
