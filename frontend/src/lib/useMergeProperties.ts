/* Browse's merge of a statement of letters (MS15): one click, then the one
 * receipt (`pushMergeReceipt`: "Otevřít #S" for one letter, a line per letter
 * for several), the read-your-writes refresh (the merge txn patched browse_list
 * and moved the curation onto each survivor) and `onMerged`. It declares no
 * onError, so a refusal, rule 15's Czech sentence included, is the app
 * MutationCache's one toast (lib/mutationCache). Before the click,
 * `useMergePreview` reads how many "Různé" rulings each letter would take back
 * (MS12). The Rulings page merges a pair as a one-letter statement. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
  getMergePreview,
  lettersParam,
  mergeProperties,
  type Letters,
  type MergeLettersResult,
  type MergePreview,
} from '@/lib/api';
import { mergedAdvertsKeys, pushMergeReceipt, refreshAfterSplit } from '@/lib/mergedAdverts';
import { ROUTES } from '@/lib/routes';

export function useMergeProperties(onMerged: (res: MergeLettersResult) => void) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: (letters: Letters) => mergeProperties(letters),
    onSuccess: (res) => {
      pushMergeReceipt(res, (id) => navigate(ROUTES.property.build({ propertyId: id })));
      refreshAfterSplit(qc);
      onMerged(res);
    },
  });
}

/* The letters the server would merge: those held by two or more properties. */
function mergeable(letters: Letters): Letters {
  const held = new Map<string, number>();
  for (const letter of Object.values(letters)) held.set(letter, (held.get(letter) ?? 0) + 1);
  return Object.fromEntries(
    Object.entries(letters).filter(([, letter]) => (held.get(letter) ?? 0) >= 2),
  ) as Letters;
}

/* MS12's counts before the click, read once a letter holds two properties. */
export function useMergePreview(letters: Letters) {
  const statement = lettersParam(mergeable(letters));
  return useQuery<MergePreview, Error>({
    queryKey: mergedAdvertsKeys.mergePreview(statement),
    queryFn: () => getMergePreview(mergeable(letters)),
    enabled: statement !== '',
    staleTime: 30_000,
  });
}
