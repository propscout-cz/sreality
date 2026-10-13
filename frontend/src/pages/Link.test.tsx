/* The Odkaz tab (#1767): a pasted portal link lands on /listing/:source/:nativeId
 * with the URL in the navigation state; anything else says why, in a sentence
 * that names the portals the platform reads. */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { READ_PORTALS, RECENT_LINKS_KEY } from '@/lib/pastedLink';
import { portalLabel } from '@/lib/portals';

import LinkPage from './Link';

const SREALITY = 'https://www.sreality.cz/detail/prodej/byt/2+kk/praha-vysocany-freyova/2861372748';

function Landed() {
  const l = useLocation();
  return (
    <p data-testid="landed">
      {l.pathname} {JSON.stringify(l.state)}
    </p>
  );
}

function renderLink() {
  return render(
    <MemoryRouter initialEntries={['/link']}>
      <Routes>
        <Route path="/link" element={<LinkPage />} />
        <Route path="/listing/:source/:nativeId" element={<Landed />} />
      </Routes>
    </MemoryRouter>,
  );
}

const field = () => screen.getByLabelText('Odkaz na inzerát');

beforeEach(() => localStorage.clear());

describe('<LinkPage>', () => {
  it('opens a link pasted inside a message on its listing alias, the URL in the state', async () => {
    renderLink();
    fireEvent.change(field(), { target: { value: `Koukni: ${SREALITY} co ty na to?` } });
    /* The recognised portal + id show before the tap. */
    expect(screen.getByText('2861372748')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Otevřít' }));
    const landed = await screen.findByTestId('landed');
    expect(landed).toHaveTextContent('/listing/sreality/2861372748');
    expect(landed).toHaveTextContent(JSON.stringify({ pastedUrl: SREALITY }));
    /* …and the link joins this device's roll. */
    expect(JSON.parse(localStorage.getItem(RECENT_LINKS_KEY) ?? '[]')).toMatchObject([
      { source: 'sreality', nativeId: '2861372748', url: SREALITY },
    ]);
  });

  it('names the nine portals it reads when the link is on none of them', async () => {
    renderLink();
    fireEvent.change(field(), { target: { value: 'https://www.ulovdomov.cz/inzerat/123' } });
    await userEvent.click(screen.getByRole('button', { name: 'Otevřít' }));
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('nevede na žádný z portálů, které čteme');
    expect(READ_PORTALS).toHaveLength(9);
    for (const source of READ_PORTALS) expect(alert.textContent).toContain(portalLabel(source));
    expect(screen.queryByTestId('landed')).toBeNull();
  });

  it('says a portal search page is not a listing', async () => {
    renderLink();
    fireEvent.change(field(), { target: { value: 'https://www.sreality.cz/hledani/prodej/byty' } });
    await userEvent.click(screen.getByRole('button', { name: 'Otevřít' }));
    expect(screen.getByRole('alert')).toHaveTextContent('ale ne jeden inzerát');
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
    await userEvent.click(screen.getByRole('button', { name: /196123456/ }));
    expect(await screen.findByTestId('landed')).toHaveTextContent('/listing/bazos/196123456');
  });
});
