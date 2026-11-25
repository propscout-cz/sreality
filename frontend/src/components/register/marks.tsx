import type { ComparedFact } from '@/lib/api';
import { registerBuildingUrl, registerDifference } from '@/lib/registerBuilding';
import { useRegisterBuilding } from '@/lib/useRegisterBuilding';

/* THE sign that an ad's value differs from the register's: one glyph, one colour, on every surface. It says that
 * the two differ, never which is right. */
export function DiffersGlyph() {
  return <span aria-hidden className="font-sans font-semibold text-[var(--color-ochre)]">≠</span>;
}

/* Beside a value an ad states: drawn only where the API's verdict is "differs"; the register's own value is its
 * title. With `adId` the value is that ad's; without it, the property's (what its page shows, rolled up over its
 * ads). Draws nothing for a property with no register building. */
export function RegisterMark({ propertyId, adId, fact }: {
  propertyId: number | null | undefined;
  adId?: number;
  fact: ComparedFact;
}) {
  const building = useRegisterBuilding(propertyId);
  const text = building ? registerDifference(building, fact, adId) : null;
  if (!text) return null;
  return (
    <span role="img" aria-label={text} title={text} className="ml-1.5 cursor-help">
      <DiffersGlyph />
    </span>
  );
}

/* In the property header: the property's door names a building we hold from the register. Opens that building
 * in the register's own viewer. */
export function RegisterBadge({ propertyId }: { propertyId: number | null | undefined }) {
  const building = useRegisterBuilding(propertyId);
  if (!building) return null;
  return (
    <a
      href={registerBuildingUrl(building.code)}
      target="_blank"
      rel="noreferrer"
      title={`Propojeno s registrem RÚIAN: stavební objekt ${building.code}`}
      className="inline-flex h-5 shrink-0 items-center gap-1 whitespace-nowrap rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] px-1 font-sans text-[11px] text-[var(--color-ink-2)] hover:text-[var(--color-copper)]"
    >
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M5 12.5 L10 17.5 L19 7" />
      </svg>
      RÚIAN
    </a>
  );
}
