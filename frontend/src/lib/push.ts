/* Push on this device (notifications push program, docs/design/notifications-push
 * PROGRAM.md § 3.4-3.6): the small pieces every caller shares. The account
 * sheet's switch is lib/usePush (a lazy chunk); this file is what the eager
 * callers need: the landing's open report, the re-sync and the identity
 * binding.
 *
 * A subscription belongs to the service-worker registration, not to a user,
 * so this device remembers which account switched it on (OWNER_KEY): a
 * subscription found under any other account is ended, never re-bound. */

import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';

import { postPushOpened, type PushSubscriptionBody } from './api';

const OWNER_KEY = 'sreality.push.owner';

/* The account that switched push on here; undefined when storage is unreadable
 * (then nothing can be told apart, and the subscription is left as it is). */
export function readPushOwner(): string | null | undefined {
  try {
    return localStorage.getItem(OWNER_KEY);
  } catch {
    return undefined;
  }
}

export function writePushOwner(userId: string | null): void {
  try {
    if (userId) localStorage.setItem(OWNER_KEY, userId);
    else localStorage.removeItem(OWNER_KEY);
  } catch {
    /* ignore */
  }
}

/* This device's subscription, or null where there is none or no push at all.
 * `getRegistration` rather than `ready`: it never waits for a worker to
 * activate (none ever does under the dev server), and reading a subscription
 * needs no active worker. */
export async function currentPushSubscription(): Promise<PushSubscription | null> {
  if (!('serviceWorker' in navigator) || typeof window.PushManager === 'undefined') return null;
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    return (await registration?.pushManager.getSubscription()) ?? null;
  } catch {
    return null;
  }
}

/* What PUT /push/subscription takes: the subscription's own JSON and the UA. */
export function pushSubscriptionBody(subscription: PushSubscription): PushSubscriptionBody {
  return {
    ...(subscription.toJSON() as Omit<PushSubscriptionBody, 'user_agent'>),
    user_agent: navigator.userAgent,
  };
}

/* The VAPID public key as `subscribe` takes it: base64url to the raw 65-byte
 * uncompressed P-256 point; null for anything else. */
export function decodePushKey(base64url: string): Uint8Array<ArrayBuffer> | null {
  try {
    const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '='));
    const key = Uint8Array.from(raw, (c) => c.charCodeAt(0));
    return key.length === 65 && key[0] === 4 ? key : null;
  } catch {
    return null;
  }
}

/* False only when the subscription names a DIFFERENT server key (a rotated
 * VAPID key: Apple refuses every push to it). A browser that does not say
 * which key it used is never treated as a mismatch. */
export function samePushKey(subscription: PushSubscription, key: Uint8Array): boolean {
  const used = subscription.options?.applicationServerKey;
  if (!used) return true;
  const bytes = new Uint8Array(used);
  return bytes.length === key.length && bytes.every((b, i) => b === key[i]);
}

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
