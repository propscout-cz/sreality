/* The stage wash over a filed property's photo — the extension's mark
 * (`chrome-extension/src/index_photo_mark.ts`) at the quiet values its hover
 * lifts to, so the photo reads first (operator ruling 2026-10-22): the photo
 * toned in the stage colour (`mix-blend-mode: color` at 0.25), deepened toward
 * it (`multiply` at 0.15), a hairline frame 10 px in at white 0.45, the badge
 * and the stage's name beneath at the extension's sizes, with its soft text
 * shadow — the prototype (#1762) dropped it under the old deep wash, but at 0.15
 * the multiply no longer darkens a sky or a white wall behind the white type.
 *
 * Purely a mark (aria-hidden): the funnel in the card's margin carries the stage
 * in its label. The colour is `stageAccent` (the operator's, copper when none),
 * the badge `stageBadge` (the stage's code, else its ordinal) — both computed by
 * the caller from the one shared membership read.
 *
 * Two sizes, one set of values: `lg` over the feed card's full-width photo and
 * `sm` over the pipeline card's 104 px thumbnail, where the frame steps in 5 px
 * and the caller passes no label (the badge alone). The property page's gallery
 * wears none: there the stage is the curation row's.
 *
 * The parent must isolate (`isolation: isolate`) so the blend stops at the photo. */

import type { CSSProperties } from 'react';

const ON_WASH = '#ffffff';
const TEXT_SHADOW = '0 1px 12px rgba(0, 0, 0, 0.3)';

export interface StageWashProps {
  /* A CSS colour — stageAccent(...).fg. */
  color: string;
  badge: string | null;
  label: string | null;
  /* Keep the badge clear of a column laid over the photo's right edge (px). */
  insetRight?: number;
  size?: 'lg' | 'sm';
}

export default function StageWash({ color, badge, label, insetRight = 0, size = 'lg' }: StageWashProps) {
  const layer: CSSProperties = { background: color };
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      <div className="absolute inset-0 opacity-25 mix-blend-color" style={layer} />
      <div className="absolute inset-0 opacity-15 mix-blend-multiply" style={layer} />
      <div
        className="absolute inset-y-0 left-0 flex flex-col items-center justify-center overflow-hidden text-center"
        style={{ right: insetRight, color: ON_WASH, containerType: 'size', textShadow: TEXT_SHADOW }}
      >
        <div className={`absolute border border-white/45 ${size === 'sm' ? 'inset-[5px]' : 'inset-[10px]'}`} />
        {badge && (
          <div
            className="whitespace-nowrap font-light"
            style={{
              fontSize: 'clamp(22px, min(20cqh, 16cqw), 64px)',
              lineHeight: 0.85,
              letterSpacing: '-0.02em',
              opacity: 0.9,
              fontVariantNumeric: 'lining-nums tabular-nums',
            }}
          >
            {badge}
          </div>
        )}
        {label && (
          <>
            <div
              className="h-px w-5 shrink-0 bg-white/60"
              style={{ margin: 'clamp(6px, 3cqh, 10px) 0 clamp(5px, 2cqh, 8px)' }}
            />
            <div
              className="max-w-[calc(100%-48px)] truncate font-semibold uppercase"
              style={{ fontSize: 'clamp(9px, 3.4cqh, 11px)', lineHeight: 1.2, letterSpacing: '0.2em', opacity: 0.9 }}
            >
              {label}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
