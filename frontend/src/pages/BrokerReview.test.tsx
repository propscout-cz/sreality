/* /brokers/review — the admin's broker-duplicate queue. */
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import BrokerReview from './BrokerReview';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listBrokerMergeCandidates: vi.fn(async () => ({ candidates: [], count: 0, reason_counts: {} })),
  listBrokerMerges: vi.fn(async () => ({ merges: [] })),
}));

describe('<BrokerReview>', () => {
  it('P26: the title stands alone, with no small-caps label above it', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/brokers/review']}>
          <BrokerReview />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByRole('heading', { level: 1, name: 'Sloučit duplicity' })).toBeInTheDocument();
    expect(screen.queryByText('broker intelligence')).toBeNull();
    await waitFor(() => expect(api.listBrokerMergeCandidates).toHaveBeenCalled());
  });
});
