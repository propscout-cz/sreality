/* The portal filter, both sides (MS19 + migration 597): one chip per portal that
 * cycles off → on it → not on it (`cyclePortal`), and — once a portal is excluded
 * — which of a property's ads count against it. Browse's sidebar and the Watchdog
 * editor render this one control; the phone Feed renders the same transition in
 * its own chip language. */

import { PickButton } from '@/components/controls';
import { filterById } from '@/lib/filterRegistry.generated';
import { cyclePortal, type PortalChoice } from '@/lib/portals';

const OPTIONS = (filterById('portals')?.enum_values ?? []).map((o) => ({
  value: String(o.value),
  label: o.label_cs,
}));

export function PortalFilterControl({
  value,
  onChange,
}: {
  value: PortalChoice;
  onChange: (next: PortalChoice) => void;
}) {
  const setActiveOnly = (portalsExcludedActiveOnly: boolean) =>
    onChange({ portals: value.portals, portalsExcluded: value.portalsExcluded, portalsExcludedActiveOnly });
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {OPTIONS.map((opt) => {
          const out = value.portalsExcluded.includes(opt.value);
          return (
            <PickButton
              key={opt.value}
              on={out || value.portals.includes(opt.value)}
              negative={out}
              onClick={() => onChange(cyclePortal(value, opt.value))}
              variant="solid"
              className="min-w-16"
              ariaLabel={out ? `${opt.label} (excluded)` : undefined}
            >
              {out ? `− ${opt.label}` : opt.label}
            </PickButton>
          );
        })}
      </div>
      {value.portalsExcluded.length > 0 && (
        <div role="group" aria-label="Which ads exclude a property" className="flex items-center gap-1">
          <span className="mr-1 text-xs text-[var(--color-ink-2)]">Exclude by</span>
          <PickButton on={!value.portalsExcludedActiveOnly} onClick={() => setActiveOnly(false)}>
            any ad
          </PickButton>
          <PickButton on={value.portalsExcludedActiveOnly} onClick={() => setActiveOnly(true)}>
            active ads only
          </PickButton>
        </div>
      )}
    </div>
  );
}
