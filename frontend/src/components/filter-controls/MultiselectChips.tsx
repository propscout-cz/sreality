/* MultiselectChips — multi-select rendered as a wrapping row of toggleable pills.
 *
 * Used for dispositions (1+kk … 9+1), districts when sourced from a
 * closed list, etc. Mirrors the existing DispositionPicker in
 * Filters.tsx but in a registry-driven shape: takes an option list
 * with value + label and emits a list of selected values.
 *
 * Given `excluded`, a chip has a third state: off → in → out (brick, "−") → off —
 * the include/exclude polarity a location chip carries, for a closed list. One
 * chip holds one state, so a value can never be both included and excluded.
 *
 * The chips USED to be a fixed N-column grid (`repeat(cols, minmax(0,1fr))`),
 * which sizes a column from the container and not from its contents: any
 * caller narrower than N × the longest label got that label CLIPPED by the
 * button's own overflow — "Komerční" as "Komerč", "Zemědělská usedlost" as
 * "Zeměděls usedlost" (measured on Browse's sidebar and on the sold-comps
 * block at 1280 AND 390 px). A wrapping flex row cannot clip: a chip is as wide
 * as its label (floored at 4rem so a row of "1+kk" still reads as a row), and
 * how many fit per row is measured by the browser rather than declared. That
 * deletes the `cols` prop with it — no caller has to guess a column count that
 * the longest translated label then has to live inside.
 */

import { PickButton } from '@/components/controls';

import type { EnumOptionLite } from './types';

export function MultiselectChips<T extends string | number>({
  value,
  options,
  onChange,
  excluded,
}: {
  value: ReadonlyArray<T>;
  options: ReadonlyArray<EnumOptionLite<T>>;
  onChange: (next: T[], nextExcluded: T[]) => void;
  excluded?: ReadonlyArray<T>;
}) {
  const selected = new Set(value);
  const out = new Set(excluded ?? []);
  const without = (xs: ReadonlyArray<T>, v: T) => xs.filter((x) => x !== v);
  const cycle = (v: T) => {
    const ex = excluded ?? [];
    if (selected.has(v)) onChange(without(value, v), excluded ? [...ex, v] : []);
    else if (out.has(v)) onChange([...value], without(ex, v));
    else onChange([...value, v], [...ex]);
  };
  return (
    <div className="flex flex-wrap gap-1">
      {options.map((opt) => {
        const isOut = out.has(opt.value);
        return (
          <PickButton
            key={String(opt.value)}
            on={selected.has(opt.value) || isOut}
            negative={isOut}
            onClick={() => cycle(opt.value)}
            variant="solid"
            className="min-w-16"
            ariaLabel={isOut ? `${opt.label} (excluded)` : undefined}
          >
            {isOut ? `− ${opt.label}` : opt.label}
          </PickButton>
        );
      })}
    </div>
  );
}
