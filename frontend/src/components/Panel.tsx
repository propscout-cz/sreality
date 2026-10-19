import { useId, type ReactNode } from 'react';

/* The admin page's card: a ruled paper panel with a small-caps title (Fraunces, as every
 * heading), an optional lede and an accessory at the title's right (a link, a button, a badge).
 * The local Card copies (Costs, LocationPinAudit, NewDedupDashboard) move onto it in wave 10;
 * `as` exists because Costs titles its cards h3 and the others h2. An `id` makes the panel an
 * anchor target, so it clears the sticky h-14 header (scroll-mt-20). */
export default function Panel({
  as: Heading = 'h2',
  title,
  lede,
  accessory,
  id,
  children,
}: {
  as?: 'h2' | 'h3';
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
        <Heading id={titleId} className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)] font-medium">
          {title}
        </Heading>
        {accessory ?? null}
      </div>
      {lede ? (
        <p className="mt-2 text-[0.78rem] leading-relaxed text-[var(--color-ink-2)] max-w-[52rem]">{lede}</p>
      ) : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}
