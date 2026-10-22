/* <DelegatedStamp> on the board: my own board names a delegate from my
 * recipients; a shared board (acting) shows the time alone and never reads my
 * share lists; no stamp renders and reads nothing. */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import * as api from '@/lib/api';
import { ActingAs } from '@/lib/acting';
import type { PipelineShare } from '@/lib/types';
import DelegatedStamp from './DelegatedStamp';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  listPipelineShares: vi.fn(),
}));

const EDIT: PipelineShare = { account_id: 'owner-1', email: 'owner@x.cz', mode: 'edit' };
const STAMP = { edited_by_account_id: 'acc-c', edited_at: '2026-10-20T10:00:00Z' };

function renderStamp(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.mocked(api.listPipelineShares).mockReset().mockResolvedValue({
    shared_with: [{ account_id: 'acc-c', email: 'c@x.cz', mode: 'edit' }],
    shared_with_me: [],
  });
});

describe('<DelegatedStamp>', () => {
  it('names my recipient on my own board', async () => {
    renderStamp(<DelegatedStamp stamp={STAMP} />);
    expect(await screen.findByText(/^upravil c@x\.cz · /)).toBeInTheDocument();
  });

  it('shows the time alone on a shared board, without reading my shares', () => {
    renderStamp(
      <ActingAs share={EDIT}>
        <DelegatedStamp stamp={STAMP} />
      </ActingAs>,
    );
    expect(screen.getByText(/^upraveno · /)).toBeInTheDocument();
    expect(api.listPipelineShares).not.toHaveBeenCalled();
  });

  it('renders nothing for an unstamped row', () => {
    const { container } = renderStamp(<DelegatedStamp stamp={{ edited_by_account_id: null, edited_at: null }} />);
    expect(container.textContent).toBe('');
    expect(api.listPipelineShares).not.toHaveBeenCalled();
  });
});
