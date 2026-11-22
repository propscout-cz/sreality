/* What you pick is what you filter, on own place search (#2453).
 *
 * Every row the route answers is already a chip: a pick adds it at once, with no
 * second call. A městská část adds its quarters and says so; an address adds its
 * street; an address with no street adds nothing and says why. The map flies for
 * every pick that has a point. When the route is unavailable the box says so, the
 * chips keep filtering and nothing else is asked. */
import { useState } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LocationTypeahead } from './LocationTypeahead';
import type { DistrictChip } from '@/lib/filters';
import { PLACES_UNAVAILABLE, type PlaceRow } from '@/lib/places';

const fetchPlaces = vi.hoisted(() => vi.fn());
vi.mock('@/lib/places', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/places')>()),
  fetchPlaces,
}));

const BRNO = 582786;
const BRNO_ROW: PlaceRow = { level: 'obec', id: BRNO, name: 'Brno', context: 'Brno-město', point: { lat: 49.19, lng: 16.61 } };
const BRNO_OKRES_ROW: PlaceRow = {
  level: 'okres', id: 3702, name: 'Brno-město', context: 'Jihomoravský kraj', point: { lat: 49.2, lng: 16.6 },
};
const BRNO_CHIP: DistrictChip = { name: 'Brno', context: 'Brno-město', level: 'obec', id: BRNO };

