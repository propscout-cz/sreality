/* Browse card grid — the W7a non-blocking split, and the W5 card semantics.
 *
 * Photos used to be awaited INSIDE the cards read (`fetchListingsForCards`),
 * so `CardRow` carried an `images` array and no card painted until every card's
 * carousel had landed. Measured live on 24 real ids, that await was 178 image
 * rows, 178 correlated CLIP-tag lookups, 750 buffers and ~131 ms of server work
 * sitting directly on the paint path.
 *
 * Two things have to stay true, and they pull in opposite directions — which is
 * why both are pinned here:
 *
 *   1. The grid paints WITHOUT its photos. A hanging photo read must cost a card
 *      its carousel and nothing else.
 *   2. The carousel keeps EVERY row. This wave is the non-blocking split, not a
 *      move to cover-only; collapsing Browse cards onto the board's one-cover
 *      read would "fix" the paint path by deleting the feature.
 *
 * W5 added the second half of this file. Until then NOT ONE assertion here
 * called getByRole — which is exactly why nobody saw that the card was an <a>
 * wrapping eight controls: every query matched on text, and text is blind to
 * roles, to accessible names and to nesting. The rails below query the way a
 * keyboard or a screen reader reaches the card, and nothing else.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';

import { useEffect } from 'react';
import ListingCards from './ListingCards';
import { CardHydrationProvider } from '@/lib/hydration';
import { useMergeMode, type MergeMode } from '@/lib/useMergeMode';
import { expectNoNestedInteractive } from '@/test/a11y';
import * as api from '@/lib/api';
import * as brokers from '@/lib/brokers';
import * as queries from '@/lib/queries';
import { fmtShortDate } from '@/lib/format';
import type { CardRow } from '@/lib/queries';
import type { HoverTarget } from '@/lib/hoverSync';
import type { ImagePublic, ListingEstimate } from '@/lib/types';
import type { CoverTag } from '@/lib/imageTags';

vi.mock('@/lib/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/queries')>();
  return {
    ...actual,
    fetchImagesForListingIds: vi.fn(async () => new Map()),
    fetchListingCovers: vi.fn(async () => new Map()),
    fetchPropertyCollectionMemberSet: vi.fn(async () => new Map()),
    /* The card's pipeline funnel reads these two shared queries. Unmocked they
       reach the network, so every role assertion below would depend on it. */
    fetchPipelineMembers: vi.fn(async () => new Map()),
    fetchPipelineStages: vi.fn(async () => []),
    /* The dismiss control and the note mark (MS16), the same way. */
    fetchIsDismissed: vi.fn(async () => false),
    fetchNoteCounts: vi.fn(async () => new Map()),
  };
});
vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  fetchListingBrokersByIds: vi.fn(async () => new Map()),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  addPipelineCard: vi.fn(),
  listCollections: vi.fn(),
  addPropertiesToCollection: vi.fn(),
  removePropertyFromCollection: vi.fn(),
}));

const ROW = {
  property_id: 42,
  listing_id: 111,
  sreality_id: 900,
  price_czk: 5_400_000,
  area_m2: 62,
  display_label: 'Sadová 12, Praha',
  disposition: '2+kk',
  first_seen_at: '2026-01-01T00:00:00Z',
  last_seen_at: '2026-01-02T00:00:00Z',
  is_active: true,
  tom_days: 3,
  category_main: 'byt',
  category_type: 'prodej',
  source: 'sreality',
  source_id_native: '900',
  mf_gross_yield_pct: null,
  total_price_change_pct: null,
  price_change_count: null,
  all_sources: ['sreality'],
  active_sources: ['sreality'],
  source_count: 1,
} as unknown as CardRow;

/* The card's own title, and therefore the accessible name its ONE link must
   have. `\s` rather than a literal space: fmtArea joins the number and the unit
   with a non-breaking space, and the anchors make this an equality test rather
   than a substring one. */
const TITLE_RE = /^Byt na prodej · 2\+kk · 62\sm²$/;
const NO_LETTER_RE = /^Bez písmene: Byt na prodej · 2\+kk · 62\sm²$/;

const ESTIMATE: ListingEstimate = {
  sreality_id: 900,
  run_id: 77,
  status: 'success',
  estimate_kind: 'rent',
  gross_yield_pct: 4.2,
  estimated_monthly_rent_czk: null,
  created_at: null,
};

const photo = (id: number): ImagePublic =>
  ({
    id,
    sreality_url: `https://img/${id}.jpg`,
    storage_path: null,
    clip_fine_tag: null,
    clip_confidence: null,
    clip_render_score: null,
  }) as unknown as ImagePublic;

