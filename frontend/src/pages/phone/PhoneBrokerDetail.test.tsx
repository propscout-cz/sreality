/* A broker on the phone (#1769): the dossier with the contact masked exactly as
 * the desktop masks it, "Kopírovat" through the clipboard with a selection
 * fallback, the footprint as numbers with no map anywhere, and the broker's
 * live ads as Browse's cards. */
/* fireEvent, not userEvent, for the copy: user-event installs its own clipboard
 * stub on setup, over the one each test defines. */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as brokers from '@/lib/brokers';
import * as queries from '@/lib/queries';
import type { PipelineMembers } from '@/lib/queries';
import * as toast from '@/lib/toast';
import { cardRow } from '@/test/cardRow';

import BrokerDetail from '../BrokerDetail';
import { brokerCardFilters } from './PhoneBrokerDetail';

vi.mock('@/lib/phoneShell', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/phoneShell')>()),
  usePhoneShell: () => true,
}));
vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  fetchBrokerDossier: vi.fn(),
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchListingsForCards: vi.fn(),
  fetchPipelineMembers: vi.fn(),
  fetchPipelineStages: vi.fn(),
  fetchPropertyCollectionMemberSet: vi.fn(),
  fetchIsDismissed: vi.fn(),
  fetchImagesForListingIds: vi.fn(),
}));
vi.mock('@/lib/toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/toast')>()),
  pushToast: vi.fn(),
}));
/* The map lives behind the explore modal; the phone must never reach for it. */
const { exploreHook } = vi.hoisted(() => ({ exploreHook: vi.fn() }));
vi.mock('@/components/ExploreBrokerModal', () => ({
  useExploreBrokerModal: () => {
    exploreHook();
    return { open: vi.fn(), close: vi.fn(), isOpen: false };
  },
}));

const dossier = (broker: Partial<brokers.BrokerPublic> = {}): brokers.BrokerDossier => ({
  broker: {
    broker_id: 7,
    display_name: 'Jana Kubíčková',
    firm_id: 3,
    firm_domain: 'domov9.cz',
    firm_name: 'Domov 9 Reality',
    firm_is_franchise: false,
    source_count: 3,
    distinct_source_count: 3,
    properties: 50,
    active_properties: 35,
    first_seen_at: '2026-01-01T00:00:00Z',
    last_seen_at: '2026-10-12T08:00:00Z',
    has_phone: true,
    has_email: false,
    ...broker,
  },
  memberships: [
    {
      broker_id: 7,
      firm_id: 3,
      firm_domain: 'domov9.cz',
      firm_name: 'Domov 9 Reality',
      first_seen_at: null,
      last_seen_at: null,
      is_current: true,
    },
  ],
  region_shares: [
    { geo_id: 19, name: 'Hlavní město Praha', properties: 40, active_properties: 30 },
    { geo_id: 27, name: 'Středočeský kraj', properties: 10, active_properties: 5 },
  ],
  categories: [
    { category_main: 'byt', category_type: 'prodej', properties: 45, active_properties: 33 },
    { category_main: 'byt', category_type: 'pronajem', properties: 5, active_properties: 2 },
  ],
  pii_masked: true,
});

beforeEach(() => {
  vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue(dossier());
  vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [cardRow(11), cardRow(12)], nextCursor: null });
  vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map() as PipelineMembers);
  vi.mocked(queries.fetchPipelineStages).mockResolvedValue([]);
  vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map());
  vi.mocked(queries.fetchIsDismissed).mockResolvedValue(false);
  vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map());
});

