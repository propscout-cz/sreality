import { useQuery } from '@tanstack/react-query';
import { fetchPropertyBuilding } from '@/lib/api';

/* The property's register building, read once per page: the Budova card, the header badge and every mark share
 * it. A failed read is no building: the page stands without it. */
export function useRegisterBuilding(propertyId: number | null | undefined) {
  const { data } = useQuery({
    queryKey: ['property-building', propertyId],
    queryFn: () => fetchPropertyBuilding(propertyId as number),
    enabled: propertyId != null,
    staleTime: 60 * 60_000,
  });
  return data?.building ?? null;
}
