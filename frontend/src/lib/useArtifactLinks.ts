/* The account's artifact links (migration 599) as every surface reads them: ONE
 * whole-set read under one key, so the property page's row, the board card's
 * button and the phone card's button cannot drift on whether a property has a
 * link. The row owns the writes and patches this same entry. */

import { useQuery } from '@tanstack/react-query';

import { fetchArtifactLinks } from '@/lib/api';
import { curationKeys } from '@/lib/queries';

export const useArtifactLinks = () =>
  useQuery({
    queryKey: curationKeys.artifactLinks,
    queryFn: fetchArtifactLinks,
    staleTime: 30_000,
  });
