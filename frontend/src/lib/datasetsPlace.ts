/* Datasets' place filter. Its rows are per obec, so a chip is tested through the
 * obec tree — the obec itself, its okres, its kraj — by the one in-memory
 * predicate (`matchesDistricts`). A část obce or ulice chip names a unit finer
 * than any row: as an include it hits no obec, as an exclude it subtracts none. */

import { matchesDistricts } from './districtCodes';
import type { DistrictChip } from './filters';

export interface ObecAncestors {
  okresId: ReadonlyMap<number, number>;
  krajId: ReadonlyMap<number, number>;
}

export const keepObecFor = (
  districts: ReadonlyArray<DistrictChip>,
  { okresId, krajId }: ObecAncestors,
) => (obecId: number): boolean =>
  matchesDistricts(
    {
      obec_id: obecId,
      okres_id: okresId.get(obecId) ?? null,
      region_id: krajId.get(obecId) ?? null,
      cast_obce_id: null,
      ulice_id: null,
    },
    districts,
  );

export const hasChipFinerThanObec = (districts: ReadonlyArray<DistrictChip>): boolean =>
  districts.some((c) => c.level === 'cast_obce' || c.level === 'ulice');
