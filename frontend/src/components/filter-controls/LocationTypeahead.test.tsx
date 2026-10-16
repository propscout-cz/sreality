/* What you pick is what you filter.
 *
 * A pick becomes ONE RÚIAN unit at the level it was picked at: a street chip is
 * its street (badged ULICE, named by the register), never its town. Identity is
 * the resolved unit, so two streets of one town are two chips and the same
 * street picked twice is one. A pick the register cannot bind, a country, or a
 * resolve that fails adds NO chip and says why under the input — the map still
 * flies. Picks resolve asynchronously, so every edit lands on the latest chips.
 */
import { useState } from 'react';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LocationTypeahead } from './LocationTypeahead';
import type { DistrictChip } from '@/lib/filters';
import { SUGGEST_NOT_CONFIGURED, type LocationResolution, type MapySuggestion } from '@/lib/maps';

const fetchSuggest = vi.hoisted(() => vi.fn());
const resolveSuggestion = vi.hoisted(() => vi.fn());
vi.mock('@/lib/maps', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/maps')>()),
  fetchSuggest,
  resolveSuggestion,
}));

const PARDUBICE = 'Pardubice';

const street = (name: string): MapySuggestion => ({
  name,
  label: 'Ulice',
  type: 'regional.street',
  location: 'Pardubice, Pardubický kraj',
  position: { lon: 15.77, lat: 50.03 },
  regionalStructure: [
    { name, type: 'regional.street' },
    { name: PARDUBICE, type: 'regional.municipality' },
  ],
});

/* The server's answers, in its one shape (api/maps.resolve). */
const ulice = (name: string, id: number): LocationResolution => ({
  level: 'ulice', id, name, context: PARDUBICE,
});
/* A street the register cannot bind inside the town it lies in: the level it
 * was picked at and the town, no code and no unit name. */
const UNBOUND: LocationResolution = { level: 'ulice', id: null, name: null, context: PARDUBICE };

const RYBNICKY: DistrictChip = {
  name: 'Na Rybníčkách', context: PARDUBICE, level: 'ulice', id: 666742,
};
const NADRAZNI: DistrictChip = {
  name: 'Nádražní', context: PARDUBICE, level: 'ulice', id: 666001,
};

let onChange: ReturnType<typeof vi.fn>;
let onPick: ReturnType<typeof vi.fn>;

function Host({ initial }: { initial: DistrictChip[] | null }) {
  const [value, setValue] = useState<DistrictChip[] | null>(initial);
  return (
    <LocationTypeahead
      label="Lokalita"
      value={value}
      onChange={(next) => {
        onChange(next);
        setValue(next);
      }}
      onPick={onPick}
    />
  );
}

const renderHost = (initial: DistrictChip[] | null = null) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Host initial={initial} />
    </QueryClientProvider>,
  );
  return userEvent.setup();
};

const input = () => screen.getByRole('textbox', { name: 'Lokalita' });

/* Type a query, wait for the dropdown, click the one suggestion. */
const pick = async (
  user: ReturnType<typeof userEvent.setup>,
  query: string,
  suggestion: MapySuggestion,
) => {
  fetchSuggest.mockResolvedValue([suggestion]);
  await user.type(input(), query);
  const listbox = await screen.findByRole('listbox');
  await user.click(within(listbox).getByRole('button'));
};

const deferred = <T,>() => {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
};

const lastValue = (): DistrictChip[] | null =>
  onChange.mock.calls[onChange.mock.calls.length - 1]?.[0] ?? null;

const notice = () => screen.getByRole('status');
const busy = () => input().closest('[aria-busy]');

beforeEach(() => {
  fetchSuggest.mockReset();
  resolveSuggestion.mockReset();
  onChange = vi.fn();
  onPick = vi.fn();
});

