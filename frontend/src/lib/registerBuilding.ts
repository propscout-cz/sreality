/* The rows of the Budova card: what the RÚIAN register states about a building, in the order the card shows them.
 * Register values only. Where the property's page states something else, the row carries the ad's value as a note and
 * neither is hidden; a value the register does not know is a gap, never dropped. WHETHER the two differ is the
 * API's verdict (`api/register_comparison`); this file only words it. */
import type { ComparedFact, RegisterBuilding, RegisterCode } from './api';
import { enumLabel } from './enums';
import { fmtFloor } from './format';

export interface RegisterFact {
  label: string;
  /* null = the register does not know. */
  value: string | null;
  /* The ad states something else. */
  note?: string;
  /* How the value was arrived at, when it is not the register's own wording. */
  derived?: string;
}

/* An ISO date as "31. 12. 1990": a register date always shows its year. */
export const registerDate = (iso: string): string => {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return `${d}. ${m}. ${y}`;
};
const num = (n: number) => n.toLocaleString('cs-CZ');
const coded = (c: RegisterCode | null): string | null =>
  c == null || c.unknown ? null : c.label ?? `kód ${c.code}`;
const area = (m2: number | null): string | null => (m2 == null ? null : `${num(m2)} m²`);

/* The register holds a 31 December placeholder for most older buildings; the period is our reading of it, so the
 * row says which date it comes from. A real date is shown as the register gives it. */
export function completionFact(completed: RegisterBuilding['completed']): RegisterFact {
  const label = 'Dokončení';
  if (completed == null) return { label, value: null };
  if (completed.from === completed.to) return { label, value: registerDate(completed.on) };
  return {
    label,
    value: completed.from == null ? `do ${completed.to}` : `${completed.from}–${completed.to}`,
    derived: `období odvozené z data v registru (${registerDate(completed.on)})`,
  };
}

const FLOORS_LABEL = 'Podlaží včetně podzemních';

/* The ad's side of a difference, for the register's row. */
const AD_SIDE: Record<ComparedFact, (ad: unknown) => string> = {
  lift: (ad) => (ad ? 'inzerát uvádí výtah' : 'inzerát uvádí bez výtahu'),
  floors: (ad) => {
    const { floor, total_floors } = ad as { floor: number | null; total_floors: number | null };
    return `inzerát uvádí ${fmtFloor(floor, total_floors) ?? `${total_floors} podlaží`}`;
  },
  construction: (ad) => `inzerát uvádí: ${enumLabel('building_type', ad as string) ?? ad}`,
};
/* The register's side, for a mark beside the ad's value. */
const REGISTER_SIDE: Record<ComparedFact, (b: RegisterBuilding) => string> = {
  lift: (b) => `Výtah: ${coded(b.lift)}`,
  floors: (b) => `${FLOORS_LABEL}: ${b.floors}`,
  construction: (b) => `Konstrukce: ${coded(b.construction)}`,
};

/* `adId` names one ad; without it the question is about the property, by the values its page shows. */
const differing = (b: RegisterBuilding, fact: ComparedFact, adId?: number) => {
  const verdict = (adId == null ? b.shown : b.ads?.[adId])?.[fact];
  return verdict?.differs ? verdict : null;
};

/* What the register states where the ad says something else; null when they do not differ (or cannot be compared). */
export function registerDifference(b: RegisterBuilding, fact: ComparedFact, adId?: number): string | null {
  return differing(b, fact, adId) ? `Liší se od RÚIAN. ${REGISTER_SIDE[fact](b)}` : null;
}

export function registerFacts(b: RegisterBuilding): RegisterFact[] {
  const compared = (fact: ComparedFact, row: RegisterFact): RegisterFact => {
    const verdict = differing(b, fact);
    return verdict ? { ...row, note: AD_SIDE[fact](verdict.ad) } : row;
  };
  return [
    { label: 'Využití', value: coded(b.usage) },
    completionFact(b.completed),
    compared('construction', { label: 'Konstrukce', value: coded(b.construction) }),
    compared('floors', { label: FLOORS_LABEL, value: b.floors == null ? null : num(b.floors) }),
    { label: 'Bytů v domě', value: b.dwellings == null ? null : num(b.dwellings) },
    { label: 'Vchody', value: num(b.entrances) },
    compared('lift', { label: 'Výtah', value: coded(b.lift) }),
    { label: 'Vytápění', value: coded(b.heating) },
    { label: 'Vodovod', value: coded(b.water) },
    { label: 'Kanalizace', value: coded(b.sewer) },
    { label: 'Plyn', value: coded(b.gas) },
    { label: 'Zastavěná plocha', value: area(b.built_up_area_m2) },
    { label: 'Podlahová plocha', value: area(b.floor_area_m2) },
    /* Not only heritage: the list holds nature and water-source zones too, so the row names no kind. An empty
     * list is the register stating none; a missing one (an API one deploy behind) is a gap like any unknown fact.
     * Joined by "; " because the register's own labels hold commas. */
    {
      label: 'Ochrana',
      value: b.protection == null ? null
        : b.protection.length ? b.protection.map((p) => p.label ?? `kód ${p.code}`).join('; ')
        : 'registr žádnou neuvádí',
    },
  ];
}

export const registerBuildingUrl = (code: number): string =>
  `https://vdp.cuzk.gov.cz/vdp/ruian/stavebniobjekty/${code}`;
