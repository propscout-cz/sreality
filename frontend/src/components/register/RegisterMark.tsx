import type { ComparedFact } from '@/lib/api';
import { registerDifference } from '@/lib/registerBuilding';
import { useRegisterBuilding } from '@/lib/useRegisterBuilding';

/* THE sign that an ad's value differs from the register's: one glyph, one colour, on every surface. It says that
 * the two differ, never which is right. */
export function DiffersGlyph() {
  return <span aria-hidden className="font-sans font-semibold text-[var(--color-ochre)]">≠</span>;
}

/* Beside an ad's value: drawn only where the API's verdict for that ad and fact is "differs"; the register's own
 * value is its title. Draws nothing for a property with no register building. */
export function RegisterMark({ propertyId, adId, fact }: {
  propertyId: number | null | undefined;
  adId: number | null | undefined;
  fact: ComparedFact;
}) {
  const building = useRegisterBuilding(propertyId);
  const text = building && adId != null ? registerDifference(building, adId, fact) : null;
  if (!text) return null;
  return (
    <span role="img" aria-label={text} title={text} className="ml-1.5 cursor-help">
      <DiffersGlyph />
    </span>
  );
}

/* In the property header: the property's door names a building we hold from the register. Jumps to the Budova
 * card, where the record and its source are. */
export function RegisterBadge({ propertyId }: { propertyId: number | null | undefined }) {
  const building = useRegisterBuilding(propertyId);
  if (!building) return null;
  return (
    <a
      href="#budova"
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
