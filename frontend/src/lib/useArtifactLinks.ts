/* The account's artifact links (migration 599) as every surface reads them: ONE
 * whole-set read under one key, so the property page's row, the board card's
 * button and the phone card's button cannot drift on whether a property has a
 * link. The row owns the writes and patches this same entry. */

import { useQuery } from '@tanstack/react-query';

import { actingKey, actOpts, useActAs } from '@/lib/acting';
import { fetchArtifactLinks } from '@/lib/api';
import { curationKeys } from '@/lib/queries';

export const useArtifactLinks = () => {
  const actAs = useActAs();
  return useQuery({
    queryKey: actingKey(actAs, curationKeys.artifactLinks),
    queryFn: () => fetchArtifactLinks(...actOpts(actAs)),
    staleTime: 30_000,
  });
};
