/* The one curated-set control (docs/design/curated-set-scope.md): unit chips
 * cycle off → in → not in, the dedicated "not in" chip closes the row and
 * disables the units, the pipeline alone offers "V pipeline", units group as
 * Mine / Shared with me / No longer available, and a failed read is a retry
 * (MS16), never "none yet". */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import * as api from '@/lib/api';
import * as queries from '@/lib/queries';
import type { SetScope } from '@/lib/setScope';

import { AccountCuratedSetFilter, CuratedSetFilter, type SetUnit } from './CuratedSetFilter';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listCollections: vi.fn(),
  listTags: vi.fn(),
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchPipelineStages: vi.fn(),
}));

const READ = { isLoading: false, isError: false, data: [], refetch: () => {} };
const TAGS: SetUnit[] = [
  { id: 5, label: 'k prohlídce' },
  { id: 6, label: 'odmítnuto' },
];
const IN = (ids: number[]): SetScope => ({ set: null, in: ids, out: [] });

const withQueries = (ui: React.ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

describe('<CuratedSetFilter> unit chips', () => {
  it('cycles a chip off → in → not → off and says which it is', () => {
    const onChange = vi.fn();
    const props = { set: 'tags' as const, units: TAGS, onChange, query: READ };
    const { rerender } = render(<CuratedSetFilter {...props} value={null} />);
    fireEvent.click(screen.getByRole('button', { name: 'k prohlídce' }));
    expect(onChange).toHaveBeenLastCalledWith(IN([5]));

    rerender(<CuratedSetFilter {...props} value={IN([5])} />);
    const on = screen.getByRole('button', { name: 'k prohlídce' });
    expect(on).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(on);
    expect(onChange).toHaveBeenLastCalledWith({ set: null, in: [], out: [5] });

    rerender(<CuratedSetFilter {...props} value={{ set: null, in: [], out: [5] }} />);
    const not = screen.getByRole('button', { name: 'k prohlídce (excluded)' });
    expect(not).toHaveTextContent('− k prohlídce');
    fireEvent.click(not);
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('explains AND only for tags, and only once two are required', () => {
    const { rerender } = render(
      <CuratedSetFilter set="tags" units={TAGS} value={IN([5])} onChange={() => {}} query={READ} />,
    );
    expect(screen.queryByText(/všechny vybrané/)).toBeNull();
    rerender(<CuratedSetFilter set="tags" units={TAGS} value={IN([5, 6])} onChange={() => {}} query={READ} />);
    expect(screen.getByText(/všechny vybrané/)).toBeInTheDocument();
    /* Collections are ANY: no AND hint. */
    rerender(
      <CuratedSetFilter set="collections" units={TAGS} value={IN([5, 6])} onChange={() => {}} query={READ} />,
    );
    expect(screen.queryByText(/všechny vybrané/)).toBeNull();
  });
});

describe('<CuratedSetFilter> the whole-set chips', () => {
  it('ends the row with the dedicated "not in" chip, which cycles off ↔ out', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <CuratedSetFilter set="collections" units={TAGS} value={IN([5])} onChange={onChange} query={READ} />,
    );
    const chips = screen.getAllByRole('button');
    expect(chips.at(-1)).toHaveTextContent('V žádné kolekci');
    fireEvent.click(screen.getByRole('button', { name: 'V žádné kolekci' }));
    expect(onChange).toHaveBeenLastCalledWith({ set: 'out', in: [], out: [] });

    rerender(
      <CuratedSetFilter
        set="collections"
        units={TAGS}
        value={{ set: 'out', in: [], out: [] }}
        onChange={onChange}
        query={READ}
      />,
    );
    const notIn = screen.getByRole('button', { name: 'V žádné kolekci' });
    expect(notIn).toHaveAttribute('aria-pressed', 'true');
    /* The units cannot be picked while the whole set is excluded. */
    for (const u of TAGS) expect(screen.getByRole('button', { name: u.label })).toBeDisabled();
    fireEvent.click(notIn);
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('names the "not in" chip per set: Bez štítků, Mimo pipeline', () => {
    const { rerender } = render(
      <CuratedSetFilter set="tags" units={TAGS} value={null} onChange={() => {}} query={READ} />,
    );
    expect(screen.getByRole('button', { name: 'Bez štítků' })).toBeInTheDocument();
    rerender(<CuratedSetFilter set="pipeline" units={TAGS} value={null} onChange={() => {}} query={READ} />);
    expect(screen.getByRole('button', { name: 'Mimo pipeline' })).toBeInTheDocument();
  });

  it('offers the whole-set include chip for the pipeline only, leading the row', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <CuratedSetFilter set="pipeline" units={TAGS} value={null} onChange={onChange} query={READ} />,
    );
    expect(screen.getAllByRole('button')[0]).toHaveTextContent('V pipeline');
    fireEvent.click(screen.getByRole('button', { name: 'V pipeline' }));
    expect(onChange).toHaveBeenLastCalledWith({ set: 'in', in: [], out: [] });

    /* A stage picked while "V pipeline" is on is the more specific choice. */
    rerender(
      <CuratedSetFilter
        set="pipeline"
        units={TAGS}
        value={{ set: 'in', in: [], out: [] }}
        onChange={onChange}
        query={READ}
      />,
    );
    expect(screen.getByRole('button', { name: 'V pipeline' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'k prohlídce' }));
    expect(onChange).toHaveBeenLastCalledWith(IN([5]));

    for (const set of ['tags', 'collections'] as const) {
      rerender(<CuratedSetFilter set={set} units={TAGS} value={null} onChange={onChange} query={READ} />);
      expect(screen.queryByRole('button', { name: 'V pipeline' })).toBeNull();
      expect(screen.getAllByRole('button')).toHaveLength(TAGS.length + 1);
    }
  });
});

describe('<CuratedSetFilter> groups and vanished units', () => {
  it('groups mine and shared-with-me over one value, and keeps a vanished pick clearable', () => {
    const onChange = vi.fn();
    const units: SetUnit[] = [
      { id: 7, label: 'Šortlist', group: 'mine' },
      { id: 9, label: 'monitoring · petr@example.cz', group: 'shared' },
    ];
    render(
      <CuratedSetFilter
        set="collections"
        units={units}
        value={{ set: null, in: [9, 404], out: [] }}
        onChange={onChange}
        query={READ}
      />,
    );
    const mine = screen.getByRole('group', { name: 'Mine' });
    const shared = screen.getByRole('group', { name: 'Shared with me' });
    expect(within(mine).getByRole('button', { name: 'Šortlist' })).toBeInTheDocument();
    expect(within(shared).getByRole('button', { name: 'monitoring · petr@example.cz' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    /* A pick the list no longer holds (revoked, deleted) stays visible and clears. */
    const gone = within(screen.getByRole('group', { name: 'No longer available' })).getByRole('button', {
      name: '#404',
    });
    fireEvent.click(gone);
    expect(onChange).toHaveBeenLastCalledWith(IN([9]));

    /* Both groups write the ONE scope. */
    fireEvent.click(within(mine).getByRole('button', { name: 'Šortlist' }));
    expect(onChange).toHaveBeenLastCalledWith(IN([9, 404, 7]));
    /* The "not in" chip still closes the control. */
    expect(screen.getAllByRole('button').at(-1)).toHaveTextContent('V žádné kolekci');
  });

  it('draws no group headings while nothing is shared and nothing has vanished', () => {
    render(
      <CuratedSetFilter
        set="collections"
        units={[{ id: 7, label: 'Šortlist', group: 'mine' }]}
        value={null}
        onChange={() => {}}
        query={READ}
      />,
    );
    expect(screen.getByRole('button', { name: 'Šortlist' })).toBeInTheDocument();
    expect(screen.queryByRole('group')).toBeNull();
  });

  it('names an excluded vanished tag by its id, and the click clears it', () => {
    const onChange = vi.fn();
    render(
      <CuratedSetFilter
        set="tags"
        units={TAGS}
        value={{ set: null, in: [5], out: [99] }}
        onChange={onChange}
        query={READ}
      />,
    );
    const gone = screen.getByRole('button', { name: '#99 (excluded)' });
    expect(gone).toHaveTextContent('− #99');
    fireEvent.click(gone);
    expect(onChange).toHaveBeenLastCalledWith(IN([5]));
  });
});

describe('<CuratedSetFilter> the frame', () => {
  it('says the units are loading, or that there are none yet', () => {
    const { rerender } = render(
      <CuratedSetFilter
        set="tags"
        units={[]}
        value={null}
        onChange={() => {}}
        query={{ ...READ, isLoading: true }}
      />,
    );
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    rerender(<CuratedSetFilter set="tags" units={[]} value={null} onChange={() => {}} query={READ} />);
    expect(screen.getByText(/No tags yet/)).toBeInTheDocument();
    /* An active scope with no units left stays deselectable. */
    rerender(
      <CuratedSetFilter
        set="tags"
        units={[]}
        value={{ set: 'out', in: [], out: [] }}
        onChange={() => {}}
        query={READ}
      />,
    );
    expect(screen.getByRole('button', { name: 'Bez štítků' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('says the collections failed instead of "none yet", and reads them again', async () => {
    vi.mocked(api.listCollections)
      .mockRejectedValueOnce(new Error('HTTP 500'))
      .mockResolvedValue({ data: [{ id: 7, name: 'Šortlist', owner_email: null, shared_with: [] }], total: 1 } as never);
    withQueries(<AccountCuratedSetFilter set="collections" value={null} onChange={() => {}} />);
    const retry = await screen.findByRole('button', { name: /Kolekce se nepodařilo načíst/ });
    expect(screen.queryByText(/No collections yet/)).toBeNull();
    fireEvent.click(retry);
    expect(await screen.findByRole('button', { name: 'Šortlist' })).toBeInTheDocument();
  });

  it('says the tags failed instead of "none yet", and reads them again', async () => {
    vi.mocked(api.listTags)
      .mockRejectedValueOnce(new Error('HTTP 500'))
      .mockResolvedValue({ data: [{ id: 3, name: 'k prohlídce', color: 'copper' }] } as never);
    withQueries(<AccountCuratedSetFilter set="tags" value={null} onChange={() => {}} />);
    const retry = await screen.findByRole('button', { name: /Štítky se nepodařilo načíst/ });
    expect(screen.queryByText(/No tags yet/)).toBeNull();
    fireEvent.click(retry);
    expect(await screen.findByRole('button', { name: 'k prohlídce' })).toBeInTheDocument();
  });

  it('gives the pipeline stages the failed-read state they never had', async () => {
    vi.mocked(queries.fetchPipelineStages)
      .mockRejectedValueOnce(new Error('HTTP 500'))
      .mockResolvedValue([
        { id: 1, key: 'new', label: 'Nové', position: 1, color: null, is_terminal: false, is_entry: true, code: '1' },
      ]);
    withQueries(<AccountCuratedSetFilter set="pipeline" value={null} onChange={() => {}} />);
    const retry = await screen.findByRole('button', { name: /Pipeline se nepodařilo načíst/ });
    fireEvent.click(retry);
    /* Stage chips are visible without ticking anything (ruling 7). */
    expect(await screen.findByRole('button', { name: 'Nové' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'V pipeline' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mimo pipeline' })).toBeInTheDocument();
  });
});
