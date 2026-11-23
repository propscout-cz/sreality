/* The photo-size switch, as N steps: the app's one pick-one bar (SegmentedBar,
 * `hug`) with a glyph per step, so a page offering three steps (the
 * deal-pipeline board: small, medium, large) and the two-step small/large
 * version (`ImageSizeToggle`, five grids) draw the same control as every other
 * pick-one. Presentation only: the caller owns the persisted preference and
 * what each step does to its own grid. */

import type { ReactNode } from 'react';

import { SegmentedBar } from '@/components/controls';

export interface SizeStep<T extends string> {
  value: T;
  label: string;
  /* Native-title hover box: what this step actually does to THIS grid. */
  title?: string;
  glyph?: ReactNode;
}

interface Props<T extends string> {
  value: T;
  onChange: (v: T) => void;
  steps: readonly SizeStep<T>[];
  /* What this switch sizes, for the a11y group name — "Card image size",
   * "Velikost karet". Each surface has exactly one, so the label is what tells
   * a screen-reader user which grid they are about to reshape. */
  label: string;
}

export default function SizeToggle<T extends string>({
  value,
  onChange,
  steps,
  label,
}: Props<T>) {
  return <SegmentedBar hug label={label} options={steps} value={value} onChange={onChange} />;
}

/* Many small photo frames — the smallest step. */
export function SmallImageGlyph() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="1.5" y="2" width="3.5" height="2.8" rx="0.6" />
      <rect x="6.5" y="2" width="3.5" height="2.8" rx="0.6" />
      <rect x="11.5" y="2" width="3" height="2.8" rx="0.6" />
      <rect x="1.5" y="6.4" width="3.5" height="2.8" rx="0.6" />
      <rect x="6.5" y="6.4" width="3.5" height="2.8" rx="0.6" />
      <rect x="11.5" y="6.4" width="3" height="2.8" rx="0.6" />
      <rect x="1.5" y="10.8" width="3.5" height="2.8" rx="0.6" />
      <rect x="6.5" y="10.8" width="3.5" height="2.8" rx="0.6" />
      <rect x="11.5" y="10.8" width="3" height="2.8" rx="0.6" />
    </svg>
  );
}

/* Four bigger frames — the middle step. Only offered where there are three. */
export function MediumImageGlyph() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="1.5" y="2" width="5.5" height="4.5" rx="0.8" />
      <rect x="9" y="2" width="5.5" height="4.5" rx="0.8" />
      <rect x="1.5" y="9" width="5.5" height="4.5" rx="0.8" />
      <rect x="9" y="9" width="5.5" height="4.5" rx="0.8" />
    </svg>
  );
}

/* One big photo frame — the largest step. */
export function LargeImageGlyph() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="1.5" y="2.5" width="13" height="8.5" rx="1.2" />
      <path d="M1.5 8.5 L5.5 5.5 L8.5 8 L11 6 L14.5 9" />
    </svg>
  );
}
