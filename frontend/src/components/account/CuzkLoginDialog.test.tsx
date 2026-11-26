/* The avatar's ČÚZK login dialog: it shows whether a login is stored, saves through the API, clears the password
 * after a save, says why a save failed, and removes the login. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchCuzkLogin: vi.fn(),
  saveCuzkLogin: vi.fn(),
  removeCuzkLogin: vi.fn(),
}));

import { ApiError, fetchCuzkLogin, removeCuzkLogin, saveCuzkLogin } from '@/lib/api';
import CuzkLoginDialog from './CuzkLoginDialog';

const load = vi.mocked(fetchCuzkLogin);
const save = vi.mocked(saveCuzkLogin);
const remove = vi.mocked(removeCuzkLogin);

const STORED = { set: true, username: 'firma01', checked_at: '2026-11-22T10:00:00+00:00' };

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><CuzkLoginDialog onClose={vi.fn()} /></QueryClientProvider>);
}

const user = () => screen.findByRole('textbox', { name: 'WSDP username' });
const pass = (label = 'Password') => screen.getByLabelText(label);

beforeEach(() => {
  load.mockReset().mockResolvedValue({ set: false, username: null, checked_at: null });
  save.mockReset().mockResolvedValue(STORED);
  remove.mockReset().mockResolvedValue(undefined);
});

describe('<CuzkLoginDialog>', () => {
  it('says no login is stored and offers no removal', async () => {
    mount();
    expect(await user()).toHaveValue('');
    expect(screen.getByText('No login stored.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove login' })).toBeNull();
  });

  it('says so when ČÚZK refused the stored login in use, instead of "accepted"', async () => {
    load.mockResolvedValue({ ...STORED, refused: true });
    mount();
    expect(await screen.findByText(/ČÚZK refused the login stored for/)).toBeInTheDocument();
    expect(screen.getByText(/further attempts cannot lock your ČÚZK account/)).toBeInTheDocument();
    expect(screen.queryByText(/accepted by ČÚZK on/)).toBeNull();
  });

  it('saves a login, then shows it as stored with the password box emptied', async () => {
    mount();
    fireEvent.change(await user(), { target: { value: ' firma01 ' } });
    fireEvent.change(pass(), { target: { value: 'heslo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check and save' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ username: 'firma01', password: 'heslo' }, expect.anything()));
    expect(await screen.findByText('Saved.')).toBeInTheDocument();
    expect(screen.getByText(/Stored for/)).toHaveTextContent('firma01');
    expect(pass('New password')).toHaveValue('');
  });

  it('shows a stored login with its username and never a password', async () => {
    load.mockResolvedValue(STORED);
    mount();
    expect(await user()).toHaveValue('firma01');
    expect(pass('New password')).toHaveValue('');
  });

  it('says why a save failed and keeps what was typed', async () => {
    save.mockRejectedValue(new ApiError('ČÚZK refused this login.', 422, null, 'http'));
    mount();
    fireEvent.change(await user(), { target: { value: 'firma01' } });
    fireEvent.change(pass(), { target: { value: 'spatne' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check and save' }));
    expect(await screen.findByText(/ČÚZK refused this login/)).toBeInTheDocument();
    expect(pass()).toHaveValue('spatne');
  });

  it('removes a stored login', async () => {
    load.mockResolvedValue(STORED);
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove login' }));
    expect(await screen.findByText('Removed.')).toBeInTheDocument();
    expect(screen.getByText('No login stored.')).toBeInTheDocument();
    expect(remove).toHaveBeenCalled();
  });
});
