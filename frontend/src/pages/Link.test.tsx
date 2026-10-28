/* The Odkaz tab (#1767): a pasted link runs the "Nová nemovitost" flow inline for
 * an admin (the server reads the text, the card is added, the tab lands on the
 * property), while a non-admin keeps the quick path to /listing/:source/:nativeId
 * with the URL in the navigation state. The two refusals that need no server —
 * no link, a portal page that is not one ad — stay on the device; any other site
 * is the server's call. */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import * as auth from '@/lib/auth';
import { RECENT_LINKS_KEY } from '@/lib/pastedLink';
import type { IngestState } from '@/lib/types';

import LinkPage from './Link';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  ingestListing: vi.fn(),
  ingestState: vi.fn(),
  addPipelineCard: vi.fn(),
}));
vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));

const SREALITY = 'https://www.sreality.cz/detail/prodej/byt/2+kk/praha-vysocany-freyova/2861372748';

const held = (over: Partial<IngestState> = {}): IngestState => ({
  state: 'held',
  message_cs: null,
  source: 'sreality',
  source_id_native: '2861372748',
  listing_id: 9,
  is_active: true,
  property_id: 456,
  queue: null,
  ...over,
});

function Landed() {
  const l = useLocation();
  return (
    <p data-testid="landed">
      {l.pathname} {JSON.stringify(l.state)}
    </p>
  );
}

