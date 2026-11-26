/* The cadastral map's click (docs/design/map-overlays.md): while the `cadastre` pick is
 * drawn (lib/basemap), a click asks ČÚZK which parcel lies under it, and the popup
 * (lib/basemap openReferencePopup) names it — number, katastrální území, area, land type
 * and use — with a link to its page in Nahlížení do KN. Asked of ČÚZK's public RÚIAN view
 * service straight from the browser, as the map's tiles are: no key, nothing of ours on
 * the way. maplibre-gl is imported for its types only. */

import type { Map as MaplibreMap } from 'maplibre-gl';

import { escapeHtml } from './escapeHtml';
import { formatOverlayNumber } from './mapOverlayDraw';
import { CADASTRE_MINZOOM, basemapPicks } from './mapOverlays';

const RUIAN_SERVICE =
  'https://ags.cuzk.gov.cz/arcgis/rest/services/RUIAN/Prohlizeci_sluzba_nad_daty_RUIAN/MapServer';
const PARCEL_LAYER = 5;
const KU_LAYER = 7;
const PARCEL_FIELDS = [
  'id',
  'cisloparcely',
  'kmenovecislo',
  'poddelenicisla',
  'druhcislovanikod',
  'vymeraparcely',
  'druhpozemkukod',
  'zpusobyvyuzitipozemku',
  'katastralniuzemi',
].join(',');

/* ČÚZK's code lists SC_D_POZEMKU and SC_ZP_VYUZITI_POZ (services.cuzk.cz/sestavy/cis,
 * read 2026-11-26); 8 is the travní porost retired in 2000, still on old parcels. */
const LAND_TYPE: Readonly<Record<number, string>> = {
  2: 'orná půda',
  3: 'chmelnice',
  4: 'vinice',
  5: 'zahrada',
  6: 'ovocný sad',
  7: 'trvalý travní porost',
  8: 'trvalý travní porost',
  10: 'lesní pozemek',
  11: 'vodní plocha',
  13: 'zastavěná plocha a nádvoří',
  14: 'ostatní plocha',
};

const LAND_USE: Readonly<Record<number, string>> = {
  1: 'skleník, pařeniště',
  2: 'školka',
  3: 'plantáž dřevin',
  4: 'les jiný než hospodářský',
  5: 'lesní pozemek, na kterém je budova',
  6: 'rybník',
  7: 'koryto vodního toku přirozené nebo upravené',
  8: 'koryto vodního toku umělé',
  9: 'vodní nádrž přírodní',
  10: 'vodní nádrž umělá',
  11: 'zamokřená plocha',
  12: 'společný dvůr',
  13: 'zbořeniště',
  14: 'dráha',
  15: 'dálnice',
  16: 'silnice',
  17: 'ostatní komunikace',
  18: 'ostatní dopravní plocha',
  19: 'zeleň',
  20: 'sportoviště a rekreační plocha',
  21: 'pohřebiště',
  22: 'kulturní a osvětová plocha',
  23: 'manipulační plocha',
  24: 'dobývací prostor',
  25: 'skládka',
  26: 'jiná plocha',
  27: 'neplodná půda',
  28: 'vodní plocha, na které je budova',
  29: 'fotovoltaická elektrárna',
  30: 'mez, stráň',
};

export interface Parcel {
  /* RÚIAN's parcel id, the key of its page in Nahlížení do KN. */
  id: string;
  /* As the cadastre writes it: "1/1", or "st. 64" for a building plot. */
  number: string;
  area: number | null;
  landType: string | null;
  landUse: string | null;
  ku: string | null;
}

const numberOf = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/* A parcel row (layer 5) and the katastrální území under the same point (layer 7) as the
 * popup reads them; null without an id or a number. Where a k.ú. numbers building plots
 * in a series of their own (druhcislovanikod 1) the number wears "st.", as on the map. */
export function parcelFromAttributes(
  attrs: Record<string, unknown>,
  ku: Record<string, unknown> | null,
): Parcel | null {
  const id = numberOf(attrs.id);
  const stem = numberOf(attrs.kmenovecislo);
  const part = numberOf(attrs.poddelenicisla);
  const written =
    typeof attrs.cisloparcely === 'string' && attrs.cisloparcely.trim() !== ''
      ? attrs.cisloparcely.trim()
      : stem != null
        ? `${stem}${part != null ? `/${part}` : ''}`
        : null;
  if (id == null || written == null) return null;
  const type = numberOf(attrs.druhpozemkukod);
  const use = numberOf(attrs.zpusobyvyuzitipozemku);
  const sameKu = ku != null && ku.kod === attrs.katastralniuzemi && typeof ku.nazev === 'string';
  return {
    id: id.toFixed(0),
    number: attrs.druhcislovanikod === 1 ? `st. ${written}` : written,
    area: numberOf(attrs.vymeraparcely),
    landType: type == null ? null : LAND_TYPE[type] ?? null,
    landUse: use == null ? null : LAND_USE[use] ?? null,
    ku: sameKu ? (ku.nazev as string) : null,
  };
}

