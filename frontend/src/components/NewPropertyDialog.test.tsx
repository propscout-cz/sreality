/* "Nová nemovitost" (docs/design/new-property-from-link.md §3.6): the flow's state
 * machine against a mocked API — queued → attaching → card → toast; the three
 * refusal states as one sentence; an inactive held ad still gets its card; an ad
 * already on the board says so with its stage; an API failure stays inline and
 * never reaches the global toast; the poll survives a transient error, has no
 * cap, and stops on unmount or a changed link; the card lands on the board the
 * dialog was opened from. The provider: admins only, prefilled, lazy, closable.
 *
 * Polling runs on fake setTimeout (the 2 s beat), advanced explicitly; the
 * provider's own tests stay on real timers for the lazy chunk. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import * as auth from '@/lib/auth';
import { createMutationCache } from '@/lib/mutationCache';
import * as toast from '@/lib/toast';
import type { IngestState, PipelineShare } from '@/lib/types';
import { expectDialogContract } from '@/test/a11y';

import NewPropertyDialog from './NewPropertyDialog';
import { NewPropertyProvider, useNewPropertyDialog, type NewPropertyPrefill } from './NewPropertyProvider';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  ingestListing: vi.fn(),
  ingestState: vi.fn(),
  addPipelineCard: vi.fn(),
}));
vi.mock('@/lib/toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/toast')>()),
  pushToast: vi.fn(() => 7),
  dismissToast: vi.fn(),
}));
vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));

const URL_IDNES = 'https://reality.idnes.cz/detail/prodej/byt/praha-5-smichov/6ad7ca9d45dd870c0a0b152a/';
const NATIVE = '6ad7ca9d45dd870c0a0b152a';

const state = (over: Partial<IngestState>): IngestState => ({
  state: 'queued',
  message_cs: null,
  source: 'idnes',
  source_id_native: NATIVE,
  listing_id: null,
  is_active: null,
  property_id: null,
  queue: { attempts: 0, claimed_at: null, given_up: false },
  ...over,
});
const QUEUED = state({});
const UNBORN = state({ state: 'held', listing_id: 123, is_active: true, queue: null });
const HELD = state({ state: 'held', listing_id: 123, is_active: true, property_id: 456, queue: null });
const ADDED = { property_id: 456, stage_key: 'interested', stage_label: 'Zájem', added: true };

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname}</p>;
}

const client = () =>
  new QueryClient({
    mutationCache: createMutationCache(),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

function renderDialog(prefill: NewPropertyPrefill = {}) {
  const onClose = vi.fn();
  const utils = render(
    <QueryClientProvider client={client()}>
      <MemoryRouter initialEntries={['/here']}>
        <Routes>
          <Route path="/here" element={<NewPropertyDialog prefill={prefill} onClose={onClose} />} />
          <Route path="/property/:propertyId" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, onClose };
}

const field = () => screen.getByLabelText('Odkaz na inzerát');
const tick = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));

async function paste(text: string) {
  fireEvent.change(field(), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Přidat' }));
  await tick();
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  vi.mocked(auth.useAuth).mockReturnValue({ isAdmin: true } as ReturnType<typeof auth.useAuth>);
  for (const fn of [api.ingestListing, api.ingestState, api.addPipelineCard, toast.pushToast, toast.dismissToast]) {
    vi.mocked(fn).mockReset();
  }
  vi.mocked(toast.pushToast).mockReturnValue(7);
  vi.mocked(api.addPipelineCard).mockResolvedValue(ADDED);
});
afterEach(() => vi.useRealTimers());

describe('<NewPropertyDialog> the flow', () => {
  it('fetches the page, waits for the birth, adds the card and stays with a toast (Pipeline)', async () => {
    let answer!: (s: IngestState) => void;
    vi.mocked(api.ingestListing).mockReturnValue(new Promise((r) => (answer = r)));
    vi.mocked(api.ingestState).mockResolvedValueOnce(UNBORN).mockResolvedValueOnce(HELD);
    const { onClose } = renderDialog({ stay: true });

    expect(screen.getByRole('dialog')).toHaveAccessibleName('Nová nemovitost');
    await paste(`Koukni ${URL_IDNES} co ty na to`);
    expect(screen.getByRole('status')).toHaveTextContent('Hledám inzerát…');
    /* The server is authoritative: the text goes as pasted. */
    expect(api.ingestListing).toHaveBeenCalledWith(`Koukni ${URL_IDNES} co ty na to`, expect.anything());

    answer(QUEUED);
    await tick();
    expect(screen.getByRole('status')).toHaveTextContent('Stahuji stránku…');
    expect(api.ingestState).not.toHaveBeenCalled();

    await tick(2_000);
    expect(api.ingestState).toHaveBeenCalledWith('idnes', NATIVE, expect.anything());
    expect(screen.getByRole('status')).toHaveTextContent('Zakládám nemovitost…');
    expect(api.addPipelineCard).not.toHaveBeenCalled();

    await tick(2_000);
    expect(api.ingestState).toHaveBeenCalledTimes(2);
    expect(api.addPipelineCard).toHaveBeenCalledWith(456);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(toast.pushToast).toHaveBeenCalledWith(
      'ok',
      'Přidáno do pipeline',
      0,
      expect.objectContaining({ label: 'Otevřít' }),
    );
    expect(screen.queryByTestId('where')).toBeNull();

    /* "Otevřít" dismisses the toast and opens the property. */
    act(() => vi.mocked(toast.pushToast).mock.calls[0][3]!.onClick());
    expect(toast.dismissToast).toHaveBeenCalledWith(7);
    expect(screen.getByTestId('where')).toHaveTextContent('/property/456');
  });

  it('keeps waiting for a slow birth — the poll has no cap', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(UNBORN);
    vi.mocked(api.ingestState).mockResolvedValue(UNBORN);
    renderDialog({ stay: true });
    await paste(URL_IDNES);
    await tick(2_000 * 40);
    expect(api.ingestState).toHaveBeenCalledTimes(40);
    expect(screen.getByRole('status')).toHaveTextContent('Zakládám nemovitost…');
    expect(api.addPipelineCard).not.toHaveBeenCalled();
  });

  it.each([
    ['refused', state({ state: 'refused', source: null, source_id_native: null, queue: null, message_cs: 'Tuto stránku zatím nečteme.' })],
    ['gone', state({ state: 'gone', queue: null, message_cs: 'Inzerát na portálu už není.' })],
  ])('says the %s sentence and writes nothing', async (_name, answer) => {
    vi.mocked(api.ingestListing).mockResolvedValue(answer);
    renderDialog();
    await paste('https://www.ulovdomov.cz/inzerat/123');
    expect(screen.getByRole('alert')).toHaveTextContent(answer.message_cs!);
    expect(screen.queryByRole('status')).toBeNull();
    await tick(4_000);
    expect(api.ingestState).not.toHaveBeenCalled();
    expect(api.addPipelineCard).not.toHaveBeenCalled();
    expect(toast.pushToast).not.toHaveBeenCalled();
  });

  it('says so when the drain gave up on the page while we waited', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(QUEUED);
    vi.mocked(api.ingestState).mockResolvedValue(
      state({ state: 'failed', message_cs: 'Stránku se nepodařilo načíst, zkuste to později.', queue: { attempts: 5, claimed_at: null, given_up: true } }),
    );
    renderDialog();
    await paste(URL_IDNES);
    await tick(2_000);
    expect(screen.getByRole('alert')).toHaveTextContent('Stránku se nepodařilo načíst, zkuste to později.');
    await tick(4_000);
    expect(api.ingestState).toHaveBeenCalledTimes(1);
    expect(api.addPipelineCard).not.toHaveBeenCalled();
  });

  it('adds the card of an inactive ad we hold too, and lands on the property (Browse)', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue({ ...HELD, is_active: false });
    renderDialog();
    await paste(URL_IDNES);
    expect(api.addPipelineCard).toHaveBeenCalledWith(456);
    expect(toast.pushToast).toHaveBeenCalledWith('ok', 'Inzerát je neaktivní, karta přidána');
    expect(screen.getByTestId('where')).toHaveTextContent('/property/456');
  });

  it('names the stage of a card already on the board', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(HELD);
    vi.mocked(api.addPipelineCard).mockResolvedValue({ ...ADDED, stage_key: 'offer', stage_label: 'Nabídka', added: false });
    renderDialog({ stay: true });
    await paste(URL_IDNES);
    expect(toast.pushToast).toHaveBeenCalledWith(
      'info',
      'Už je v pipeline · Nabídka',
      0,
      expect.objectContaining({ label: 'Otevřít' }),
    );
  });

  it('adds the card on the board it was opened from, acting as its owner', async () => {
    const share: PipelineShare = { account_id: 'owner-e', email: 'jana@example.cz', mode: 'edit' };
    vi.mocked(api.ingestListing).mockResolvedValue(HELD);
    renderDialog({ stay: true, acting: share });
    await paste(URL_IDNES);
    expect(api.addPipelineCard).toHaveBeenCalledWith(456, { actAs: 'owner-e' });
    /* The ingest itself never acts: market data, not the owner's. */
    expect(api.ingestListing).toHaveBeenCalledWith(URL_IDNES, { signal: expect.any(AbortSignal) });
  });

  it('shows an API failure inline, never as the global toast', async () => {
    vi.mocked(api.ingestListing).mockRejectedValue(new ApiError('admin only', 403, { detail: 'admin only' }));
    renderDialog();
    await paste(URL_IDNES);
    const block = screen.getByRole('alert');
    expect(block).toHaveTextContent('Přidávat inzeráty mohou zatím jen správci.');
    expect(block).toHaveTextContent('admin only');
    expect(toast.pushToast).not.toHaveBeenCalled();
  });

  it('shows a failed card write inline too — the card hook stays silent here', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(HELD);
    vi.mocked(api.addPipelineCard).mockRejectedValue(new ApiError('boom', 500, { detail: 'boom' }));
    renderDialog({ stay: true });
    await paste(URL_IDNES);
    await tick();
    expect(screen.getByRole('alert')).toHaveTextContent('Požadavek selhal (HTTP 500).');
    expect(toast.pushToast).not.toHaveBeenCalled();
  });

  it('rides out a transient failure of one re-read', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(QUEUED);
    vi.mocked(api.ingestState)
      .mockRejectedValueOnce(new ApiError('busy', 503, null))
      .mockResolvedValueOnce(HELD);
    renderDialog({ stay: true });
    await paste(URL_IDNES);
    await tick(2_000);
    expect(screen.getByRole('status')).toHaveTextContent('Stahuji stránku…');
    await tick(2_000);
    expect(api.ingestState).toHaveBeenCalledTimes(2);
    expect(api.addPipelineCard).toHaveBeenCalledWith(456);
  });

  it('stops polling when it unmounts', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(QUEUED);
    vi.mocked(api.ingestState).mockResolvedValue(QUEUED);
    const { unmount } = renderDialog();
    await paste(URL_IDNES);
    unmount();
    await tick(10_000);
    expect(api.ingestState).not.toHaveBeenCalled();
  });

  it('drops the old link\'s progress when the field changes', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(QUEUED);
    vi.mocked(api.ingestState).mockResolvedValue(QUEUED);
    renderDialog();
    await paste(URL_IDNES);
    expect(screen.getByRole('status')).toHaveTextContent('Stahuji stránku…');
    fireEvent.change(field(), { target: { value: 'https://www.sreality.cz/detail/prodej/byt/2+kk/praha/12345678' } });
    expect(screen.queryByRole('status')).toBeNull();
    await tick(10_000);
    expect(api.ingestState).not.toHaveBeenCalled();
  });

  it('keeps only the link when a whole message is pasted into the field', () => {
    renderDialog();
    fireEvent.paste(field(), { clipboardData: { getData: () => `Koukni ${URL_IDNES} co ty na to` } });
    expect(field()).toHaveValue(URL_IDNES);
  });
});

