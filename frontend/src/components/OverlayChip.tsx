import { createContext, useContext, useEffect, useRef, type ReactNode } from 'react';

import { usePersistedFlag } from '@/lib/persistedFlag';

/* The floating paper every map-overlay control sits on. */
const SURFACE =
  'pointer-events-auto rounded-[var(--radius-sm)] bg-[var(--color-paper-3)]/95 backdrop-blur-sm border border-[var(--color-rule)] shadow-[0_2px_6px_rgba(0,0,0,0.04)]';

/* Where a layer list is drawn: a web map's Vrstvy panel, or the phone's Vrstvy sheet,
 * whose rows read at the sheet's 15 px and are at least 44 px tall (a thumb's reach). */
export type MapControlsTone = 'web' | 'phone';

export const MapControlsToneContext = createContext<MapControlsTone>('web');

export const TONE_TEXT: Record<
  MapControlsTone,
  { label: string; sub: string; note: string; foot: string; hint: string; box: string; indent: string }
> = {
  web: {
    label: 'text-[0.8rem] text-[var(--color-ink)]',
    sub: 'text-[0.75rem] text-[var(--color-ink-2)]',
    note: 'text-[0.65rem]',
    foot: 'text-[0.62rem]',
    hint: 'text-[0.68rem]',
    box: '',
    indent: 'ml-5 mb-1.5',
  },
  phone: {
    label: 'min-h-11 text-[15px] text-[var(--color-ink-2)]',
    sub: 'min-h-10 text-[14px] text-[var(--color-ink-2)]',
    note: 'text-[12px]',
    foot: 'text-[12px]',
    hint: 'text-[13px]',
    box: 'h-5 w-5 shrink-0',
    indent: 'ml-7 mb-2',
  },
};

export const useMapControlsTone = () => TONE_TEXT[useContext(MapControlsToneContext)];

/* One map-layer toggle: a checkbox and its label — a row in a Vrstvy panel, or,
 * `compact`, a chip of its own on the small maps. `indeterminate` is the
 * tri-state of a group whose members are only partly on. */
export function OverlayChip({
  label,
  checked,
  onChange,
  indeterminate = false,
  count,
  compact = false,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  indeterminate?: boolean;
  count?: number;
  compact?: boolean;
}) {
  const tone = useMapControlsTone();
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (inputRef.current) inputRef.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <div className={compact ? `${SURFACE} flex items-center gap-2 px-2 py-1` : 'flex items-center py-1'}>
      <label
        className={`inline-flex items-center gap-1.5 ${compact ? 'text-[0.7rem] text-[var(--color-ink-2)]' : tone.label} cursor-pointer`}
      >
        <input
          ref={inputRef}
          type="checkbox"
          className={compact ? undefined : tone.box || undefined}
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span>{label}</span>
        {count != null && (
          <span className="text-[var(--color-ink-3)] tabular-nums">({count})</span>
        )}
      </label>
    </div>
  );
}

/* A layer's settings, indented under its toggle while the layer is on. */
export function OverlayPanel({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  const tone = useMapControlsTone();
  return <div className={`${tone.indent} flex flex-col gap-2 ${className}`}>{children}</div>;
}

/* A drawn layer's key, on the map itself so it reads with the panel closed —
 * or, `inline`, in the page under a map too small to carry it (the phone's Poloha). */
export function MapLegendCard({
  title,
  inline = false,
  children,
}: {
  title: ReactNode;
  inline?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`${inline ? 'w-full' : `${SURFACE} w-[200px] px-2.5 py-1.5`} flex flex-col gap-1`}>
      <span className="text-[0.7rem] font-medium text-[var(--color-ink)] leading-snug">{title}</span>
      {children}
    </div>
  );
}

/* Whether the map's layer keys are folded to their button (per browser, every map). */
const LEGENDS_FOLDED_KEY = 'sreality.browse.mapLegendsFolded';

/* Bottom-left, over the scale: the keys of every layer that is on, so a choropleth
 * reads with the panel closed. It folds to one button (and stays folded in this
 * browser) when the map is too narrow to spare it. `centreMark`: an area card reads
 * the map's centre, so a cross marks it while the keys show. Place it in the map's
 * root, beside the map's container. */
export function MapLegendDock({ centreMark = false, children }: { centreMark?: boolean; children: ReactNode }) {
  const folded = usePersistedFlag(LEGENDS_FOLDED_KEY, false);
  return (
    <>
      {centreMark && !folded.value && <CentreMark />}
      <div className="pointer-events-none absolute z-[3] bottom-9 left-2 flex flex-col gap-1.5 items-start max-h-[45%] max-w-[calc(100%-4rem)]">
        {!folded.value && (
          <div className="pointer-events-auto flex flex-col gap-1.5 items-start min-h-0 overflow-y-auto">
            {children}
          </div>
        )}
        <button
          type="button"
          onClick={folded.toggle}
          aria-expanded={!folded.value}
          className="pointer-events-auto shrink-0 inline-flex items-center gap-1 px-2 py-0.5 text-[0.7rem] rounded-[var(--radius-sm)] bg-[var(--color-paper-3)]/95 backdrop-blur-sm border border-[var(--color-rule)] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] shadow-[0_2px_6px_rgba(0,0,0,0.04)]"
        >
          {folded.value ? 'Ukázat legendu' : 'Skrýt legendu'}
        </button>
      </div>
    </>
  );
}

/* A small cross at the map's centre, over the tiles and under every control. */
export function CentreMark() {
  return (
    <svg
      aria-hidden="true"
      width="22"
      height="22"
      viewBox="0 0 22 22"
      className="pointer-events-none absolute z-[2] left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
    >
      <path d="M1 11h6M15 11h6M11 1v6M11 15v6" stroke="#ffffff" strokeWidth="4" strokeLinecap="round" />
      <path d="M1 11h6M15 11h6M11 1v6M11 15v6" stroke="#16181d" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
