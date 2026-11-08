/* A fake MapLibre map for jsdom (which has no WebGL): `fakeCreateMap` stands in for
 * lib/basemap's createMap, keeps every map it built in `fakeMaps`, records what the map
 * was given (its container, options, sources and layers, in drawing order) and lets a test
 * fire the map's events. `layers` stays what the map was given (`getLayer` reads it);
 * `setLayoutProperty` acts on `layout`, each layer's live layout, so a test reads a layer's
 * birth state apart from its state now. `resetFakeMaps()` clears the record but keeps a
 * queued mockImplementationOnce. */
import type { Map as MaplibreMap } from 'maplibre-gl';
import { vi } from 'vitest';

type Listener = (event?: unknown) => void;

export class FakeMap {
  readonly sources = new Map<string, unknown>();
  readonly layers: Array<Record<string, unknown>> = [];
  readonly layout = new Map<string, Record<string, unknown>>();
  removed = false;
  private readonly listeners = new Map<string, Array<{ fn: Listener; once: boolean }>>();

  constructor(
    readonly container: HTMLElement,
    readonly options: Record<string, unknown>,
  ) {}

  on(type: string, fn: Listener): this {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), { fn, once: false }]);
    return this;
  }

  /* As MapLibre's: `off` with the same listener takes a pending `once` back. */
  once(type: string, fn: Listener): this {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), { fn, once: true }]);
    return this;
  }

  off(type: string, fn: Listener): this {
    this.listeners.set(type, (this.listeners.get(type) ?? []).filter((l) => l.fn !== fn));
    return this;
  }

  fire(type: string, event?: unknown): void {
    const heard = this.listeners.get(type) ?? [];
    this.listeners.set(type, heard.filter((l) => !l.once));
    for (const l of heard) l.fn(event);
  }

  addSource(id: string, source: unknown): this {
    this.sources.set(id, source);
    return this;
  }

  /* A missing layer (a `beforeId`, a layer to style) throws here: stricter than MapLibre,
   * which once its style has loaded only fires an error event for one. */
  addLayer(layer: Record<string, unknown>, beforeId?: string): this {
    const at = beforeId === undefined ? this.layers.length : this.layers.findIndex((l) => l.id === beforeId);
    if (at < 0) throw new Error(`Cannot add layer "${String(layer.id)}" before non-existing layer "${beforeId}".`);
    this.layers.splice(at, 0, { ...layer });
    this.layout.set(String(layer.id), { ...(layer.layout as Record<string, unknown> | undefined) });
    return this;
  }

  getLayer(id: string): Record<string, unknown> | undefined {
    return this.layers.find((l) => l.id === id);
  }

  setLayoutProperty(id: string, name: string, value: unknown): this {
    const layout = this.layout.get(id);
    if (!layout) throw new Error(`Cannot style non-existing layer "${id}".`);
    this.layout.set(id, { ...layout, [name]: value });
    return this;
  }

  remove(): void {
    this.removed = true;
  }
}

export const fakeMaps: FakeMap[] = [];

export const fakeCreateMap = vi.fn(
  (container: HTMLElement, options: Record<string, unknown> = {}): MaplibreMap => {
    const map = new FakeMap(container, options);
    fakeMaps.push(map);
    return map as unknown as MaplibreMap;
  },
);

export function resetFakeMaps(): void {
  fakeMaps.length = 0;
  fakeCreateMap.mockClear();
}
