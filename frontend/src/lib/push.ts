/* Push on this device (notifications push program, docs/design/notifications-push
 * PROGRAM.md § 3.4-3.6): the pieces every caller shares. The switch and the
 * feed's notice are lib/usePush; this file holds the platform gate, the one
 * way a subscription comes to exist here, the re-sync (which is also how push
 * comes ON BY DEFAULT), the identity binding and the landing's open report.
 *
 * A subscription belongs to the service-worker registration, not to a user,
 * so this device remembers which account switched it on (OWNER_KEY): a
 * subscription found under any other account is ended, never re-bound. It
 * also remembers which account switched it OFF (OFF_KEY): for that account
 * alone the default is suspended until it switches on again. */

import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';

import {
  deletePushSubscription,
  getPushKey,
  postPushOpened,
  putPushSubscription,
  type PushSubscriptionBody,
} from './api';
import { isInstalledApp } from './serviceWorker';
import { supabase } from './supabase';

const OWNER_KEY = 'sreality.push.owner';
const OFF_KEY = 'sreality.push.off';

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
  writeKey(OWNER_KEY, userId);
}

/* The account that switched push off here (the switch); null when none did,
 * undefined when storage is unreadable (then nothing is switched on by default). */
export function readPushOff(): string | null | undefined {
  try {
    return localStorage.getItem(OFF_KEY);
  } catch {
    return undefined;
  }
}

export function writePushOff(userId: string | null): void {
  writeKey(OFF_KEY, userId);
}

function writeKey(key: string, value: string | null): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/* What the platform allows before anything is read. 'unsupported' for anything
 * but an iPhone or iPad (by UA, or by `navigator.standalone`, which only
 * Apple's mobile WebKit has; the server takes Apple's push service only, so
 * nothing else is ever asked for permission); then 'not_installed' for a
 * Safari tab, which has no PushManager at all and must hear "install the
 * app", not "unsupported"; then 'unsupported' for an installed app whose
 * engine has no Web Push (iOS before 16.4). */
export function pushPlatformGate(): 'unsupported' | 'not_installed' | null {
  if (!(/iPhone|iPad|iPod/.test(navigator.userAgent) || 'standalone' in navigator)) return 'unsupported';
  if (!isInstalledApp()) return 'not_installed';
  if (
    !('serviceWorker' in navigator) ||
    typeof window.PushManager === 'undefined' ||
    typeof window.Notification === 'undefined'
  ) {
    return 'unsupported';
  }
  return null;
}

/* The worker, once active (`subscribe` needs one); null where none is
 * registered, so a dev server reads "unsupported" instead of waiting forever. */
export async function activeRegistration(): Promise<ServiceWorkerRegistration | null> {
  try {
    if (!(await navigator.serviceWorker.getRegistration())) return null;
    return await navigator.serviceWorker.ready;
  } catch {
    return null;
  }
}

/* The server's key as `subscribe` takes it; null when it cannot be read (503:
 * push not configured; offline), which is never a verdict on a subscription. */
export function readServerKey(): Promise<Uint8Array<ArrayBuffer> | null> {
  return getPushKey()
    .then((r) => decodePushKey(r.public_key))
    .catch(() => null);
}

/* THE one way a subscription comes to exist here: subscribe, register it with
 * the server, remember who it belongs to. One the server refused is ended
 * again, so none stays behind, and the refusal is the caller's. */
export async function subscribePushHere(
  registration: ServiceWorkerRegistration,
  key: Uint8Array<ArrayBuffer>,
  userId: string | null,
): Promise<void> {
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: key,
  });
  try {
    await putPushSubscription(pushSubscriptionBody(subscription));
  } catch (err) {
    await subscription.unsubscribe().catch(() => false);
    throw err;
  }
  writePushOwner(userId);
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

/* The notifications gate for the signed-in user (lib/auth
 * `notificationsVisible`, the bell's): true or false once the plan is read,
 * null while signed out or reading. Turning true re-sends this device's
 * subscription (the launch's and each sign-in's re-sync); turning false ends
 * it here, telling no server (its row ends at the next push's 404/410). */
let pushGate: boolean | null = null;

export function setPushGate(allowed: boolean | null): void {
  if (allowed === pushGate) return;
  pushGate = allowed;
  if (allowed === true) void resyncPush();
  else if (allowed === false) void endPushHere();
}

