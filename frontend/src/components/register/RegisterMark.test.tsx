import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { ReactElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RegisterBadge, RegisterMark } from './RegisterMark';

const fetchPropertyBuilding = vi.fn();
vi.mock('@/lib/api', () => ({ fetchPropertyBuilding: (id: number) => fetchPropertyBuilding(id) }));

const building = {
  code: 22948236, floors: 6, lift: { code: 2, label: 'Bez výtahu', unknown: false },
  construction: { code: 4, label: 'Stěnové panely', unknown: false },
  shown: { lift: { differs: false, ad: false }, floors: { differs: true, ad: { floor: 6, total_floors: 5 } },
    construction: null },
  ads: {
    31: { lift: { differs: true, ad: true }, floors: { differs: false, ad: { floor: 2, total_floors: 5 } },
      construction: null },
  },
};
const show = (ui: ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

beforeEach(() => fetchPropertyBuilding.mockReset());

describe('<RegisterMark>', () => {
  it('marks the fact the API says differs, with the register value as its name', async () => {
    fetchPropertyBuilding.mockResolvedValue({ building });
    show(<RegisterMark propertyId={7} adId={31} fact="lift" />);
    expect(await screen.findByRole('img')).toHaveAccessibleName('Liší se od RÚIAN. Výtah: Bez výtahu');
  });

  it('marks the property by what its page shows, not by one of its ads', async () => {
    fetchPropertyBuilding.mockResolvedValue({ building });
    show(<><RegisterMark propertyId={7} fact="floors" /><RegisterMark propertyId={7} fact="lift" /></>);
    expect(await screen.findByRole('img')).toHaveAccessibleName('Liší se od RÚIAN. Podlaží včetně podzemních: 6');
    expect(screen.getAllByRole('img')).toHaveLength(1);
  });

  it('draws nothing for a property with no register building', async () => {
    fetchPropertyBuilding.mockResolvedValue({ building: null });
    const { container } = show(<RegisterMark propertyId={7} adId={31} fact="lift" />);
    await vi.waitFor(() => expect(fetchPropertyBuilding).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('draws nothing where the ad agrees, has no verdict or is not compared', async () => {
    fetchPropertyBuilding.mockResolvedValue({ building });
    const { container } = show(
      <>
        <RegisterMark propertyId={7} adId={31} fact="floors" />
        <RegisterMark propertyId={7} adId={31} fact="construction" />
        <RegisterMark propertyId={7} adId={99} fact="lift" />
        <RegisterBadge propertyId={7} />
      </>,
    );
    await screen.findByRole('link');
    expect(container.querySelector('[role="img"]')).toBeNull();
  });

  it('reads the register once for the whole page', async () => {
    fetchPropertyBuilding.mockResolvedValue({ building });
    show(
      <>
        <RegisterBadge propertyId={7} />
        <RegisterMark propertyId={7} adId={31} fact="lift" />
        <RegisterMark propertyId={7} adId={31} fact="floors" />
      </>,
    );
    await screen.findByRole('img');
    expect(fetchPropertyBuilding).toHaveBeenCalledTimes(1);
  });
});

describe('<RegisterBadge>', () => {
  it('names the building and opens it in the register', async () => {
    fetchPropertyBuilding.mockResolvedValue({ building });
    show(<RegisterBadge propertyId={7} />);
    const badge = await screen.findByRole('link', { name: /RÚIAN/ });
    expect(badge).toHaveAttribute('href', 'https://vdp.cuzk.gov.cz/vdp/ruian/stavebniobjekty/22948236');
    expect(badge).toHaveAttribute('title', 'Propojeno s registrem RÚIAN: stavební objekt 22948236');
  });

  it('is absent for a property with no register building, and asks nothing without a property', async () => {
    fetchPropertyBuilding.mockResolvedValue({ building: null });
    const { container } = show(<><RegisterBadge propertyId={7} /><RegisterBadge propertyId={null} /></>);
    await vi.waitFor(() => expect(fetchPropertyBuilding).toHaveBeenCalledTimes(1));
    expect(container).toBeEmptyDOMElement();
  });
});
