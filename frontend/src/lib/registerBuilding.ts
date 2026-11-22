/* The rows of the Budova card: what the RÚIAN register states about a building, in the order the card shows them.
 * Register values only. Where the ad states something else, the row carries the ad's value as a note and neither
 * is hidden; a value the register does not know is a gap, never dropped. */
import type { RegisterBuilding, RegisterCode } from './api';

export interface RegisterFact {
  label: string;
  /* null = the register does not know. */
  value: string | null;
  note?: string;
}

/* What the property's ads say, for the two facts both sides state. */
export interface AdFacts {
  total_floors: number | null;
  has_lift: boolean | null;
}

const LIFT_YES = 1;
const LIFT_NO = 2;
const num = (n: number) => n.toLocaleString('cs-CZ');
const coded = (c: RegisterCode | null): string | null => (c == null || c.unknown ? null : c.label);
const area = (m2: number | null): string | null => (m2 == null ? null : `${num(m2)} m²`);

export function completionLabel(completed: RegisterBuilding['completed']): string | null {
  if (completed == null) return null;
  if (completed.from == null) return `do ${completed.to}`;
  return completed.from === completed.to ? String(completed.to) : `${completed.from}–${completed.to}`;
}

export function registerFacts(b: RegisterBuilding, ad: AdFacts): RegisterFact[] {
  const floors: RegisterFact = { label: 'Podlaží', value: b.floors == null ? null : num(b.floors) };
  if (b.floors != null && ad.total_floors != null && ad.total_floors !== b.floors) {
    floors.note = `inzerát uvádí ${ad.total_floors}`;
  }
  const lift: RegisterFact = { label: 'Výtah', value: coded(b.lift) };
  if (b.lift?.code === LIFT_NO && ad.has_lift === true) lift.note = 'inzerát uvádí výtah';
  if (b.lift?.code === LIFT_YES && ad.has_lift === false) lift.note = 'inzerát uvádí bez výtahu';
  return [
    { label: 'Využití', value: coded(b.usage) },
    { label: 'Dokončení', value: completionLabel(b.completed) },
    { label: 'Konstrukce', value: coded(b.construction) },
    floors,
    { label: 'Bytů v domě', value: b.dwellings == null ? null : num(b.dwellings) },
    { label: 'Vchody', value: num(b.entrances) },
    lift,
    { label: 'Vytápění', value: coded(b.heating) },
    { label: 'Vodovod', value: coded(b.water) },
    { label: 'Kanalizace', value: coded(b.sewer) },
    { label: 'Plyn', value: coded(b.gas) },
    { label: 'Zastavěná plocha', value: area(b.built_up_area_m2) },
    { label: 'Podlahová plocha', value: area(b.floor_area_m2) },
    { label: 'Památková ochrana', value: b.protected ? 'Ano' : 'Žádná' },
  ];
}

export const registerBuildingUrl = (code: number): string =>
  `https://vdp.cuzk.gov.cz/vdp/ruian/stavebniobjekty/${code}`;
