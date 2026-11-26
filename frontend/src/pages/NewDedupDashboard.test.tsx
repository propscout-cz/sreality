/* NewDedupDashboard — the NEW DEDUP program's front page.
 *
 * Hermetic: the settings registry is mocked.
 *
 * Pins:
 *   * the COST TABLE reads the two spend knobs out of the settings registry, and
 *     says "not yet" — never a zero — wherever nothing has been measured or the
 *     registry has no such knob. A zero would read as "we ran it and it was
 *     free", which is the one wrong answer here;
 *   * the wave strip marks W2, whose lane was deleted, as deleted.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import NewDedupDashboard from './NewDedupDashboard';
import * as api from '@/lib/api';
import { expectNoNestedInteractive } from '@/test/a11y';
import type { NewDedupSetting } from '@/lib/api';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    listNewDedupSettings: vi.fn(),
  };
});

function setting(key: string, value: unknown, valueType: NewDedupSetting['value_type']): NewDedupSetting {
  return {
    key,
    category: 'l3_embeddings',
    value_type: valueType,
    value,
    default: value,
    is_override: false,
    decided: true,
    explanation: '',
    enum_choices: null,
    minimum: null,
    maximum: null,
  };
}

const SETTINGS: NewDedupSetting[] = [
  setting('l3_runpod_daily_cost_cap_usd', 1, 'numeric'),
  setting('l4_vision_model', 'gpt-5-mini', 'text'),
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <NewDedupDashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('<NewDedupDashboard>', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listNewDedupSettings).mockResolvedValue({ data: SETTINGS });
  });

  it('shows the two spend knobs and says "not yet" for every unmeasured cell', async () => {
    renderPage();
    /* Wait for a cell the SETTINGS query fills: the table's chrome renders
     * before that query resolves, so finding the row is not enough. */
    await screen.findByText('gpt-5-mini');
    const gpuRow = screen.getByText('L3 · Embeddings').closest('tr')!;
    const visionRow = screen.getByText('L4 · Vision').closest('tr')!;

    /* The knobs come from the settings registry, never from a constant here. */
    expect(within(gpuRow).getByText('$1.00 / day')).toBeInTheDocument();
    expect(within(visionRow).getByText('gpt-5-mini')).toBeInTheDocument();

    /* Spent so far and Projected — neither level has run. */
    expect(within(gpuRow).getAllByText('not yet')).toHaveLength(2);
    expect(within(visionRow).getAllByText('not yet')).toHaveLength(2);
    expectNoNestedInteractive(document.body);
  });

  it('says "not yet" for a knob the settings registry does not carry', async () => {
    vi.mocked(api.listNewDedupSettings).mockResolvedValue({ data: [] });
    renderPage();
    await waitFor(() => expect(api.listNewDedupSettings).toHaveBeenCalled());
    const gpuRow = (await screen.findByText('L3 · Embeddings')).closest('tr')!;
    /* Setting, spent and projected — three gaps, no invented cap. */
    await waitFor(() => expect(within(gpuRow).getAllByText('not yet')).toHaveLength(3));
  });

  it('marks the deleted wave as deleted', async () => {
    renderPage();
    await screen.findByText('Where the program is');
    const wave2 = screen.getByText('W2').closest('li')!;
    expect(wave2).toHaveTextContent('deleted');
    expect(wave2).toHaveTextContent('Level 0 — candidate selection');
  });
});
