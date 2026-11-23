/* The rows of the Budova card: what the RÚIAN register states about a building, in the order the card shows them.
 * Register values only. Where the ad states something else, the row carries the ad's value as a note and neither
 * is hidden; a value the register does not know is a gap, never dropped. */
import type { RegisterBuilding, RegisterCode } from './api';

export interface RegisterFact {
  label: string;
  /* null = the register does not know. */
  value: string | null;
  /* The ad states something else. */
  note?: string;
  /* How the value was arrived at, when it is not the register's own wording. */
  derived?: string;
}

/* What the property's ads say, for the one fact both sides state on the same basis. */
export interface AdFacts {
  has_lift: boolean | null;
}

const LIFT_YES = 1;
const LIFT_NO = 2;
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

export function registerFacts(b: RegisterBuilding, ad: AdFacts): RegisterFact[] {
  /* An API one deploy behind this bundle serves no list yet: no row then, never a throw. */
  const protection: RegisterBuilding['protection'] | undefined = b.protection;
  const lift: RegisterFact = { label: 'Výtah', value: coded(b.lift) };
  if (b.lift?.code === LIFT_NO && ad.has_lift === true) lift.note = 'inzerát uvádí výtah';
  if (b.lift?.code === LIFT_YES && ad.has_lift === false) lift.note = 'inzerát uvádí bez výtahu';
  return [
    { label: 'Využití', value: coded(b.usage) },
    completionFact(b.completed),
    { label: 'Konstrukce', value: coded(b.construction) },
    /* The register counts storeys below ground too, an ad does not: a different basis, so never a disagreement. */
    { label: 'Podlaží včetně podzemních', value: b.floors == null ? null : num(b.floors) },
    { label: 'Bytů v domě', value: b.dwellings == null ? null : num(b.dwellings) },
    { label: 'Vchody', value: num(b.entrances) },
    lift,
    { label: 'Vytápění', value: coded(b.heating) },
    { label: 'Vodovod', value: coded(b.water) },
    { label: 'Kanalizace', value: coded(b.sewer) },
    { label: 'Plyn', value: coded(b.gas) },
    { label: 'Zastavěná plocha', value: area(b.built_up_area_m2) },
    { label: 'Podlahová plocha', value: area(b.floor_area_m2) },
    /* Not only heritage: the list holds nature and water-source zones too, so the row names no kind. An empty
     * list is the register stating none, which is not the same as not knowing. */
    ...(protection == null ? [] : [{
      label: 'Ochrana',
      value: protection.length ? protection.map((p) => p.label ?? `kód ${p.code}`).join(', ') : 'registr žádnou neuvádí',
    }]),
  ];
}

export const registerBuildingUrl = (code: number): string =>
  `https://vdp.cuzk.gov.cz/vdp/ruian/stavebniobjekty/${code}`;