/* The carousel's "n / total" pill, matched on the LEAF node: every ancestor's
   textContent contains it too, so an unguarded matcher finds several. */
const counterIs = (want: string) => (_t: string, el: Element | null) =>
  el != null && el.children.length === 0 && el.textContent?.trim() === want;
const anyCounter = (_t: string, el: Element | null) =>
  el != null && el.children.length === 0 && /^\d+ \/ \d+$/.test(el.textContent?.trim() ?? '');

/* The router's live location, rendered so a test can assert that activating a
   control did NOT navigate — the defect this wave exists for. */
function LocationProbe() {
  const { pathname, search, hash } = useLocation();
  return <span data-testid="loc">{`${pathname}${search}${hash}`}</span>;
}
const locationNow = () => screen.getByTestId('loc').textContent;

/* Merge mode as the grid reads it: the card's letter and the verbs a test
   watches; the hook's own keys are proved through `renderMergeGrid` below. */
function mergeMode(opts: { letter?: string | null; stamp?: (propertyId: number) => void }): MergeMode {
  return {
    active: true,
    letters: opts.letter ? { 42: opts.letter } : {},
    activeLetter: 'A',
    groups: [],
    complete: false,
    enter: () => {},
    exit: () => {},
    stamp: opts.stamp ?? (() => {}),
    mark: () => {},
    clear: () => {},
    setActiveLetter: () => {},
    onKeyDown: () => {},
  };
}

function renderGrid(
  opts: {
    merge?: { letter?: string | null; stamp?: (propertyId: number) => void } | MergeMode;
    estimates?: Record<number, ListingEstimate>;
    row?: CardRow;
    rows?: CardRow[];
    onHover?: (targets: ReadonlyArray<HoverTarget> | null) => void;
    orderPortal?: string | null;
    coverTag?: CoverTag;
    onCoverTag?: (next: CoverTag) => void;
  } = {},
) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const merge = opts.merge == null ? null : 'active' in opts.merge ? opts.merge : mergeMode(opts.merge);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <LocationProbe />
        <CardHydrationProvider listingIds={[111]} renders={{ photos: 50 }}>
          <ListingCards
            rows={opts.rows ?? [opts.row ?? ROW]}
            total={1}
            sort={{ field: 'last_seen_at', dir: 'desc' } as never}
            orderPortal={opts.orderPortal}
            imageLarge={false}
            coverTag={opts.coverTag ?? 'default'}
            onCoverTag={opts.onCoverTag ?? (() => {})}
            isLoading={false}
            isFetchingNextPage={false}
            hasNextPage={false}
            onReachEnd={() => {}}
            restorationKey="test"
            hasFilters={false}
            hasBounds={false}
            hoveredIds={new Set()}
            onHover={opts.onHover ?? (() => {})}
            onSort={() => {}}
            onClearFilters={() => {}}
            onClearBounds={() => {}}
            merge={merge}
            estimates={opts.estimates}
            estimatingIds={new Set()}
            onEstimate={() => {}}
          />
        </CardHydrationProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/* Two photos, so the carousel actually renders its chevrons. */
const withPhotos = () =>
  vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(
    new Map([[111, [1, 2].map(photo)]]),
  );

beforeEach(() => {
  vi.mocked(queries.fetchImagesForListingIds).mockReset();
  vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map());
  vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map());
  vi.mocked(queries.fetchPipelineStages).mockResolvedValue([]);
  vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map());
  vi.mocked(queries.fetchIsDismissed).mockResolvedValue(false);
  vi.mocked(queries.fetchNoteCounts).mockResolvedValue(new Map());
  vi.mocked(api.listCollections).mockResolvedValue({ data: [], total: 0 });
  vi.mocked(api.addPipelineCard).mockResolvedValue({
    property_id: 42,
    stage_key: 'review',
    added: true,
  });
});

