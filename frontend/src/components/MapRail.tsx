/* Browse's map control rail: a column of icon buttons on the map's right edge,
 * each opening one panel beside it. On a map narrower than 520 px the panel
 * rises from the bottom instead (the map root is the `@container`). One panel
 * at a time; a click outside the rail, Escape, or the same button closes it.
 * Everything else stays off the map, which is the point: the controls used to
 * sit in all four corners and covered a quarter of a 450 px map. */

import { useEffect, useRef, useState, type ReactNode } from 'react';

const SURFACE =
  'pointer-events-auto rounded-[var(--radius-sm)] bg-[var(--color-paper-3)]/95 backdrop-blur-sm border border-[var(--color-rule)] shadow-[0_2px_6px_rgba(0,0,0,0.06)]';

/* The open panel and the element a click must land in to keep it open. */
export function useRailPanel<P extends string>() {
  const [open, setOpen] = useState<P | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open == null) return;
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(null);
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return {
    open,
    rootRef,
    toggle: (panel: P) => setOpen((cur) => (cur === panel ? null : panel)),
    close: () => setOpen(null),
  };
}

export function RailGroup({ children }: { children: ReactNode }) {
  return <div className={`${SURFACE} flex flex-col overflow-hidden divide-y divide-[var(--color-rule)]`}>{children}</div>;
}

/* One rail button. `label` is its name and tooltip; `pressed` marks a toggle
 * that is on, `expanded` a panel that is open; `badge` counts what is on. */
export function RailButton({
  label,
  onClick,
  pressed,
  expanded,
  badge,
  children,
}: {
  label: string;
  onClick: () => void;
  pressed?: boolean;
  expanded?: boolean;
  badge?: number;
  children: ReactNode;
}) {
  const lit = pressed || expanded;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      aria-expanded={expanded}
      className={`relative w-9 h-9 grid place-items-center transition-colors ${
        lit
          ? 'bg-[var(--color-copper-soft)] text-[var(--color-copper)]'
          : 'text-[var(--color-ink)] hover:bg-[var(--color-inset)]'
      }`}
    >
      {children}
      {badge ? (
        <span
          aria-hidden="true"
          className="absolute top-0.5 right-0.5 min-w-3.5 h-3.5 px-0.5 rounded-full bg-[var(--color-copper)] text-[var(--color-paper-3)] text-[9px] leading-[14px] font-semibold tabular-nums text-center"
        >
          {badge}
        </span>
      ) : null}
    </button>
  );
}

/* The panel a rail button opens: beside the rail, or a bottom sheet on a narrow map. */
export function RailFlyout({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className={[
        'pointer-events-auto absolute z-30 top-2 right-[52px] w-[300px] max-w-[calc(100%-4.5rem)] max-h-[calc(100%-1rem)] overflow-y-auto',
        'rounded-[var(--radius-md)] bg-[var(--color-paper-3)] border border-[var(--color-rule)] shadow-[0_10px_30px_rgba(0,0,0,0.16)] px-3 pt-2 pb-3',
        '@max-[520px]:top-auto @max-[520px]:left-0 @max-[520px]:right-0 @max-[520px]:bottom-0 @max-[520px]:w-auto @max-[520px]:max-w-none @max-[520px]:max-h-[70%] @max-[520px]:rounded-b-none @max-[520px]:rounded-t-xl',
      ].join(' ')}
    >
      <div className="flex items-center justify-between gap-2 pb-1">
        <h3 className="text-[0.8rem] font-semibold text-[var(--color-ink)]">{title}</h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="Zavřít"
          className="w-6 h-6 grid place-items-center rounded-[var(--radius-sm)] text-[var(--color-ink-3)] hover:bg-[var(--color-inset)] hover:text-[var(--color-ink)]"
        >
          <Icon d="M4 4l8 8M12 4l-8 8" />
        </button>
      </div>
      {children}
    </section>
  );
}

function Icon({ d, children }: { d?: string; children?: ReactNode }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {d ? <path d={d} /> : children}
    </svg>
  );
}

export const LayersIcon = () => (
  <Icon>
    <path d="M8 2.2 14 5.4 8 8.6 2 5.4Z" />
    <path d="M2 8.3 8 11.5 14 8.3" />
    <path d="M2 11.1 8 14.3 14 11.1" />
  </Icon>
);
export const SlidersIcon = () => (
  <Icon>
    <path d="M2.5 4.5h6.5M12.5 4.5h1M2.5 11.5h1.5M7 11.5h6.5" />
    <circle cx="10.7" cy="4.5" r="1.6" />
    <circle cx="5.5" cy="11.5" r="1.6" />
  </Icon>
);
export const PlusIcon = () => <Icon d="M8 3v10M3 8h10" />;
export const MinusIcon = () => <Icon d="M3 8h10" />;
export const ShowAllIcon = () => <Icon d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" />;
