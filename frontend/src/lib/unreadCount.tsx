/* THE unread count (notifications push program W5a, AUDIT F9): one query
 * observer, mounted once by Shell, read by both frames (the desktop top bar's
 * Notifications badge, the phone header's bell) and by the Notifications page,
 * and the number the installed app's icon badge shows (lib/appBadge; ruling
 * R4). The number is the account's own unseen rows
 * (`/notifications/unread-count`; no system row counts since ruling R2b).
 *
 * The route costs ~300 ms of unpooled connection setup for ~15 ms of server
 * work, so it is read only while the Notifications entry is visible to this
 * session — admin, unconfigured local dev, a plan read not yet in (shown rather
 * than blanked over a hiccup), or the plan's `notifications` agenda. It follows
 * the curation clock (W7, lib/curationSync `notification_dispatches`): a
 * mark-seen anywhere, a new event, a retired watchdog re-read it within one clock
 * read; the 60 s poll it had under ruling Q1b is gone. A failed read is the one
 * thing the clock cannot answer (it moves on a change, and the clock read does not
 * cross the API), so only while the last read failed does it ask again, each minute. */

import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getNotificationUnreadCount } from './api';
import { syncAppBadge } from './appBadge';
import { notificationsVisible, useAuth } from './auth';
import { notificationKeys } from './queries';

export interface UnreadCount {
  /* The agenda gate: false hides every bell and reads no count. */
  visible: boolean;
  /* Unseen rows; 0 while hidden or before the first read lands. */
  count: number;
}

const RETRY_FAILED_READ_MS = 60_000;

const UnreadCountContext = createContext<UnreadCount>({ visible: false, count: 0 });

export const useUnreadCount = (): UnreadCount => useContext(UnreadCountContext);

export function UnreadCountProvider({ children }: { children: ReactNode }) {
  const { isAdmin, agendas } = useAuth();
  const visible = notificationsVisible(isAdmin, agendas);
  const unreadQ = useQuery({
    queryKey: notificationKeys.unreadCount,
    queryFn: () => getNotificationUnreadCount(),
    enabled: visible,
    staleTime: 60_000,
    refetchInterval: (query) => (query.state.status === 'error' ? RETRY_FAILED_READ_MS : false),
  });
  /* undefined until the first read lands: the icon keeps what it showed. */
  const known = visible ? unreadQ.data?.unread_count : 0;
  useEffect(() => {
    if (known !== undefined) syncAppBadge(known);
  }, [known]);
  /* Shell unmounts on sign-out (it sits under RequireAuth): no count, no badge. */
  useEffect(() => () => syncAppBadge(0), []);
  const count = known ?? 0;
  const value = useMemo(() => ({ visible, count }), [visible, count]);
  return <UnreadCountContext.Provider value={value}>{children}</UnreadCountContext.Provider>;
}
