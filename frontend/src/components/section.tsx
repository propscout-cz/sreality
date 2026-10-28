import type { ReactNode } from 'react';

/* The section card (components/Panel) separates a page's sections now (operator ruling 2026-10-28,
 * system.md § Section cards). What is left here: the hairline inside a dialog, and the L4 field label —
 * small caps 11/14, weight 600, 0.08em, ink-3, only above a value, a control or a table column. */

export function Hairline({ tight = false }: { tight?: boolean }) {
  return <div className={`${tight ? 'my-5' : 'my-7'} h-px bg-[var(--color-rule)]`} />;
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="text-[11px] leading-[14px] font-semibold tracking-[0.08em] uppercase text-[var(--color-ink-3)]">
      {children}
    </p>
  );
}
