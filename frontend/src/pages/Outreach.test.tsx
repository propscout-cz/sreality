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

/* A campaign target stores the chips as picked — a street too — and the
 * leaderboard compiles them like Browse does; no chip is the whole country. */
describe('<Outreach> the campaign target', () => {
  const STREET = { level: 'ulice', id: 666742, name: 'Na Rybníčkách', context: 'Pardubice' };

  it('posts the picked chips in the target, Create enabled throughout', async () => {
    vi.mocked(api.createOutreachCampaign).mockResolvedValue({ id: 5 } as api.OutreachCampaign);
    vi.mocked(maps.fetchSuggest).mockResolvedValue([
      { name: STREET.name, label: '', type: 'regional.street', position: { lon: 15.77, lat: 50.03 } },
    ]);
    vi.mocked(maps.resolveSuggestion).mockResolvedValue(STREET);
    renderPage();
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Off-market byty Praha'), 'Kampaň');
    await user.type(screen.getByRole('textbox', { name: 'Lokalita' }), 'Na Ryb');
    await user.click(within(await screen.findByRole('listbox')).getByRole('button'));
    await screen.findByRole('button', { name: 'Remove Na Rybníčkách · Pardubice (Ulice)' });

    const create = screen.getByRole('button', { name: 'Založit kampaň' });
    expect(create).toBeEnabled();
    await user.click(create);
    await waitFor(() =>
      expect(api.createOutreachCampaign).toHaveBeenCalledWith(
        expect.objectContaining({
          target: {
            districts: [STREET],
            category_main: 'byt',
            category_type: 'prodej',
          },
        }),
      ),
    );
  });
});

/* A pick-one is the ONE joined bar (operator ruling 2026-10-27), drawn via Segmented:
 * Typ and Nabídka keep their order, Vše last. */
describe('<Outreach> the campaign scope pickers', () => {
  it('draws Typ and Nabídka as joined bars, the chosen segment filled copper', async () => {
    renderPage();
    for (const [name, lit, labels] of [
      ['Typ', 'Byty', ['Byty', 'Domy', 'Pozemky', 'Komerční', 'Vše']],
      ['Nabídka', 'Prodej', ['Prodej', 'Pronájem', 'Vše']],
    ] as const) {
      const field = await screen.findByRole('group', { name });
      const on = within(field).getByRole('button', { name: lit });
      expect(on).toHaveAttribute('aria-pressed', 'true');
      expect(on).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
      expect(on.parentElement).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
      expect(within(field).getAllByRole('button').map((b) => b.textContent)).toEqual(labels);
    }
  });
});
