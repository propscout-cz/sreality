import type { ReactNode } from 'react';

/* One row of a tab's settings panel (Browse › Statistiky, Browse › Makléři): a
 * tracked small-caps label naming what the row sets, stacked over its controls
 * while the PANEL is narrow. The panel (an `@container`) decides, not the
 * screen: the filter sidebar stays open below lg, and a dashboard chip (up to
 * ~20rem: a 16rem name plus grip and ⋯) never shrinks, so the column waits for
 * a cell that holds one. A plain label, not a heading or a group: the content
 * under the panel carries the headings, and each control names its own group. */
export default function SettingsRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-y-1.5 py-3 @min-[30rem]:grid-cols-[6.5rem_minmax(0,1fr)] @min-[30rem]:items-baseline @min-[30rem]:gap-x-4">
      <span className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--color-ink-3)]">{label}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
