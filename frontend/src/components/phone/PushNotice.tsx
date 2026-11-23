/* The feed's push notice (notifications push program § 3.6): push is on by
 * default, and Apple lets only a tap ask for permission, so the start screen
 * asks for that tap itself instead of leaving it to the account sheet's
 * switch. One hairline row above the roll, only while this iPhone could
 * receive notifications and does not: off (the prompt not yet answered; the
 * whole row is the tap, the switch's own enable), denied (what to do in
 * Settings). On, a Safari tab, another phone, no server key and a plan without
 * notifications all draw nothing, and the hook mounts only under the plan's
 * gate (the bell's). While a tap runs the row stays as it was: the state is
 * read back, never assumed. */

import { useRef } from 'react';

import { useUnreadCount } from '@/lib/unreadCount';
import { usePush } from '@/lib/usePush';

import { BellIcon } from './phoneIcons';

const ROW =
  'flex min-h-11 w-full items-center gap-3 border-b border-[var(--color-rule-soft)] px-4 py-2 text-left text-[14px] text-[var(--color-ink-2)]';

export default function PushNotice() {
  const { visible } = useUnreadCount();
  return visible ? <Notice /> : null;
}

function Notice() {
  const { state, error, enable } = usePush();
  const shown = useRef<'off' | 'denied' | null>(null);
  if (state !== 'busy') shown.current = state === 'off' || state === 'denied' ? state : null;
  if (shown.current === null) return null;
  if (shown.current === 'denied') {
    return (
      <p role="status" className={ROW}>
        <BellIcon className="h-4 w-4 shrink-0 text-[var(--color-ink-3)]" />
        <span>Oznámení jsou zakázaná. Povolte je v Nastavení iPhonu.</span>
      </p>
    );
  }
  return (
    <button
      type="button"
      onClick={enable}
      aria-busy={state === 'busy' || undefined}
      className={`${ROW} active:bg-[var(--color-paper-2)]`}
    >
      <BellIcon className="h-4 w-4 shrink-0 text-[var(--color-ink-3)]" />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span>Oznámení na tomto iPhonu jsou vypnutá.</span>
        {error && <span className="text-[12px] text-[var(--color-brick)]">{error}</span>}
      </span>
      <span className="shrink-0 font-medium text-[var(--color-copper)]">Zapnout</span>
    </button>
  );
}
