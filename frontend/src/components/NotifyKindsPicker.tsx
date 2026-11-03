/* "Notify me about": the property events a watchdog tells (migration 720). A pick-several, so
 * chips (system.md § Option groups), drawn as MultiselectChips draws them, with the one click rule
 * of lib/changeKinds (`toggleNotifyKind`: Relisted comes with Delisted). The words are the feed's
 * own labels, so the chip a user turned on reads like the alerts it sends. An empty set tells
 * nothing: the hosting form refuses to save it, and this says why. */
import { PickButton } from '@/components/controls';
import { CHANGE_META, WATCHDOG_KINDS, toggleNotifyKind } from '@/lib/changeKinds';
import type { WatchdogKind } from '@/lib/types';

export const NOTIFY_KINDS_EMPTY = 'Choose at least one event to be notified about.';

export default function NotifyKindsPicker({
  value,
  onChange,
  labelledBy,
}: {
  value: ReadonlyArray<WatchdogKind>;
  onChange: (next: WatchdogKind[]) => void;
  labelledBy: string;
}) {
  return (
    <div>
      <div role="group" aria-labelledby={labelledBy} className="flex flex-wrap gap-1">
        {WATCHDOG_KINDS.map(({ kind, hint }) => (
          <PickButton
            key={kind}
            on={value.includes(kind)}
            onClick={() => onChange(toggleNotifyKind(value, kind))}
            variant="solid"
            className="min-w-16"
            title={hint}
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
          ? 'About the properties these filters match, from now on. Relisted comes with Delisted.'
          : NOTIFY_KINDS_EMPTY}
      </p>
    </div>
  );
}
