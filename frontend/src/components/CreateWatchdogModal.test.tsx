/* CreateWatchdogModal — the "save these Browse filters as a watchdog" prompt.
 *
 * The modal-dialog contract (src/test/a11y.ts, over lib/useDialog): named,
 * focus trapped and wrapping both ways, Escape closing exactly ONE layer,
 * focus handed back to the trigger, body scroll released.
 *
 * The name is pinned separately because it CHANGED once: an aria-label that
 * said "Create watchdog" (the submit button's words) instead of the heading's.
 *
 * The map area is the dialog's one choice (rule 16): the box Browse filters
 * on is watched unless unticked, and a centre+radius square always is.
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';

import { expectDialogContract } from '@/test/a11y';
import { createWatchdogSubscription } from '@/lib/api';
import { DEFAULT_FILTERS, effectiveBbox, type ListingFilters } from '@/lib/filters';
import CreateWatchdogModal, { MAP_AREA_LABEL } from './CreateWatchdogModal';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  createWatchdogSubscription: vi.fn(async () => ({}) as never),
}));

const BOUNDS = { west: 14.3, south: 50.0, east: 14.5, north: 50.2 };

function Host({ filters }: { filters: ListingFilters }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Watch these filters
      </button>
      {/* Mounting IS opening — the shape lib/useDialog's contract requires. */}
      {open ? <CreateWatchdogModal filters={filters} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function renderHost(filters: ListingFilters = DEFAULT_FILTERS) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Host filters={filters} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const open = () => fireEvent.click(screen.getByRole('button', { name: 'Watch these filters' }));
const create = () => fireEvent.click(screen.getByRole('button', { name: 'Create watchdog' }));
const lastSavedSpec = async () => {
  await waitFor(() => expect(vi.mocked(createWatchdogSubscription)).toHaveBeenCalled());
  return vi.mocked(createWatchdogSubscription).mock.calls.at(-1)![0].filter_spec;
};

describe('<CreateWatchdogModal> dialog contract', () => {
  it('names itself, traps focus, closes one layer on Escape and gives focus back', () => {
    renderHost();
    const trigger = screen.getByRole('button', { name: 'Watch these filters' });
    expectDialogContract({ trigger, open: () => fireEvent.click(trigger) });
  });

  it('announces the heading the operator is reading, not the submit button words', () => {
    renderHost();
    open();
    expect(screen.getByRole('dialog')).toHaveAccessibleName(
      'Save these filters as a watchdog',
    );
  });

  it('opens with the suggested name focused AND selected, so it can be typed over', () => {
    renderHost();
    open();
    const input = screen.getByRole('textbox', { name: 'Watchdog name' }) as HTMLInputElement;
    expect(input.value).toBe('byt prodej');
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe(input.value.length);
  });

  it('does not close on a click that lands inside the panel', () => {
    renderHost();
    open();
    /* <Dialog> tests the mousedown target instead of asking the panel to
     * swallow every click that reaches it. */
    fireEvent.mouseDown(screen.getByRole('textbox', { name: 'Watchdog name' }));
    fireEvent.click(screen.getByRole('textbox', { name: 'Watchdog name' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('still surfaces the unwatchable-filters heads-up inside the panel, and the map area is not one', () => {
    renderHost({ ...DEFAULT_FILTERS, recentlyAddedDays: 7, bounds: BOUNDS });
    open();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('recently added/changed');
    expect(screen.getByText(/can't be watched/).textContent).not.toContain('map area');
  });
});

describe('<CreateWatchdogModal> map area', () => {
  it('offers the viewport box ticked, and saves it', async () => {
    renderHost({ ...DEFAULT_FILTERS, bounds: BOUNDS });
    open();
    expect(screen.getByRole('button', { name: MAP_AREA_LABEL })).toHaveAttribute('aria-pressed', 'true');
    create();
    expect((await lastSavedSpec()).bounds).toEqual(BOUNDS);
  });

  it('unticked, the box is left off the spec', async () => {
    renderHost({ ...DEFAULT_FILTERS, bounds: BOUNDS });
    open();
    fireEvent.click(screen.getByRole('button', { name: MAP_AREA_LABEL }));
    expect(screen.getByRole('button', { name: MAP_AREA_LABEL })).toHaveAttribute('aria-pressed', 'false');
    create();
    expect((await lastSavedSpec()).bounds).toBeNull();
  });

  it('has no checkbox without a map area', () => {
    renderHost();
    open();
    expect(screen.queryByRole('button', { name: MAP_AREA_LABEL })).toBeNull();
  });

  it('has no checkbox in centre+radius mode: the square Browse filtered on always rides along', async () => {
    const filters: ListingFilters = {
      ...DEFAULT_FILTERS,
      locationMode: 'center_radius',
      centerRadius: { lat: 50.08, lng: 14.42, radius_m: 1500 },
      bounds: BOUNDS,
    };
    renderHost(filters);
    open();
    expect(screen.queryByRole('button', { name: MAP_AREA_LABEL })).toBeNull();
    create();
    const saved = await lastSavedSpec();
    expect(saved.bounds).toEqual(effectiveBbox(filters));
    expect(saved.bounds).not.toEqual(BOUNDS);
  });
});