async function featureAt(
  layer: number,
  { lng, lat }: { lng: number; lat: number },
  fields: string,
  signal: AbortSignal,
): Promise<Record<string, unknown> | null> {
  const query = new URLSearchParams({
    geometry: `${lng},${lat}`,
    geometryType: 'esriGeometryPoint',
    inSR: '4326',
    spatialRel: 'esriSpatialRelIntersects',
    outFields: fields,
    returnGeometry: 'false',
    f: 'json',
  });
  const res = await fetch(`${RUIAN_SERVICE}/${layer}/query?${query}`, { signal });
  if (!res.ok) throw new Error(`ČÚZK RÚIAN answered ${res.status}`);
  // ArcGIS reports a failed query as HTTP 200 with an `error` body.
  const body = (await res.json()) as {
    features?: { attributes?: Record<string, unknown> }[];
    error?: { message?: string };
  };
  if (body.error) throw new Error(`ČÚZK RÚIAN: ${body.error.message ?? 'query failed'}`);
  return body.features?.[0]?.attributes ?? null;
}

/* The parcel under a point, null where there is none (abroad, a gap in the map). Its
 * k.ú. is asked beside it, by the same point, so both answers arrive in one round trip. */
export async function fetchParcelAt(at: { lng: number; lat: number }, signal: AbortSignal): Promise<Parcel | null> {
  const [parcel, ku] = await Promise.all([
    featureAt(PARCEL_LAYER, at, PARCEL_FIELDS, signal),
    featureAt(KU_LAYER, at, 'kod,nazev', signal),
  ]);
  return parcel ? parcelFromAttributes(parcel, ku) : null;
}

export const nahlizeniUrl = (parcelId: string): string =>
  `https://nahlizenidokn.cuzk.gov.cz/ZobrazObjekt.aspx?typ=parcela&id=${encodeURIComponent(parcelId)}`;

export type ParcelLookup =
  | { state: 'loading' }
  | { state: 'found'; parcel: Parcel }
  | { state: 'none' }
  | { state: 'failed' };

const LOOKUP_WORDS: Record<Exclude<ParcelLookup['state'], 'found'>, string> = {
  loading: 'Hledám parcelu…',
  none: 'Tady katastr žádnou parcelu nevede.',
  failed: 'Katastr teď neodpovídá. Klikněte znovu.',
};

/* The parcel's card, in the reference popups' own classes. */
export function parcelPopupHtml(lookup: ParcelLookup): string {
  const kicker = '<p class="lp-kicker">Katastrální mapa</p>';
  if (lookup.state !== 'found') {
    return `<div class="lp">${kicker}<p class="lp-meta">${LOOKUP_WORDS[lookup.state]}</p></div>`;
  }
  const { parcel } = lookup;
  const meta = [parcel.ku && `k.ú. ${parcel.ku}`, parcel.area != null && `${formatOverlayNumber(parcel.area)} m²`]
    .filter(Boolean)
    .join(' · ');
  const kind = [parcel.landType, parcel.landUse].filter(Boolean).join(' · ');
  return `
    <div class="lp">
      ${kicker}
      <div class="lp-row">
        <p class="lp-price">Parcela ${escapeHtml(parcel.number)}</p>
      </div>
      ${meta ? `<p class="lp-meta">${escapeHtml(meta)}</p>` : ''}
      ${kind ? `<p class="lp-district">${escapeHtml(kind)}</p>` : ''}
      <a class="lp-link" href="${escapeHtml(nahlizeniUrl(parcel.id))}" target="_blank" rel="noreferrer">Nahlížení do KN <span aria-hidden="true">↗</span></a>
    </div>
  `;
}

/* Whether a click on this map asks for its parcel: the cadastral map is picked and drawn. */
export const asksParcel = (map: MaplibreMap, selection: string): boolean =>
  basemapPicks(selection).cadastre && map.getZoom() >= CADASTRE_MINZOOM;

/* What ČÚZK said, as the popup shows it; never rejects. */
export const lookUpParcel = (at: { lng: number; lat: number }, signal: AbortSignal): Promise<ParcelLookup> =>
  fetchParcelAt(at, signal).then(
    (parcel): ParcelLookup => (parcel ? { state: 'found', parcel } : { state: 'none' }),
    (): ParcelLookup => ({ state: 'failed' }),
  );
