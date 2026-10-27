/* What a viewing adds to a note (migration 666: a viewing is a note with a
 * date): the day + time fields its composer and its editor share, the toggle
 * that adds or drops the date, and the calendar leaf a viewing note opens with.
 * The fields read and write the Prague wall clock (lib/viewings); the native
 * pickers are the phone's own wheels. */

import { CalendarIcon } from '@/components/phone/phoneIcons';
import {
  defaultSlot,
  isUpcoming,
  leaf,
  relativeDay,
  viewingWhen,
  type ViewingSlot,
} from '@/lib/viewings';

const FIELD =
  'h-8 rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper)] px-2 text-[0.85rem] tabular-nums text-[var(--color-ink)] focus:border-[var(--color-copper)] disabled:opacity-50';

export function ViewingFields({
  slot,
  onChange,
  disabled = false,
}: {
  slot: ViewingSlot;
  onChange: (slot: ViewingSlot) => void;
  disabled?: boolean;
}) {
  return (
    <div role="group" aria-label="Termín prohlídky" className="flex flex-wrap items-center gap-2">
      <CalendarIcon className="h-4 w-4 shrink-0 text-[var(--color-copper)]" />
      <span className="text-[0.8rem] font-medium text-[var(--color-ink-2)]">Prohlídka</span>
      <input
        type="date"
        aria-label="Den prohlídky"
        required
        value={slot.day}
        onChange={(e) => onChange({ ...slot, day: e.target.value })}
        disabled={disabled}
        className={FIELD}
      />
      <input
        type="time"
        aria-label="Čas prohlídky"
        required
        step={300}
        value={slot.time}
        onChange={(e) => onChange({ ...slot, time: e.target.value })}
        disabled={disabled}
        className={FIELD}
      />
    </div>
  );
}

/* Adds a date (the next sensible slot) or drops it again. */
export function ViewingToggle({
  slot,
  onChange,
  disabled = false,
  className,
}: {
  slot: ViewingSlot | null;
  onChange: (slot: ViewingSlot | null) => void;
  disabled?: boolean;
  className: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(slot ? null : defaultSlot())}
      disabled={disabled}
      className={className}
    >
      <CalendarIcon className="h-3.5 w-3.5" />
      <span>{slot ? 'Odebrat termín' : 'Prohlídka'}</span>
    </button>
  );
}

/* A viewing note's first line: the calendar leaf (copper while ahead, grey once
 * past), the date and time, and how far away it is. */
export function ViewingHead({ iso }: { iso: string }) {
  const ahead = isUpcoming(iso);
  const { month, day } = leaf(iso);
  const relative = relativeDay(iso);
  return (
    <div className="flex items-center gap-3">
      <span
        aria-hidden
        className={[
          'flex h-11 w-10 shrink-0 flex-col items-center overflow-hidden rounded-[var(--radius-sm)] border bg-[var(--color-paper)]',
          ahead ? 'border-[var(--color-copper)]/50' : 'border-[var(--color-rule-strong)]',
        ].join(' ')}
      >
        <span
          className={[
            'w-full py-px text-center text-[0.6rem] font-medium uppercase leading-[1.5] tracking-[0.08em]',
            ahead ? 'bg-[var(--color-copper)] text-[var(--color-on-copper)]' : 'bg-[var(--color-inset)] text-[var(--color-ink-3)]',
          ].join(' ')}
        >
          {month}
        </span>
        <span
          className={[
            'flex flex-1 items-center font-display text-[1.1rem] leading-none tabular-nums',
            ahead ? 'text-[var(--color-ink)]' : 'text-[var(--color-ink-3)]',
          ].join(' ')}
        >
          {day}
        </span>
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-[var(--color-ink)]">Prohlídka · {viewingWhen(iso)}</p>
        <p className="text-[0.75rem] text-[var(--color-ink-3)]">{ahead ? relative : `proběhla ${relative}`}</p>
      </div>
    </div>
  );
}
