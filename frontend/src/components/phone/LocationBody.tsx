/* Poloha on the phone: where the property is, drawn the same in the feed card's sheet and
 * on the property page. A 4:3 plate holding a map of the location ad's point (a lazy
 * chunk, so the place never waits for it), under it the place as the page names it, a muted
 * note when the point is only a town or a part of one, and the three map apps an iPhone
 * hands the point to. A property with no point says "Bez polohy" and nothing else. */

import MapSlot from '@/components/MapSlot';
import { ApiError } from '@/lib/api';
import { lazyChunk } from '@/lib/lazyChunk';

import { ExternalIcon } from './phoneIcons';
import { mapZoom, phoneMapLinks, usePropertyLocation } from './propertyLocation';

const PhoneMap = lazyChunk(() => import('./PhoneMap'));

const PLATE = 'relative isolate aspect-[4/3] w-full overflow-hidden bg-[var(--color-inset)]';
const QUIET = 'text-[13px] text-[var(--color-ink-3)]';

/* The plate's edge: a hairline inside the box, over the map, so the map never paints it. */
function PlateRule() {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute inset-0 shadow-[inset_0_0_0_1px_var(--color-rule)]"
    />
  );
}

export default function LocationBody({
  propertyId,
  enabled = true,
}: {
  propertyId: number;
  /* The sheet reads only while it is open. */
  enabled?: boolean;
}) {
  const loc = usePropertyLocation(propertyId, enabled);

  if (loc.isError) {
    if (loc.error instanceof ApiError && loc.error.status === 404) {
      return <p className={QUIET}>Bez polohy</p>;
    }
    return (
      <p className={`flex items-center justify-between gap-3 ${QUIET}`}>
        Polohu se nepodařilo načíst
        <button
          type="button"
          onClick={() => void loc.refetch()}
          className="h-8 text-[13px] font-medium text-[var(--color-copper)]"
        >
          Zkusit znovu
        </button>
      </p>
    );
  }

  const data = loc.data;

  return (
    <div className="flex flex-col gap-3">
      <div className={PLATE}>
        {data && (
          <MapSlot>
            <PhoneMap lat={data.lat} lon={data.lon} zoom={mapZoom(data.precision)} />
          </MapSlot>
        )}
        <PlateRule />
      </div>
      {data ? (
        <div className="flex flex-col gap-0.5">
          {data.label && <p className="text-[15px] text-[var(--color-ink)]">{data.label}</p>}
          {data.note && <p className="text-[12px] text-[var(--color-ink-3)]">{data.note}</p>}
        </div>
      ) : (
        <div aria-hidden className="flex flex-col gap-1.5">
          <span className="h-4 w-3/5 bg-[var(--color-paper-2)]" />
          <span className="h-3 w-2/5 bg-[var(--color-paper-2)]" />
        </div>
      )}
      {data && (
        <nav aria-label="Otevřít v mapách" className="grid grid-cols-3 border-y border-[var(--color-rule-soft)]">
          {phoneMapLinks(data.lat, data.lon, data.label, mapZoom(data.precision)).map((l) => (
            <a
              key={l.key}
              href={l.url}
              className="inline-flex h-11 min-w-0 items-center justify-center gap-0.5 border-l border-[var(--color-rule-soft)] px-1 text-[13px] font-medium text-[var(--color-copper)] first:border-l-0 active:bg-[var(--color-paper-2)]"
            >
              <span className="truncate">{l.label}</span>
              <ExternalIcon className="h-4 w-4 shrink-0" />
            </a>
          ))}
        </nav>
      )}
    </div>
  );
}
