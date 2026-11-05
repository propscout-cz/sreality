/* How precise a map pin is, carried by the pin itself.
 *
 * Ruling 2026-11-01 retired the radius: "A pin is exact or it is the centre of
 * the RÚIAN unit we hold, marked inaccurate." An ad below address level that
 * stands on a portal pin keeps its pin and reads as approximate too (Q3). So the
 * map draws no uncertainty circle and prints no metre figure: a pin resolved at
 * building level or better wears a black outer contour (the pin IS the
 * building), anything coarser, or not resolved at all, does not, and the popup
 * says "Přesná poloha" or "Přibližná poloha", naming the rung in its tooltip.
 *
 * Granularity is compared by RANK and never by enum text or enum ordinality
 * (migration 380's `location_granularity_rank` is the only legal comparator);
 * the projection publishes the rank as an int precisely so this file can hold a
 * number. A count mark (lib/mapCounts) carries no per-pin identity, so none of
 * this applies to a tile drawn as a count. */

/* location_granularity_rank: building = 90, address_point = 100. Everything
 * below (street 60, cast_obce 50, obec 40, okres 30 …) is a
 * position the resolver would not put a door on. */
export const BUILDING_GRANULARITY_RANK = 90;

export interface PinPrecision {
  granularity_rank: number | null;
}

/* Whether the pin gets the black outer contour — the claim "exactly here". Only
 * the building rungs make it. An unresolved pin deliberately does NOT: a contour
 * with no verdict behind it would be the one claim nobody made. */
export const isPinExact = (p: PinPrecision): boolean =>
  p.granularity_rank != null && p.granularity_rank >= BUILDING_GRANULARITY_RANK;

/* The rung names the popup prints, at migration 380's seed ranks. Looked up as
 * the highest rung AT OR BELOW the pin's rank, so a rung inserted into the table
 * later reads as its nearest coarser neighbour rather than as nothing. */
const RUNG_NAMES: ReadonlyArray<readonly [number, string]> = [
  [100, 'adresní bod'],
  [90, 'budova'],
  [80, 'parcela'],
  [70, 'úsek ulice'],
  [60, 'ulice'],
  [50, 'část obce'],
  [40, 'obec'],
  [30, 'okres'],
  [20, 'kraj'],
  [10, 'stát'],
  [0, 'bez upřesnění'],
];

const rungName = (rank: number): string =>
  RUNG_NAMES.find(([r]) => rank >= r)?.[1] ?? 'bez upřesnění';

/* The lead words of the popup's precision line (ruling P13); pinPrecisionLabel
 * below is the detail in its tooltip. */
export const pinPrecisionShort = (p: PinPrecision): string =>
  p.granularity_rank == null ? 'Přesnost polohy neznámá' : isPinExact(p) ? 'Přesná poloha' : 'Přibližná poloha';

/* The popup's precision detail: the rung the pin stands on, in words. */
export const pinPrecisionLabel = (p: PinPrecision): string => {
  const rank = p.granularity_rank;
  if (rank == null) return 'Přesnost polohy neznámá';
  if (isPinExact(p)) return `Přesná poloha (${rungName(rank)})`;
  return `Přibližná poloha: ${rungName(rank)}`;
};
