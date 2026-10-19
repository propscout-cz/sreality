/* The one tag assign list: a row per tag that attaches or detaches, a typed
 * name that creates and attaches, and a failed read that retries instead of
 * reading as "no tags". Every write revalidates through lib/tagCache. */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, type InfiniteData } from '@tanstack/react-query';

import { browseKeys } from '@/lib/browseKeys';
import { DEFAULT_FILTERS } from '@/lib/filters';
import type { Tag } from '@/lib/types';
import { curationKeys, DEFAULT_SORT } from '@/lib/queries';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  listTags: vi.fn(),
  attachTag: vi.fn(),
  detachTag: vi.fn(),
  createTag: vi.fn(),
}));

import { attachTag, createTag, detachTag, listTags } from '@/lib/api';
import { TAG_SEARCH_LABEL, TagAssignList } from './TagAssignList';

const tag = (id: number, name: string): Tag => ({
  id, name, color: 'copper', created_at: '2026-05-01T00:00:00+00:00', listing_count: 1,
});
const TAGS = [tag(5, 'k prohlídce'), tag(6, 'odmítnuto')];

function renderList(memberIds: number[] = [], seed: (qc: QueryClient) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(qc);
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  render(
    <QueryClientProvider client={qc}>
      <TagAssignList property_id={42} memberIds={new Set(memberIds)} />
    </QueryClientProvider>,
  );
  return { invalidate, qc };
}

describe('<TagAssignList>', () => {
  beforeEach(() => {
    vi.mocked(listTags).mockResolvedValue({ data: TAGS });
    vi.mocked(attachTag).mockResolvedValue({ attached: true });
    vi.mocked(detachTag).mockResolvedValue({ detached: true });
  });

  it('lists the property\'s own tags first, ticked, and toggles membership per row', async () => {
    const { invalidate } = renderList([6]);
    const rows = await screen.findAllByRole('button', { pressed: true });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('odmítnuto');
    const list = screen.getByRole('list', { name: 'Štítky' });
    expect(list.textContent?.indexOf('odmítnuto')).toBeLessThan(list.textContent!.indexOf('k prohlídce'));

    fireEvent.click(screen.getByRole('button', { name: /k prohlídce/, pressed: false }));
    await waitFor(() => expect(attachTag).toHaveBeenCalledWith(42, 5));
    fireEvent.click(screen.getByRole('button', { name: /odmítnuto/, pressed: true }));
    await waitFor(() => expect(detachTag).toHaveBeenCalledWith(42, 6));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: curationKeys.propertyTagMembers });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: curationKeys.tags });
  });

  it('a tag put on a card listed under "Bez štítků" drops that card from the list (ruling 9)', async () => {
    const f = { ...DEFAULT_FILTERS, tags: { set: 'out' as const, in: [], out: [] } };
    const cards = browseKeys.cards(f, DEFAULT_SORT);
    type Page = { rows: { property_id: number }[] };
    const { qc } = renderList([], (c) => {
      c.setQueryData(curationKeys.propertyTagMembers, new Map<number, number[]>());
      c.setQueryData<InfiniteData<Page>>(cards, { pages: [{ rows: [{ property_id: 42 }, { property_id: 43 }] }], pageParams: [null] });
    });
    fireEvent.click(await screen.findByRole('button', { name: /k prohlídce/, pressed: false }));
    await waitFor(() =>
      expect(qc.getQueryData<InfiniteData<Page>>(cards)!.pages[0].rows).toEqual([{ property_id: 43 }]),
    );
  });

  it('creates a typed name that no tag carries, and puts it on the property', async () => {
    vi.mocked(createTag).mockResolvedValue(tag(9, 'nový'));
    renderList();
    await screen.findByRole('list', { name: 'Štítky' });
    const box = screen.getByRole('textbox', { name: TAG_SEARCH_LABEL });
    fireEvent.change(box, { target: { value: 'nový' } });
    expect(screen.getByText('Nic nenalezeno.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Vytvořit' }));
    await waitFor(() => expect(createTag).toHaveBeenCalledWith({ name: 'nový', color: 'copper' }));
    await waitFor(() => expect(attachTag).toHaveBeenCalledWith(42, 9));
    expect(box).toHaveValue('');
  });

  it('offers no create for a name a tag already carries, in any case', async () => {
    renderList();
    await screen.findByRole('list', { name: 'Štítky' });
    fireEvent.change(screen.getByRole('textbox', { name: TAG_SEARCH_LABEL }), {
      target: { value: 'K PROHLÍDCE' },
    });
    expect(screen.queryByRole('button', { name: 'Vytvořit' })).toBeNull();
    expect(screen.getByRole('button', { name: /k prohlídce/, pressed: false })).toBeInTheDocument();
  });

  it('says the index failed and reads it again, never "no tags yet"', async () => {
    vi.mocked(listTags).mockRejectedValueOnce(new Error('HTTP 500'));
    renderList();
    const retry = await screen.findByRole('button', { name: 'Zkusit znovu' });
    expect(screen.queryByText(/Zatím žádné štítky/)).toBeNull();
    fireEvent.click(retry);
    expect(await screen.findByRole('list', { name: 'Štítky' })).toBeInTheDocument();
  });
});