afterEach(() => {
  vi.clearAllMocks();
  Reflect.deleteProperty(navigator, 'clipboard');
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/brokers/7']}>
        <Routes>
          <Route path="/brokers/:id" element={<BrokerDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('<BrokerDetail> on the phone', () => {
  it('draws the dossier: initials, name, firm, the contact masked as on the desktop', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Jana Kubíčková' })).toBeInTheDocument();
    expect(screen.getByText('JK')).toBeInTheDocument();
    expect(screen.getByText('Domov 9 Reality', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('telefon · kontakt na vyžádání')).toBeInTheDocument();
    expect(screen.getByText('e-mail —')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Kopírovat' })).toBeNull();
    // Ruling 4: property grain, no ad counts.
    expect(screen.getByText('Nemovitosti').nextElementSibling?.textContent).toBe('35 / 50');
    expect(screen.queryByText('Inzeráty')).toBeNull();
  });

  it('mounts no map: the footprint is numbers and the explore modal is never asked for', async () => {
    const { container } = renderPage();
    const reach = await screen.findByRole('region', { name: 'Působnost' });
    expect(within(reach).getByText('Hlavní město Praha')).toBeInTheDocument();
    expect(reach.textContent?.replace(/ /g, ' ')).toContain('30 / 40');
    expect(within(reach).getByText('Byty · pronájem').parentElement?.nextElementSibling?.textContent).toBe('2 / 5');
    expect(exploreHook).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /explore|mapě/i })).toBeNull();
    expect(container.querySelector('.maplibregl-map, canvas')).toBeNull();
  });

  /* Section cards (operator ruling 2026-10-28): Působnost and Firmy are cards like the
     phone property page's, their titles L2 (never small caps), Firmy's count bare. */
  it('draws Působnost and Firmy as section cards', async () => {
    renderPage();
    const reach = await screen.findByRole('region', { name: 'Působnost' });
    const firms = screen.getByRole('region', { name: 'Firmy' });
    for (const card of [reach, firms]) {
      expect(card.tagName).toBe('SECTION');
      expect(card).toHaveClass('border', 'bg-[var(--color-paper-2)]', 'rounded-[var(--radius-md)]');
    }
    const title = within(firms).getByRole('heading', { level: 2, name: 'Firmy' });
    expect(title).not.toHaveClass('uppercase');
    expect(title.nextElementSibling).toHaveTextContent(/^1$/);
  });

  it("lists the broker's live ads as Browse's cards", async () => {
    renderPage();
    const offer = await screen.findByRole('region', { name: 'Nabídka' });
    expect(await within(offer).findByRole('link', { name: /Místo 11/ })).toBeInTheDocument();
    const [filters] = vi.mocked(queries.fetchListingsForCards).mock.calls[0];
    expect(filters).toEqual(brokerCardFilters(7));
    expect(filters.brokerId).toBe(7);
    expect(filters.status).toBe('active');
  });

  it('copies the number through the clipboard for an admin', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue(
      dossier({ primary_phone: '420604218377', has_phone: undefined }),
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderPage();
    const call = await screen.findByRole('link', { name: 'Zavolat +420 604 218 377' });
    expect(call).toHaveAttribute('href', 'tel:+420604218377');
    fireEvent.click(screen.getByRole('button', { name: 'Kopírovat' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('+420 604 218 377'));
    expect(toast.pushToast).toHaveBeenCalledWith('ok', 'Číslo zkopírováno.');
  });

  it('selects the number when the clipboard refuses', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue(
      dossier({ primary_phone: '420604218377', has_phone: undefined }),
    );
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
      configurable: true,
    });
    renderPage();
    await screen.findByRole('link', { name: 'Zavolat +420 604 218 377' });
    fireEvent.click(screen.getByRole('button', { name: 'Kopírovat' }));
    await waitFor(() => expect(toast.pushToast).toHaveBeenCalledWith('info', 'Číslo je označené, zkopírujte ho.'));
    expect(window.getSelection()?.toString()).toBe('+420 604 218 377');
  });

  it('tells an unknown broker from a failed read', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue(null);
    const { unmount } = renderPage();
    expect(await screen.findByText('Makléř nenalezen.')).toBeInTheDocument();
    unmount();
    vi.mocked(brokers.fetchBrokerDossier).mockRejectedValue(new Error('503'));
    renderPage();
    expect(await screen.findByText('Makléře se nepodařilo načíst.')).toBeInTheDocument();
  });
});
