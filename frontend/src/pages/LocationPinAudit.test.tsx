/* The audit page — the set W5 hides from consumers (migration 514). The
 * properties worth pinning down, because each is a way the page could quietly
 * mislead the operator:
 *   - a filter reaches the SERVER, and the one number on the page is the exact
 *     count the server returned for that filtered list (W1 deleted every count
 *     the page held of its own: waterfall, matrix, state totals, split);
 *   - the listing link is built by the shared helper, not hand-assembled;
 *   - W7-b: the page shows ONE state at a time, and the quality buckets never
 *     leak into „čeká na zpracování“.
 */

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import LocationPinAudit from './LocationPinAudit';
import * as pinAudit from '@/lib/pinAudit';
import * as listingUrl from '@/lib/listingUrl';
import { expectNoNestedInteractive } from '@/test/a11y';

vi.mock('@/lib/pinAudit', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/pinAudit')>();
  return {
    ...actual,
    fetchPinAuditPage: vi.fn(),
  };
});

const REFRESHED = '2026-09-13T05:25:00Z';

const ROW: pinAudit.PinAuditRow = {
  listing_id: 4242,
  property_id: 77,
  sreality_id: 876654668,
  source: 'sreality',
  source_id_native: '876654668',
  source_url: 'https://www.sreality.cz/detail/pronajem/byt/1+1/x/876654668',
  category_main: 'byt',
  category_type: 'pronajem',
  disposition: '1+1',
  area_m2: 47,
  display_label: 'Korunní 123/4, Praha 2',
  price_czk: 24000,
  is_active: false,
  first_seen_at: '2026-05-01T20:18:00Z',
  last_seen_at: '2026-05-05T11:51:57Z',
  country_status: 'undetermined',
  granularity: 'unknown',
  match_confidence: 'low',
  resolver_version: 'resolver:v4.1',
  resolved_at: '2026-09-12T19:58:11Z',
  has_row: true,
  has_claims: false,
  claims_now: false,
  sibling_has_pin: true,
  quality: 'delisted_no_claims',
  state: 'unresolved',
  refreshed_at: REFRESHED,
};

/* The first page carries the filtered list's exact count (`count: 'exact'`). */
const PAGE: pinAudit.PinAuditPage = { rows: [ROW], nextCursor: null, total: 1332 };

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/new-dedup/pin-audit']}>
        <LocationPinAudit />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('LocationPinAudit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(pinAudit.fetchPinAuditPage).mockResolvedValue(PAGE);
  });

  it('shows the refresh time so the operator knows how old the list is', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/Stav k/)).toBeInTheDocument(),
    );
    /* ONCE, and it is the list's own stamp (one value across the relation). */
    expect(screen.getAllByText(/Stav k 13\/09\/2026/)).toHaveLength(1);
  });

  it('states the exact count the server returned for the filtered list, and no other', async () => {
    renderPage();
    expect(await screen.findByTestId('pin-audit-total')).toHaveTextContent('1 332 inzerátů');
    /* W1: no waterfall, no matrix, no state totals, no split on this page. */
    expect(screen.queryByText('Vodopád')).toBeNull();
    expect(screen.queryByText('Přehled')).toBeNull();
    expect(screen.queryByTestId('pin-audit-state-totals')).toBeNull();
    expect(screen.queryByText(/stažených · u/)).toBeNull();
    /* The portal chips are the static list, not read off any count: a portal
     * with no row still offers its chip. */
    expect(screen.getByRole('button', { name: 'iDNES Reality' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('sends a quality-bucket filter to the server, not just to the client', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('pin-audit-row-4242');

    await user.click(
      screen.getByRole('button', {
        name: 'inzerát běží · nebylo z čeho polohu určit',
      }),
    );

    await waitFor(() => {
      const calls = vi.mocked(pinAudit.fetchPinAuditPage).mock.calls;
      const last = calls[calls.length - 1];
      expect(last[0].qualities).toEqual(['active_no_claims']);
    });
  });

  it('links the advert’s row on its property page', async () => {
    renderPage();
    await screen.findByTestId('pin-audit-row-4242');
    const link = screen
      .getByTestId('pin-audit-row-4242')
      .querySelector('a[href]') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe(listingUrl.propertyPath(77, 4242));
  });

  it('keeps the portal link and the resolver verdict on the row', async () => {
    renderPage();
    const row = await screen.findByTestId('pin-audit-row-4242');
    expect(
      within(row).getByRole('link', { name: /portál/ }).getAttribute('href'),
    ).toBe(ROW.source_url);
    expect(within(row).getByText(/undetermined/)).toBeInTheDocument();
    /* W6-a: the superseded-evidence column is GONE, because the rows behind it
     * are. A claim is deleted the moment its contract version is retired, so a
     * column that could only ever say "žádná" would read as a finding. */
    expect(screen.queryByText('Stará data')).not.toBeInTheDocument();
    expect(within(row).getByText('má polohu')).toBeInTheDocument();
    expectNoNestedInteractive(document.body);
  });

  it('opens on the issue, not on the waiting room', async () => {
    renderPage();
    await waitFor(() =>
      expect(vi.mocked(pinAudit.fetchPinAuditPage)).toHaveBeenCalled(),
    );
    const first = vi.mocked(pinAudit.fetchPinAuditPage).mock.calls[0];
    expect(first[0].state).toBe('unresolved');
  });

  it('the state toggle re-scopes the server read', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('pin-audit-row-4242');

    await user.click(
      screen.getByRole('button', { name: 'čeká na zpracování' }),
    );

    await waitFor(() => {
      const calls = vi.mocked(pinAudit.fetchPinAuditPage).mock.calls;
      expect(calls[calls.length - 1][0].state).toBe('pending');
    });
  });

  it('hides the verdict buckets under pending — there is no verdict yet', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('pin-audit-row-4242');
    /* Chosen first, so the assertion cannot pass on a stale render. */
    await user.click(
      screen.getByRole('button', {
        name: 'inzerát běží · nebylo z čeho polohu určit',
      }),
    );

    await user.click(
      screen.getByRole('button', { name: 'čeká na zpracování' }),
    );

    await waitFor(() =>
      expect(
        screen.queryByRole('button', {
          name: 'inzerát běží · nebylo z čeho polohu určit',
        }),
      ).toBeNull(),
    );
    /* And the filter goes with the chips: a leftover bucket must not narrow the
     * pending list behind the operator's back. */
    const calls = vi.mocked(pinAudit.fetchPinAuditPage).mock.calls;
    expect(calls[calls.length - 1][0].qualities).toEqual([]);
  });

  it('sends the sibling filter to the server', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('pin-audit-row-4242');

    await user.click(screen.getByRole('button', { name: 'Má' }));

    await waitFor(() => {
      const calls = vi.mocked(pinAudit.fetchPinAuditPage).mock.calls;
      expect(calls[calls.length - 1][0].sibling).toBe('yes');
    });
  });

  it('shows the one place string, never a hand-assembled one', async () => {
    renderPage();
    const row = await screen.findByTestId('pin-audit-row-4242');
    expect(within(row).getByText('Korunní 123/4, Praha 2')).toBeInTheDocument();
  });

  it('says what the set IS: hidden from customers until the location is decided', async () => {
    renderPage();
    expect(
      await screen.findByText(/nezobrazují, dokud nemají rozhodnutou polohu/),
    ).toBeInTheDocument();
  });
});

