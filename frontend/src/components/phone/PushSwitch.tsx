/* The account sheet's push row, "Oznámení na tomto iPhonu" (notifications push
 * program § 3.6, W5b; the logic is lib/usePush). It mounts when the sheet
 * opens, which is when usePush does its reading, so a tap only asks for
 * permission and subscribes. Imported eagerly: the sheet must open offline,
 * where a chunk that cannot load would end in the app's last-resort boundary.
 *
 * One row, the sheet's idiom (15 px label, a 12 px line under it), the whole
 * row the switch and at least 44 px tall. Copper means on, as everywhere. A
 * state the user cannot toggle draws the switch disabled and says, in the line
 * under the label, what would make it work. While an action runs the thumb
 * stays where it last settled: the state is read back, never assumed. */

import { useId, useRef } from 'react';

import { usePush, type PushState } from '@/lib/usePush';

const HINTS: Partial<Record<PushState, string>> = {
  not_installed: 'Přidejte aplikaci na plochu',
  denied: 'Povolte oznámení v Nastavení',
  unsupported: 'Oznámení zatím fungují jen na iPhonu',
  unavailable: 'Oznámení teď nejdou zapnout',
};

export default function PushSwitch() {
  const { state, error, enable, disable } = usePush();
  const labelId = useId();
  const noteId = useId();
  const lastOn = useRef(false);
  if (state !== 'busy') lastOn.current = state === 'on';
  const on = lastOn.current;
  const usable = state === 'on' || state === 'off';
  const note = error ?? HINTS[state] ?? null;

  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-labelledby={labelId}
      aria-describedby={note ? noteId : undefined}
      aria-busy={state === 'busy' || undefined}
      disabled={!usable}
      onClick={on ? disable : enable}
      className="flex min-h-11 w-full items-center gap-3 border-b border-[var(--color-rule-soft)] px-4 py-2.5 text-left active:bg-[var(--color-paper-2)] disabled:active:bg-transparent"
    >
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span id={labelId} className="text-[15px] text-[var(--color-ink-2)]">
          Oznámení na tomto iPhonu
        </span>
        {note && (
          <span
            id={noteId}
            className={`text-[12px] ${error ? 'text-[var(--color-brick)]' : 'text-[var(--color-ink-3)]'}`}
          >
            {note}
          </span>
        )}
      </span>
      <span
        aria-hidden
        className={[
          'relative h-[26px] w-[44px] shrink-0 rounded-full transition-colors',
          on ? 'bg-[var(--color-copper)]' : 'bg-[var(--color-rule-strong)]',
          usable ? '' : 'opacity-40',
        ].join(' ')}
      >
        <span
          className={[
            'absolute top-[3px] h-5 w-5 rounded-full bg-[var(--color-paper)] transition-[left]',
            on ? 'left-[21px]' : 'left-[3px]',
          ].join(' ')}
        />
      </span>
    </button>
  );
}
