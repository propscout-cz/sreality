import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Dialog, { DialogClose } from '@/components/Dialog';
import ErrorBanner from '@/components/ErrorBanner';
import { Field } from '@/components/controls';
import { fetchCuzkLogin, removeCuzkLogin, saveCuzkLogin, type CuzkLoginState } from '@/lib/api';
import { fmtShortDate } from '@/lib/format';

/* This account's own ČÚZK WSDP login, opened from the avatar's menu. The API checks it with ČÚZK before it stores
 * anything; the password goes up once and is never read back, so the box is always empty on open. */

const KEY = ['account', 'cuzk-login'] as const;
const NONE: CuzkLoginState = { set: false, username: null, checked_at: null };
/* What a card may offer depends on the stored login (Prodeje v domě): a save or a removal refreshes it. */
const USES_THE_LOGIN = ['property-building-prices'] as const;
const INPUT =
  'w-full rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] px-2 py-1.5 text-sm text-[var(--color-ink)]';

export default function CuzkLoginDialog({ onClose }: { onClose: () => void }) {
  const titleId = useId();
  const queryClient = useQueryClient();
  const login = useQuery({ queryKey: KEY, queryFn: fetchCuzkLogin });
  const [username, setUsername] = useState<string | null>(null);
  const [password, setPassword] = useState('');

  const save = useMutation({
    mutationFn: saveCuzkLogin,
    onSuccess: (saved) => {
      queryClient.setQueryData<CuzkLoginState>(KEY, saved);
      void queryClient.invalidateQueries({ queryKey: USES_THE_LOGIN });
      setUsername(null);
      setPassword('');
    },
  });
  const remove = useMutation({
    mutationFn: removeCuzkLogin,
    onSuccess: () => {
      queryClient.setQueryData<CuzkLoginState>(KEY, NONE);
      void queryClient.invalidateQueries({ queryKey: USES_THE_LOGIN });
      setUsername(null);
      setPassword('');
    },
  });

  const name = username ?? login.data?.username ?? '';
  const busy = save.isPending || remove.isPending;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    remove.reset();
    save.mutate({ username: name.trim(), password });
  };

  let body;
  if (login.isPending) {
    body = <p className="text-sm text-[var(--color-ink-3)]">Loading…</p>;
  } else if (!login.data) {
    body = (
      <ErrorBanner flush title="Could not load your ČÚZK login:" message={login.error?.message ?? ''}
        retryLabel="Try again" onRetry={() => void login.refetch()} />
    );
  } else {
    const state = login.data;
    body = (
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-[var(--color-ink-2)]">
          {state.set && state.refused ? (
            <span role="status">
              ČÚZK refused the login stored for <span className="font-mono">{state.username}</span> when it was last
              used. Nothing more is sent on it, so that further attempts cannot lock your ČÚZK account. Enter it
              again below.
            </span>
          ) : state.set ? (
            <>Stored for <span className="font-mono">{state.username}</span>, accepted by ČÚZK on {fmtShortDate(state.checked_at)}.</>
          ) : (
            'No login stored.'
          )}
        </p>
        <Field label="WSDP username" as="control">
          <input value={name} autoComplete="off" spellCheck={false} required
            onChange={(e) => { setUsername(e.target.value); save.reset(); }} className={`${INPUT} font-mono`} />
        </Field>
        <Field label={state.set ? 'New password' : 'Password'} as="control">
          <input type="password" value={password} autoComplete="new-password" required
            onChange={(e) => { setPassword(e.target.value); save.reset(); }} className={INPUT} />
        </Field>
        <div className="space-y-1.5 text-[0.78rem] text-[var(--color-ink-2)]">
          <p>This is the login of your own ČÚZK web-services (WSDP) account. Paid cadastre outputs you request here are bought on that account and appear on its invoice.</p>
          <p>Saving checks the login with ČÚZK first, free of charge. The password is stored encrypted and is never shown again.</p>
        </div>
        {save.isError && <ErrorBanner flush title="Not saved:" message={save.error.message} />}
        {remove.isError && <ErrorBanner flush title="Not removed:" message={remove.error.message} />}
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={busy}
            className="rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 py-1.5 text-sm text-[var(--color-on-copper)] transition-colors hover:bg-[var(--color-copper-2)] disabled:opacity-50">
            Check and save
          </button>
          {state.set && (
            <button type="button" disabled={busy} onClick={() => { save.reset(); remove.mutate(); }}
              className="rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] px-3 py-1.5 text-sm text-[var(--color-ink-2)] transition-colors hover:text-[var(--color-brick)] disabled:opacity-50">
              Remove login
            </button>
          )}
          {save.isPending && <span className="text-sm text-[var(--color-ink-3)]">Checking with ČÚZK…</span>}
          {save.isSuccess && <span role="status" className="text-sm text-[var(--color-ink-2)]">Saved.</span>}
          {remove.isSuccess && <span role="status" className="text-sm text-[var(--color-ink-2)]">Removed.</span>}
        </div>
      </form>
    );
  }

  return (
    <Dialog open onClose={onClose} labelledBy={titleId} className="w-full max-w-md p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <h2 id={titleId} className="text-lg leading-tight">ČÚZK login</h2>
        <DialogClose onClick={onClose} />
      </div>
      {body}
    </Dialog>
  );
}
