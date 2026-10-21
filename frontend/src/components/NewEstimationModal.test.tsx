/* NewEstimationModal — "New estimation" of one ad we hold.
 *
 * Pinned here. (1) The subject is the ad the opener names: the POST body
 * carries its `listing_id` and nothing that could name another subject (no
 * URL arm exists any more, docs/design/new-property-from-link.md §3.5). (2) The
 * dialog and its fields are wired by useId(), not by hard-coded DOM ids, so two
 * mounted providers cannot cross-wire a <label> to the other modal's control.
 * (3) The shared modal-dialog contract (src/test/a11y.ts, over lib/useDialog).
 * (4) Initial focus lands on the primary action rather than on the header's
 * close glyph — which is what the primitive would pick, being the first
 * focusable control — and NO dismissal gesture fires while a submit is in
 * flight.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Link, MemoryRouter, useLocation } from 'react-router-dom';

import { expectDialogContract } from '@/test/a11y';
import { ApiError } from '@/lib/api';
import { ROUTES } from '@/lib/routes';
import { NewEstimationProvider, useNewEstimationModal } from './NewEstimationModal';

/* Only the POST is replaced: a promise that never settles is the in-flight
 * state under test, a resolved run is the navigation under test. */
const createEstimation = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createEstimation,
}));

beforeEach(() => {
  createEstimation.mockReset();
});

function Opener() {
  const { open } = useNewEstimationModal();
  return (
    <button
      type="button"
      onClick={() => open({ listingId: 105053, categoryMain: 'dum', estimateKind: 'sale' })}
    >
      Open estimation
    </button>
  );
}

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search + l.hash}</p>;
}

function renderHost(count = 1) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const providers = Array.from({ length: count }, (_, i) => (
    <NewEstimationProvider key={i}>
      <Opener />
      {/* Below the provider on purpose: this is a navigation started from a
        * page the provider sits ABOVE, which is the shape useCloseOnNavigation
        * exists for. */}
      <Link to={ROUTES.browse.build()}>go elsewhere</Link>
    </NewEstimationProvider>
  ));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/property/774']}>
        {providers}
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const renderModals = async (count = 1) => {
  const utils = renderHost(count);
  screen.getAllByRole('button', { name: 'Open estimation' }).forEach((b) => fireEvent.click(b));
  /* The body is a lazy chunk (first-visit budget): wait for every dialog to mount. */
  await screen.findAllByRole('dialog');
  return utils;
};

describe('<NewEstimationModal>', () => {
  it('names the dialog and offers no URL field', async () => {
    await renderModals();
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Estimate this property');
    expect(screen.queryByRole('textbox', { name: 'Listing URL' })).not.toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: /Building/ })).not.toBeInTheDocument();
  });

  it('seeds the selectors from the ad', async () => {
    await renderModals();
    expect(screen.getByRole('radio', { name: 'House' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: 'Sale' })).toHaveAttribute('aria-checked', 'true');
  });

  it('names the operator-context textareas', async () => {
    await renderModals();
    expect(screen.getByRole('textbox', { name: 'Special instructions' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Property context' })).toBeInTheDocument();
  });

  it('estimates the ad it was opened for, by its listing id, and lands on the run', async () => {
    createEstimation.mockResolvedValue({
      id: 88, status: 'pending', subject_source: 'idnes', subject_source_id_native: 'abc123',
    });
    await renderModals();
    fireEvent.click(screen.getByRole('radio', { name: 'Rent' }));
    fireEvent.click(screen.getByRole('button', { name: 'Estimate' }));

    await waitFor(() => expect(createEstimation).toHaveBeenCalledTimes(1));
    const body = createEstimation.mock.calls[0][0];
    expect(body).toMatchObject({
      listing_id: 105053,
      estimate_kind: 'rent',
      category_main: 'dum',
      category_type: 'pronajem',
      source: 'ui',
    });
    expect(body).not.toHaveProperty('url');
    expect(body).not.toHaveProperty('sreality_id');
    expect(body).not.toHaveProperty('spec');
    await waitFor(() =>
      expect(screen.getByTestId('where').textContent).toBe('/listing/idnes/abc123?run=88#estimations'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('says what the API refused, in its own words', async () => {
    createEstimation.mockRejectedValue(new ApiError('This listing needs a location to be estimated.', 422, null));
    await renderModals();
    fireEvent.click(screen.getByRole('button', { name: 'Estimate' }));
    expect(await screen.findByText('This listing needs a location to be estimated.')).toBeInTheDocument();
  });

  it('gives two mounted modals distinct ids, so neither steals the other label', async () => {
    await renderModals(2);
    const [a, b] = screen.getAllByRole('textbox', { name: 'Special instructions' });
    expect(a.id).not.toBe('');
    expect(a.id).not.toBe(b.id);
    const dialogs = screen.getAllByRole('dialog');
    expect(dialogs[0].getAttribute('aria-labelledby')).not.toBe(
      dialogs[1].getAttribute('aria-labelledby'),
    );
  });
});

describe('<NewEstimationModal> dialog contract', () => {
  it('names itself, traps focus, closes one layer on Escape and gives focus back', async () => {
    renderHost();
    const trigger = screen.getByRole('button', { name: 'Open estimation' });
    expectDialogContract({ trigger, open: () => fireEvent.click(trigger) });
  });

  it('opens on the primary action, not on the close glyph in front of it', async () => {
    await renderModals();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Estimate' }));
  });

  it('leaves no scroll lock behind when the page underneath navigates away', async () => {
    renderHost();
    const trigger = screen.getByRole('button', { name: 'Open estimation' });
    trigger.focus();
    fireEvent.click(trigger);
    expect(document.body.style.overflow).toBe('hidden');

    /* The defect useCloseOnNavigation fixes: the provider mounts above
     * <Outlet>, so without it this left the modal — and this lock — sitting
     * over a page the operator never asked to see it over. */
    fireEvent.click(screen.getByRole('link', { name: 'go elsewhere' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe('');
  });
});

describe('<NewEstimationModal> in-flight guard', () => {
  /* The gating lives in ONE guarded onClose, so every dismissal gesture is
   * asserted against that one place. */
  async function submitAndHang() {
    createEstimation.mockReturnValue(new Promise(() => {}));
    await renderModals();
    fireEvent.click(screen.getByRole('button', { name: 'Estimate' }));
    /* react-query settles `isPending` a microtask later, so waiting on the
     * button's in-flight label is what makes this test about the guard rather
     * than about a race it would otherwise win by accident. */
    await screen.findByText('Opening…');
    return screen.getByRole('dialog');
  }

  it('ignores Escape while a submit is in flight', async () => {
    const dialog = await submitAndHang();
    fireEvent.keyDown(document.activeElement ?? document, { key: 'Escape' });
    expect(dialog).toBeInTheDocument();
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('ignores the close glyph while a submit is in flight', async () => {
    const dialog = await submitAndHang();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(dialog).toBeInTheDocument();
  });
});