describe('<ListingCards> photo hydration', () => {
  /* The split, pinned: with the photo read hanging forever, the card is fully
     rendered. Before W7a this state was unreachable in the other direction —
     there was no card at all until the photos resolved. */
  it('paints the card while the photo read is still in flight', async () => {
    vi.mocked(queries.fetchImagesForListingIds).mockReturnValue(
      new Promise(() => {}) as ReturnType<typeof queries.fetchImagesForListingIds>,
    );

    renderGrid();

    // Structure: place and price are on screen with zero photos resolved.
    expect(await screen.findByText(/Sadová/)).toBeInTheDocument();
    expect(screen.getByText(/5\s*400\s*000/)).toBeInTheDocument();
    // The carousel renders its own empty frame — no loading string stands in
    // for the card, and no photo counter claims images that have not arrived.
    expect(screen.getByText('no image')).toBeInTheDocument();
    expect(screen.queryByText(anyCounter)).toBeNull();
  });

  /* The other half of the contract. The ledger's warning for this wave was
     explicit — the win is the non-blocking split, NOT cover-only — so a card
     must still get every photo it had. The "n / total" counter is the carousel's
     own report of how many rows it holds, so it is the honest assertion here. */
  it('keeps the whole carousel, not just a cover', async () => {
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(
      new Map([[111, [1, 2, 3, 4, 5, 6, 7].map(photo)]]),
    );

    renderGrid();

    // The counter is JSX-interpolated ({i + 1} / {n}), so it arrives as several
    // text nodes — match the LEAF element's normalized text (every ancestor
    // contains it too, hence the childless guard).
    expect(await screen.findByText(counterIs('1 / 7'))).toBeInTheDocument();
    expect(screen.getByLabelText('Next photo')).toBeInTheDocument();
    expect(screen.queryByText('no image')).toBeNull();
  });

  /* A listing with no photos at all is a real answer, not a pending one: the
     empty frame is correct and must not be mistaken for the case above. */
  it('draws the empty frame for a listing that genuinely has no photos', async () => {
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map());

    renderGrid();

    expect(await screen.findByText(/Sadová/)).toBeInTheDocument();
    expect(screen.getByText('no image')).toBeInTheDocument();
  });

  /* One cohort read for the whole grid, keyed on the SURROGATE listing_id —
     never sreality_id, which is NULL on a post-Gate-2 non-sreality card and
     would silently cost it every photo. */
  it('reads photos once for the cohort, keyed on the surrogate id', async () => {
    renderGrid();

    expect(await screen.findByText(/Sadová/)).toBeInTheDocument();
    expect(queries.fetchImagesForListingIds).toHaveBeenCalledTimes(1);
    expect(queries.fetchImagesForListingIds).toHaveBeenCalledWith([111], 50, expect.anything());
  });

  /* The north star as a test: every surface pays only for what it RENDERS.
     Browse cards show no cover thumbnail and no broker line, so mounting the
     shared provider must not fetch either.

     This is a REGRESSION TEST for a defect that reached production. W7a's first
     cut made only `photos` opt-in and left covers and brokers always-on, so
     Browse began quietly fetching a cover per card and a broker per card that
     nothing on the page displays — caught on the live post-deploy smoke run,
     /browse 22 -> 24 requests. Asymmetric defaults are how that happens. */
  it('fetches ONLY what the grid renders — no covers, no brokers', async () => {
    renderGrid();

    expect(await screen.findByText(/Sadová/)).toBeInTheDocument();
    expect(queries.fetchListingCovers).not.toHaveBeenCalled();
    expect(brokers.fetchListingBrokersByIds).not.toHaveBeenCalled();
  });
});

/* The Cover dropdown: which tagged photo every card opens on. */
describe('<ListingCards> the cover photo', () => {
  const tagged = (id: number, clip_fine_tag: string | null, tag_head_scores: Record<string, number> | null = null) =>
    ({ ...photo(id), clip_fine_tag, clip_confidence: 0.9, tag_head_scores }) as unknown as ImagePublic;
  const kitchenThird = () =>
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(
      new Map([[111, [tagged(1, 'exterior_facade'), tagged(2, 'hallway'), tagged(3, 'kitchen'), tagged(4, 'bedroom')]]]),
    );

  it('opens the card on the kitchen photo, keeping the gallery order', async () => {
    kitchenThird();
    renderGrid({ coverTag: 'kitchen' });
    expect(await screen.findByText(counterIs('3 / 4'))).toBeInTheDocument();
    expect(document.querySelector('img')!.getAttribute('src')).toBe('https://img/3.jpg');
  });

  it('shows the first photo when the ad has no photo with the tag', async () => {
    kitchenThird();
    renderGrid({ coverTag: 'garden' });
    expect(await screen.findByText(counterIs('1 / 4'))).toBeInTheDocument();
    expect(document.querySelector('img')!.getAttribute('src')).toBe('https://img/1.jpg');
  });

  it('lets the trained head overrule CLIP where it scored the photos', async () => {
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(
      new Map([[111, [
        tagged(1, 'exterior_facade', { '25': 0.02 }),
        tagged(2, 'kitchen', { '25': 0.1 }),
        tagged(3, 'hallway', { '25': 0.91 }),
      ]]]),
    );
    renderGrid({ coverTag: 'kitchen' });
    expect(await screen.findByText(counterIs('3 / 3'))).toBeInTheDocument();
  });

  it('reports the operator\'s pick from the dropdown beside Sort', async () => {
    const onCoverTag = vi.fn();
    renderGrid({ onCoverTag });
    fireEvent.change(await screen.findByRole('combobox', { name: /Cover/ }), {
      target: { value: 'garage' },
    });
    expect(onCoverTag).toHaveBeenCalledWith('garage');
    expect(screen.getByRole('option', { name: 'Fasáda' })).toBeInTheDocument();
  });
});