/* -------------------------------------------------------------------------- */

function Opener() {
  const dialog = useNewPropertyDialog();
  if (!dialog) return <p>no trigger</p>;
  return (
    <>
      <button type="button" onClick={() => dialog.open()}>
        Open
      </button>
      <button type="button" onClick={() => dialog.open({ url: URL_IDNES })}>
        Open prefilled
      </button>
    </>
  );
}

const renderProvider = () =>
  render(
    <QueryClientProvider client={client()}>
      <MemoryRouter>
        <NewPropertyProvider>
          <Opener />
        </NewPropertyProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('<NewPropertyProvider>', () => {
  beforeEach(() => vi.useRealTimers());

  it('opens the lazy dialog, prefilled when the opener has the link, and closes it', async () => {
    renderProvider();
    fireEvent.click(screen.getByRole('button', { name: 'Open prefilled' }));
    expect(await screen.findByRole('dialog', { name: 'Nová nemovitost' })).toBeInTheDocument();
    expect(field()).toHaveValue(URL_IDNES);
    fireEvent.click(screen.getByRole('button', { name: 'Zavřít' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    expect(await screen.findByLabelText('Odkaz na inzerát')).toHaveValue('');
  });

  it('meets the modal-dialog contract', async () => {
    renderProvider();
    const trigger = screen.getByRole('button', { name: 'Open' });
    /* Load the chunk once, so the contract's open renders synchronously. */
    fireEvent.click(trigger);
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: 'Zavřít' }));
    expectDialogContract({ open: () => fireEvent.click(trigger), trigger });
  });

  it('offers no trigger to a non-admin: the route is admins-only in v1', () => {
    vi.mocked(auth.useAuth).mockReturnValue({ isAdmin: false } as ReturnType<typeof auth.useAuth>);
    renderProvider();
    expect(screen.getByText('no trigger')).toBeInTheDocument();
  });

  it('offers no trigger outside the provider', () => {
    render(<Opener />);
    expect(screen.getByText('no trigger')).toBeInTheDocument();
  });
});
