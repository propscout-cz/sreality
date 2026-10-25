import { useEffect, useRef, type ReactNode } from 'react';

/* The floating paper every map-overlay control sits on. */
const SURFACE =
  'pointer-events-auto rounded-[var(--radius-sm)] bg-[var(--color-paper-3)]/95 backdrop-blur-sm border border-[var(--color-rule)] shadow-[0_2px_6px_rgba(0,0,0,0.04)]';

/* One map-layer toggle: a checkbox and its label — a row in Browse's Vrstvy
 * panel, or, `compact`, a chip of its own on the small maps. `indeterminate`
 * is the tri-state of a group whose members are only partly on. */
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
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (inputRef.current) inputRef.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <div className={compact ? `${SURFACE} flex items-center gap-2 px-2 py-1` : 'flex items-center py-1'}>
      <label
        className={`inline-flex items-center gap-1.5 ${compact ? 'text-[0.7rem] text-[var(--color-ink-2)]' : 'text-[0.8rem] text-[var(--color-ink)]'} cursor-pointer`}
      >
        <input
          ref={inputRef}
          type="checkbox"
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
  return <div className={`ml-5 mb-1.5 flex flex-col gap-2 ${className}`}>{children}</div>;
}

/* A drawn layer's key, on the map itself so it reads with the panel closed. */
export function MapLegendCard({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <div className={`${SURFACE} w-[200px] flex flex-col gap-1 px-2.5 py-1.5`}>
      <span className="text-[0.7rem] font-medium text-[var(--color-ink)] leading-snug">{title}</span>
      {children}
    </div>
  );
}