describe('<ListingCards> hover-sync', () => {
  /* The card hands the map its own point, so the locator halo lands on the
   * property even while the map shows it only inside a count mark. */
  it('reports the hovered card with its point, and nothing on leave', async () => {
    const onHover = vi.fn();
    renderGrid({ row: { ...ROW, lat: 50.08, lng: 14.43 } as CardRow, onHover });
    const price = await screen.findByText(/5.400.000/);
    fireEvent.mouseEnter(price);
    /* The row itself is the target: its key and its point. */
    expect(onHover).toHaveBeenLastCalledWith([expect.objectContaining({ listing_id: 111, lat: 50.08, lng: 14.43 })]);
    fireEvent.mouseLeave(price);
    expect(onHover).toHaveBeenLastCalledWith(null);
  });
});

/* How many ads the card's property holds, every ad active or not: read off the
 * row (`source_count`, migration 590), so the badge paints with the card. Two
 * ads or more only — most properties are one ad, and a badge on each would be
 * noise. */
describe('<ListingCards> the ad-count badge', () => {
  const BADGE = /^Nemovitost spojuje/;
  const withAds = (n: number) => ({ ...ROW, source_count: n }) as CardRow;

  it('labels a property of three ads "3 inzeráty", the sentence in its title', () => {
    renderGrid({ row: withAds(3) });

    const badge = screen.getByTitle('Nemovitost spojuje 3 inzeráty (počítají se i neaktivní)');
    expect(badge).toHaveTextContent(/^3\s*inzeráty$/);
  });

  it('says "inzerátů" from five ads on', () => {
    renderGrid({ row: withAds(5) });

    const badge = screen.getByTitle('Nemovitost spojuje 5 inzerátů (počítají se i neaktivní)');
    expect(badge).toHaveTextContent(/^5\s*inzerátů$/);
  });

  /* Both sides of the threshold: one ad draws nothing, two draw the badge. */
  it('draws nothing for a property of one ad, "2 inzeráty" for two', () => {
    const one = renderGrid({ row: withAds(1) });
    expect(screen.getByText(/Sadová/)).toBeInTheDocument();
    expect(screen.queryByTitle(BADGE)).toBeNull();
    one.unmount();

    renderGrid({ row: withAds(2) });
    expect(screen.getByTitle(BADGE)).toHaveTextContent(/^2\s*inzeráty$/);
  });
});

/* THE W5 DEFECT, pinned from the outside.
 *
 * The card used to be `<Link>` wrapped around its whole body, with the carousel
 * chevrons, the pipeline funnel, the collection trigger and the estimate corner
 * as DOM descendants. Consequences, all of them invisible to a text query: the
 * anchor's accessible name was the entire card read aloud; one card was five
 * tab stops nested inside one; and a preventDefault/stopPropagation regime in
 * four files existed only to keep those controls from navigating. */
