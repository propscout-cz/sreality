import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { isAuthRetryableFetchError, type Session, type User } from '@supabase/supabase-js';
import { resetActiveBoard } from './acting';
import { isSupabaseConfigured, supabase } from './supabase';
import { isTransientApiError } from './api';
import { pgRead } from './pgRead';
import { resetReads } from './revalidate';
import { useCurationSync } from './curationSyncOwner';
import { syncAppBadge } from './appBadge';
import { endPushHere, setPushGate, signOutPush } from './push';
import { ROUTES } from './routes';
import { syncAppearance, type AppearanceProfile } from './theme';

/**
 * Phase 1 auth context. Tracks the Supabase session and exposes sign-in/out.
 * The login gate is wired via <RequireAuth> / <RequireAdmin> (components/guards);
 * `isAdmin` mirrors the JWT's app_metadata.is_admin claim (stamped server-side
 * from the admins table — never client-writable).
 */
type AuthState = {
  session: Session | null;
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  /** The session's plan agenda-visibility map (RLS reads of entitlements +
   *  plans); null while loading / on error / logged out — treated as
   *  "show everything" by consumers so a billing hiccup can't blank the nav. */
  agendas: Record<string, boolean> | null;
  signInWithPassword: (email: string, password: string) => Promise<void>;
  signUpWithPassword: (email: string, password: string) => Promise<void>;
  signInWithGoogle: () => Promise<void>;
  sendPasswordReset: (email: string) => Promise<void>;
  updatePassword: (password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

/* The appearance lives in the user's own Supabase Auth profile (lib/theme). */
const appearanceProfile: AppearanceProfile = {
  read: async () => (await supabase.auth.getUser()).data.user,
  save: (appearance) => supabase.auth.updateUser({ data: { appearance } }),
};

/* THE agenda gate (Settings › Tiers): admins, unconfigured local dev and a plan
 * read that failed (null: shown rather than blanked over a hiccup) pass;
 * otherwise the plan's agenda decides. UX only — routes carry the security gates. */
export function agendaVisible(
  isAdmin: boolean,
  agendas: Record<string, boolean> | null,
  agenda: string,
): boolean {
  return isAdmin || !isSupabaseConfigured() || agendas === null || agendas[agenda] === true;
}

/* The notifications gate: the bell, the feed's nav entry, the unread read and
 * push (lib/push `setPushGate`) all follow it. */
export function notificationsVisible(
  isAdmin: boolean,
  agendas: Record<string, boolean> | null,
): boolean {
  return agendaVisible(isAdmin, agendas, 'notifications');
}

/* Cache key for the session's plan agendas. Deliberately NOT under any
 * feature's namespace: it is session state, read once per user. */
export const sessionAgendasKey = (userId: string | null) =>
  ['session', 'agendas', userId] as const;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  /* No session, but not certainly nobody: a launch whose token refresh failed in
   * transit (offline, a captive Wi-Fi, an Auth 5xx) is answered without a
   * session while the stored one is kept for the next try. Cleared by any
   * session or a SIGNED_OUT, the two certain answers. */
  const [sessionUncertain, setSessionUncertain] = useState(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    supabase.auth.getSession().then(({ data, error }) => {
      setSession(data.session);
      setSessionUncertain(data.session == null && isAuthRetryableFetchError(error));
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((event, next) => {
      setSession(next);
      if (next || event === 'SIGNED_OUT') setSessionUncertain(false);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    syncAppearance(session?.user ?? null, appearanceProfile);
  }, [session]);

  /* The session's plan agendas: the explicit entitlements row's plan, else the
   * default plan. RLS returns only the caller's own entitlement. Admins never
   * consult this (nav bypass).
   *
   * Keyed on the USER ID, not the session object. This used to be a bare
   * useEffect with `[session]` in its dependency array, and Supabase's
   * onAuthStateChange hands out a fresh session object per event (initial
   * session, signed-in, token-refreshed) — so the pair of reads ran three times
   * on every single app start, on every route, outside React Query where
   * nothing could dedupe them. Measured in production: six requests at
   * 231-433 ms each before any page data moved. A user id is a stable string,
   * so the same user's three events now collapse into one cache entry.
   *
   * staleTime Infinity because a plan does not change under a live session; a
   * real change arrives with a new sign-in, which is a new key. That is also
   * why a TRANSIENT failure must reject rather than resolve: a resolved
   * fallback would be cached for the whole session, while a rejection gets
   * main.tsx's one retry and, if that fails too, leaves no data (agendas null
   * for now) and is read again on the next mount instead of being cached. */
  const userId = session?.user?.id ?? null;
  const agendasQ = useQuery({
    queryKey: sessionAgendasKey(userId),
    queryFn: async ({ signal }): Promise<Record<string, boolean> | null> => {
      /* allSettled, not all: the two failures mean different things. */
      const settled = await Promise.allSettled([
        pgRead<{ plan: string | null } | null>(
          supabase.from('entitlements').select('plan,status').maybeSingle(),
          { signal },
        ),
        pgRead<Array<{ key: string; agendas: unknown; is_default: boolean }> | null>(
          supabase.from('plans').select('key,agendas,is_default'),
          { signal },
        ),
      ]);
      for (const r of settled) {
        if (r.status === 'rejected' && (signal.aborted || isTransientApiError(r.reason))) {
          throw r.reason;
        }
      }
      const [ent, plansRes] = settled;
      // A billing read that fails for good or returns nothing must not blank
      // the nav — null means "no constraint" to every consumer.
      if (plansRes.status === 'rejected' || !plansRes.value.data) return null;
      const plans = plansRes.value.data;
      // A non-transient entitlements failure falls to the DEFAULT plan (constrained).
      const planKey =
        (ent.status === 'fulfilled' ? ent.value.data?.plan : null)
        ?? plans.find((p) => p.is_default)?.key;
      const plan = plans.find((p) => p.key === planKey);
      return (plan?.agendas as Record<string, boolean> | undefined) ?? null;
    },
    enabled: userId != null,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const agendas = userId == null ? null : agendasQ.data ?? null;

  /* Drop every cached read when the signed-in identity changes.
   *
   * The cache is keyed by query, not by account, and almost everything in it is
   * RLS-scoped rows belonging to whoever was signed in when it was fetched. On
   * a sign-out or an account switch those rows stayed resident for up to gcTime
   * (5 min), so the next session could paint the previous account's collections,
   * pipeline and notifications before its own reads landed. Clearing on the
   * TRANSITION (rather than only inside signOut) also covers a switch that
   * happens through a token change rather than an explicit sign-out.
   *
   * Push follows identity the same way (AUDIT F18): on any change of user,
   * sign-out included, and on a launch with CERTAINLY nobody signed in, this
   * device's subscription ends locally (no JWT needed) and the icon badge
   * clears, so no subscription outlives its user. A launch that could not
   * refresh its session (`sessionUncertain`) ends nothing: its user is most
   * likely still there. None of it runs before the stored session is read
   * (`loading`): the null it starts from is not yet anyone. */
  const prevUserId = useRef<string | null>(null);
  useEffect(() => {
    if (loading) return;
    const prev = prevUserId.current;
    prevUserId.current = userId;
    if (prev !== null && prev !== userId) {
      /* First the read scheduler's epoch (lib/revalidate): a hold, rollback or
       * owed read of the previous account then paints and parks nothing here. */
      resetReads(queryClient);
      queryClient.clear();
      /* The per-device pipeline board and "edit as owner" were the previous
       * account's pick (lib/acting): the next one starts on its own board. */
      resetActiveBoard();
      syncAppBadge(0);
      void endPushHere();
    } else if (prev === null && userId === null && !sessionUncertain) {
      syncAppBadge(0);
      void endPushHere();
    }
  }, [loading, userId, sessionUncertain, queryClient]);

  /* Curation sync (lib/curationSyncOwner): this window follows the signed-in
   * user's curation through the curation clock. Declared AFTER the identity
   * effect on purpose: on a change of user React runs every changed effect's
   * cleanup before any setup, so a switch is stop → resetReads + clear() →
   * start, and the new owner never sees the previous account's cache. Keyed on
   * the user id: the hourly token refresh restarts nothing. */
  useCurationSync(loading ? null : userId);

  /* Push rides the notifications gate, once the plan is read (admins never
   * wait for it): turning true (a launch, a sign-in) re-sends this device's
   * subscription (lib/push `resyncPush`; idempotent, never prompts), turning
   * false (a plan without notifications) ends it here and re-sends nothing.
   * Null while signed out or while the plan is read. */
  const isAdmin = session?.user?.app_metadata?.is_admin === true;
  const pushGate =
    userId === null
      ? null
      : isAdmin || !agendasQ.isPending
        ? notificationsVisible(isAdmin, agendas)
        : null;
  useEffect(() => {
    setPushGate(pushGate);
  }, [pushGate]);

  const value = useMemo<AuthState>(
    () => ({
      session,
      user: session?.user ?? null,
      loading,
      isAdmin,
      agendas,
      async signInWithPassword(email, password) {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      },
      async signUpWithPassword(email, password) {
        const { error } = await supabase.auth.signUp({ email, password });
        if (error) throw error;
      },
      async signInWithGoogle() {
        const { error } = await supabase.auth.signInWithOAuth({
          provider: 'google',
          options: { redirectTo: `${window.location.origin}${ROUTES.browse.build()}` },
        });
        if (error) throw error;
      },
      async sendPasswordReset(email) {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: `${window.location.origin}${ROUTES.resetPassword.build()}`,
        });
        if (error) throw error;
      },
      async updatePassword(password) {
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw error;
      },
      async signOut() {
        /* While the JWT still works: the server hears it (≤ 3 s), then the
         * subscription ends here; the transition above then finds none. */
        await signOutPush();
        const { error } = await supabase.auth.signOut();
        if (error) throw error;
      },
    }),
    [session, loading, isAdmin, agendas],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>');
  return ctx;
}
