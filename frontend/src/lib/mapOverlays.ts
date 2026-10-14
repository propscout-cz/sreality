/* Reference overlays (docs/design/map-overlays.md), the half Browse loads
 * eagerly: the registry the API serves, the `layers` selection grammar and the
 * selection this browser remembers. Drawing — the `overlay://` protocol, the
 * style layers, the hook and the popup — is lib/mapOverlayDraw.ts, imported
 * only by the lazily loaded maps, so neither it nor maplibre-gl reaches the
 * first-visit bundle (scripts/bundle-budget.mjs). Nothing here names a layer:
 * adding one is a registry row and an ingest run. */

import { useCallback, useState } from 'react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import { apiGet, isApiConfigured } from './api';
import { readValue, writeValue } from './persistedFlag';

/* ------------------------------------------------------------------ registry */

export interface OverlayLegendEntry {
  class: string;
  label: string;
  color: string;
  dash?: boolean;
  /* How the class is drawn, for its legend swatch. Absent: a dashed line when
   * `dash`, else a filled area. */
  geom?: 'fill' | 'line' | 'point';
}

export interface OverlayScale {
  prop: string;
  stops: [number, string][];
  null_color: string;
  unit?: string | null;
}

export interface OverlayVariant {
  key: string;
  label: string;
  prop: string;
}

export interface OverlayLayer {
  key: string;
  label: string;
  sort: number;
  legend: OverlayLegendEntry[];
  scale: OverlayScale | null;
  variants: OverlayVariant[] | null;
  feature_count: number;
}

export interface OverlaySection {
  key: string;
  label: string;
  sort: number;
  minzoom: number;
  maxzoom: number;
  attribution: string;
  licence: string;
  source_url: string;
  note: string | null;
  source_revised_at: string | null;
  fetched_at: string | null;
  version: number;
  layers: OverlayLayer[];
}

export interface MapOverlayRegistry {
  sections: OverlaySection[];
}

const bySort = <T extends { sort: number }>(a: T, b: T): number => a.sort - b.sort;

export async function fetchMapOverlays(signal?: AbortSignal): Promise<MapOverlayRegistry> {
  const body = await apiGet<Partial<MapOverlayRegistry> | null>('/map-overlays', undefined, signal);
  const sections = Array.isArray(body?.sections) ? body.sections : [];
  return {
    sections: sections
      .map((s) => ({
        ...s,
        layers: (s.layers ?? []).map((l) => ({ ...l, legend: l.legend ?? [] })).sort(bySort),
      }))
      .sort(bySort),
  };
}

export const MAP_OVERLAYS_QUERY_KEY = ['map_overlays'] as const;

export const useMapOverlayRegistry = (
  enabled = true,
): UseQueryResult<MapOverlayRegistry, Error> =>
  useQuery({
    queryKey: MAP_OVERLAYS_QUERY_KEY,
    queryFn: ({ signal }) => fetchMapOverlays(signal),
    enabled: enabled && isApiConfigured(),
    staleTime: Infinity,
  });

/* ----------------------------------------------------------------- selection */

/* `key[:variantKey]`, comma-separated; canonical = sorted by key, '' = none.
 * A null variant means the layer's first one. */
export type LayerSelection = ReadonlyMap<string, string | null>;

const TOKEN = /^[A-Za-z0-9_-]+$/;

export function parseLayers(s: string | null | undefined): LayerSelection {
  const sel = new Map<string, string | null>();
  for (const raw of (s ?? '').split(',')) {
    const token = raw.trim();
    if (!token) continue;
    const colon = token.indexOf(':');
    const key = colon < 0 ? token : token.slice(0, colon);
    const variant = colon < 0 ? '' : token.slice(colon + 1);
    if (!TOKEN.test(key) || (variant !== '' && !TOKEN.test(variant))) continue;
    sel.set(key, variant || null);
  }
  return sel;
}

export function serializeLayers(sel: LayerSelection): string {
  return [...sel.keys()]
    .sort()
    .map((key) => {
      const variant = sel.get(key);
      return variant ? `${key}:${variant}` : key;
    })
    .join(',');
}

export const canonicalLayers = (s: string | null | undefined): string =>
  serializeLayers(parseLayers(s));

/* The part of a selection the registry can draw: unknown keys dropped, an
 * unknown variant — or the first one, spelled out — folded to null, so one
 * state has one spelling. */
export function layersOf(
  sel: LayerSelection,
  registry: MapOverlayRegistry | undefined,
): LayerSelection {
  const out = new Map<string, string | null>();
  for (const section of registry?.sections ?? []) {
    for (const layer of section.layers) {
      if (!sel.has(layer.key)) continue;
      const variant = sel.get(layer.key) ?? null;
      const known = (layer.variants ?? []).findIndex((v) => v.key === variant) > 0;
      out.set(layer.key, known ? variant : null);
    }
  }
  return out;
}

export function activeVariant(
  layer: OverlayLayer,
  variantKey: string | null | undefined,
): OverlayVariant | null {
  const variants = layer.variants ?? [];
  return variants.find((v) => v.key === variantKey) ?? variants[0] ?? null;
}

/* The URL's selection, or null when the URL says nothing and the remembered
 * one applies. `layers` present — even empty — is explicit; the legacy
 * `kraje=1` (the rent-map panel's old checkbox) reads as `kraje`. */
export function layersFromSearchParams(sp: URLSearchParams): string | null {
  const explicit = sp.get('layers');
  if (explicit != null) return canonicalLayers(explicit);
  if (sp.get('kraje') === '1') return 'kraje';
  return null;
}

const REMEMBERED_LAYERS_KEY = 'map.layers';

export const readRememberedLayers = (): string =>
  canonicalLayers(readValue(REMEMBERED_LAYERS_KEY, ''));

export const writeRememberedLayers = (layers: string): void =>
  writeValue(REMEMBERED_LAYERS_KEY, canonicalLayers(layers));

/* A map with no URL of its own (listing detail, comparables): the remembered
 * selection, written back on every change. */
export function useRememberedLayers(): [string, (next: string) => void] {
  const [layers, setLayers] = useState(readRememberedLayers);
  const set = useCallback((next: string) => {
    const canonical = canonicalLayers(next);
    writeRememberedLayers(canonical);
    setLayers(canonical);
  }, []);
  return [layers, set];
}