describe('<ListingCards> the card is not a link — its TITLE is', () => {
  it('gives the card ONE link, named by the listing and nothing else', async () => {
    renderGrid();

    const link = await screen.findByRole('link', { name: /^Byt na prodej/ });
    expect(link).toHaveAccessibleName(TITLE_RE);
    // What the wrapping anchor used to absorb: the card's every other word.
    expect(link).not.toHaveAccessibleName(/Sadová/);
    expect(link).not.toHaveAccessibleName(/Odhad/);
    // And it is the only link on the card — the title, not the card, is the
    // destination.
    expect(screen.getAllByRole('link')).toHaveLength(1);
  });

  it('exposes every control by its OWN role and name', async () => {
    withPhotos();
    renderGrid();

    expect(await screen.findByRole('button', { name: 'Previous photo' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next photo' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Přidat do pipeline' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Uložit do kolekce' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Odhad' })).toBeInTheDocument();
  });

  /* The whole point of un-nesting: a control does its own job and only its own
     job. Every one of these clicks used to need a preventDefault to avoid
     opening the listing. */
  it('never navigates when a control on the card is activated', async () => {
    withPhotos();
    renderGrid();

    expect(await screen.findByRole('button', { name: 'Previous photo' })).toBeInTheDocument();
    expect(locationNow()).toBe('/');

    fireEvent.click(screen.getByRole('button', { name: 'Next photo' }));
    fireEvent.click(screen.getByRole('button', { name: 'Odhad' }));
    fireEvent.click(screen.getByRole('button', { name: 'Uložit do kolekce' }));
    fireEvent.click(screen.getByRole('button', { name: 'Přidat do pipeline' }));

    expect(locationNow()).toBe('/');
  });

  it('navigates to the property page on a plain click of the title link', async () => {
    renderGrid();

    fireEvent.click(await screen.findByRole('link', { name: /^Byt na prodej/ }));

    // The card's own advert rides along; the page ignores it when it is the
    // canonical one and opens its row when the card is one portal's advert.
    expect(locationNow()).toBe('/property/42?advert=111');
  });

  /* Same rule lib/linkGestures encodes for the surfaces the router does not
     own: a modified click is the user asking the BROWSER for a tab, and the SPA
     must not swallow it. A real <a> gets this for free; the old role="button"
     merge wrapper and navigate()-in-onClick could not.

     jsdom prints "Not implemented: navigation" on this run. That log IS the
     assertion's other half: it only happens because nothing called
     preventDefault, i.e. the gesture really was handed to the browser. */
  it('leaves a ctrl-click to the browser', async () => {
    renderGrid();

    fireEvent.click(await screen.findByRole('link', { name: /^Byt na prodej/ }), {
      ctrlKey: true,
    });

    expect(locationNow()).toBe('/');
  });

  /* The query axe structurally cannot run for an <a> wrapper (see test/a11y.ts)
     — and an <a> wrapper is exactly what this card was. */
  it('has no interactive element nested inside another', async () => {
    withPhotos();
    const { container } = renderGrid();

    expect(await screen.findByRole('button', { name: 'Previous photo' })).toBeInTheDocument();
    expectNoNestedInteractive(container);
  });

  /* "Open the run" is a destination, so it is a link — it was a <button>
     calling navigate() only because an <a> inside the card's <a> is invalid
     markup. With the wrapper gone the honest element is available again. */
  it('makes a finished estimate a real link to the run surface', async () => {
    renderGrid({ estimates: { 900: ESTIMATE } });

    const link = await screen.findByRole('link', { name: /Výnos/ });
    expect(link).toHaveAttribute('href', '/listing/900?run=77#estimations');

    fireEvent.click(link);
    expect(locationNow()).toBe('/listing/900?run=77#estimations');
  });
});

/* Merge mode used to swap the card's <Link> for a role="button" div carrying
 * aria-pressed, while DRAWING a checkbox that was pure decoration. The drawn
 * thing is now the real thing. */
/* The grid under the REAL merge mode (lib/useMergeMode): the letters it holds
   are rendered beside it, so a test reads the statement a key produced. */
function MergeHarness({ rows }: { rows: CardRow[] }) {
  const merge = useMergeMode();
  const { enter } = merge;
  useEffect(() => enter(), [enter]);
  return (
    <>
      <span data-testid="letters">{JSON.stringify(merge.letters)}</span>
      <span data-testid="active">{merge.activeLetter}</span>
      {/* A field beside the grid, the way the filter sidebar sits beside it. */}
      <input data-testid="field" aria-label="Cena od" />
      <ListingCards
        rows={rows}
        total={rows.length}
        sort={{ field: 'last_seen_at', dir: 'desc' } as never}
        imageLarge={false}
        coverTag="default"
        onCoverTag={() => {}}
        isLoading={false}
        isFetchingNextPage={false}
        hasNextPage={false}
        onReachEnd={() => {}}
        restorationKey="test"
        hasFilters={false}
        hasBounds={false}
        hoveredIds={new Set()}
        onHover={() => {}}
        onSort={() => {}}
        onClearFilters={() => {}}
        onClearBounds={() => {}}
        merge={merge.active ? merge : null}
        estimates={undefined}
        estimatingIds={new Set()}
        onEstimate={() => {}}
      />
    </>
  );
}
const ROW_2 = { ...ROW, property_id: 43, listing_id: 112, sreality_id: 901 } as CardRow;
function renderMergeGrid(rows: CardRow[] = [ROW, ROW_2]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CardHydrationProvider listingIds={rows.map((r) => r.listing_id)} renders={{ photos: 50 }}>
          <MergeHarness rows={rows} />
        </CardHydrationProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return {
    letters: () => JSON.parse(screen.getByTestId('letters').textContent ?? '{}') as Record<string, string>,
    active: () => screen.getByTestId('active').textContent,
    cardButtons: () => screen.getAllByRole('button', { name: /^(Bez písmene|Písmeno [A-Z]):/ }),
  };
}

