/* The trace's selection layout: its groups (Strategy, Selection rounds) are L3
 * titles (Inter 600 14/20, ink), never small caps — small caps are L4 field
 * labels only (system.md § Section cards). */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import Timeline from './Timeline';
import type { Trace } from '@/lib/types';

const FILTERS = {
  radius_m: 1000,
  area_band_pct: 20,
  disposition_match: 'exact',
  max_age_days: 90,
  min_results: 5,
};

const TRACE = {
  version: 2,
  summary: 'Two rounds.',
  steps: [
    {
      n: 1,
      kind: 'computation',
      label: 'comparable_selection_summary',
      started_at: '2026-09-01T10:00:00Z',
      duration_ms: 1,
      output_summary: {
        n_rounds: 1,
        rounds: [
          {
            n: 1,
            filters: FILTERS,
            cohort_size: 6,
            cohort_ids: [1, 2, 3, 4, 5, 6],
            added_ids: [1, 2, 3, 4, 5, 6],
            removed_ids: [],
            n_relaxations: 0,
            reasoning: 'Enough within a kilometre.',
          },
        ],
        final_filters: FILTERS,
        final_comparable_ids: [1, 2, 3, 4, 5, 6],
        stop_reason: 'enough',
      },
    },
  ],
} as unknown as Trace;

describe('<Timeline> selection layout', () => {
  it('titles Strategy and Selection rounds as L3 headings', () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Timeline trace={TRACE} runId={77} />
      </QueryClientProvider>,
    );
    for (const name of ['Strategy', 'Selection rounds']) {
      const h = screen.getByRole('heading', { level: 3, name });
      expect(h).toHaveClass('text-sm', 'font-semibold', 'text-[var(--color-ink)]');
      expect(h).not.toHaveClass('uppercase');
    }
  });
});
