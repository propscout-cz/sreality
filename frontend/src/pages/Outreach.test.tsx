/* /outreach — campaign rows are destinations, so they are anchors.
 *
 * Two defects were fixed together here: the row offered no right-click "open in
 * new tab" (it was a <button> calling navigate()), and CampaignRow's root is a
 * <div>, which a <button> may not legally contain (phrasing content only). An
 * <a> is transparent content, so the anchor fixes both. */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import Outreach from './Outreach';
import * as api from '@/lib/api';
import { GEO_UNSERVED_HINT } from '@/lib/brokers';
import * as maps from '@/lib/maps';
import { ROUTES } from '@/lib/routes';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listOutreachCampaigns: vi.fn(),
  createOutreachCampaign: vi.fn(),
}));
vi.mock('@/lib/maps', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/maps')>()),
  fetchSuggest: vi.fn(),
  resolveSuggestion: vi.fn(),
}));

function campaign(id: number, name: string) {
  return {
    id,
    name,
    goal: 'test goal',
    status: 'draft',
    created_at: '2026-09-01T00:00:00Z',
    draft_count: 1,
    approved_count: 0,
    sent_count: 0,
  } as unknown as api.OutreachCampaign;
}

function payload(...cs: ReturnType<typeof campaign>[]) {
  return { campaigns: cs } as unknown as Awaited<
    ReturnType<typeof api.listOutreachCampaigns>
  >;
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/outreach']}>
        <Outreach />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.listOutreachCampaigns).mockResolvedValue(payload());
});

describe('<Outreach> campaign rows are links', () => {
  it('renders each campaign as an anchor to its detail route', async () => {
    vi.mocked(api.listOutreachCampaigns).mockResolvedValue(
      payload(campaign(3, 'Jarni kampan'), campaign(9, 'Podzimni kampan')),
    );
    renderPage();

    const first = await screen.findByRole('link', { name: /Jarni kampan/ });
    expect(first).toHaveAttribute('href', ROUTES.outreachDetail.build({ id: 3 }));
    expect(screen.getByRole('link', { name: /Podzimni kampan/ })).toHaveAttribute(
      'href',
      ROUTES.outreachDetail.build({ id: 9 }),
    );
  });

  it('exposes no row-shaped button once the list has rendered', async () => {
    vi.mocked(api.listOutreachCampaigns).mockResolvedValue(payload(campaign(3, 'Jarni kampan')));
    renderPage();
    await screen.findByRole('link', { name: /Jarni kampan/ });
    expect(screen.queryByRole('button', { name: /Jarni kampan/ })).toBeNull();
  });
});

/* A campaign target is stored, so it must never hold the no-match code: until
 * the leaderboard serves the finer levels (PR 2) a street chip keeps Create
 * disabled and the page says why; a town chip does not. */
describe('<Outreach> a location the page cannot serve', () => {
  const pickPlace = async (res: maps.LocationResolution, type: string) => {
    vi.mocked(maps.fetchSuggest).mockResolvedValue([
      { name: res.name ?? '', label: '', type, position: { lon: 15.77, lat: 50.03 } },
    ]);
    vi.mocked(maps.resolveSuggestion).mockResolvedValue(res);
    const user = userEvent.setup();
    await user.type(screen.getByRole('textbox', { name: 'Lokalita' }), 'Pard');
    await user.click(within(await screen.findByRole('listbox')).getByRole('button'));
  };

  it('keeps Create disabled while a street chip is selected', async () => {
    renderPage();
    await userEvent.setup().type(screen.getByPlaceholderText('Off-market byty Praha'), 'Kampaň');
    const create = screen.getByRole('button', { name: 'Založit kampaň' });
    expect(create).toBeEnabled();

    await pickPlace(
      { level: 'ulice', id: 666742, name: 'Na Rybníčkách', context: 'Pardubice' },
      'regional.street',
    );
    await waitFor(() => expect(screen.getByText(GEO_UNSERVED_HINT)).toBeInTheDocument());
    expect(create).toBeDisabled();
  });

  it('enables Create for a town chip', async () => {
    renderPage();
    await userEvent.setup().type(screen.getByPlaceholderText('Off-market byty Praha'), 'Kampaň');
    await pickPlace(
      { level: 'obec', id: 555134, name: 'Pardubice', context: null },
      'regional.municipality',
    );
    await screen.findByRole('button', { name: 'Remove Pardubice (Obec)' });
    expect(screen.queryByText(GEO_UNSERVED_HINT)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Založit kampaň' })).toBeEnabled();
  });
});
