/* MS16: a merge hides nothing it will touch. The merge bar names every lettered
 * property with its marks, letter by letter, says how many "Různé" rulings the
 * statement would take back (MS12), holds the merge while a letter has one
 * property, and offers the letter a click writes; a failed read is a retry,
 * never an absent mark or a count of none. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import MergeBar, { MergeStatement } from './MergeBar';
import type { Letters } from '@/lib/api';
import type { MergeMode } from '@/lib/useMergeMode';
import * as api from '@/lib/api';
import * as queries from '@/lib/queries';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getMergePreview: vi.fn(),
  listCollections: vi.fn(),
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchPipelineMembers: vi.fn(),
  fetchPipelineStages: vi.fn(async () => []),
  fetchPropertyCollectionMemberSet: vi.fn(),
  fetchIsDismissed: vi.fn(),
  fetchNoteCounts: vi.fn(),
}));

// The collection mark counts MY collections, so the list read names id 7 as mine.
const MINE_7 = {
  id: 7, name: 'Šortlist', description: null, created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-01T00:00:00Z', listing_count: 1, monitoring_enabled: false,
  is_system: false, owner_email: null, shared_with: [],
};

const renderWith = (ui: React.ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

/* The mode as the bar reads it, over a statement; the hook's own grouping is
   proved with the card grid (ListingCards.test.tsx). */
function mode(letters: Letters, over: Partial<MergeMode> = {}): MergeMode {
  const by = new Map<string, number[]>();
  for (const [id, letter] of Object.entries(letters)) by.set(letter, [...(by.get(letter) ?? []), Number(id)]);
  const groups = [...by.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([letter, ids]) => [letter, ids.sort((a, b) => a - b)] as const);
  return {
    active: true, letters, activeLetter: 'A', groups,
    complete: groups.length > 0 && groups.every(([, ids]) => ids.length >= 2),
    enter: vi.fn(), exit: vi.fn(), stamp: vi.fn(), mark: vi.fn(), clear: vi.fn(),
    setActiveLetter: vi.fn(), onKeyDown: vi.fn(),
    ...over,
  };
}
const bar = (letters: Letters, over: Partial<MergeMode> = {}, onMerge = () => {}) => (
  <MergeBar merge={mode(letters, over)} merging={false} onMerge={onMerge} />
);
const preview = (n: number) => ({ letters: { A: { property_ids: [42, 43], rulings_taken_back: n } } });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map([[42, {
    property_id: 42, stage_id: 1, stage_label: 'Prověřit', stage_color: 'copper',
    stage_code: '1', stage_position: 1, is_terminal: false,
  }]]));
  vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[43, [7]]]));
  vi.mocked(queries.fetchIsDismissed).mockImplementation(async (id) => id === 43);
  vi.mocked(queries.fetchNoteCounts).mockResolvedValue(new Map([[42, 2]]));
  vi.mocked(api.getMergePreview).mockResolvedValue(preview(0));
  vi.mocked(api.listCollections).mockResolvedValue({ data: [MINE_7], total: 1 });
});

