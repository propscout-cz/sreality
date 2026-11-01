/* The avatar's limit dialog: it shows the limit and today's spend, saves through the API, says when a save failed, and
 * catches what is plainly no amount before the round trip. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AgentRunOptions } from '@/lib/agentRunOptions';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchAgentRunOptions: vi.fn(),
  setAgentRunLimit: vi.fn(),
}));

import { ApiError, fetchAgentRunOptions, setAgentRunLimit } from '@/lib/api';
import AgentLimitDialog from './AgentLimitDialog';

const options = vi.mocked(fetchAgentRunOptions);
const save = vi.mocked(setAgentRunLimit);

const ALLOWED: AgentRunOptions = {
  start: { allowed: true, reason: null }, queue: 'open', agents: [], limit: { limit_usd: 10, spent_today_usd: 3.2 },
};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AgentLimitDialog onClose={vi.fn()} /></QueryClientProvider>);
}

const field = () => screen.findByRole('textbox', { name: /USD per day/ });

beforeEach(() => {
  options.mockReset().mockResolvedValue(ALLOWED);
  save.mockReset().mockResolvedValue({ limit_usd: 12.5, spent_today_usd: 3.2 });
});

describe('<AgentLimitDialog>', () => {
  it('shows a loading state, then the limit, the unit and today\'s spend', async () => {
    mount();
    expect(screen.getByText(/Loading your limit/)).toBeInTheDocument();
    expect(await field()).toHaveValue('10');
    expect(screen.getByText('$3.20')).toBeInTheDocument();
    expect(screen.getByText('$10.00')).toBeInTheDocument();
    expect(screen.getByText(/up to two runs' caps/)).toBeInTheDocument();
  });

  it('saves the number, says so, and reads the figures again', async () => {
    mount();
    fireEvent.change(await field(), { target: { value: '12,50' } });
    options.mockResolvedValue({ ...ALLOWED, limit: { limit_usd: 12.5, spent_today_usd: 3.2 } });
    const reads = options.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: 'Save limit' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.');
    expect(save.mock.calls[0][0]).toBe(12.5);
    await waitFor(() => expect(options.mock.calls.length).toBeGreaterThan(reads));
    expect(await screen.findByText('$12.50')).toBeInTheDocument();
  });

  it('takes 0, which means no run starts', async () => {
    mount();
    fireEvent.change(await field(), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save limit' }));
    await waitFor(() => expect(save.mock.calls[0][0]).toBe(0));
  });

  it.each(['-1', 'ten', '', '1.234', '1e3', 'NaN'])('refuses %j before the round trip', async (value) => {
    mount();
    fireEvent.change(await field(), { target: { value } });
    fireEvent.click(screen.getByRole('button', { name: 'Save limit' }));
    expect(await screen.findByText(/an amount of 0 or more/)).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it('shows the API\'s words when a save fails', async () => {
    save.mockRejectedValue(new ApiError('The limit must be a number of dollars.', 422, null));
    mount();
    fireEvent.change(await field(), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save limit' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The limit must be a number of dollars.');
  });

  it('offers a retry when the figures do not load', async () => {
    options.mockRejectedValue(new Error('down'));
    mount();
    expect(await screen.findByRole('alert')).toHaveTextContent('down');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows only the reason once the account can no longer use agents', async () => {
    options.mockResolvedValue({ start: { allowed: false, reason: 'Your plan does not include agent runs.' }, queue: null, agents: [], limit: null });
    mount();
    expect(await screen.findByText('Your plan does not include agent runs.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).toBeNull();
  });
});
