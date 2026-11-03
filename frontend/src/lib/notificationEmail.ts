/* The account's notification email choice, "E-mailová upozornění" (docs/design/email/PROGRAM.md PR 4): one
 * account-wide mode, read and set through the API, shown under the avatar on the desktop (AccountMenu) and in
 * the phone's account sheet (PhoneShell). A pick is the one optimistic write: painted at once, rolled back
 * and re-read if the API refuses it. Until the read lands no segment is lit. */

import { useQuery } from '@tanstack/react-query';

import { getNotificationEmail, putNotificationEmail, type NotificationEmailMode, type NotificationEmailSetting } from './api';
import { cachePatch } from './revalidate';
import { useOptimisticWrite } from './useOptimisticWrite';

export const NOTIFICATION_EMAIL_LABEL = 'E-mailová upozornění';

export const NOTIFICATION_EMAIL_OPTIONS: ReadonlyArray<{ value: NotificationEmailMode; label: string }> = [
  { value: 'off', label: 'Vypnuto' },
  { value: 'daily', label: 'Denní přehled' },
  { value: 'instant', label: 'Okamžitě' },
];

export const notificationEmailKey = ['account', 'notification-email'] as const;

export function useNotificationEmail(): {
  mode: NotificationEmailMode | null;
  setMode: (mode: NotificationEmailMode) => void;
} {
  const read = useQuery({
    queryKey: notificationEmailKey,
    queryFn: getNotificationEmail,
    staleTime: 5 * 60_000,
  });
  const write = useOptimisticWrite<NotificationEmailMode, NotificationEmailSetting>({
    mutationKey: ['write', 'notification-email', 'set'],
    mutationFn: putNotificationEmail,
    patch: (mode) => [
      cachePatch<NotificationEmailSetting>({ queryKey: notificationEmailKey, exact: true }, () => ({ mode })),
    ],
    revalidate: [{ queryKey: notificationEmailKey, exact: true }],
  });
  return { mode: read.data?.mode ?? null, setMode: (mode) => write.mutate(mode) };
}
