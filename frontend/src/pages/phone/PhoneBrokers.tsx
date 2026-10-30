/* Makléři on the phone (#1769): the broker board (lib/brokerBoard) over the
 * URL's Browse filters, edited in the Filtry sheet's own body (FilterSheetBody)
 * — the same request the desktop's Makléři tab sends — plus the name search.
 *
 * The roll is a ruled notebook page: the rank sits in the left margin behind a
 * hairline, like line numbers, the person and the firm in the body, the volume
 * in mono at the right edge with the speed under it. Typing a name swaps the
 * roll for the search results in place (a dropdown has no room on a phone, and
 * the keyboard covers half the screen); clearing the field brings it back. */

import { Suspense, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { CoverageLine, IgnoredNote, MarketLine, RefusalLine, SpeedLegend, sharePct } from '@/components/brokers/BoardNotes';
import BottomSheet from '@/components/phone/BottomSheet';
import DayRule from '@/components/phone/DayRule';
import { RollEmpty } from '@/components/phone/RollParts';
import { SegmentedBar } from '@/components/controls';
import { CHIP, CHIP_MD, CHIP_ROW, NO_FOCUS_ZOOM, chipState } from '@/components/phone/chips';
import { filtersBeyond } from '@/components/phone/filterRows';
import { ChevronIcon, ClearIcon, SearchIcon, SlidersIcon } from '@/components/phone/phoneIcons';
import { placeLabelOf } from '@/components/phone/placeLabel';
import { BOARD_HORIZONS, shownSort, speedOf, useBrokerBoard, type BoardSort } from '@/lib/brokerBoard';
import { searchBrokersByName } from '@/lib/brokers';
import { useUrlBrowseState, type BrowseViewState } from '@/lib/browseState';
import { categoryMainLabelPlural, categoryTypeLabel } from '@/lib/enums';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import { czPlural, fmtCount } from '@/lib/format';
import { lazyChunk } from '@/lib/lazyChunk';
import { ROUTES } from '@/lib/routes';
import { useDebouncedTerm } from '@/lib/useDebouncedTerm';

const FilterSheetBody = lazyChunk(() =>
  import('@/components/phone/FilterSheet').then((m) => ({ default: m.FilterSheetBody })),
);

const HORIZON_OPTIONS = BOARD_HORIZONS.map((h) => ({ value: h, label: `${h} dní` }));
/* A bar's equal columns are minmax(0, 1fr), so the sideways-scrolling chip row
 * could squeeze it below its labels; the slot keeps it whole, as a chip is. */
const BAR_SLOT = 'flex shrink-0';
const NOTE = 'px-4 pb-2 text-[12px] leading-[1.45] text-[var(--color-ink-3)]';

const PROPERTY_NOUN = ['nemovitost', 'nemovitosti', 'nemovitostí'] as const;

export default function PhoneBrokers() {
  const view = useUrlBrowseState();
  const [q, debounced, setQ] = useDebouncedTerm();
  const searching = q.trim().length > 0;

  return (
    <div className="pt-3">
      <SearchField value={q} onChange={setQ} />
      {searching && <SearchResults typed={q.trim()} term={debounced} />}
      {/* Hidden, not unmounted, while a name is typed: the board is still there
        * when the search is cleared. */}
      <div hidden={searching}>
        <Board view={view} />
      </div>
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
      <>
        <UnitHead unit={PROPERTY_NOUN[2]} />
        <ol aria-label="Nalezení makléři" className="border-b border-[var(--color-rule)]">
          {results.map((b) => (
            <li key={b.broker_id}>
              {/* The live properties the search orders by. */}
              <BrokerRow
                id={b.broker_id}
                lead={<Initials name={b.display_name} />}
                name={b.display_name}
                firm={b.firm_name ?? b.firm_domain}
                value={b.active_properties}
                unit={czPlural(b.active_properties, ...PROPERTY_NOUN)}
              />
            </li>
          ))}
        </ol>
      </>
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

/* The figures' unit, said once over their column (P28). pr-10 is the row's pr-3
 * plus the chevron and its gap, so the word ends where the figures end; each row
 * keeps its unit as sr-only text, so this is for the eye only. */
function UnitHead({ unit }: { unit: string }) {
  return (
    <p aria-hidden className="pb-1.5 pr-10 text-right text-[11px] text-[var(--color-ink-3)]">
      {unit}
    </p>
  );
}

/* "Celá ČR · byty · prodej": the place and the kind the board counts; the
 * Filtry chip's count says what else is set. */
function caption(f: ListingFilters): string {
  const kinds = f.categoryMain.map((c) => categoryMainLabelPlural(c).toLocaleLowerCase('cs-CZ')).join(' + ');
  const deal = f.categoryType && categoryTypeLabel(f.categoryType).toLocaleLowerCase('cs-CZ');
  return [placeLabelOf(f.districts), kinds, deal].filter(Boolean).join(' · ');
}

function Board({ view }: { view: BrowseViewState }) {
  const { filters, board } = view;
  const [sheetOpen, setSheetOpen] = useState(false);
  const boardQ = useBrokerBoard(filters, board);
  const beyond = filtersBeyond(filters, DEFAULT_FILTERS);
  const result = boardQ.data;
  const sortOptions: ReadonlyArray<{ value: BoardSort; label: string; disabled?: boolean }> = [
    { value: 'properties', label: 'Nemovitosti' },
    { value: 'speed', label: 'Rychlost', disabled: result?.meta.speed_refused != null },
  ];

  return (
    <>
      <div className={`${CHIP_ROW} mt-3`} role="group" aria-label="Výběr">
        <button
          type="button"
          onClick={() => setSheetOpen(true)}
          aria-haspopup="dialog"
          aria-label={beyond > 0 ? `Filtry (${beyond} navíc)` : 'Filtry'}
          className={`${CHIP} ${CHIP_MD} ${chipState(beyond > 0)}`}
        >
          <SlidersIcon className="h-3.5 w-3.5" />
          Filtry
          {beyond > 0 && <span className="font-mono text-[11px] tabular-nums text-[var(--color-ink)]">{beyond}</span>}
        </button>
        <div className={BAR_SLOT}>
          <SegmentedBar
            tone="phone"
            label="Řadit dle"
            options={sortOptions}
            value={shownSort(board, result?.meta)}
            onChange={(sort) => view.setBoard({ sort })}
          />
        </div>
        <div className={BAR_SLOT}>
          <SegmentedBar
            tone="phone"
            label="Opustilo trh do (dní)"
            options={HORIZON_OPTIONS}
            value={board.horizonDays}
            onChange={(horizonDays) => view.setBoard({ horizonDays })}
          />
        </div>
      </div>

      <DayRule label={caption(filters)} />
      <IgnoredNote filters={filters} className={NOTE} />

      {boardQ.isPending ? (
        <RowsSkeleton />
      ) : boardQ.isError ? (
        <RollEmpty
          title="Žebříček se nepodařilo načíst."
          detail={boardQ.error.message}
          action={{ label: 'Zkusit znovu', onClick: () => void boardQ.refetch() }}
        />
      ) : (
        <>
          <CoverageLine meta={boardQ.data.meta} filters={filters} className={NOTE} />
          <MarketLine meta={boardQ.data.meta} className={NOTE} />
          <RefusalLine meta={boardQ.data.meta} className={`${NOTE} text-[var(--color-ink-2)]`} />
          {boardQ.data.rows.length === 0 ? (
            <RollEmpty title="Pro tento výběr nikdo." detail="Zkuste ve Filtrech širší výběr." />
          ) : (
            <>
              <UnitHead unit={PROPERTY_NOUN[2]} />
              <ol
                aria-label="Žebříček makléřů"
                aria-busy={boardQ.isPlaceholderData || undefined}
                className={`border-b border-[var(--color-rule)] transition-opacity ${boardQ.isPlaceholderData ? 'opacity-60' : ''}`}
              >
                {boardQ.data.rows.map((r, i) => {
                  const s = speedOf(r, boardQ.data.meta);
                  return (
                    <li key={r.broker_id}>
                      <BrokerRow
                        id={r.broker_id}
                        lead={
                          <span className="font-mono text-[12px] tabular-nums text-[var(--color-ink-3)]">{i + 1}</span>
                        }
                        name={r.display_name}
                        firm={r.firm_name ?? r.firm_domain}
                        value={r.properties}
                        unit={czPlural(r.properties, ...PROPERTY_NOUN)}
                        speed={
                          s.kind === 'share' ? (
                            <>
                              {fmtCount(s.exited)} z {fmtCount(s.n)} · {sharePct(s.share)}
                            </>
                          ) : s.kind === 'small' && s.n > 0 ? (
                            <>n = {fmtCount(s.n)}</>
                          ) : null
                        }
                      />
                    </li>
                  );
                })}
              </ol>
              <p className="py-5 text-center text-[0.75rem] tabular-nums text-[var(--color-ink-3)]">
                {boardQ.data.rows.length >= board.limit
                  ? `Prvních ${fmtCount(board.limit)} makléřů.`
                  : 'Konec žebříčku.'}
              </p>
            </>
          )}
          <SpeedLegend meta={boardQ.data.meta} className={`${NOTE} pb-5`} />
        </>
      )}

      {sheetOpen && (
        <BoardFilterSheet filters={filters} onApply={view.setFilters} onClose={() => setSheetOpen(false)} />
      )}
    </>
  );
}

/* Browse's whole filter set over a draft: "Zobrazit makléře" applies it to the
 * URL, "Zavřít" lets it go. The order and the presets are the Feed's. */
function BoardFilterSheet({
  filters,
  onApply,
  onClose,
}: {
  filters: ListingFilters;
  onApply: (next: ListingFilters) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(filters);
  const footer = (
    <div className="flex items-center gap-2 px-4">
      <button
        type="button"
        onClick={() => setDraft(DEFAULT_FILTERS)}
        disabled={filtersBeyond(draft, DEFAULT_FILTERS) === 0}
        className="h-11 shrink-0 px-1 text-[15px] text-[var(--color-ink-2)] disabled:text-[var(--color-ink-4)]"
      >
        Vymazat
      </button>
      <button
        type="button"
        onClick={() => {
          onApply(draft);
          onClose();
        }}
        className="h-11 min-w-0 flex-1 truncate rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 text-[15px] font-medium text-[var(--color-on-copper)] active:bg-[var(--color-copper-2)]"
      >
        Zobrazit makléře
      </button>
    </div>
  );
  return (
    <BottomSheet label="Filtry" closeLabel="Zavřít" onClose={onClose} footer={footer}>
      <div className={`pb-6 ${NO_FOCUS_ZOOM}`}>
        <Suspense
          fallback={
            <p role="status" className="px-4 py-3 text-[14px] text-[var(--color-ink-3)]">
              Načítám filtry…
            </p>
          }
        >
          <FilterSheetBody draft={draft} setDraft={setDraft} />
        </Suspense>
      </div>
    </BottomSheet>
  );
}

/* One line of the ledger: the margin (rank or initials), the person and the
 * firm, the figure, and on the board the speed under it ("k z n · share"). */
function BrokerRow({
  id,
  lead,
  name,
  firm,
  value,
  unit,
  speed,
}: {
  id: number;
  lead: ReactNode;
  name: string | null;
  firm: string | null;
  value: number;
  unit: string;
  speed?: ReactNode;
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
          <span className="block font-mono text-[15px] tabular-nums text-[var(--color-ink)]">
            {fmtCount(value)}
            <span className="sr-only"> {unit}</span>
          </span>
          {speed && (
            <span className="mt-0.5 block font-mono text-[11px] tabular-nums text-[var(--color-ink-3)]">
              <span className="sr-only">opustilo trh: </span>
              {speed}
            </span>
          )}
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
