/* The post-login round trip must hand back the WHOLE destination.
 *
 * RequireAuth (components/guards.tsx) stashes `state={{ from: location }}` —
 * the full Location — but Login used to read only `.pathname`. A logged-out
 * session opening a shared Browse cohort (filters live entirely in the query
 * string) or a run deep-link (#estimations) authenticated successfully and then
 * landed on a bare /browse with no error. Both consumers dropped it: the
 * post-sign-in navigate() and the already-authed <Navigate>. */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, createPath, useLocation } from 'react-router-dom';

import Login from './Login';
import * as auth from '@/lib/auth';
import { ROUTES } from '@/lib/routes';

vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));

const signInWithPassword = vi.fn();

function asAuth(session: unknown) {
  vi.mocked(auth.useAuth).mockReturnValue({
    session,
    loading: false,
    signInWithPassword,
    signUpWithPassword: vi.fn(),
    signInWithGoogle: vi.fn(),
  } as unknown as ReturnType<typeof auth.useAuth>);
}

/* Reports wherever the app ended up, in full — pathname + search + hash. */
function Sink() {
  return <div data-testid="landed">{createPath(useLocation())}</div>;
}

function renderLogin(fromState: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/login', state: fromState }]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Sink />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  signInWithPassword.mockReset().mockResolvedValue(undefined);
});

const COHORT = { pathname: '/browse', search: '?deal=prodej&city=554782', hash: '' };
const RUN_LINK = { pathname: '/listing/-284913', search: '?run=99', hash: '#estimations' };

describe('<Login> already-authed deep link', () => {
  it('restores the full query string, not just the pathname', async () => {
    asAuth({ user: { id: 'u1' } });
    renderLogin({ from: COHORT });
    expect(await screen.findByTestId('landed')).toHaveTextContent(
      '/browse?deal=prodej&city=554782',
    );
  });

  it('restores the hash of a run deep-link', async () => {
    asAuth({ user: { id: 'u1' } });
    renderLogin({ from: RUN_LINK });
    expect(await screen.findByTestId('landed')).toHaveTextContent(
      '/listing/-284913?run=99#estimations',
    );
  });

  /* `/` and not /browse: the index route starts each shell where it starts — the
     feed on a phone, Browse on a desktop (#1767). */
  it('falls back to the app root when nothing was stashed', async () => {
    asAuth({ user: { id: 'u1' } });
    renderLogin(null);
    expect(await screen.findByTestId('landed')).toHaveTextContent(/^\/$/);
  });
});

describe('<Login> a waitlist invitation', () => {
  it('opens on account creation, in Czech', () => {
    asAuth(null);
    render(
      <MemoryRouter initialEntries={['/login?signup=1']}>
        <Routes><Route path="/login" element={<Login />} /></Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: 'Vytvořit účet' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Pokračovat přes Google' })).toBeTruthy();
  });

  it('explains a Google sign-in the invitation check refused, and drops it from the way back', async () => {
    asAuth(null);
    const message = 'Tato adresa zatím nemá pozvánku. Přidejte se na čekací listinu na propscout.cz.';
    renderLogin({ from: { pathname: '/browse', search: `?error=access_denied&error_description=${encodeURIComponent(message)}`, hash: '' } });
    expect(screen.getByText(message)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('E-mail'), { target: { value: 'a@b.cz' } });
    fireEvent.change(screen.getByLabelText('Heslo'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Přihlásit se' }));
    expect(await screen.findByTestId('landed')).toHaveTextContent(/^\/$/);
  });
});

describe('<Login> post-sign-in redirect', () => {
  async function signIn() {
    fireEvent.change(screen.getByLabelText('E-mail'), { target: { value: 'a@b.cz' } });
    fireEvent.change(screen.getByLabelText('Heslo'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Přihlásit se' }));
    await waitFor(() => expect(signInWithPassword).toHaveBeenCalled());
  }

  it('carries the whole stashed location through the sign-in', async () => {
    asAuth(null);
    renderLogin({ from: RUN_LINK });
    await signIn();
    // The mocked auth never flips `session`, so the component stays mounted and
    // the imperative navigate() is what moves the router — which is exactly the
    // consumer under test (Login's navigate(from, { replace: true })).
    expect(await screen.findByTestId('landed')).toHaveTextContent(
      '/listing/-284913?run=99#estimations',
    );
  });

  it('lands on the app root when there was no stashed location', async () => {
    asAuth(null);
    renderLogin(null);
    await signIn();
    expect(await screen.findByTestId('landed')).toHaveTextContent(/^\/$/);
    expect(screen.getByTestId('landed').textContent).not.toBe(ROUTES.browse.build());
  });
});
