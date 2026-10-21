/* Push on this device (notifications push program, docs/design/notifications-push
 * PROGRAM.md § 3.4-3.6). */

import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';

import { postPushOpened } from './api';

/* A push tap carries its delivery id as `?d=` (`/n/{id}?d=`, or the coalesced
 * `/notifications?d=`). Reported once, best effort: an open that fails to
 * report is a lost measurement, never a lost page. */
export function reportPushOpened(raw: string | null): void {
  if (raw == null || !/^\d{1,15}$/.test(raw)) return;
  postPushOpened(Number(raw)).catch(() => {});
}

/* The coalesced push opens the feed itself: report its `?d=` once, then drop it
 * from the address, so a reload or a shared link never reports it again. */
export function usePushOpenedParam(): void {
  const [params, setParams] = useSearchParams();
  const delivery = params.get('d');
  const reported = useRef<string | null>(null);
  useEffect(() => {
    if (delivery == null || reported.current === delivery) return;
    reported.current = delivery;
    reportPushOpened(delivery);
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('d');
        return next;
      },
      { replace: true },
    );
  }, [delivery, setParams]);
}