const VOKOVICE: DistrictChip = { name: 'Vokovice', context: 'Praha', level: 'cast_obce', id: 490067 };
const DEJVICE: DistrictChip = { name: 'Dejvice', context: 'Praha', level: 'cast_obce', id: 400301 };
const PRAHA_6_ROW: PlaceRow = {
  level: 'momc', id: 500178, name: 'Praha 6', context: 'Praha', point: { lat: 50.1, lng: 14.38 },
  places: [
    { level: 'cast_obce', id: 400301, name: 'Dejvice', context: 'Praha' },
    { level: 'cast_obce', id: 490067, name: 'Vokovice', context: 'Praha' },
  ],
};
const KOUNICOVA: DistrictChip = { name: 'Kounicova', context: 'Brno', level: 'ulice', id: 391891 };
const KOUNICOVA_10_ROW: PlaceRow = {
  level: 'adresni_misto', id: 19173318, name: 'Kounicova 10, Brno', context: 'Brno-město',
  point: { lat: 49.205, lng: 16.6 }, street: { level: 'ulice', id: 391891, name: 'Kounicova', context: 'Brno' },
};
const KRHANICE_25_ROW: PlaceRow = {
  level: 'adresni_misto', id: 3012841, name: 'Krhanice č.p. 25', context: 'Benešov',
  point: { lat: 49.85, lng: 14.55 }, street: null,
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
const lastValue = (): DistrictChip[] | null => onChange.mock.calls[onChange.mock.calls.length - 1]?.[0] ?? null;
const notice = () => screen.getByRole('status');

/* Type a query, wait for the list, click the row named `name`. */
const pick = async (user: ReturnType<typeof userEvent.setup>, query: string, rows: PlaceRow[], name: string) => {
  fetchPlaces.mockResolvedValue(rows);
  await user.type(input(), query);
  const listbox = await screen.findByRole('listbox');
  const row = within(listbox).getAllByRole('button').find((b) => b.firstElementChild?.firstElementChild?.textContent === name);
  await user.click(row!);
};

beforeEach(() => {
  fetchPlaces.mockReset();
  onChange = vi.fn();
  onPick = vi.fn();
});

describe('<LocationTypeahead> on own place search', () => {
  it('lists the route\'s "Brno" town row first, and picking it adds {obec, Brno} at once', async () => {
    const user = renderHost();
    fetchPlaces.mockResolvedValue([BRNO_ROW, BRNO_OKRES_ROW]);
    await user.type(input(), 'Brno');
    const listbox = await screen.findByRole('listbox');
    const [first] = within(listbox).getAllByRole('button');
    expect(first).toHaveTextContent('Brno');

    await user.click(first);
    expect(lastValue()).toEqual([BRNO_CHIP]);
    expect(fetchPlaces).toHaveBeenLastCalledWith('Brno', expect.anything());
    expect(onPick).toHaveBeenCalledWith(BRNO_ROW);
  });
});

describe('<LocationTypeahead> rows read as places', () => {
  it('shows each row\'s name, context and level label, and no country badge', async () => {
    const user = renderHost();
    fetchPlaces.mockResolvedValue([BRNO_ROW, PRAHA_6_ROW, KOUNICOVA_10_ROW]);
    await user.type(input(), 'Br');
    const rows = within(await screen.findByRole('listbox')).getAllByRole('button');
    expect(rows.map((r) => r.textContent)).toEqual([
      'BrnoBrno-městoObec', 'Praha 6PrahaMěstská část', 'Kounicova 10, BrnoBrno-městoAdresa']);
    expect(screen.queryByText('Stát')).not.toBeInTheDocument();
  });
});

describe('<LocationTypeahead> a městská část adds its quarters (ruling 2026-11-02, #2380)', () => {
  it('adds one ordinary část chip per quarter and says what it did', async () => {
    const user = renderHost();
    await pick(user, 'Praha 6', [PRAHA_6_ROW], 'Praha 6');
    expect(lastValue()).toEqual([DEJVICE, VOKOVICE]);
    expect(notice()).toHaveTextContent('Praha 6 added as its 2 parts');
  });

  it('keeps a quarter already chosen as it is: an excluded one stays excluded', async () => {
    const user = renderHost([{ ...VOKOVICE, excluded: true }]);
    await pick(user, 'Praha 6', [PRAHA_6_ROW], 'Praha 6');
    expect(lastValue()).toEqual([{ ...VOKOVICE, excluded: true }, DEJVICE]);
  });

  it('a second pick of the same městská část changes nothing', async () => {
    const user = renderHost([DEJVICE, VOKOVICE]);
    await pick(user, 'Praha 6', [PRAHA_6_ROW], 'Praha 6');
    expect(onChange).not.toHaveBeenCalled();
    expect(notice()).toHaveTextContent('Already selected');
  });
});

describe('<LocationTypeahead> an address filters its street', () => {
  it('adds the street\'s chip and hands the door to the map', async () => {
    const user = renderHost();
    await pick(user, 'Kounicova 10', [KOUNICOVA_10_ROW], 'Kounicova 10, Brno');
    expect(lastValue()).toEqual([KOUNICOVA]);
    expect(onPick).toHaveBeenCalledWith(KOUNICOVA_10_ROW);
  });

  it('adds nothing for an address with no street, says why, and the map still flies (ruling 2026-11-22)', async () => {
    const user = renderHost([BRNO_CHIP]);
    await pick(user, 'Krhanice 25', [KRHANICE_25_ROW], 'Krhanice č.p. 25');
    expect(onChange).not.toHaveBeenCalled();
    expect(notice()).toHaveTextContent("Krhanice č.p. 25 can't be used as a filter — nothing was added");
    expect(onPick).toHaveBeenCalledWith(KRHANICE_25_ROW);
  });
});

describe('<LocationTypeahead> chips', () => {
  it('says "Already selected" on a re-pick, and leaves an excluded chip excluded', async () => {
    const user = renderHost([{ ...BRNO_CHIP, excluded: true }]);
    await pick(user, 'Brno', [BRNO_ROW], 'Brno');
    expect(onChange).not.toHaveBeenCalled();
    expect(notice()).toHaveTextContent('Already selected');
  });

  it('clears the notice on the next keystroke', async () => {
    const user = renderHost([BRNO_CHIP]);
    await pick(user, 'Brno', [BRNO_ROW], 'Brno');
    expect(notice()).toHaveTextContent('Already selected');
    await user.type(input(), 'x');
    expect(notice()).toHaveTextContent('');
  });

  it('tells an okres chip from the obec chip of the same name, and × removes only one', async () => {
    const user = renderHost();
    await pick(user, 'Brno', [BRNO_ROW, BRNO_OKRES_ROW], 'Brno-město');
    await pick(user, 'Brno', [BRNO_ROW, BRNO_OKRES_ROW], 'Brno');
    expect(lastValue()).toEqual([{ name: 'Brno-město', context: 'Jihomoravský kraj', level: 'okres', id: 3702 }, BRNO_CHIP]);
    await user.click(screen.getByRole('button', { name: 'Remove Brno-město · Jihomoravský kraj (Okres)' }));
    expect(lastValue()).toEqual([BRNO_CHIP]);
  });
});

describe('<LocationTypeahead> when own search is unavailable', () => {
  it('says so, keeps the chips and the box, and asks nothing else', async () => {
    const user = renderHost([BRNO_CHIP]);
    fetchPlaces.mockRejectedValue(new Error(PLACES_UNAVAILABLE));
    await user.type(input(), 'Br');
    expect(await screen.findByText(/Place search is unavailable right now/)).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(input()).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Remove Brno · Brno-město (Obec)' })).toBeInTheDocument();
    expect(fetchPlaces).toHaveBeenCalledWith('Br', expect.anything());
  });

  it('answers again on the next keystroke once the route is back', async () => {
    const user = renderHost();
    fetchPlaces.mockRejectedValueOnce(new Error(PLACES_UNAVAILABLE));
    await user.type(input(), 'Br');
    await screen.findByText(/Place search is unavailable right now/);
    fetchPlaces.mockResolvedValue([BRNO_ROW]);
    await user.type(input(), 'no');
    await waitFor(() => expect(screen.getByRole('listbox')).toBeInTheDocument());
    expect(screen.queryByText(/Place search is unavailable/)).not.toBeInTheDocument();
  });
});
