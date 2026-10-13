/* The pieces every photo roll on the phone shares (#1762): the loading
 * skeleton, the dashed empty/error block and the Suspense boundary. The feed
 * keeps its own day-cut roll (pages/Feed.tsx) and the collection and broker
 * screens draw CardRoll (./CardRoll), but all paint their loading and empty
 * states from here, so the four screens wait and fail in one voice.
 *
 * Kept free of FeedCard on purpose: four desktop pages import PhoneSuspense
 * eagerly, and anything this file imports lands in every first visit. */

import { Suspense, type ReactNode } from 'react';

/* Two card-shaped placeholders: the photo frame and two text lines. */
export function RollSkeleton({ count = 2 }: { count?: number }) {
  return (
    <>
      <p className="sr-only" role="status">
        Načítám…
      </p>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} aria-hidden className="border-t border-[var(--color-rule)]">
          <div className="aspect-[4/3] animate-pulse bg-[var(--color-inset)] motion-reduce:animate-none" />
          <div className="flex flex-col gap-2 px-4 pb-4 pt-3">
            <div className="h-5 w-40 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
            <div className="h-4 w-56 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          </div>
        </div>
      ))}
    </>
  );
}

/* The boundary a phone layout loads behind: the collection and broker screens
 * are their own chunks (a desktop first visit never pays for them), and while
 * one loads — or a stale one recovers by reloading — the roll's skeleton holds. */
export function PhoneSuspense({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="pt-3">
          <RollSkeleton count={1} />
        </div>
      }
    >
      {children}
    </Suspense>
  );
}

/* The dashed block that stands in for a roll with nothing in it, or one that
 * failed to load: a Fraunces line, a muted explanation, at most one action. */
export function RollEmpty({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="mx-4 mb-4 mt-1 flex flex-col items-start gap-2 rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule-strong)] px-4 py-6">
      <p className="font-display text-[17px] text-[var(--color-ink)]">{title}</p>
      <p className="text-[13px] text-[var(--color-ink-3)]">{detail}</p>
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="inline-flex h-9 items-center rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper)] px-3 text-[13px] text-[var(--color-ink-2)] active:bg-[var(--color-inset)]"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
