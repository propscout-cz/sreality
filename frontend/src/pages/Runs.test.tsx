/* The Runs page: only an allowed account gets a usable form, a refusal says why, an empty skills folder says so, a
 * seeded definition starts a run under one idempotency key, and a list row shows its live state. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { AgentRunOptions, AgentRunRow } from '@/lib/agentRuns';
import { ROUTES } from '@/lib/routes';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchAgentRunOptions: vi.fn(),
  fetchAgentRuns: vi.fn(),
  startAgentRun: vi.fn(),
}));

import { ApiError, fetchAgentRunOptions, fetchAgentRuns, startAgentRun } from '@/lib/api';
import Runs from './Runs';

const options = vi.mocked(fetchAgentRunOptions);
const list = vi.mocked(fetchAgentRuns);
const start = vi.mocked(startAgentRun);

const AGENT = {
  name: 'probe_scout_v1',
  description: 'Finds properties.',
  input_schema: {
    type: 'object', required: ['town'], additionalProperties: false,
    properties: { town: { type: 'string', description: 'Where to look.' }, budget: { type: 'integer' } },
  },
};
const OPEN: AgentRunOptions = { start: { allowed: true, reason: null }, queue: 'open', agents: [AGENT] };

const row = (over: Partial<AgentRunRow> = {}): AgentRunRow => ({
  id: 7, agent: 'probe_scout_v1', status: 'succeeded', attempts: 1, provider: 'anthropic', model: 'claude-opus-5-5',
  cost_usd: 0.1234, stop_reason: 'submit_result', error: null, input: { town: 'Brno' }, cancel_requested_at: null,
  created_at: '2026-11-01T10:00:00Z', started_at: '2026-11-01T10:00:01Z', finished_at: '2026-11-01T10:01:00Z', ...over,
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[ROUTES.runs.build()]}>
        <Routes>
          <Route path={ROUTES.runs.pattern} element={<Runs />} />
          <Route path={ROUTES.runDetail.pattern} element={<p>the run page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  options.mockReset().mockResolvedValue(OPEN);
  list.mockReset().mockResolvedValue({ data: [], truncated: false });
  start.mockReset();
});

describe('the start form', () => {
  it('shows an account off the allowlist why, in plain words, with no form', async () => {
    options.mockResolvedValue({ start: { allowed: false, reason: 'Agent runs are not open to this account yet.' }, queue: null, agents: [] });
    mount();
    expect(await screen.findByText('Agent runs are not open to this account yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start run' })).toBeNull();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByText('Queue')).toBeNull();
  });

  it('says truthfully that no agent exists when the skills folder holds none', async () => {
    options.mockResolvedValue({ start: { allowed: true, reason: null }, queue: 'dark', agents: [] });
    mount();
    expect(await screen.findByText(/No agent is available yet/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start run' })).toBeNull();
  });

  it('says it could not load, and offers a retry, rather than a form or a refusal', async () => {
    options.mockRejectedValue(new ApiError('boom', 500, null));
    mount();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('boom');
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start run' })).toBeNull();
  });

  it('renders a seeded definition, and refuses an empty required field without calling the API', async () => {
    mount();
    expect(await screen.findByText('Finds properties.')).toBeInTheDocument();
    expect(screen.getByLabelText(/town/)).toBeInTheDocument();
    expect(screen.getByLabelText(/budget/)).toHaveAttribute('inputmode', 'numeric');
    expect(screen.getByRole('textbox', { name: 'town *' })).toHaveAttribute('aria-required', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }));
    expect(await screen.findByText('Required.')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'town *' })).toHaveAccessibleDescription(/Required/);
    expect(start).not.toHaveBeenCalled();
  });

  it('starts the run under one key and opens it', async () => {
    start.mockResolvedValue({ id: 41, status: 'queued', created: true });
    mount();
    fireEvent.change(await screen.findByLabelText(/town/), { target: { value: 'Brno' } });
    fireEvent.change(screen.getByLabelText(/budget/), { target: { value: '5000000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }));
    expect(await screen.findByText('the run page')).toBeInTheDocument();
    expect(start).toHaveBeenCalledTimes(1);
    const body = start.mock.calls[0][0];
    expect(body).toMatchObject({ agent: 'probe_scout_v1', input: { town: 'Brno', budget: 5000000 } });
    expect(body.idempotency_key).toMatch(/\S{8,}/);
  });

  it('shows the API refusal as its own sentence and keeps the key for an unchanged retry', async () => {
    start.mockRejectedValueOnce(new ApiError('This account already has 20 runs queued or running.', 429, null));
    start.mockResolvedValueOnce({ id: 42, status: 'queued', created: true });
    mount();
    fireEvent.change(await screen.findByLabelText(/town/), { target: { value: 'Brno' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This account already has 20 runs queued or running.');
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }));
    await screen.findByText('the run page');
    expect(start.mock.calls[0][0].idempotency_key).toBe(start.mock.calls[1][0].idempotency_key);
  });

  it('a changed input is a new run, so it gets a new key', async () => {
    start.mockRejectedValueOnce(new ApiError('nope', 503, null));
    start.mockResolvedValueOnce({ id: 43, status: 'queued', created: true });
    mount();
    const town = await screen.findByLabelText(/town/);
    fireEvent.change(town, { target: { value: 'Brno' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }));
    await screen.findByRole('alert');
    fireEvent.change(town, { target: { value: 'Zlín' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }));
    await screen.findByText('the run page');
    expect(start.mock.calls[0][0].idempotency_key).not.toBe(start.mock.calls[1][0].idempotency_key);
  });

  it('says when a started run will only wait', async () => {
    options.mockResolvedValue({ ...OPEN, queue: 'dark' });
    mount();
    expect(await screen.findByText(/waits until it is switched on/)).toBeInTheDocument();
  });
});

describe('the list', () => {
  it('loads honestly, then says when there are no runs', async () => {
    let resolve: (v: { data: AgentRunRow[]; truncated: boolean }) => void = () => {};
    list.mockReturnValue(new Promise((r) => { resolve = r; }));
    mount();
    expect(await screen.findByText('Loading runs…')).toBeInTheDocument();
    resolve({ data: [], truncated: false });
    expect(await screen.findByText('No runs yet.')).toBeInTheDocument();
  });

  it('shows a failed read as a failure, not as no runs', async () => {
    list.mockRejectedValue(new ApiError('down', 500, null));
    mount();
    expect(await screen.findByRole('alert')).toHaveTextContent('down');
    expect(screen.queryByText('No runs yet.')).toBeNull();
  });

  it('links each row to its run and shows its state and cost', async () => {
    list.mockResolvedValue({ data: [row(), row({ id: 8, status: 'queued', cost_usd: 0, finished_at: null })], truncated: true });
    mount();
    const link = await screen.findByRole('link', { name: '#7' });
    expect(link).toHaveAttribute('href', ROUTES.runDetail.build({ id: 7 }));
    expect(within(link.closest('tr') as HTMLElement).getByText('$0.12')).toBeInTheDocument();
    expect(within(link.closest('tr') as HTMLElement).getByText('succeeded')).toBeInTheDocument();
    expect(screen.getByText('queued')).toBeInTheDocument();
    expect(screen.getByText('Showing the newest 2')).toBeInTheDocument();
  });

  it('keeps the list on screen when a later read fails', async () => {
    list.mockResolvedValue({ data: [row({ status: 'running', finished_at: null })], truncated: false });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mount();
      await screen.findByRole('link', { name: '#7' });
      list.mockRejectedValue(new ApiError('down', 503, null));
      await vi.advanceTimersByTimeAsync(3100);
      await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1));
      expect(screen.getByRole('link', { name: '#7' })).toBeInTheDocument();
      expect(screen.queryByRole('alert')).toBeNull();
      expect(await screen.findByText(/The last refresh failed/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('polls while a run is live and stops once none is', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      list.mockResolvedValue({ data: [row({ status: 'running', finished_at: null })], truncated: false });
      mount();
      await screen.findByRole('link', { name: '#7' });
      const before = list.mock.calls.length;
      list.mockResolvedValue({ data: [row()], truncated: false });
      await vi.advanceTimersByTimeAsync(3100);
      await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(before));
      const settled = list.mock.calls.length;
      await vi.advanceTimersByTimeAsync(9000);
      expect(list.mock.calls.length).toBe(settled);
    } finally {
      vi.useRealTimers();
    }
  });
});
