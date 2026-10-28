import { useId, type ReactNode } from 'react';

/* The section card (operator ruling 2026-10-28, `.interface-design/system.md` § Section cards, C1–C10):
 * every section of a detail page and every admin panel is one of these. A title bar (L2: Fraunces 600
 * 17/24) holds the title, a bare count, an optional note and the actions pushed right, over a rule-soft
 * line; the body stacks its blocks 12 px apart. The count sits OUTSIDE the h2, so the region keeps the
 * title as its name. An `id` makes the card an anchor target that clears the sticky h-14 header. */
export default function Panel({
  title,
  count,
  note,
  accessory,
  lede,
  id,
  className = '',
  children,
}: {
  title: ReactNode;
  /* A bare number (no brackets); 0 is drawn, null/undefined is not. */
  count?: number | string | null;
  note?: ReactNode;
  accessory?: ReactNode;
  /* The body's first line. */
  lede?: ReactNode;
  id?: string;
  className?: string;
  children?: ReactNode;
}) {
  const titleId = useId();
  const right = note != null || accessory != null;
  return (
    <section
      id={id}
      aria-labelledby={titleId}
      className={`scroll-mt-20 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] ${className}`.trim()}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-[var(--color-rule-soft)] px-4 pt-[13px] pb-3 sm:px-5">
        <h2 id={titleId} className="font-display text-[17px] leading-6 font-semibold text-[var(--color-ink)]">
          {title}
        </h2>
        {count != null ? (
          <span className="font-mono text-[13px] tabular-nums text-[var(--color-ink-3)]">{count}</span>
        ) : null}
        {right ? (
          <div className="ml-auto flex flex-wrap items-baseline justify-end gap-x-3 gap-y-1">
            {note != null ? <span className="text-xs text-[var(--color-ink-3)]">{note}</span> : null}
            {accessory ?? null}
          </div>
        ) : null}
      </div>
      <div className="space-y-3 px-4 pt-4 pb-[18px] sm:px-5">
        {lede ? (
          <p className="text-[0.78rem] leading-relaxed text-[var(--color-ink-2)] max-w-[52rem]">{lede}</p>
        ) : null}
        {children}
      </div>
    </section>
  );
}

/* A group inside a card (C7, L3: Inter 600 14/20, ink): a title over its blocks, a rule-soft line above
 * every group but the first. Never a bordered or dashed box. */
export function PanelGroup({
  title,
  count,
  accessory,
  children,
}: {
  title: ReactNode;
  count?: number | string | null;
  accessory?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="space-y-2 border-t border-[var(--color-rule-soft)] pt-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm leading-5 font-semibold text-[var(--color-ink)]">{title}</h3>
        {count != null ? (
          <span className="font-mono text-xs tabular-nums text-[var(--color-ink-3)]">{count}</span>
        ) : null}
        {accessory != null ? <div className="ml-auto flex items-baseline gap-3">{accessory}</div> : null}
      </div>
      {children}
    </div>
  );
}

/* An empty section's body (C10): one muted line, then its action. The card, its title and its count 0 stay. */
export function PanelEmpty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <p className="text-sm text-[var(--color-ink-3)]">{children}</p>
      {action ?? null}
    </div>
  );
}
