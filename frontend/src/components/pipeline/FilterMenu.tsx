/* The pipeline bar's dropdown filters (Typ, Kolekce, Štítky) and the row of
 * active filters under it (operator's variant A, 2026-10-22). A menu names its
 * filter and how many of its choices are on, and opens the very control the bar
 * used to draw inline (MultiselectChips, CuratedSetFilter), so a choice means
 * what it meant; the bar just no longer grows with the account's collections
 * and tags. The row names every choice that is on, each with its own ×. */

import { useId, useRef, useState, type ReactNode } from 'react';

import AnchoredPopover from '@/components/AnchoredPopover';

export function FilterMenu({
  label,
  count,
  failed = false,
  children,
}: {
  label: string;
  /* Choices on; 0 draws the bare label. */
  count: number;
  /* The menu's read failed: its panel holds the retry, the button says so. */
  failed?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const on = count > 0;
  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className={[
          'inline-flex items-center gap-1.5 h-8 px-2.5 rounded-[var(--radius-sm)] border text-[0.8rem] whitespace-nowrap transition-colors',
          on
            ? 'border-[var(--color-copper)] bg-[var(--color-copper-soft)] text-[var(--color-copper)] font-medium'
            : 'border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)] hover:text-[var(--color-ink)]',
        ].join(' ')}
      >
        {on ? `${label} · ${count}` : label}
        {failed && (
          <span aria-hidden className="text-[var(--color-brick)]" title={`${label} se nepodařilo načíst`}>
            !
          </span>
        )}
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
          <path d="M2 3.5 5 6.5l3-3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <AnchoredPopover
          anchorRef={anchorRef}
          id={panelId}
          ariaLabel={label}
          onClose={() => setOpen(false)}
          className="w-[22rem] max-w-[calc(100vw-1rem)] p-3"
        >
          {children}
        </AnchoredPopover>
      )}
    </>
  );
}

export interface ActiveFilter {
  key: string;
  label: string;
  /* An exclusion ("− name", "V žádné kolekci"): brick, not copper. */
  out?: boolean;
  onRemove: () => void;
}

export function ActiveFilters({ filters }: { filters: readonly ActiveFilter[] }) {
  if (filters.length === 0) return null;
  return (
    <ul aria-label="Aktivní filtry" className="mt-2.5 flex flex-wrap gap-1.5">
      {filters.map((f) => (
        <li
          key={f.key}
          className={`inline-flex items-center gap-1 rounded-full pl-2.5 pr-1 py-0.5 text-[0.75rem] ${
            f.out
              ? 'bg-[var(--color-brick-soft)] text-[var(--color-brick)]'
              : 'bg-[var(--color-copper-soft)] text-[var(--color-copper)]'
          }`}
        >
          <span>{f.label}</span>
          <button
            type="button"
            onClick={f.onRemove}
            aria-label={`Zrušit filtr ${f.label}`}
            className="grid place-items-center w-4 h-4 rounded-full hover:bg-[var(--color-paper-3)]"
          >
            <svg width="8" height="8" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
              <path d="M2 2l6 6M8 2l-6 6" strokeLinecap="round" />
            </svg>
          </button>
        </li>
      ))}
    </ul>
  );
}
