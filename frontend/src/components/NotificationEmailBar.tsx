/* "E-mailová upozornění": the account's notification email on the one joined bar, under the avatar on the
 * desktop (a menuitemradio bar inside the account menu, like light or dark) and in the phone's account sheet
 * (the phone tone, like Vzhled). Unlit and inert until the read lands. A chunk of its own, loaded only when
 * a menu or sheet opens (NotificationEmail.tsx): the first-visit budget has no room for it. */

import { SegmentedBar } from './controls';
import {
  NOTIFICATION_EMAIL_LABEL,
  NOTIFICATION_EMAIL_OPTIONS,
  useNotificationEmail,
} from '@/lib/notificationEmail';

export default function NotificationEmailBar({ tone }: { tone: 'desktop' | 'phone' }) {
  const { mode, setMode } = useNotificationEmail();
  const phone = tone === 'phone';
  return (
    <div className={phone ? 'px-4 pb-1 pt-3' : 'px-3 pt-1 pb-2'}>
      <p
        className={
          phone
            ? 'text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]'
            : 'text-[0.7rem] tracking-wide text-[var(--color-ink-3)]'
        }
      >
        {NOTIFICATION_EMAIL_LABEL}
      </p>
      <div className={phone ? 'mt-2' : 'mt-1.5'}>
        <SegmentedBar<typeof mode>
          tone={tone}
          fill
          segmentRole={phone ? undefined : 'menuitemradio'}
          label={NOTIFICATION_EMAIL_LABEL}
          options={NOTIFICATION_EMAIL_OPTIONS}
          value={mode}
          disabled={mode == null}
          onChange={(next) => {
            if (next) setMode(next);
          }}
        />
      </div>
    </div>
  );
}
