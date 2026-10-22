/* MS16: the note mark counts the caller's notes, and a failed read is a retry,
 * never an absent mark or a count of none. The merge bar's own rails (every
 * lettered property with its marks, the count before the click) are in
 * MergeBar.test.tsx. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import NoteMark from './NoteMark';
import * as queries from '@/lib/queries';

vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchNoteCounts: vi.fn(),
}));

const renderWith = (ui: React.ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(queries.fetchNoteCounts).mockResolvedValue(new Map([[42, 2]]));
});

describe('<NoteMark>', () => {
  it('counts the caller’s notes, and shows nothing for a property without any', async () => {
    renderWith(<NoteMark property_id={42} />);
    expect(await screen.findByTitle('Poznámky: 2')).toHaveTextContent('2');
    const { container } = renderWith(<NoteMark property_id={43} />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('offers a retry when the counts cannot be read, and reads again', async () => {
    vi.mocked(queries.fetchNoteCounts).mockRejectedValueOnce(new Error('HTTP 500'));
    renderWith(<NoteMark property_id={42} />);
    fireEvent.click(await screen.findByRole('button', { name: /Poznámky se nepodařilo načíst/ }));
    expect(await screen.findByTitle('Poznámky: 2')).toBeInTheDocument();
  });
});
