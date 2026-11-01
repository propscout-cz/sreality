/* One run's page. The acceptance criterion lives here: a recorded run's steps and result render without executing any
 * model-written link or image, wherever the model's words land. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { AgentProposal, AgentRun, AgentRunFile, AgentRunStep } from '@/lib/agentRuns';
import { ROUTES } from '@/lib/routes';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchAgentRunOptions: vi.fn(),
  fetchAgentRun: vi.fn(),
  fetchAgentRunSteps: vi.fn(),
  fetchAgentRunFile: vi.fn(),
  fetchAgentRunProposals: vi.fn(),
  applyAgentProposal: vi.fn(),
  undoAgentProposal: vi.fn(),
  cancelAgentRun: vi.fn(),
}));

vi.mock('@/lib/shareFiles', () => ({ saveBlob: vi.fn() }));

import {
  ApiError, applyAgentProposal, cancelAgentRun, fetchAgentRun, fetchAgentRunFile, fetchAgentRunOptions, fetchAgentRunProposals,
  fetchAgentRunSteps, undoAgentProposal,
} from '@/lib/api';
import { saveBlob } from '@/lib/shareFiles';
import RunDetail from './RunDetail';

const options = vi.mocked(fetchAgentRunOptions);
const fetchRun = vi.mocked(fetchAgentRun);
const fetchSteps = vi.mocked(fetchAgentRunSteps);
const cancel = vi.mocked(cancelAgentRun);
const fetchFile = vi.mocked(fetchAgentRunFile);
const save = vi.mocked(saveBlob);

const file = (over: Partial<AgentRunFile> = {}): AgentRunFile =>
  ({ id: 5, name: 'model.xlsx', media_type: 'text/csv', size_bytes: 2048, stored: true, ...over });
const fetchProposals = vi.mocked(fetchAgentRunProposals);
const apply = vi.mocked(applyAgentProposal);
const undo = vi.mocked(undoAgentProposal);

const proposal = (over: Partial<AgentProposal> = {}): AgentProposal => ({
  index: 0, entry: 'create_collection', state: 'open', title: 'New collection', before: ['No such collection.'],
  after: ['A new collection holding 1 property.'], agent_text: [{ label: 'Name', text: 'Shortlist' }],
  properties: [{ property_id: 42, title: '2+kk · 54 m² · Brno' }], filters: [], refused: null, answer: null, applied_at: null,
  undone_at: null, undo: null, undo_confirm: false, ...over,
});
const applied = (over: Partial<AgentProposal> = {}): AgentProposal => proposal({
  state: 'applied', applied_at: '2026-11-01T12:00:00Z', answer: 'Created the collection with 1 of the properties.',
  undo: { available: true, says: 'Deletes the collection.' }, ...over,
});

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
  fetchFile.mockReset();
  save.mockReset();
  fetchProposals.mockReset().mockResolvedValue({ data: [] });
  apply.mockReset();
  undo.mockReset();
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
      files: [file({ id: 1, name: `${IMG}.xlsx` }), file({ id: 2, name: 'javascript:alert(1)', stored: false })],
      proposals: [{ entry: 'add_note', args: { body: IMG } }],
      output: { note: HOSTILE, href: 'javascript:alert(1)' },
      cited_property_ids: [12, 'javascript:alert(1)', -1, 34] as unknown as number[],
    }));
    fetchProposals.mockResolvedValue({ data: [proposal({ title: 'New note', agent_text: [{ label: 'Note', text: HOSTILE }] })] });
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
    expect(within(screen.getByRole('region', { name: 'Files' })).getByText(`${IMG}.xlsx`)).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Proposals' }).textContent).toContain(HOSTILE);
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
    expect(screen.getByText('Files appear when the run ends.')).toBeInTheDocument();
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

  it('lists a file by name and size and saves it as a download, under its own run and id', async () => {
    const bytes = new Blob(['x']);
    fetchFile.mockResolvedValue(bytes);
    fetchRun.mockResolvedValue(run({ id: 7, files: [file()] }));
    mount();
    const files = await screen.findByRole('region', { name: 'Files' });
    expect(within(files).getByText('model.xlsx')).toBeInTheDocument();
    expect(within(files).getByText('2 KB')).toBeInTheDocument();
    expect(within(files).queryByRole('link')).toBeNull();
    fireEvent.click(within(files).getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(bytes, 'model.xlsx'));
    expect(fetchFile).toHaveBeenCalledWith(7, 5);
  });

  it('says a failed download failed, and leaves the file there to try again', async () => {
    fetchFile.mockRejectedValue(new Error('File storage is not answering; try again shortly.'));
    fetchRun.mockResolvedValue(run({ files: [file()] }));
    mount();
    const files = await screen.findByRole('region', { name: 'Files' });
    fireEvent.click(within(files).getByRole('button', { name: 'Download' }));
    expect(await within(files).findByRole('alert')).toHaveTextContent('not answering');
    expect(within(files).getByRole('button', { name: 'Download' })).toBeEnabled();
    expect(save).not.toHaveBeenCalled();
  });

  it('says a file was not stored and offers nothing to download', async () => {
    fetchRun.mockResolvedValue(run({ files: [file({ stored: false })] }));
    mount();
    const files = await screen.findByRole('region', { name: 'Files' });
    expect(within(files).getByText('Not stored')).toBeInTheDocument();
    expect(within(files).getByText(/couldn’t be stored/)).toBeInTheDocument();
    expect(within(files).queryByRole('button')).toBeNull();
  });

  it('says an ended run with no files has none', async () => {
    fetchRun.mockResolvedValue(run({ files: [] }));
    mount();
    const files = await screen.findByRole('region', { name: 'Files' });
    expect(within(files).getByText('No files.')).toBeInTheDocument();
  });
});

describe('proposals', () => {
  const ONE = [{ entry: 'create_collection', args: { name: 'Shortlist' } }];
  const panel = async () => {
    mount();
    const region = await screen.findByRole('region', { name: 'Proposals' });
    return within(region);
  };

  it('draws the before and after, marks the agent’s words, and links only the property the server names', async () => {
    fetchRun.mockResolvedValue(run({ proposals: ONE }));
    fetchProposals.mockResolvedValue({ data: [proposal({ agent_text: [{ label: 'Name', text: JS }] })] });
    const p = await panel();
    expect(await p.findByText('No such collection.')).toBeInTheDocument();
    expect(p.getByText('A new collection holding 1 property.')).toBeInTheDocument();
    expect(p.getByText('Written by the agent')).toBeInTheDocument();
    expect(p.getByText(JS)).toBeInTheDocument();
    expect(p.getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([ROUTES.property.build({ propertyId: 42 })]);
  });

  it('applies on a click, once, and shows what the server then says', async () => {
    fetchRun.mockResolvedValue(run({ proposals: ONE }));
    fetchProposals.mockResolvedValueOnce({ data: [proposal()] }).mockResolvedValue({ data: [applied()] });
    apply.mockResolvedValue(applied());
    const p = await panel();
    fireEvent.click(await p.findByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(apply).toHaveBeenCalledWith(7, 0));
    expect(await p.findByText('Created the collection with 1 of the properties.')).toBeInTheDocument();
    expect(p.getByText('Applied')).toBeInTheDocument();
    expect(p.queryByRole('button', { name: 'Apply' })).toBeNull();
    expect(p.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(apply).toHaveBeenCalledTimes(1);
  });

  it('holds the Apply button while its write is in flight', async () => {
    fetchRun.mockResolvedValue(run({ proposals: ONE }));
    fetchProposals.mockResolvedValue({ data: [proposal()] });
    apply.mockReturnValue(new Promise(() => {}));
    const p = await panel();
    fireEvent.click(await p.findByRole('button', { name: 'Apply' }));
    expect(await p.findByRole('button', { name: 'Applying…' })).toBeDisabled();
  });

  it('undoes on a click and says what it will leave', async () => {
    fetchRun.mockResolvedValue(run({ proposals: ONE }));
    fetchProposals.mockResolvedValueOnce({
      data: [applied({ undo: { available: true, says: 'Removes the 1 the agent added and keeps the collection: it also holds 2 of yours.' } })],
    }).mockResolvedValue({ data: [applied({ state: 'undone', undone_at: '2026-11-01T13:00:00Z', undo: null })] });
    undo.mockResolvedValue(applied({ state: 'undone', undo: null }));
    const p = await panel();
    expect(await p.findByText(/also holds 2 of yours/)).toBeInTheDocument();
    fireEvent.click(p.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(undo).toHaveBeenCalledWith(7, 0));
    expect(await p.findByText('Undone')).toBeInTheDocument();
    expect(p.queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('offers no Undo where it cannot work, and says why', async () => {
    fetchRun.mockResolvedValue(run({ proposals: ONE }));
    fetchProposals.mockResolvedValue({
      data: [applied({ undo: { available: false, says: 'You renamed or edited this collection, so Undo leaves it to you.' } })],
    });
    const p = await panel();
    expect(await p.findByText(/You renamed or edited/)).toBeInTheDocument();
    expect(p.queryByRole('button')).toBeNull();
  });

  it('takes a pipeline card back only behind the one confirm step (rule 22)', async () => {
    fetchRun.mockResolvedValue(run({ proposals: [{ entry: 'add_pipeline_card', args: { property_id: 42 } }] }));
    fetchProposals.mockResolvedValue({
      data: [applied({ entry: 'add_pipeline_card', title: 'Pipeline card', undo_confirm: true, undo: { available: true, says: 'Takes this card off your board.' } })],
    });
    undo.mockResolvedValue(applied());
    const p = await panel();
    fireEvent.click(await p.findByRole('button', { name: 'Undo' }));
    expect(undo).not.toHaveBeenCalled();
    fireEvent.click(p.getByRole('button', { name: 'Keep it' }));
    expect(undo).not.toHaveBeenCalled();
    fireEvent.click(p.getByRole('button', { name: 'Undo' }));
    fireEvent.click(p.getByRole('button', { name: 'Yes, take the card off' }));
    await waitFor(() => expect(undo).toHaveBeenCalledWith(7, 0));
  });

  it('says why a write failed, beside the proposal, and keeps the proposal open', async () => {
    fetchRun.mockResolvedValue(run({ proposals: ONE }));
    fetchProposals.mockResolvedValue({ data: [proposal()] });
    apply.mockRejectedValue(new ApiError('collection name already exists', 409, null));
    const p = await panel();
    fireEvent.click(await p.findByRole('button', { name: 'Apply' }));
    expect(await p.findByRole('alert')).toHaveTextContent('Not applied: collection name already exists');
    expect(p.getByRole('button', { name: 'Apply' })).toBeEnabled();
  });

  it('offers no Apply on a proposal today’s schema refuses', async () => {
    fetchRun.mockResolvedValue(run({ proposals: ONE }));
    fetchProposals.mockResolvedValue({ data: [proposal({ refused: 'unknown field', before: [], after: [] })] });
    const p = await panel();
    expect(await p.findByText(/This can’t be applied/)).toBeInTheDocument();
    expect(p.queryByRole('button')).toBeNull();
  });

  it('says so when they cannot be read, and offers another try', async () => {
    fetchRun.mockResolvedValue(run({ proposals: ONE }));
    fetchProposals.mockRejectedValue(new Error('down'));
    const p = await panel();
    expect(await p.findByRole('alert')).toHaveTextContent('Could not load the proposals: down');
    expect(p.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
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
