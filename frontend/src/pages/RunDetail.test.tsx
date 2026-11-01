/* One run's page. The acceptance criterion lives here: a recorded run's steps and result render without executing any
 * model-written link or image, wherever the model's words land. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { AgentRun, AgentRunStep } from '@/lib/agentRuns';
import { ROUTES } from '@/lib/routes';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchAgentRunOptions: vi.fn(),
  fetchAgentRun: vi.fn(),
  fetchAgentRunSteps: vi.fn(),
  cancelAgentRun: vi.fn(),
}));

import { ApiError, cancelAgentRun, fetchAgentRun, fetchAgentRunOptions, fetchAgentRunSteps } from '@/lib/api';
import RunDetail from './RunDetail';

const options = vi.mocked(fetchAgentRunOptions);
const fetchRun = vi.mocked(fetchAgentRun);
const fetchSteps = vi.mocked(fetchAgentRunSteps);
const cancel = vi.mocked(cancelAgentRun);

const IMG = '<img src="https://evil.example/x.png" onerror="alert(1)">';
const JS = '[click me](javascript:alert(1))';
const MD_IMG = '![pixel](https://evil.example/p.png)';
const BARE = 'https://evil.example/bare';
const SCRIPT = '<script>alert(1)</script>';
const HOSTILE = `# Heading\n${IMG}\n${JS}\n${MD_IMG}\n${BARE}\n${SCRIPT}\n<a href="https://evil.example">a</a>`;

const run = (over: Partial<AgentRun> = {}): AgentRun => ({
  id: 7, agent: 'probe_scout_v1', status: 'succeeded', attempts: 1, provider: 'anthropic', model: 'claude-opus-5-5',
  cost_usd: 0.1234, stop_reason: 'submit_result', error: null, input: { town: 'Brno' }, cancel_requested_at: null,
  created_at: '2026-11-01T10:00:00Z', started_at: '2026-11-01T10:00:01Z', finished_at: '2026-11-01T10:01:00Z',
  result_markdown: 'A shortlist.', cited_property_ids: [], files: [], proposals: [], output: null,
  definition_sha: 'defsha', tool_set_sha: 'toolsha', commit_sha: 'c0mm1t', ...over,
});

const step = (over: Partial<AgentRunStep>): AgentRunStep => ({
  attempt: 1, n: 1, kind: 'tool', name: 'search_properties', summary: '{}', input: {}, text: null, cost_usd: 0,
  duration_ms: 12, created_at: '2026-11-01T10:00:00Z', ...over,
});

function mount(id: string | number = 7) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/runs/${id}`]}>
        <Routes>
          <Route path={ROUTES.runDetail.pattern} element={<RunDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function expectInert(root: HTMLElement) {
  expect(root.querySelector('img, script, iframe, object, embed, picture, video, audio, source, svg image')).toBeNull();
  for (const el of root.querySelectorAll('*')) {
    for (const attr of el.getAttributeNames()) {
      expect(attr.startsWith('on'), `${el.tagName} carries ${attr}`).toBe(false);
    }
  }
  for (const a of root.querySelectorAll('a')) {
    const href = a.getAttribute('href') ?? '';
    expect(href === ROUTES.runs.build() || /^\/property\/\d+$/.test(href), `a link we did not build: ${href}`).toBe(true);
  }
}

beforeEach(() => {
  options.mockReset().mockResolvedValue({ start: { allowed: true, reason: null }, queue: 'open', agents: [], limit: null });
  fetchRun.mockReset().mockResolvedValue(run());
  fetchSteps.mockReset().mockResolvedValue({ data: [] });
  cancel.mockReset();
});

describe('model-written text stays inert', () => {
  it('in the result, every step, the input, the error, a file name, a proposal and the output', async () => {
    fetchRun.mockResolvedValue(run({
      status: 'failed',
      result_markdown: HOSTILE,
      error: HOSTILE,
      stop_reason: IMG,
      input: { town: HOSTILE },
      files: [`${IMG}.xlsx`, 'javascript:alert(1)'],
      proposals: [{ entry: IMG, args: { title: JS, html: IMG } }],
      output: { note: HOSTILE, href: 'javascript:alert(1)' },
      cited_property_ids: [12, 'javascript:alert(1)', -1, 34] as unknown as number[],
    }));
    fetchSteps.mockResolvedValue({
      data: [
        step({ n: 1, kind: 'model', name: IMG, text: [HOSTILE], input: { max_tokens: 1 }, cost_usd: 0.01 }),
        step({ n: 2, kind: 'tool', name: JS, input: { q: HOSTILE, html: IMG }, summary: HOSTILE }),
        step({ n: 3, kind: 'file', name: IMG, input: { file_id: JS }, summary: HOSTILE }),
      ],
    });
    const { container } = mount();
    await screen.findByText(/A shortlist|^Result$/);
    await screen.findAllByText(/search_properties|reasoning/);

    expectInert(container);
    // Open every step, whatever the default, and look again.
    for (const button of within(container).getAllByRole('button')) {
      if (button.getAttribute('aria-expanded') === 'false') fireEvent.click(button);
    }
    await waitFor(() => expect(container.textContent).toContain(`"q": ${JSON.stringify(HOSTILE)}`));
    expectInert(container);

    // The words are there, as words.
    for (const words of [IMG, JS, MD_IMG, BARE, SCRIPT]) {
      expect(container.textContent).toContain(words);
    }
    const result = screen.getByRole('region', { name: 'Result' });
    expect(within(result).getByText((_, el) => el?.tagName === 'DIV' && el.textContent === HOSTILE)).toHaveClass('whitespace-pre-wrap');
  });

  it('links only the cited ids, to pages we build, and nothing the words name', async () => {
    fetchRun.mockResolvedValue(run({
      result_markdown: 'See /property/999 and https://evil.example',
      cited_property_ids: [12, 34],
    }));
    const { container } = mount();
    expect(await screen.findByRole('link', { name: '#12' })).toHaveAttribute('href', ROUTES.property.build({ propertyId: 12 }));
    expect(screen.getByRole('link', { name: '#34' })).toHaveAttribute('href', ROUTES.property.build({ propertyId: 34 }));
    expect([...container.querySelectorAll('a')].filter((a) => a.getAttribute('href')?.startsWith('/property/'))).toHaveLength(2);
    expect(container.querySelector('a[href*="999"], a[href*="evil"]')).toBeNull();
  });
});

describe('a run read honestly', () => {
  it('says while loading, and says a foreign or missing run is not there', async () => {
    fetchRun.mockRejectedValue(new ApiError('no such run', 404, null));
    fetchSteps.mockRejectedValue(new ApiError('no such run', 404, null));
    mount();
    expect(screen.getByText('Loading the run…')).toBeInTheDocument();
    expect(await screen.findByText(/There is no run #7 on your account/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /All runs/ })).toHaveAttribute('href', ROUTES.runs.build());
  });

  it('treats a garbled id as no run, asking the API nothing', async () => {
    mount('abc');
    expect(await screen.findByText(/There is no run/)).toBeInTheDocument();
    expect(fetchRun).not.toHaveBeenCalled();
  });

  it('shows another failure as a failure', async () => {
    fetchRun.mockRejectedValue(new ApiError('database busy', 503, null));
    mount();
    expect(await screen.findByRole('alert')).toHaveTextContent('database busy');
  });

  it('says the account\'s own limit holds a queued run, with the figures', async () => {
    options.mockResolvedValue({ start: { allowed: true, reason: null }, queue: 'limited', agents: [], limit: { limit_usd: 10, spent_today_usd: 10.5 } });
    fetchRun.mockResolvedValue(run({ status: 'queued', result_markdown: null, stop_reason: null, cost_usd: 0, finished_at: null, started_at: null }));
    mount();
    expect(await screen.findByText(/spent \$10\.50 of its \$10\.00 daily limit/)).toBeInTheDocument();
  });

  it('says why a queued run waits, and shows the empty sections as empty', async () => {
    options.mockResolvedValue({ start: { allowed: true, reason: null }, queue: 'dark', agents: [], limit: null });
    fetchRun.mockResolvedValue(run({ status: 'queued', result_markdown: null, stop_reason: null, cost_usd: 0, finished_at: null, started_at: null }));
    mount();
    expect(await screen.findByText(/switched off/)).toBeInTheDocument();
    expect(screen.getByText('No result yet.')).toBeInTheDocument();
    expect(screen.getByText('No steps yet.')).toBeInTheDocument();
    expect(screen.getByText('No files.')).toBeInTheDocument();
    expect(screen.getByText('No proposals.')).toBeInTheDocument();
  });

  it('shows the failure with its reason and no result', async () => {
    fetchRun.mockResolvedValue(run({ status: 'failed', result_markdown: null, error: 'the model call failed' }));
    mount();
    expect(await screen.findByRole('alert')).toHaveTextContent('the model call failed');
    expect(screen.getByText('This run ended without a result.')).toBeInTheDocument();
  });

  it('shows a retry as two attempts and records what ran', async () => {
    fetchRun.mockResolvedValue(run({ attempts: 2 }));
    fetchSteps.mockResolvedValue({ data: [step({ attempt: 1, n: 1 }), step({ attempt: 2, n: 1, name: 'rank_brokers' })] });
    mount();
    expect(await screen.findByRole('heading', { name: 'Attempt 2' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Attempt 1' })).toBeInTheDocument();
    expect(screen.getByText('defsha')).toBeInTheDocument();
    expect(screen.getByText('toolsha')).toBeInTheDocument();
    expect(screen.getByText('c0mm1t')).toBeInTheDocument();
  });

  it('shows files and proposals as plain data: no link, no Apply, no Undo', async () => {
    fetchRun.mockResolvedValue(run({ files: ['model.xlsx'], proposals: [{ entry: 'create_collection', args: { name: 'Shortlist' } }] }));
    mount();
    const files = await screen.findByRole('region', { name: 'Files' });
    expect(within(files).getByText('model.xlsx')).toBeInTheDocument();
    expect(within(files).queryByRole('link')).toBeNull();
    const proposals = screen.getByRole('region', { name: 'Proposals' });
    expect(within(proposals).getByText('create_collection')).toBeInTheDocument();
    expect(within(proposals).getByText(/"Shortlist"/)).toBeInTheDocument();
    expect(within(proposals).queryByRole('button')).toBeNull();
  });
});

describe('cancel', () => {
  it('asks once more, then marks the run and refreshes it', async () => {
    fetchRun.mockResolvedValue(run({ status: 'running', result_markdown: null, finished_at: null }));
    cancel.mockResolvedValue({ id: 7, status: 'running' });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel run' }));
    expect(cancel).not.toHaveBeenCalled();
    fetchRun.mockResolvedValue(run({ status: 'running', result_markdown: null, finished_at: null, cancel_requested_at: '2026-11-01T10:00:30Z' }));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, cancel it' }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith(7));
    expect(await screen.findByText(/Stopping at its next step/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel run' })).toBeNull();
  });

  it('keeps the run when the second answer is no', async () => {
    fetchRun.mockResolvedValue(run({ status: 'queued', result_markdown: null, finished_at: null }));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel run' }));
    fireEvent.click(screen.getByRole('button', { name: 'Keep it' }));
    expect(cancel).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Cancel run' })).toBeInTheDocument();
  });

  it('offers nothing on a run that has ended', async () => {
    mount();
    await screen.findByText('A shortlist.');
    expect(screen.queryByRole('button', { name: 'Cancel run' })).toBeNull();
  });

  it('shows a cancel the API refused', async () => {
    fetchRun.mockResolvedValue(run({ status: 'running', result_markdown: null, finished_at: null }));
    cancel.mockRejectedValue(new ApiError('database busy', 503, null));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel run' }));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, cancel it' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('database busy');
  });
});

describe('live refresh', () => {
  it('says the diary may be short when the read at the end of a run fails, and offers another', async () => {
    fetchRun.mockResolvedValue(run({ status: 'running', result_markdown: null, finished_at: null }));
    fetchSteps.mockResolvedValue({ data: [step({ name: 'rank_brokers' })] });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mount();
      await screen.findAllByText('rank_brokers');
      fetchRun.mockResolvedValue(run());
      fetchSteps.mockRejectedValue(new ApiError('database busy', 503, null));
      await vi.advanceTimersByTimeAsync(3100);
      expect(await screen.findByText(/this list may be short/)).toBeInTheDocument();
      fetchSteps.mockResolvedValue({ data: [step({ name: 'rank_brokers' }), step({ n: 2, name: 'submit_result' })] });
      fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
      await screen.findAllByText('submit_result');
      expect(screen.queryByText(/this list may be short/)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('reads a run the queue holds rarely, not every few seconds', async () => {
    options.mockResolvedValue({ start: { allowed: true, reason: null }, queue: 'dark', agents: [], limit: null });
    fetchRun.mockResolvedValue(run({ status: 'queued', result_markdown: null, finished_at: null, started_at: null }));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mount();
      await screen.findByText(/switched off/);
      const reads = fetchRun.mock.calls.length;
      await vi.advanceTimersByTimeAsync(9000);
      expect(fetchRun.mock.calls.length - reads).toBeLessThanOrEqual(3);
      await vi.advanceTimersByTimeAsync(30_000);
      expect(fetchRun.mock.calls.length).toBeGreaterThan(reads);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the last good read on screen when a poll fails', async () => {
    fetchRun.mockResolvedValue(run({ status: 'running', result_markdown: null, finished_at: null }));
    fetchSteps.mockResolvedValue({ data: [step({ name: 'rank_brokers' })] });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mount();
      await screen.findAllByText('rank_brokers');
      fetchRun.mockRejectedValue(new ApiError('database busy', 503, null));
      fetchSteps.mockRejectedValue(new ApiError('database busy', 503, null));
      await vi.advanceTimersByTimeAsync(3100);
      await waitFor(() => expect(fetchRun.mock.calls.length).toBeGreaterThan(1));
      expect(screen.getAllByText('rank_brokers').length).toBeGreaterThan(0);
      expect(screen.queryByRole('alert')).toBeNull();
      expect((await screen.findAllByText(/The last refresh failed/)).length).toBeGreaterThan(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('polls the run and its steps while live, and fetches the steps once more at the end', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      fetchRun.mockResolvedValue(run({ status: 'running', result_markdown: null, finished_at: null }));
      mount();
      await screen.findByRole('button', { name: 'Cancel run' });
      const stepsBefore = fetchSteps.mock.calls.length;
      fetchRun.mockResolvedValue(run());
      fetchSteps.mockResolvedValue({ data: [step({ name: 'rank_brokers' })] });
      await vi.advanceTimersByTimeAsync(3100);
      await screen.findByText('A shortlist.');
      await screen.findAllByText('rank_brokers');
      expect(fetchSteps.mock.calls.length).toBeGreaterThan(stepsBefore);
      const runs = fetchRun.mock.calls.length;
      const steps = fetchSteps.mock.calls.length;
      await vi.advanceTimersByTimeAsync(9000);
      expect(fetchRun.mock.calls.length).toBe(runs);
      expect(fetchSteps.mock.calls.length).toBe(steps);
    } finally {
      vi.useRealTimers();
    }
  });
});