describe('<LocationTypeahead> street picks', () => {
  it('badges the dropdown entry and the chip as a street, with the register name', async () => {
    const user = renderHost();
    fetchSuggest.mockResolvedValue([street('Na Rybnickach')]);
    await user.type(input(), 'Na Ryb');
    const listbox = await screen.findByRole('listbox');
    expect(within(listbox).getByText('Ulice')).toBeInTheDocument();

    /* Mapy spells it one way, the register another: the chip carries ONLY the
       register's name and its town as display-only context. */
    resolveSuggestion.mockResolvedValue(ulice('Na Rybníčkách', 666742));
    await user.click(within(listbox).getByRole('button'));

    await waitFor(() => expect(lastValue()).toEqual([RYBNICKY]));
    const chip = screen.getByRole('button', { name: 'Remove Na Rybníčkách · Pardubice (Ulice)' })
      .closest('li')!;
    expect(within(chip).getByText('Ulice')).toBeInTheDocument();
    expect(within(chip).queryByText('Obec')).not.toBeInTheDocument();
    expect(onPick).toHaveBeenCalledTimes(1);
  });

  it('gives two streets of one town two chips, and × removes only one', async () => {
    const user = renderHost();
    resolveSuggestion.mockResolvedValueOnce(ulice('Na Rybníčkách', 666742));
    await pick(user, 'Na Ryb', street('Na Rybníčkách'));
    await waitFor(() => expect(lastValue()).toHaveLength(1));

    resolveSuggestion.mockResolvedValueOnce(ulice('Nádražní', 666001));
    await pick(user, 'Nádra', street('Nádražní'));
    await waitFor(() => expect(lastValue()).toHaveLength(2));

    await user.click(screen.getByRole('button', { name: 'Remove Na Rybníčkách · Pardubice (Ulice)' }));
    expect(lastValue()).toEqual([NADRAZNI]);
  });
});

describe('<LocationTypeahead> a pick that makes no chip says why', () => {
  it('says "Already selected" on a re-pick, and leaves an excluded chip excluded', async () => {
    const user = renderHost([{ ...RYBNICKY, excluded: true }]);
    /* An address on the same street resolves to the same unit. */
    resolveSuggestion.mockResolvedValue(ulice('Na Rybníčkách', 666742));
    await pick(user, 'Na Rybníčkách 12', { ...street('Na Rybníčkách 12'), type: 'regional.address' });

    await waitFor(() => expect(notice()).toHaveTextContent('Already selected'));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getAllByRole('button', { name: /^Remove / })).toHaveLength(1);
    expect(
      screen.getByRole('button', { name: 'Include Na Rybníčkách · Pardubice (Ulice)' }),
    ).toBeInTheDocument();
  });

  it('adds nothing for a place the server cannot bind — and the map still flies', async () => {
    const user = renderHost();
    resolveSuggestion.mockResolvedValue(UNBOUND);
    await pick(user, 'Nádra', street('Nádražní'));

    await waitFor(() =>
      expect(notice()).toHaveTextContent("Nádražní can't be used as a filter — nothing was added"),
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /^Remove / })).not.toBeInTheDocument();
    expect(onPick).toHaveBeenCalledTimes(1);
  });

  it('adds nothing for a country', async () => {
    const user = renderHost();
    resolveSuggestion.mockResolvedValue({ level: null, id: null, name: null, context: null });
    await pick(user, 'Česko', {
      name: 'Česko', label: 'Stát', type: 'regional.country', position: { lon: 15.3, lat: 49.8 },
    });

    await waitFor(() =>
      expect(notice()).toHaveTextContent("A country can't be used as a filter"),
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(onPick).toHaveBeenCalledTimes(1);
  });

  it('adds nothing when the resolve call fails, and stores no Mapy string', async () => {
    const user = renderHost();
    resolveSuggestion.mockImplementation(() => Promise.reject(new Error('HTTP 422')));
    await pick(user, 'Na Ryb', street('Na Rybnickach'));

    await waitFor(() =>
      expect(notice()).toHaveTextContent("Couldn't verify this place — try again"),
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByText(/Na Rybnickach/)).not.toBeInTheDocument();
  });

  it('clears the notice on the next keystroke', async () => {
    const user = renderHost();
    resolveSuggestion.mockResolvedValue(UNBOUND);
    await pick(user, 'Nádra', street('Nádražní'));
    await waitFor(() => expect(notice()).toHaveTextContent(/nothing was added/));

    await user.type(input(), 'x');
    expect(notice()).toHaveTextContent('');
  });

  it('clears the notice on the next pick', async () => {
    const user = renderHost();
    const unbound = deferred<LocationResolution>();
    resolveSuggestion.mockReturnValueOnce(unbound.promise);
    await pick(user, 'Nádra', street('Nádražní'));

    /* The next suggestion list is open when the first pick's answer lands. */
    fetchSuggest.mockResolvedValue([street('Na Rybníčkách')]);
    await user.type(input(), 'Na Ryb');
    const listbox = await screen.findByRole('listbox');
    await act(async () => unbound.resolve(UNBOUND));
    expect(notice()).toHaveTextContent("Nádražní can't be used as a filter — nothing was added");

    resolveSuggestion.mockResolvedValueOnce(ulice('Na Rybníčkách', 666742));
    await user.click(within(listbox).getByRole('button'));
    expect(notice()).toHaveTextContent('');
    await waitFor(() => expect(lastValue()).toEqual([RYBNICKY]));
  });

  it('says place search is unavailable when the server has no geocoder', async () => {
    const user = renderHost([RYBNICKY]);
    fetchSuggest.mockImplementation(() => Promise.reject(new Error(SUGGEST_NOT_CONFIGURED)));
    await user.type(input(), 'Na');
    expect(await screen.findByText(/Place search is unavailable/)).toBeInTheDocument();
    expect(input()).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Remove Na Rybníčkách/ })).toBeInTheDocument();
  });
});