/* Brings this device's subscription in line with the signed-in account (the
 * PUT is idempotent). iOS never fires `pushsubscriptionchange`, so the launch,
 * each sign-in (setPushGate), the hourly resume check (lib/serviceWorker) and
 * each mount of the switch or the notice (lib/usePush, handing over the key it
 * read) are when it runs; runs that overlap share one. It re-sends nothing
 * while the gate is not open, and it never prompts: the permission prompt
 * belongs to a tap (lib/usePush `enable`).
 *
 * PUSH IS ON BY DEFAULT. Where this iPhone already allows notifications
 * (`Notification.permission` is `granted`) and holds no subscription, it
 * subscribes the signed-in account at once, unless that account switched push
 * off here (OFF_KEY): a sign-in after a sign-out, a replaced subscription and a
 * registration that once failed all come back on their own, and the switch's
 * "off" holds until the same account switches on again.
 *
 * A subscription it must not re-send is ended instead: one another account
 * switched on (not this user's to re-bind; the signed-in account then gets its
 * own, by the default above), or one made under a server key the server no
 * longer gives (a rotated key: every push to it is refused, so re-sending it
 * would only revive a dead row). A key that cannot be read is no verdict. */
let resyncing: Promise<void> | null = null;

export function resyncPush(known: { key?: Uint8Array<ArrayBuffer> | null } = {}): Promise<void> {
  resyncing ??= resync(known)
    .catch(() => {
      /* The next sign-in or resume tries again. */
    })
    .finally(() => {
      resyncing = null;
    });
  return resyncing;
}

async function resync(known: { key?: Uint8Array<ArrayBuffer> | null }): Promise<void> {
  if (pushGate === false) {
    await endPushHere();
    return;
  }
  if (pushGate !== true) return;
  const { data } = await supabase.auth.getSession();
  const userId = data.session?.user?.id;
  if (!userId) return;
  const owner = readPushOwner();
  const foreign = owner !== undefined && owner !== userId;
  /* undefined: not read yet; null: read, and the server gave none. */
  let key = known.key;
  const subscription = await currentPushSubscription();
  if (subscription) {
    if (!foreign) {
      if (key === undefined) key = await readServerKey();
      if (!key || samePushKey(subscription, key)) {
        await putPushSubscription(pushSubscriptionBody(subscription));
        return;
      }
    }
    await subscription.unsubscribe();
    writePushOwner(null);
  }
  const off = readPushOff();
  if (off === undefined || off === userId) return;
  if (pushPlatformGate() || Notification.permission !== 'granted') return;
  const registration = await activeRegistration();
  if (!registration) return;
  if (key === undefined) key = await readServerKey();
  if (!key) return;
  await subscribePushHere(registration, key, userId);
}

/* BINDING FOLLOWS IDENTITY, NOT A BUTTON (AUDIT F18, program § 3.6): no
 * subscription outlives its user. Ending one locally needs no JWT, so it also
 * covers a session that ended on its own (an expired refresh, a token-driven
 * account switch): the server's row then ends "gone" at its next push. */
export async function endPushHere(): Promise<void> {
  writePushOwner(null);
  const subscription = await currentPushSubscription();
  await subscription?.unsubscribe().catch(() => false);
}

/* The longest an explicit sign-out waits to tell the server. */
export const SIGN_OUT_NOTICE_MS = 3000;

/* An explicit sign-out, while the JWT still works: tell the server first (best
 * effort, at most SIGN_OUT_NOTICE_MS), then end the subscription here. */
export async function signOutPush(): Promise<void> {
  const subscription = await currentPushSubscription();
  if (subscription) {
    const deadline = new AbortController();
    const timer = setTimeout(() => deadline.abort(), SIGN_OUT_NOTICE_MS);
    try {
      await Promise.race([
        deletePushSubscription(
          { endpoint: subscription.endpoint, reason: 'signed_out' },
          { signal: deadline.signal },
        ),
        new Promise((resolve) => deadline.signal.addEventListener('abort', resolve, { once: true })),
      ]);
    } catch {
      /* The row ends "gone" at its next push instead. */
    } finally {
      clearTimeout(timer);
    }
  }
  await endPushHere();
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
