import { useId, type ReactNode } from 'react';

/* The admin page's card: a ruled paper panel with a small-caps h2 title (Fraunces, as every
 * heading), an optional lede and an accessory at the title's right (a link, a button, a badge).
 * The one panel of Health, Costs, LocationQuality, LocationPinAudit and NewDedupDashboard
 * (wave 10 deleted their four local Card copies). An `id` makes the panel an anchor target,
 * so it clears the sticky h-14 header (scroll-mt-20). */
export default function Panel({
  title,
  lede,
  accessory,
  id,
  children,
}: {
  title: string;
  lede?: ReactNode;
  accessory?: ReactNode;
  id?: string;
  children: ReactNode;
}) {
  const titleId = useId();
  return (
    <section
      id={id}
      aria-labelledby={titleId}
      className="scroll-mt-20 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-5 py-4"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 id={titleId} className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)] font-medium">
          {title}
        </h2>
        {accessory ?? null}
      </div>
      {lede ? (
        <p className="mt-2 text-[0.78rem] leading-relaxed text-[var(--color-ink-2)] max-w-[52rem]">{lede}</p>
      ) : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}