describe('<LocationTypeahead> picks resolve against the latest chips', () => {
  it('keeps BOTH of two fast picks, whichever resolves first, and is busy meanwhile', async () => {
    const user = renderHost();
    const first = deferred<LocationResolution>();
    const second = deferred<LocationResolution>();
    resolveSuggestion.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    await pick(user, 'Na Ryb', street('Na Rybníčkách'));
    await pick(user, 'Nádra', street('Nádražní'));
    expect(busy()).toHaveAttribute('aria-busy', 'true');

    await act(async () => second.resolve(ulice('Nádražní', 666001)));
    expect(busy()).toHaveAttribute('aria-busy', 'true');
    await act(async () => first.resolve(ulice('Na Rybníčkách', 666742)));

    expect(lastValue()).toEqual([NADRAZNI, RYBNICKY]);
    expect(busy()).toHaveAttribute('aria-busy', 'false');
  });

  it('does not undo a remove made while a pick is resolving', async () => {
    const user = renderHost([NADRAZNI]);
    const pending = deferred<LocationResolution>();
    resolveSuggestion.mockReturnValueOnce(pending.promise);
    await pick(user, 'Na Ryb', street('Na Rybníčkách'));

    await user.click(screen.getByRole('button', { name: 'Remove Nádražní · Pardubice (Ulice)' }));
    expect(lastValue()).toBeNull();

    await act(async () => pending.resolve(ulice('Na Rybníčkách', 666742)));
    expect(lastValue()).toEqual([RYBNICKY]);
  });

  it('does not undo an exclude toggled while a pick is resolving', async () => {
    const user = renderHost([NADRAZNI]);
    const pending = deferred<LocationResolution>();
    resolveSuggestion.mockReturnValueOnce(pending.promise);
    await pick(user, 'Na Ryb', street('Na Rybníčkách'));

    await user.click(screen.getByRole('button', { name: 'Exclude Nádražní · Pardubice (Ulice)' }));
    await act(async () => pending.resolve(ulice('Na Rybníčkách', 666742)));
    expect(lastValue()).toEqual([{ ...NADRAZNI, excluded: true }, RYBNICKY]);
  });
});

describe('<LocationTypeahead> unmounted while a pick resolves', () => {
  it('emits nothing and throws nothing when the answer lands', async () => {
    const pending = deferred<LocationResolution>();
    resolveSuggestion.mockReturnValueOnce(pending.promise);
    fetchSuggest.mockResolvedValue([street('Na Rybníčkách')]);
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <LocationTypeahead label="Lokalita" value={null} onChange={onChange} />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.type(input(), 'Na Ryb');
    await user.click(within(await screen.findByRole('listbox')).getByRole('button'));

    unmount();
    await act(async () => pending.resolve(ulice('Na Rybníčkách', 666742)));
    expect(onChange).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });
});

describe('<LocationTypeahead> chip controls name their level', () => {
  it('tells an okres chip from the obec chip of the same name', () => {
    renderHost([
      { name: 'Jihlava', context: null, level: 'okres', id: 3707 },
      { name: 'Jihlava', context: null, level: 'obec', id: 586846 },
    ]);
    expect(screen.getByRole('button', { name: 'Remove Jihlava (Okres)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove Jihlava (Obec)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Exclude Jihlava (Okres)' })).toBeInTheDocument();
  });
});