describe('<ListingCards> merge mode marks cards with letters', () => {
  it('exposes one button per card, named by the listing and its letter', () => {
    renderGrid({ merge: { letter: null } });
    expect(screen.getByRole('button', { name: /^Bez písmene/ })).toHaveAccessibleName(NO_LETTER_RE);
    renderGrid({ merge: { letter: 'B' } });
    expect(screen.getByRole('button', { name: /^Písmeno B: Byt na prodej/ })).toBeInTheDocument();
  });

  it('a click, Space or Enter writes the active letter', async () => {
    const stamp = vi.fn();
    const user = userEvent.setup();
    renderGrid({ merge: { letter: null, stamp } });
    const card = screen.getByRole('button', { name: /^Bez písmene/ });
    fireEvent.click(card);
    card.focus();
    await user.keyboard(' ');
    await user.keyboard('{Enter}');
    expect(stamp.mock.calls).toEqual([[42], [42], [42]]);
  });

  it('a letter key marks the focused card and becomes the active letter; the same letter again, Backspace and Delete clear it', async () => {
    const user = userEvent.setup();
    const grid = renderMergeGrid();
    const [first, second] = grid.cardButtons();
    first.focus();
    await user.keyboard('b');
    expect(grid.letters()).toEqual({ 42: 'B' });
    expect(grid.active()).toBe('B');
    expect(first).toHaveAccessibleName(/^Písmeno B:/);
    second.focus();
    await user.keyboard('b');
    expect(grid.letters()).toEqual({ 42: 'B', 43: 'B' });
    await user.keyboard('b');
    expect(grid.letters()).toEqual({ 42: 'B' });
    await user.keyboard('c');
    expect(grid.letters()).toEqual({ 42: 'B', 43: 'C' });
    await user.keyboard('{Backspace}');
    expect(grid.letters()).toEqual({ 42: 'B' });
    first.focus();
    await user.keyboard('{Delete}');
    expect(grid.letters()).toEqual({});
  });

  it('a click writes the letter the last key made active, and the same letter again clears', async () => {
    const user = userEvent.setup();
    const grid = renderMergeGrid();
    const [first, second] = grid.cardButtons();
    first.focus();
    await user.keyboard('c');
    fireEvent.click(second);
    expect(grid.letters()).toEqual({ 42: 'C', 43: 'C' });
    fireEvent.click(second);
    expect(grid.letters()).toEqual({ 42: 'C' });
  });

  it('the pointer focuses the card it moves over, so keys and Tab share one target', () => {
    const grid = renderMergeGrid();
    const [first, second] = grid.cardButtons();
    fireEvent.mouseMove(second.parentElement!);
    expect(second).toHaveFocus();
    fireEvent.mouseMove(first.parentElement!);
    expect(first).toHaveFocus();
    /* An enter without movement is what a scroll synthesizes under a still
       pointer: it must not rob the card Tab just reached. */
    fireEvent.mouseEnter(second.parentElement!);
    expect(first).toHaveFocus();
  });

  it('never takes focus from a field the operator is typing in', () => {
    const grid = renderMergeGrid();
    const [first] = grid.cardButtons();
    const field = screen.getByTestId('field');
    field.focus();
    fireEvent.mouseMove(first.parentElement!);
    expect(field).toHaveFocus();
  });

  it('Tab moves card to card past the chevrons, and ← → page the focused card’s photos', async () => {
    withPhotos();
    const user = userEvent.setup();
    const grid = renderMergeGrid();
    const [first, second] = grid.cardButtons();
    const next = await screen.findByRole('button', { name: 'Next photo' });
    expect(next).toHaveAttribute('tabindex', '-1');
    first.focus();
    await user.tab();
    expect(second).toHaveFocus();
    await user.tab({ shift: true });
    expect(first).toHaveFocus();
    expect(screen.getByText(counterIs('1 / 2'))).toBeInTheDocument();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByText(counterIs('2 / 2'))).toBeInTheDocument();
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByText(counterIs('1 / 2'))).toBeInTheDocument();
    expect(grid.letters()).toEqual({});
  });

  /* One meaning per click at any moment (TagContentsGallery's rule): in merge
     mode a click marks, so there is no listing link and no estimate control
     competing for the same gesture. */
  it('offers no link and no estimate corner while marking', () => {
    renderGrid({ merge: { letter: null } });

    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Odhad' })).toBeNull();
  });

  it('has no interactive element nested inside another', () => {
    const { container } = renderGrid({ merge: { letter: 'A' } });

    expectNoNestedInteractive(container);
  });
});

