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

  it('draws nothing where the ad agrees, has no verdict, is not compared, or the property has no building', async () => {
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
  it('names the building and jumps to its card', async () => {
    fetchPropertyBuilding.mockResolvedValue({ building });
    show(<RegisterBadge propertyId={7} />);
    const badge = await screen.findByRole('link', { name: /RÚIAN/ });
    expect(badge).toHaveAttribute('href', '#budova');
    expect(badge).toHaveAttribute('title', 'Propojeno s registrem RÚIAN: stavební objekt 22948236');
  });

  it('is absent for a property with no register building, and asks nothing without a property', async () => {
    fetchPropertyBuilding.mockResolvedValue({ building: null });
    const { container } = show(<><RegisterBadge propertyId={7} /><RegisterBadge propertyId={null} /></>);
    await vi.waitFor(() => expect(fetchPropertyBuilding).toHaveBeenCalledTimes(1));
    expect(container).toBeEmptyDOMElement();
  });
});
