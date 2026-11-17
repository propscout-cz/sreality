/* The roll's order, one menu on every surface: the Filtry sheet's Řazení row and
 * the feed's Řazení chip both draw `SortChips` over CARD_SORTS. Seventeen orders:
 * a chip row, not the joined bar (system.md § Option groups). */

import { PickButton } from '@/components/controls';
import { CARD_SORTS, sortToParam, type SortSpec } from '@/lib/browseSort';

import BottomSheet from './BottomSheet';

export function SortChips({ sort, onPick }: { sort: SortSpec; onPick: (next: SortSpec) => void }) {
  const chosen = sortToParam(sort);
  return (
    <div role="group" aria-label="Řazení" className="flex flex-wrap gap-1">
      {CARD_SORTS.map((o) => (
        <PickButton key={sortToParam(o.spec)} variant="solid" on={sortToParam(o.spec) === chosen} onClick={() => onPick(o.spec)}>
          {o.label}
        </PickButton>
      ))}
    </div>
  );
}

export default function SortSheet({
  sort,
  onPick,
  onClose,
}: {
  sort: SortSpec;
  onPick: (next: SortSpec) => void;
  onClose: () => void;
}) {
  return (
    <BottomSheet label="Řazení" onClose={onClose}>
      <div className="px-4 pb-4 pt-1">
        <SortChips
          sort={sort}
          onPick={(next) => {
            onPick(next);
            onClose();
          }}
        />
      </div>
    </BottomSheet>
  );
}
