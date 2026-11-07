/* "Notify me about": the property events a watchdog or a watched collection tells (migration 735). A
 * pick-several, so chips (system.md § Option groups), drawn as MultiselectChips draws them, with the
 * one click rule of lib/notifyKinds (`toggleNotifyKind`: a collection's Back on the market comes with
 * its Left the market).
 * The words are the feed's own labels, so the chip a user turned on reads like the alerts it sends.
 * An empty set tells nothing: the hosting form refuses to save it, and this says why. */
import { PickButton } from '@/components/controls';
import { CHANGE_META } from '@/lib/changeKinds';
import { WATCHDOG_KINDS, toggleNotifyKind } from '@/lib/notifyKinds';
import type { NotifyKind, WatchdogKind } from '@/lib/types';

export const NOTIFY_KINDS_EMPTY = 'Choose at least one event to be notified about.';

export default function NotifyKindsPicker<K extends NotifyKind = WatchdogKind>({
  value,
  onChange,
  labelledBy,
  kinds = WATCHDOG_KINDS as ReadonlyArray<{ kind: K; hint: string }>,
  about = 'About the properties these filters match, from now on.',
  disabled = false,
}: {
  value: ReadonlyArray<K>;
  onChange: (next: K[]) => void;
  labelledBy: string;
  /* The kinds this source can tell, in stored order: a watchdog's five by default. */
  kinds?: ReadonlyArray<{ kind: K; hint: string }>;
  /* Whose properties the events are about, the helper line's first sentence. */
  about?: string;
  disabled?: boolean;
}) {
  return (
    <div>
      <div role="group" aria-labelledby={labelledBy} className="flex flex-wrap gap-1">
        {kinds.map(({ kind, hint }) => (
          <PickButton
            key={kind}
            on={value.includes(kind)}
            onClick={() => onChange(toggleNotifyKind(value, kind))}
            variant="solid"
            className="min-w-16"
            title={hint}
            disabled={disabled}
          >
            {CHANGE_META[kind].label}
          </PickButton>
        ))}
      </div>
      <p
        className={`mt-1.5 text-[0.75rem] ${value.length ? 'text-[var(--color-ink-3)]' : 'text-[var(--color-brick)]'}`}
        role={value.length ? undefined : 'alert'}
      >
        {value.length
          ? kinds.some((k) => k.kind === 'reactivated')
            ? `${about} Back on the market comes with Left the market.`
            : about
          : NOTIFY_KINDS_EMPTY}
      </p>
    </div>
  );
}