function renderLink() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/link']}>
        <Routes>
          <Route path="/link" element={<LinkPage />} />
          <Route path="/listing/:source/:nativeId" element={<Landed />} />
          <Route path="/property/:propertyId" element={<Landed />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const field = () => screen.getByLabelText('Odkaz na inzerát');
const asAdmin = (isAdmin: boolean) =>
  vi.mocked(auth.useAuth).mockReturnValue({ isAdmin } as ReturnType<typeof auth.useAuth>);

beforeEach(() => {
  localStorage.clear();
  asAdmin(true);
  for (const fn of [api.ingestListing, api.ingestState, api.addPipelineCard]) vi.mocked(fn).mockReset();
  vi.mocked(api.addPipelineCard).mockResolvedValue({ property_id: 456, stage_key: 'interested', added: true });
});

describe('<LinkPage>', () => {
  it('adds a link pasted inside a message and lands on its property', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(held());
    renderLink();
    const text = `Koukni: ${SREALITY} co ty na to?`;
    fireEvent.change(field(), { target: { value: text } });
    /* The recognised portal + id show before the tap. */
    expect(screen.getByText('2861372748')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Přidat' }));
    expect(await screen.findByTestId('landed')).toHaveTextContent('/property/456');
    /* The server reads the text as pasted; the card goes on my own board. */
    expect(api.ingestListing).toHaveBeenCalledWith(text, expect.anything());
    expect(api.addPipelineCard).toHaveBeenCalledWith(456);
    /* …and the link joins this device's roll. */
    expect(JSON.parse(localStorage.getItem(RECENT_LINKS_KEY) ?? '[]')).toMatchObject([
      { source: 'sreality', nativeId: '2861372748', url: SREALITY },
    ]);
  });

  it('shows the flow under the field while the page is fetched', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(held({ state: 'queued', listing_id: null, is_active: null, property_id: null }));
    vi.mocked(api.ingestState).mockReturnValue(new Promise<never>(() => {}));
    renderLink();
    fireEvent.change(field(), { target: { value: SREALITY } });
    await userEvent.click(screen.getByRole('button', { name: 'Přidat' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Stahuji stránku…');
    expect(screen.getByRole('button', { name: 'Přidat' })).toBeDisabled();
    /* A new link drops the old one's progress. */
    fireEvent.change(field(), { target: { value: '' } });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('lets the server judge a link on any other site, and says its sentence', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(
      held({ state: 'refused', source: null, source_id_native: null, listing_id: null, is_active: null, property_id: null, message_cs: 'Tuto stránku zatím nečteme.' }),
    );
    renderLink();
    fireEvent.change(field(), { target: { value: 'https://www.ulovdomov.cz/inzerat/123' } });
    await userEvent.click(screen.getByRole('button', { name: 'Přidat' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Tuto stránku zatím nečteme.');
    expect(api.ingestListing).toHaveBeenCalledWith('https://www.ulovdomov.cz/inzerat/123', expect.anything());
    expect(screen.queryByTestId('landed')).toBeNull();
  });

  it('says a portal search page is not a listing, without asking the server', async () => {
    renderLink();
    fireEvent.change(field(), { target: { value: 'https://www.sreality.cz/hledani/prodej/byty' } });
    await userEvent.click(screen.getByRole('button', { name: 'Přidat' }));
    expect(screen.getByRole('alert')).toHaveTextContent('ale ne jeden inzerát');
    expect(api.ingestListing).not.toHaveBeenCalled();
  });

  it('keeps the quick path for a non-admin: the alias, the URL in the state, no ingest', async () => {
    asAdmin(false);
    renderLink();
    fireEvent.change(field(), { target: { value: `Koukni: ${SREALITY} co ty na to?` } });
    await userEvent.click(screen.getByRole('button', { name: 'Otevřít' }));
    const landed = await screen.findByTestId('landed');
    expect(landed).toHaveTextContent('/listing/sreality/2861372748');
    expect(landed).toHaveTextContent(JSON.stringify({ pastedUrl: SREALITY }));
    expect(api.ingestListing).not.toHaveBeenCalled();
  });

  it('tells a non-admin a link on any other site is not read, without asking the server', async () => {
    asAdmin(false);
    renderLink();
    fireEvent.change(field(), { target: { value: 'https://www.ulovdomov.cz/inzerat/123' } });
    await userEvent.click(screen.getByRole('button', { name: 'Otevřít' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Tuto stránku zatím nečteme.');
    expect(api.ingestListing).not.toHaveBeenCalled();
    expect(screen.queryByTestId('landed')).toBeNull();
  });

  it('locks the field while the card is written, so typing cannot cancel it unseen', async () => {
    vi.mocked(api.ingestListing).mockResolvedValue(held());
    vi.mocked(api.addPipelineCard).mockReturnValue(new Promise<never>(() => {}));
    renderLink();
    fireEvent.change(field(), { target: { value: SREALITY } });
    await userEvent.click(screen.getByRole('button', { name: 'Přidat' }));
    await waitFor(() => expect(field()).toBeDisabled());
  });

  it('fills the field from the clipboard, and says so when the clipboard refuses', async () => {
    const readText = vi.fn().mockResolvedValueOnce(SREALITY).mockRejectedValueOnce(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', { value: { readText }, configurable: true });
    renderLink();
    await userEvent.click(screen.getByRole('button', { name: /Vložit ze schránky/ }));
    await waitFor(() => expect(field()).toHaveValue(SREALITY));
    await userEvent.click(screen.getByRole('button', { name: /Vložit ze schránky/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Schránku se nepodařilo přečíst');
  });

  it('lists the links opened before on this device, each one tap away', async () => {
    localStorage.setItem(
      RECENT_LINKS_KEY,
      JSON.stringify([
        { url: 'https://reality.bazos.cz/inzerat/196123456/x.php', source: 'bazos', nativeId: '196123456', at: '2026-10-12T08:40:00Z' },
      ]),
    );
    renderLink();
    const roll = screen.getByRole('region', { name: 'Naposledy vložené' });
    expect(roll).toHaveTextContent('Bazoš');
    /* An L3 group title (section cards ladder), not a small-caps label. */
    const title = within(roll).getByRole('heading', { level: 2, name: 'Naposledy vložené' });
    expect(title).toHaveClass('text-sm', 'font-semibold');
    expect(title).not.toHaveClass('uppercase');
    await userEvent.click(screen.getByRole('button', { name: /196123456/ }));
    expect(await screen.findByTestId('landed')).toHaveTextContent('/listing/bazos/196123456');
  });
});
