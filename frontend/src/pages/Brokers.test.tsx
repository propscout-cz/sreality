/* /brokers: the board is Browse's Makléři tab (docs/design/broker-board.md), so a
 * desktop visit is replaced by the tab, carrying whatever the link had. The
 * phone keeps its own screen at /brokers (pages/phone/PhoneBrokers.test.tsx);
 * the board itself is components/brokers/BrokerBoard.test.tsx's. */

import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, useNavigationType } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import Brokers from './Brokers';
import { ROUTES } from '@/lib/routes';

vi.mock('@/lib/phoneShell', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/phoneShell')>()),
  usePhoneShell: () => false,
}));

function Landed() {
  const { pathname, search } = useLocation();
  return (
    <p data-testid="landed" data-nav={useNavigationType()}>
      {pathname + search}
    </p>
  );
}

const visit = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path={ROUTES.brokers.pattern} element={<Brokers />} />
        <Route path={ROUTES.browse.pattern} element={<Landed />} />
      </Routes>
    </MemoryRouter>,
  );

describe('/brokers on the desktop', () => {
  it('replaces itself with the Makléři tab', () => {
    visit('/brokers');
    expect(screen.getByTestId('landed')).toHaveTextContent(/^\/browse\?tab=brokers$/);
    expect(screen.getByTestId('landed')).toHaveAttribute('data-nav', 'REPLACE');
  });

  it('carries the filters and the board options of the link', () => {
    visit('/brokers?cat=dum&deal=pronajem&horizon=60&rank_by=speed');
    expect(screen.getByTestId('landed')).toHaveTextContent(
      /^\/browse\?cat=dum&deal=pronajem&horizon=60&rank_by=speed&tab=brokers$/,
    );
  });
});
