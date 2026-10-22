/* Where a tapped push lands: `/n/{dispatch id}?d={delivery id}` (notifications
 * push program § 3.5). It marks the dispatch read under the caller's RLS, which
 * answers with the row as it stands NOW (a merge re-points its property, so a
 * merged property opens on the survivor), reports the open when the tap named
 * its delivery, and replaces itself with what the row is about
 * (lib/dispatchTarget). A row that is gone or not the caller's (404) or any
 * other failure lands on the feed instead. The bell's rows open the same target
 * but never report an open: only a tap does. */

import { useEffect, useRef } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';

import { markWatchdogDispatchSeen } from '@/lib/api';
import { dispatchTarget } from '@/lib/dispatchTarget';
import { usePhoneShell } from '@/lib/phoneShell';
import { reportPushOpened } from '@/lib/push';
import { notificationKeys } from '@/lib/queries';
import { ROUTES } from '@/lib/routes';

export default function NotificationOpen() {
  const { dispatchId = '' } = useParams();
  const [params] = useSearchParams();
  const delivery = params.get('d');
  const navigate = useNavigate();
  const qc = useQueryClient();
  const phone = usePhoneShell();
  /* One mark-seen and one report per landing (StrictMode runs effects twice). */
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    reportPushOpened(delivery);
    markWatchdogDispatchSeen(dispatchId)
      .then((row) => {
        void qc.invalidateQueries({ queryKey: notificationKeys.all });
        navigate(dispatchTarget(row), { replace: true });
      })
      .catch(() => navigate(ROUTES.notifications.build(), { replace: true }));
  }, [dispatchId, delivery, navigate, qc]);

  return (
    <p
      role="status"
      className={
        phone
          ? 'px-4 py-6 text-[15px] text-[var(--color-ink-3)]'
          : 'px-6 py-8 text-sm text-[var(--color-ink-3)]'
      }
    >
      {phone ? 'Otevírám…' : 'Opening…'}
    </p>
  );
}
