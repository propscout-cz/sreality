import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { authErrorCs } from '@/lib/authErrors';
import { ROUTES } from '@/lib/routes';
import { useAuth } from '../lib/auth';
import { AuthShell } from './Login';

/** Phase 1 auth — request a password-reset email. */
export default function ForgotPassword() {
  const { sendPasswordReset } = useAuth();
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await sendPasswordReset(email);
      setSent(true);
    } catch (err) {
      setError(authErrorCs(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell>
      <h1 className="text-lg font-semibold">Obnovení hesla</h1>
      {sent ? (
        <p className="text-sm text-emerald-600">
          Pokud k adrese {email} existuje účet, posíláme na ni odkaz pro nové heslo.
        </p>
      ) : (
        <form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <label className="flex flex-col gap-1 text-sm">
            E-mail
            <input
              className="rounded border border-neutral-300 dark:border-neutral-600 bg-transparent px-3 py-2 text-sm"
              type="email" autoComplete="email" required
              value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            className="rounded bg-neutral-900 text-white dark:bg-white dark:text-neutral-900 px-3 py-2 text-sm font-medium disabled:opacity-50"
            type="submit" disabled={busy}>
            {busy ? '…' : 'Poslat odkaz'}
          </button>
        </form>
      )}
      <Link className="text-sm text-blue-600 hover:underline" to={ROUTES.login.build()}>Zpět na přihlášení</Link>
    </AuthShell>
  );
}
