/* The roll's order, one menu on every surface: the Filtry sheet's Řazení row and
 * the feed's Řazení chip both draw `SortChips` over CARD_SORTS. Seventeen orders:
 * a chip row, not the joined bar (system.md § Option groups). Two tones, as the
 * joined bar has: `desktop` is the copper PickButton the Filtry sheet hosts with
 * the rest of the desktop's filter body; `phone` is the feed's own chip, pressed =
 * ink on inset, never colour (./chips; operator ruling 2026-11-17). */

import { PickButton } from '@/components/controls';
import { CARD_SORTS, sortToParam, type SortSpec } from '@/lib/queries';

import BottomSheet from './BottomSheet';
import { CHIP, CHIP_MD, chipState } from './chips';

export function SortChips({
  sort,
  onPick,
  tone,
}: {
  sort: SortSpec;
  onPick: (next: SortSpec) => void;
  tone: 'desktop' | 'phone';
}) {
  const chosen = sortToParam(sort);
  return (
    <div role="group" aria-label="Řazení" className={`flex flex-wrap ${tone === 'phone' ? 'gap-2' : 'gap-1'}`}>
      {CARD_SORTS.map((o) => {
        const key = sortToParam(o.spec);
        const on = key === chosen;
        return tone === 'phone' ? (
          <button
            key={key}
            type="button"
            aria-pressed={on}
            onClick={() => onPick(o.spec)}
            className={`${CHIP} ${CHIP_MD} ${chipState(on)}`}
          >
            {o.label}
          </button>
        ) : (
          <PickButton key={key} variant="solid" on={on} onClick={() => onPick(o.spec)}>
            {o.label}
          </PickButton>
        );
      })}
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
          tone="phone"
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
