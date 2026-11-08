/* A fake MapLibre map for jsdom (which has no WebGL): `fakeCreateMap` stands in for
 * lib/basemap's createMap, keeps every map it built in `fakeMaps`, records what the map
 * was given (its container, options, sources and layers) and lets a test fire the map's
 * events. `resetFakeMaps()` clears the record but keeps a queued mockImplementationOnce. */
import type { Map as MaplibreMap } from 'maplibre-gl';
import { vi } from 'vitest';

type Listener = (event?: unknown) => void;

export class FakeMap {
  readonly sources = new Map<string, unknown>();
  readonly layers: Array<Record<string, unknown>> = [];
  removed = false;
  private readonly listeners = new Map<string, Listener[]>();

  constructor(
    readonly container: HTMLElement,
    readonly options: Record<string, unknown>,
  ) {}

  on(type: string, fn: Listener): this {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
    return this;
  }

  off(type: string, fn: Listener): this {
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((l) => l !== fn),
    );
    return this;
  }

  once(type: string, fn: Listener): this {
    const wrapped: Listener = (event) => {
      this.off(type, wrapped);
      fn(event);
    };
    return this.on(type, wrapped);
  }

  fire(type: string, event?: unknown): void {
    for (const fn of this.listeners.get(type) ?? []) fn(event);
  }

  addSource(id: string, source: unknown): this {
    this.sources.set(id, source);
    return this;
  }

  addLayer(layer: Record<string, unknown>): this {
    this.layers.push(layer);
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
