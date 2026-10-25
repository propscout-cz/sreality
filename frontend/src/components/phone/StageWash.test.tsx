/* The stage wash at the extension's quiet values (operator ruling 2026-10-22): the
 * photo reads first, the badge and the name still read at a glance. Pinned so the
 * feed card and the pipeline thumbnail cannot drift back to the old deep wash. */
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import StageWash from './StageWash';

function wash(props: { size?: 'lg' | 'sm'; label?: string | null } = {}) {
  const { container } = render(
    <div className="relative isolate">
      <StageWash color="var(--color-tag-ochre)" badge="P1" label={props.label ?? null} size={props.size} />
    </div>,
  );
  const root = container.querySelector('[aria-hidden="true"]') as HTMLElement;
  const [tone, shade, face] = Array.from(root.children) as HTMLElement[];
  const [frame, badge, rule, label] = Array.from(face.children) as HTMLElement[];
  return { tone, shade, face, frame, badge, rule, label };
}

describe('<StageWash>', () => {
  it('tones the photo at the quiet values: colour 0.25, multiply 0.15', () => {
    const { tone, shade } = wash();
    expect(tone).toHaveClass('mix-blend-color', 'opacity-25');
    expect(shade).toHaveClass('mix-blend-multiply', 'opacity-15');
    expect(tone).toHaveStyle({ background: 'var(--color-tag-ochre)' });
  });

  it('draws the frame, the badge and the name at the extension sizes, with its soft shadow', () => {
    const { face, frame, badge, rule, label } = wash({ label: 'Prohlídka' });
    expect(frame).toHaveClass('border-white/45', 'inset-[10px]');
    expect(badge).toHaveTextContent('P1');
    expect(badge.style.fontSize).toBe('clamp(22px, min(20cqh, 16cqw), 64px)');
    expect(badge.style.opacity).toBe('0.9');
    expect(rule).toHaveClass('w-5', 'bg-white/60');
    expect(label).toHaveTextContent('Prohlídka');
    expect(label.style.fontSize).toBe('clamp(9px, 3.4cqh, 11px)');
    expect(face.style.textShadow).toBe('0 1px 12px rgba(0, 0, 0, 0.3)');
  });

  it('on the pipeline thumbnail: the frame 5 px in, the badge alone at the same values', () => {
    const { tone, frame, badge, rule } = wash({ size: 'sm' });
    expect(tone).toHaveClass('opacity-25');
    expect(frame).toHaveClass('border-white/45', 'inset-[5px]');
    expect(badge.style.fontSize).toBe('clamp(22px, min(20cqh, 16cqw), 64px)');
    expect(rule).toBeUndefined();
  });
});