/* The collection popover was a hand-rolled `absolute` panel inside the card's
 * anchor: it announced nothing, dismissed itself with a document-mousedown
 * listener of its own, preventDefault'ed every click in the panel to stop the
 * card navigating, and rendered its empty state as an <a> inside that <a>. It
 * is the shared portalled AnchoredPopover now. */
describe('<ListingCards> the collection popover is a real popup', () => {
  it('announces itself, and Escape closes it and hands focus back', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [{ id: 7, name: 'Sledované', monitoring_enabled: true } as never],
      total: 1,
    });
    renderGrid();

    const trigger = await screen.findByRole('button', { name: 'Uložit do kolekce' });
    // No aria-haspopup: "true" means MENU, and this panel is a list of toggles,
    // not a menu. aria-expanded + aria-controls carry the disclosure contract.
    expect(trigger).not.toHaveAttribute('aria-haspopup');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(document.getElementById(trigger.getAttribute('aria-controls') ?? '')).not.toBeNull();
    expect(await screen.findByRole('button', { name: /Sledované/ })).toBeInTheDocument();
    // Portalled onto <body>, so nothing in the panel is nested in the card.
    expectNoNestedInteractive(document.body);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(document.activeElement).toBe(trigger);
  });

  /* The empty state is a <Link>. Inside the old panel that was an anchor inside
     the card's anchor — invalid markup React itself warns about. */
  it('offers "create a collection" as a plain link, not an anchor in an anchor', async () => {
    renderGrid();

    fireEvent.click(await screen.findByRole('button', { name: 'Uložit do kolekce' }));

    const create = await screen.findByRole('link', { name: /Create a collection/ });
    expect(create).toHaveAttribute('href', '/collections');
    expectNoNestedInteractive(document.body);
  });
});

/* The portalled popover must be keyboard-reachable: the panel lands at the END
 * of <body>, so without an explicit focus move Tab would walk the whole page
 * before reaching it. */
describe('<ListingCards> collection popover keyboard entry', () => {
  it('moves focus into the panel on open and back to the trigger on Escape', async () => {
    renderGrid();
    const trigger = await screen.findByRole('button', { name: 'Uložit do kolekce' });
    trigger.focus();
    fireEvent.click(trigger);
    const panel = await screen.findByRole('group', { name: 'Uložit do kolekce' });
    expect(panel.contains(document.activeElement)).toBe(true);
    expect(trigger).toHaveAttribute('aria-controls', panel.id);
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Uložit do kolekce' })).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });
});

/* Saved means MINE (migration 610): a property only in someone else's shared
 * collection never paints as saved, so the glyph agrees with the save menu. */
describe('<ListingCards> the collection glyph counts my collections only', () => {
  const glyph = async () =>
    (await screen.findByRole('button', { name: 'Uložit do kolekce' })).querySelector('svg');

  it('fills for a property in my own collection', async () => {
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [{ id: 7, name: 'Šortlist', owner_email: null, shared_with: [] } as never],
      total: 1,
    });
    renderGrid();
    await waitFor(async () => expect((await glyph())?.getAttribute('fill')).toBe('currentColor'));
  });

  it('stays empty for a property only in a collection shared with me', async () => {
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [9]]]));
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [{ id: 9, name: 'Od Petra', owner_email: 'petr@example.cz', shared_with: [] } as never],
      total: 1,
    });
    renderGrid();
    await waitFor(() => expect(api.listCollections).toHaveBeenCalled());
    await waitFor(() => expect(queries.fetchPropertyCollectionMemberSet).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect((await glyph())?.getAttribute('fill')).toBe('none');
  });
});

/* MS16: a merge hides nothing it will touch — merge mode keeps every mark,
 * read-only, beside the checkbox, and drops the verbs that would change them. */