describe('<MergeBar>', () => {
  it('asks for a card while nothing is lettered, naming the letter a click writes', () => {
    renderWith(bar({}));
    expect(screen.getByText('Click a card to mark it A, or press a letter on it')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Merge' })).toBeDisabled();
  });

  it('lists every lettered property under its letter with the marks a merge will carry, read-only', async () => {
    renderWith(bar({ 43: 'A', 42: 'A' }));
    const list = screen.getByRole('list', { name: 'Vybráno ke sloučení' });
    const [a] = within(list).getAllByRole('listitem');
    expect(a).toHaveTextContent(/^A#42.*#43/);
    expect(await within(a).findByText('V pipeline: Prověřit')).toBeInTheDocument();
    expect(await within(a).findByText('Poznámky: 2')).toBeInTheDocument();
    expect(await within(a).findByText('V kolekci')).toBeInTheDocument();
    expect(await within(a).findByText('Skryto')).toBeInTheDocument();
    expect(within(list).queryByRole('button')).toBeNull();
  });

  /* The one joined bar (operator ruling 2026-10-27), hugging: it grows by a letter at a time. */
  it('offers a segment per letter in use plus the next free one, the active one lit', () => {
    const setActiveLetter = vi.fn();
    renderWith(bar({ 42: 'A', 43: 'A', 44: 'B' }, { setActiveLetter }));
    const group = screen.getByRole('group', { name: 'Písmeno, které zapíše kliknutí' });
    const letters = within(group).getAllByRole('button');
    expect(letters.map((c) => c.textContent)).toEqual(['A', 'B', 'C']);
    const track = group.firstElementChild as HTMLElement;
    expect(track).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
    for (const l of letters) {
      expect(l.parentElement).toBe(track);
      expect(l).not.toHaveClass('border');
    }
    expect(letters[0]).toHaveAttribute('aria-pressed', 'true');
    expect(letters[0]).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    expect(letters[1]).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(letters[2]);
    expect(setActiveLetter).toHaveBeenCalledWith('C');
  });

  it('holds the merge while a letter has one property, says so, and asks only about the rest', async () => {
    renderWith(bar({ 42: 'A', 43: 'A', 44: 'B' }));
    expect(screen.getByText('needs a second')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Merge A, B' })).toBeDisabled();
    await waitFor(() => expect(api.getMergePreview).toHaveBeenCalledWith({ 42: 'A', 43: 'A' }));
  });

  /* Past three letters the button counts the groups: listing every letter grew it
   * (and, beside the tabs, pushed Cancel off the screen). The statement names them. */
  it('counts the groups on the button past three letters, naming them in its tooltip', () => {
    renderWith(bar({ 1: 'A', 2: 'A', 3: 'B', 4: 'B', 5: 'C', 6: 'C', 7: 'D', 8: 'D' }));
    expect(screen.getByRole('button', { name: 'Merge 4 groups' })).toHaveAttribute('title', 'Merge A, B, C, D');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('says how many "Různé" rulings the statement would take back, once a letter holds two', async () => {
    vi.mocked(api.getMergePreview).mockResolvedValue(preview(3));
    renderWith(bar({ 42: 'A' }));
    expect(api.getMergePreview).not.toHaveBeenCalled();
    renderWith(bar({ 43: 'A', 42: 'A' }));
    expect(await screen.findByText('Vezme zpět 3 rozhodnutí „Různé“.')).toBeInTheDocument();
    expect(api.getMergePreview).toHaveBeenCalledWith({ 42: 'A', 43: 'A' });
  });

  it('says nothing of rulings when the merge takes none back', async () => {
    renderWith(bar({ 42: 'A', 43: 'A' }));
    await waitFor(() => expect(api.getMergePreview).toHaveBeenCalled());
    expect(screen.queryByText(/Vezme zpět/)).toBeNull();
  });

  it('holds the merge until the count is read, then sends the statement', async () => {
    let answer: (p: api.MergePreview) => void = () => {};
    vi.mocked(api.getMergePreview).mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const onMerge = vi.fn();
    renderWith(bar({ 42: 'A', 43: 'A' }, {}, onMerge));
    expect(
      await screen.findByText('Zjišťuji, kolik rozhodnutí „Různé“ sloučení vezme zpět…'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Merge A' })).toBeDisabled();
    await act(async () => answer(preview(1)));
    expect(await screen.findByText('Vezme zpět 1 rozhodnutí „Různé“.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Merge A' }));
    expect(onMerge).toHaveBeenCalledTimes(1);
  });

  it('a count it could not read is said, with a retry, never shown as none', async () => {
    vi.mocked(api.getMergePreview)
      .mockRejectedValueOnce(new Error('HTTP 500'))
      .mockResolvedValue(preview(2));
    renderWith(bar({ 42: 'A', 43: 'A' }));
    expect(
      await screen.findByText(/Kolik rozhodnutí „Různé“ sloučení vezme zpět, se nepodařilo zjistit./),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Merge A' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Zkusit znovu' }));
    expect(await screen.findByText('Vezme zpět 2 rozhodnutí „Různé“.')).toBeInTheDocument();
  });

  /* The way in is the cards header's "Sloučit…" (ruling P10); the bar is the mode. */
  it('leaves the mode from Cancel and draws no Merge mode button', () => {
    const exit = vi.fn();
    renderWith(bar({}, { exit }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(exit).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Merge mode' })).toBeNull();
  });

  it('draws nothing while the mode is off', () => {
    renderWith(bar({}, { active: false }));
    expect(screen.queryByRole('button')).toBeNull();
  });
});

describe('<MergeStatement>', () => {
  /* Sharing (migration 610): a property held only by a collection shared WITH me
   * is not saved — the mark agrees with the save menu, which lists mine only. */
  it('draws no collection mark for a property held only by a shared collection', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [MINE_7, { ...MINE_7, id: 9, name: 'Od Petra', owner_email: 'petr@example.cz' }],
      total: 2,
    });
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(
      new Map([[43, [9]], [44, [7, 9]]]),
    );
    renderWith(<MergeStatement groups={[['A', [43]], ['B', [44]]]} />);
    const [shared, both] = within(screen.getByRole('list', { name: 'Vybráno ke sloučení' }))
      .getAllByRole('listitem');
    expect(await within(both).findByText('V kolekci')).toBeInTheDocument();
    expect(within(shared).queryByText(/V kolekc/)).toBeNull();
  });

  it('turns a failed read into a retry instead of dropping the mark', async () => {
    vi.mocked(queries.fetchPipelineMembers).mockRejectedValueOnce(new Error('HTTP 500'));
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockRejectedValueOnce(new Error('HTTP 500'));
    renderWith(<MergeStatement groups={[['A', [43]]]} />);
    fireEvent.click(await screen.findByRole('button', { name: /Kolekce se nepodařilo načíst/ }));
    expect(await screen.findByText('V kolekci')).toBeInTheDocument();
    vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map([[43, {
      property_id: 43, stage_id: 1, stage_label: 'Prověřit', stage_color: 'copper',
      stage_code: '1', stage_position: 1, is_terminal: false,
    }]]));
    fireEvent.click(screen.getByRole('button', { name: /Pipeline se nepodařilo načíst/ }));
    expect(await screen.findByText('V pipeline: Prověřit')).toBeInTheDocument();
  });
});
