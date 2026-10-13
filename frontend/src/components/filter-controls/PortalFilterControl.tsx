/* The portal filter, both sides (MS19 + migration 597): one chip per portal that
 * cycles off → on it → not on it, and — once a portal is excluded — which of a
 * property's ads count against it. Browse's sidebar and the Watchdog editor
 * render this one control over the same three filter values. */

import { PickButton } from '@/components/controls';
import { filterById } from '@/lib/filterRegistry.generated';

import { MultiselectChips } from './MultiselectChips';

const OPTIONS = (filterById('portals')?.enum_values ?? []).map((o) => ({
  value: String(o.value),
  label: o.label_cs,
}));

export interface PortalScope {
  portals: string[];
  excluded: string[];
  excludedActiveOnly: boolean;
}

export function PortalFilterControl({
  value,
  onChange,
}: {
  value: PortalScope;
  onChange: (next: PortalScope) => void;
}) {
  const setActiveOnly = (excludedActiveOnly: boolean) => onChange({ ...value, excludedActiveOnly });
  return (
    <div className="space-y-2">
      <MultiselectChips
        value={value.portals}
        excluded={value.excluded}
        options={OPTIONS}
        onChange={(portals, excluded) =>
          onChange({
            portals,
            excluded,
            excludedActiveOnly: excluded.length > 0 && value.excludedActiveOnly,
          })
        }
      />
      {value.excluded.length > 0 && (
        <div role="group" aria-label="Which ads exclude a property" className="flex items-center gap-1">
          <span className="mr-1 text-xs text-[var(--color-ink-2)]">Exclude by</span>
          <PickButton on={!value.excludedActiveOnly} onClick={() => setActiveOnly(false)}>
            any ad
          </PickButton>
          <PickButton on={value.excludedActiveOnly} onClick={() => setActiveOnly(true)}>
            active ads only
          </PickButton>
        </div>
      )}
    </div>
  );
}
