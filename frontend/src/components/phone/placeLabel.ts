/* A place chip set in a few words, short enough for a chip, a rule or a row's
 * summary: "Celá ČR", "Praha", "Praha +2", "Celá ČR − Žižkov", "Celá ČR −2".
 * Every chip counts; an include leads, else the whole country minus the
 * excludes. The brokers' place chip and the Filtry sheet's District row. */

import type { DistrictChip } from '@/lib/filters';

/* `whole` names the whole country: Browse's English pills pass their own. */
export function placeLabelOf(chips: ReadonlyArray<DistrictChip>, whole = 'Celá ČR'): string {
  const lead = chips.find((c) => !c.excluded);
  if (lead) return chips.length === 1 ? lead.name : `${lead.name} +${chips.length - 1}`;
  if (chips.length === 0) return whole;
  return chips.length === 1 ? `${whole} − ${chips[0].name}` : `${whole} −${chips.length}`;
}