/* A pick-one is the ONE joined bar (operator ruling 2026-10-27, system.md § Option
 * groups): the named group's first child is the track, no segment is a bordered chip
 * of its own, the chosen one is filled copper. Pick-several rows stay chips. */
function joinedBar(name: string | RegExp, lit: string | RegExp) {
  const bar = screen.getByRole('group', { name });
  expect(bar.firstElementChild).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
  for (const b of within(bar).getAllByRole('button')) expect(b).not.toHaveClass('border');
  const on = within(bar).getByRole('button', { name: lit });
  expect(on).toHaveAttribute('aria-pressed', 'true');
  expect(on).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
  return bar;
}

describe('LocationPinAudit pick-one rows', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(pinAudit.fetchPinAuditPage).mockResolvedValue(PAGE);
  });

  it('draws the state, status and sibling choices as joined bars, Vše first', async () => {
    renderPage();
    await screen.findByTestId('pin-audit-row-4242');
    /* Two states, and no count on either (W1: the counts left with the summary RPC). */
    const state = joinedBar('Skupina inzerátů', 'zpracováno, nerozhodnuto');
    expect(within(state).getByRole('button', { name: 'čeká na zpracování' })).toHaveClass(
      'text-[var(--color-ink-3)]',
    );
    const status = joinedBar('Stav inzerátu', 'Vše');
    expect(within(status).getAllByRole('button').map((b) => b.textContent)).toEqual(['Vše', 'Běží', 'Stažené']);
    const sibling = joinedBar('Jiný inzerát téže nemovitosti má polohu', 'Vše');
    expect(within(sibling).getAllByRole('button').map((b) => b.textContent)).toEqual(['Vše', 'Má', 'Nemá']);
    /* Pick-several stays chips: a portal is a bordered chip of its own. */
    const portal = screen
      .getAllByRole('button', { name: 'Sreality' })
      .find((b) => b.hasAttribute('aria-pressed'));
    expect(portal).toHaveClass('border');
  });

  it('sends the status picked on its bar to the server', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('pin-audit-row-4242');
    await user.click(
      within(screen.getByRole('group', { name: 'Stav inzerátu' })).getByRole('button', { name: 'Stažené' }),
    );
    await waitFor(() => {
      const calls = vi.mocked(pinAudit.fetchPinAuditPage).mock.calls;
      expect(calls[calls.length - 1][0].status).toBe('delisted');
    });
    joinedBar('Stav inzerátu', 'Stažené');
  });

  it('sorts on a joined bar whose lit segment flips the direction', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('pin-audit-row-4242');
    joinedBar('Řadit podle', 'naposledy viděno ↓');
    await user.click(screen.getByRole('button', { name: 'naposledy viděno ↓' }));
    await waitFor(() => {
      const calls = vi.mocked(pinAudit.fetchPinAuditPage).mock.calls;
      expect(calls[calls.length - 1][1]).toEqual({ field: 'last_seen_at', direction: 'asc' });
    });
    joinedBar('Řadit podle', 'naposledy viděno ↑');
    await user.click(screen.getByRole('button', { name: 'poprvé viděno' }));
    await waitFor(() => {
      const calls = vi.mocked(pinAudit.fetchPinAuditPage).mock.calls;
      expect(calls[calls.length - 1][1]).toEqual({ field: 'first_seen_at', direction: 'desc' });
    });
  });
});