describe('<ListingCards> merge mode keeps the marks, read-only', () => {
  beforeEach(() => {
    vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map([[42, {
      property_id: 42, stage_id: 1, stage_label: 'Prověřit', stage_color: 'copper',
      stage_code: '1', stage_position: 1, is_terminal: true,
    }]]));
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    // The mark counts MY collections: the list names 7 as mine (owner_email null).
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [{ id: 7, name: 'Šortlist', owner_email: null, shared_with: [] } as never],
      total: 1,
    });
    vi.mocked(queries.fetchIsDismissed).mockResolvedValue(true);
    vi.mocked(queries.fetchNoteCounts).mockResolvedValue(new Map([[42, 3]]));
  });

  it('shows the stage, collection, dismissal and note marks and none of the verbs', async () => {
    const { container } = renderGrid({ merge: { letter: null } });
    for (const mark of ['V pipeline: Prověřit', 'V kolekci', 'Skryto', 'Poznámky: 3']) {
      expect(await screen.findByText(mark)).toBeInTheDocument();
    }
    for (const verb of ['Přidat do pipeline', 'Uložit do kolekce', 'Skrýt nemovitost']) {
      expect(screen.queryByRole('button', { name: verb })).toBeNull();
    }
    expectNoNestedInteractive(container);
  });

  it('shows the note count beside the verbs outside merge mode', async () => {
    renderGrid();
    expect(await screen.findByTitle('Poznámky: 3')).toHaveTextContent('3');
  });

  it('turns a failed collection read into a retry, never "in no collection"', async () => {
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockRejectedValueOnce(new Error('HTTP 500'));
    renderGrid();
    fireEvent.click(await screen.findByRole('button', { name: /Kolekce se nepodařilo načíst/ }));
    expect(await screen.findByRole('button', { name: 'Uložit do kolekce' })).toBeInTheDocument();
  });
});

describe('<ListingCards> merge mode with a multi-photo card', () => {
  it('keeps the card button named and lets a chevron page without marking', async () => {
    withPhotos();
    const stamp = vi.fn();
    renderGrid({ merge: { letter: null, stamp } });
    expect(screen.getByRole('button', { name: /^Bez písmene/ })).toHaveAccessibleName(NO_LETTER_RE);
    fireEvent.click(await screen.findByRole('button', { name: 'Next photo' }));
    expect(screen.getByText(counterIs('2 / 2'))).toBeInTheDocument();
    expect(stamp).not.toHaveBeenCalled();
  });
});

/* MS19: a card is a property. Its badge names the portals where it has an active ad
 * (or, with none active, every portal it had, marked inactive), and under one
 * portal's "Newest first" a card whose date there is not its own first seen says so. */
describe('<ListingCards> the portal badge and the order date', () => {
  const badge = () => screen.getByTitle(/^Portály/);

  it('lists every portal with an active ad', () => {
    renderGrid({ row: { ...ROW, all_sources: ['idnes', 'sreality'], active_sources: ['idnes', 'sreality'] } });
    expect(badge().textContent).toBe('portáliDNES Reality · Sreality');
  });

  it('lists only the live portals while one is live', () => {
    renderGrid({ row: { ...ROW, all_sources: ['bazos', 'idnes'], active_sources: ['idnes'] } });
    expect(badge().textContent).toBe('portáliDNES Reality');
  });

  it('marks every portal inactive when no ad is live', () => {
    renderGrid({ row: { ...ROW, is_active: false, all_sources: ['bazos', 'idnes'], active_sources: [] } });
    expect(badge().textContent).toBe('neaktivníBazoš · iDNES Reality');
  });

  const lifespan = () => screen.getByTitle(/^Aktivní/).textContent;

  it("shows the portal's date beside the card's own when the two days differ", () => {
    renderGrid({
      orderPortal: 'idnes',
      row: { ...ROW, newest_ad_at_idnes: '2026-03-05T10:00:00Z' } as CardRow,
    });
    expect(lifespan()).toContain(`·iDNESod${fmtShortDate('2026-03-05T10:00:00Z')}`);
  });

  it('shows one date when the newest ad there is the first seen', () => {
    renderGrid({
      orderPortal: 'idnes',
      row: { ...ROW, newest_ad_at_idnes: '2026-01-01T09:00:00Z' } as CardRow,
    });
    expect(lifespan()).not.toContain('iDNES');
  });

  it('shows no portal date without the one-portal order', () => {
    renderGrid({ row: { ...ROW, newest_ad_at_idnes: '2026-03-05T10:00:00Z' } as CardRow });
    expect(lifespan()).not.toContain('iDNES');
  });
});
