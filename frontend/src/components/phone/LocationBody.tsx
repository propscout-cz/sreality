/* Poloha on the phone: where the property is, drawn the same in the feed card's sheet and
 * on the property page. A 4:3 plate holding Mapy's static map of the canonical ad's point
 * (streamed by the API on every view, never stored; dimmed after dusk like the photos),
 * under it the place as the page names it, a muted note when the point is only a town or a
 * part of one, and the three map apps an iPhone hands the point to.
 *
 * Never a WebGL map on the phone. The image degrades, the place does not: a failed map
 * leaves the label and the links with a quiet line, a switched-off one leaves them alone,
 * and a property with no point says "Bez polohy" and nothing else. */

import { ApiError } from '@/lib/api';

import { PHOTO_DARK } from './CardPhotoStrip';
import { ExternalIcon } from './phoneIcons';
import {
  STATIC_MAP_H,
  STATIC_MAP_W,
  phoneMapLinks,
  refusalReason,
  usePropertyLocation,
  useStaticMap,
} from './propertyLocation';

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
  const map = useStaticMap(propertyId, enabled);

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
  const disabled = refusalReason(map.error) === 'disabled';
  const mapFailed = map.isError && !disabled;
  const showPlate = !disabled && !mapFailed;

  return (
    <div className="flex flex-col gap-3">
      {showPlate && (
        <div className={PLATE} aria-busy={map.url ? undefined : true}>
          {map.url && (
            <img
              src={map.url}
              width={STATIC_MAP_W}
              height={STATIC_MAP_H}
              alt={data?.label ? `Mapa: ${data.label}` : 'Mapa polohy'}
              className={`block h-full w-full object-cover ${PHOTO_DARK}`}
            />
          )}
          <PlateRule />
        </div>
      )}
      {data ? (
        <div className="flex flex-col gap-0.5">
          {data.label && <p className="text-[15px] text-[var(--color-ink)]">{data.label}</p>}
          {data.note && <p className="text-[12px] text-[var(--color-ink-3)]">{data.note}</p>}
          {mapFailed && <p className="pt-1 text-[12px] text-[var(--color-ink-3)]">Mapu se nepodařilo načíst</p>}
        </div>
      ) : (
        <div aria-hidden className="flex flex-col gap-1.5">
          <span className="h-4 w-3/5 bg-[var(--color-paper-2)]" />
          <span className="h-3 w-2/5 bg-[var(--color-paper-2)]" />
        </div>
      )}
      {data && (
        <nav aria-label="Otevřít v mapách" className="grid grid-cols-3 border-y border-[var(--color-rule-soft)]">
          {phoneMapLinks(data.lat, data.lon, data.label, data.zoom).map((l) => (
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
