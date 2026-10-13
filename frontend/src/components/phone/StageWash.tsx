/* The stage wash over a filed property's photo — ported from the extension's
 * `chrome-extension/src/index_stage.ts` (#1758), whose layers and values it
 * copies: the photo toned in the stage colour (`mix-blend-mode: color` at 0.9),
 * deepened toward it (`multiply` at 0.5) so white type reads on a sky, a hairline
 * frame 10 px in, the badge set large and the stage's name beneath. The one
 * departure, as the prototype (#1762) resolved: no text shadow.
 *
 * Purely a mark (aria-hidden): the funnel in the card's margin carries the stage
 * in its label. The colour is `stageAccent` (the operator's, copper when none),
 * the badge `stageBadge` (the stage's code, else its ordinal) — both computed by
 * the caller from the one shared membership read.
 *
 * Two sizes, as the prototype draws them: `lg` over a full-width photo (feed,
 * property header) and `sm` over the pipeline card's 104 px thumbnail, where
 * the frame steps in 5 px and the caller passes no label (the badge alone).
 *
 * The parent must isolate (`isolation: isolate`) so the blend stops at the photo. */

import type { CSSProperties } from 'react';

const ON_WASH = '#ffffff';

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
      <div className="absolute inset-0 opacity-90 mix-blend-color" style={layer} />
      <div className="absolute inset-0 opacity-50 mix-blend-multiply" style={layer} />
      <div
        className="absolute inset-y-0 left-0 flex flex-col items-center justify-center overflow-hidden text-center"
        style={{ right: insetRight, color: ON_WASH, containerType: 'size' }}
      >
        <div className={`absolute border border-white/50 ${size === 'sm' ? 'inset-[5px]' : 'inset-[10px]'}`} />
        {badge && (
          <div
            className="whitespace-nowrap font-light"
            style={{
              fontSize: 'clamp(40px, min(50cqh, 40cqw), 168px)',
              lineHeight: 0.8,
              letterSpacing: '-0.04em',
              fontVariantNumeric: 'lining-nums tabular-nums',
            }}
          >
            {badge}
          </div>
        )}
        {label && (
          <>
            <div
              className="h-px w-7 shrink-0 bg-white/75"
              style={{ margin: 'clamp(10px, 5cqh, 20px) 0 clamp(7px, 3cqh, 12px)' }}
            />
            <div
              className="max-w-[calc(100%-48px)] truncate font-semibold uppercase"
              style={{ fontSize: 'clamp(10px, 4cqh, 13px)', lineHeight: 1.5, letterSpacing: '0.22em' }}
            >
              {label}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
