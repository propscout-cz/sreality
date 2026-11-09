import type { ReactNode } from 'react';

import { MapLegendCard, OverlayChip, OverlayPanel } from '@/components/OverlayChip';
import {
  OVERLAY_DASH,
  OVERLAY_FILL_OPACITY,
  OVERLAY_LINE_WIDTH,
  OVERLAY_POINT_RADIUS,
  formatOverlayNumber,
  overlayDataDate,
  overlayScaleWords,
  type OverlayPlaces,
} from '@/lib/mapOverlayDraw';
import {
  type LayerSelection,
  type MapOverlayRegistry,
  type OverlayLayer,
  type OverlayLegendEntry,
  type OverlayScale,
  type OverlaySection,
  activeVariant,
  layersOf,
  overlayHasAreaCard,
  parseLayers,
  pickVariant,
  readRememberedVariant,
  serializeLayers,
} from '@/lib/mapOverlays';

type Edit = (apply: (next: Map<string, string | null>) => void) => void;

/* The reference-overlay toggles: one chip per registry section, checked when
 * every layer of it is on. While any is on, the panel under the chip holds the
 * per-layer toggles, the zoom hint, the source's caveats and its provenance (a
 * layer's variant is picked on its area card, on the map). `compact` is the
 * chips alone. */
export default function MapOverlayControls({
  registry,
  selection,
  onChange,
  zoom = null,
  compact = false,
}: {
  registry: MapOverlayRegistry | undefined;
  selection: string;
  onChange: (next: string) => void;
  /* The map's floored zoom; below a section's minzoom its panel says to zoom in. */
  zoom?: number | null;
  compact?: boolean;
}) {
  const sections = (registry?.sections ?? []).filter((s) => s.layers.length > 0);
  if (sections.length === 0) return null;
  const sel = layersOf(parseLayers(selection), registry);
  const edit: Edit = (apply) => {
    const next = new Map(sel);
    apply(next);
    onChange(serializeLayers(layersOf(next, registry)));
  };
  return (
    <div className={compact ? 'flex flex-wrap gap-1' : 'flex flex-col'}>
      {sections.map((section) => (
        <SectionControl
          key={section.key}
          section={section}
          sel={sel}
          zoom={zoom}
          compact={compact}
          edit={edit}
        />
      ))}
    </div>
  );
}

function SectionControl({
  section,
  sel,
  zoom,
  compact,
  edit,
}: {
  section: OverlaySection;
  sel: LayerSelection;
  zoom: number | null;
  compact: boolean;
  edit: Edit;
}) {
  const on = section.layers.filter((l) => sel.has(l.key)).length;
  const chip = (
    <OverlayChip
      label={section.label}
      checked={on === section.layers.length}
      indeterminate={on > 0 && on < section.layers.length}
      compact={compact}
      onChange={(checked) =>
        edit((next) => {
          for (const layer of section.layers) {
            if (!checked) next.delete(layer.key);
            else if (!next.has(layer.key)) next.set(layer.key, readRememberedVariant(layer));
          }
        })
      }
    />
  );
  if (compact) return chip;
  return (
    <div className="flex flex-col">
      {chip}
      {on > 0 && <SectionPanel section={section} sel={sel} zoom={zoom} edit={edit} />}
    </div>
  );
}

function SectionPanel({
  section,
  sel,
  zoom,
  edit,
}: {
  section: OverlaySection;
  sel: LayerSelection;
  zoom: number | null;
  edit: Edit;
}) {
  const multi = section.layers.length > 1;
  const blocks: { key: string; node: ReactNode }[] = [];
  if (multi) {
    const toggle = (layer: OverlayLayer, checked: boolean) =>
      edit((next) => {
        if (checked) next.set(layer.key, readRememberedVariant(layer));
        else next.delete(layer.key);
      });
    blocks.push({
      key: 'layers',
      node: (
        <div className="flex flex-col gap-1">
          {section.layers.map((layer) => (
            <LayerToggle
              key={layer.key}
              layer={layer}
              checked={sel.has(layer.key)}
              onChange={(checked) => toggle(layer, checked)}
            />
          ))}
        </div>
      ),
    });
  }
  blocks.push({ key: 'about', node: <SectionAbout section={section} zoom={zoom} /> });
  return (
    <OverlayPanel>
      {blocks.map((block) => (
        <div key={block.key}>{block.node}</div>
      ))}
    </OverlayPanel>
  );
}

/* The keys of the layers that are on, one card per section for the map's
 * corner: a layer's area card (a scale with variants), its scale, or its
 * classes (a multi-layer section lists the class of each layer drawn).
 * `places` = what lies under the map's centre; `onChange` lets an area card
 * pick the variant the map is painted by. */
export function MapOverlayLegends({
  registry,
  selection,
  places,
  onChange,
}: {
  registry: MapOverlayRegistry | undefined;
  selection: string;
  places?: OverlayPlaces;
  onChange?: (next: string) => void;
}) {
  const sel = layersOf(parseLayers(selection), registry);
  return (
    <>
      {(registry?.sections ?? []).map((section) => {
        const on = section.layers.filter((layer) => sel.has(layer.key));
        if (on.length === 0) return null;
        const classes = on.filter((layer) => !layer.scale).flatMap((layer) => layer.legend);
        const cards = on.filter(overlayHasAreaCard);
        const scales = on.filter((layer) => layer.scale && !overlayHasAreaCard(layer));
        if (classes.length === 0 && scales.length === 0 && cards.length === 0) return null;
        return (
          <MapLegendCard key={section.key} title={section.label}>
            {cards.map((layer) => (
              <AreaCard
                key={layer.key}
                layer={layer}
                variantKey={sel.get(layer.key) ?? null}
                place={places?.get(layer.key) ?? null}
                onPick={onChange && ((variant) => onChange(pickVariant(selection, registry, layer.key, variant)))}
              />
            ))}
            {scales.map((layer) => <ScaleLegend key={layer.key} scale={layer.scale!} />)}
            {classes.length > 0 && <ClassLegend legend={classes} />}
          </MapLegendCard>
        );
      })}
    </>
  );
}

const figureOf = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN;
  return Number.isFinite(n) && n >= 0 ? n : null;
};

const textOf = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null);

const NUMBER = 'font-mono tabular-nums text-right';

/* A scale layer's key read where the map's centre is: the place, every
 * variant's figure (and its second one, the rent map's new-build rent) under
 * the scale's unit, the shown variant lit — a click on another paints the map
 * by it — then the shown one's worked example and the ramp. */
function AreaCard({
  layer,
  variantKey,
  place,
  onPick,
}: {
  layer: OverlayLayer;
  variantKey: string | null;
  place: Record<string, unknown> | null;
  onPick?: (variant: string) => void;
}) {
  const scale = layer.scale!;
  const variants = layer.variants ?? [];
  const words = overlayScaleWords(scale.unit, place?.level);
  const shown = activeVariant(layer, variantKey);
  const name = textOf(place?.name);
  const kraj = textOf(place?.kraj);
  const alt = variants.some((v) => v.alt_prop);
  const cols = alt ? 'grid-cols-[minmax(0,1fr)_2.25rem_3.6rem]' : 'grid-cols-[minmax(0,1fr)_2.25rem]';
  const cell = (prop: string | null | undefined) => {
    const n = prop ? figureOf(place?.[prop]) : null;
    return n == null ? '—' : formatOverlayNumber(n);
  };
  const m2 = shown?.example_m2 ?? null;
  const value = shown ? figureOf(place?.[shown.prop]) : null;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-col">
        <span className="text-[0.62rem] text-[var(--color-ink-3)] leading-snug">
          {name ? [words.place, kraj].filter(Boolean).join(' · ') : 'Střed mapy'}
        </span>
        <span className="text-[0.75rem] font-semibold text-[var(--color-ink)] leading-snug">{name ?? 'Bez údajů'}</span>
      </div>
      <div className="flex flex-col gap-0.5">
        {words.caption && <span className="text-[0.62rem] text-[var(--color-ink-3)]">{words.caption}</span>}
        <div
          aria-hidden="true"
          className={`grid ${cols} gap-x-1.5 px-1.5 text-[0.6rem] whitespace-nowrap text-[var(--color-ink-3)]`}
        >
          <span />
          <span className="text-right">{words.main}</span>
          {alt && <span className="text-right">{words.alt}</span>}
        </div>
        <div role="group" aria-label={words.variants ?? layer.label} className="flex flex-col gap-px">
          {variants.map((variant) => {
            const lit = variant.key === shown?.key;
            return (
              <button
                key={variant.key}
                type="button"
                aria-pressed={lit}
                disabled={!onPick}
                onClick={() => onPick?.(variant.key)}
                className={`grid ${cols} gap-x-1.5 items-baseline px-1.5 py-0.5 rounded-[var(--radius-sm)] text-left text-[0.7rem] ${
                  lit
                    ? 'bg-[var(--color-copper)] text-[var(--color-on-copper)]'
                    : 'text-[var(--color-ink-2)] enabled:hover:text-[var(--color-ink)] enabled:hover:bg-[var(--color-inset)]'
                }`}
              >
                <span>{variant.label}</span>
                <span className={NUMBER}>{cell(variant.prop)}</span>
                {alt && <span className={NUMBER}>{cell(variant.alt_prop)}</span>}
              </button>
            );
          })}
        </div>
      </div>
      {words.total && m2 != null && value != null && (
        <p
          className="text-[0.65rem] text-[var(--color-ink-2)]"
          title={words.example ? `${formatOverlayNumber(m2)} m² je ${words.example} ${shown?.label}` : undefined}
        >
          Příklad: <span className="font-mono tabular-nums">{formatOverlayNumber(m2)}</span> m² ×{' '}
          <span className="font-mono tabular-nums">{formatOverlayNumber(value)}</span> ={' '}
          <span className="font-semibold text-[var(--color-ink)] whitespace-nowrap">
            <span className="font-mono tabular-nums">{formatOverlayNumber(m2 * value)}</span> {words.total}
          </span>
        </p>
      )}
      <ScaleLegend scale={scale} compact />
    </div>
  );
}

/* Why the layer may not show yet, what the source says about itself, and whose
 * data it is as of when. */
function SectionAbout({ section, zoom }: { section: OverlaySection; zoom: number | null }) {
  const date = overlayDataDate(section);
  return (
    <div className="flex flex-col gap-1">
      {zoom != null && zoom < section.minzoom && (
        <p
          className="text-[0.68rem] font-medium text-[var(--color-ink-2)]"
          title={`Vrstva se kreslí od přiblížení ${section.minzoom}`}
        >
          Přibližte mapu
        </p>
      )}
      {section.note && (
        <p className="text-[0.65rem] leading-snug text-[var(--color-ink-3)]">{section.note}</p>
      )}
      <p className="text-[0.62rem] text-[var(--color-ink-3)]">
        {date ? `Data k ${date} · ` : ''}
        {/^https?:\/\//.test(section.source_url) ? (
          <a
            href={section.source_url}
            target="_blank"
            rel="noreferrer"
            title={section.licence}
            className="underline-offset-2 hover:underline hover:text-[var(--color-ink-2)]"
          >
            {section.attribution}
          </a>
        ) : (
          <span title={section.licence}>{section.attribution}</span>
        )}
      </p>
    </div>
  );
}

function LayerToggle({
  layer,
  checked,
  onChange,
}: {
  layer: OverlayLayer;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  const only = !layer.scale && layer.legend.length === 1 ? layer.legend[0] : null;
  return (
    <label className="inline-flex items-center gap-1.5 text-[0.75rem] text-[var(--color-ink-2)] cursor-pointer">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {only && <LegendSwatch entry={only} />}
      <span>{only?.label ?? layer.label}</span>
    </label>
  );
}

function ClassLegend({ legend }: { legend: readonly OverlayLegendEntry[] }) {
  return (
    <ul aria-label="Legenda" className="flex flex-wrap gap-x-3 gap-y-1">
      {legend.map((entry, i) => (
        <li
          key={`${i}:${entry.class}`}
          className="flex items-center gap-1.5 text-[0.7rem] text-[var(--color-ink-2)]"
        >
          <LegendSwatch entry={entry} />
          <span>{entry.label}</span>
        </li>
      ))}
    </ul>
  );
}

/* A scale's key: what it measures, the gradient, and the two ends of the
 * scale. `compact` (an area card, which names the unit above its figures): no
 * caption, the ends beside the gradient. */
function ScaleLegend({ scale, compact = false }: { scale: OverlayScale; compact?: boolean }) {
  const stops = [...scale.stops].sort((a, b) => a[0] - b[0]);
  if (stops.length === 0) return null;
  const colors = stops.map(([, color]) => color);
  const { caption } = overlayScaleWords(scale.unit);
  const ends = 'text-[0.65rem] text-[var(--color-ink-3)] tabular-nums';
  const low = <span className={ends}>{formatOverlayNumber(stops[0][0])}</span>;
  const high = <span className={ends}>{formatOverlayNumber(stops[stops.length - 1][0])}</span>;
  const ramp = (
    <div
      aria-hidden="true"
      className={`h-1.5 rounded-sm ${compact ? 'flex-1' : ''}`}
      style={{
        background: `linear-gradient(to right, ${(colors.length > 1 ? colors : [colors[0], colors[0]]).join(', ')})`,
      }}
    />
  );
  if (compact) {
    return (
      <div className="flex items-center gap-1.5">
        {low}
        {ramp}
        {high}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      {caption && <span className="text-[0.65rem] text-[var(--color-ink-3)]">{caption}</span>}
      {ramp}
      <div className="flex justify-between">
        {low}
        {high}
      </div>
    </div>
  );
}

/* The basemap is not themed, so each swatch sits on a patch of it: a dark
 * border line or a 40 % area tint then reads as it will on the map, even in
 * the dark panel. */
const BASEMAP_TONE = '#f2efe9';

/* The class in miniature, from the constants the map paints it with. */
function LegendSwatch({ entry }: { entry: OverlayLegendEntry }) {
  const kind = entry.geom ?? (entry.dash ? 'line' : 'fill');
  return (
    <svg aria-hidden="true" width="20" height="12" viewBox="0 0 20 12" className="shrink-0">
      <rect
        x="0.5"
        y="0.5"
        width="19"
        height="11"
        rx="2"
        fill={BASEMAP_TONE}
        stroke="rgba(26, 28, 34, 0.14)"
      />
      {kind === 'fill' && (
        <rect
          x="0.5"
          y="0.5"
          width="19"
          height="11"
          rx="2"
          fill={entry.color}
          fillOpacity={OVERLAY_FILL_OPACITY}
        />
      )}
      {kind === 'line' && (
        <line
          x1="3"
          y1="6"
          x2="17"
          y2="6"
          stroke={entry.color}
          strokeWidth={OVERLAY_LINE_WIDTH}
          strokeLinecap={entry.dash ? 'butt' : 'round'}
          strokeDasharray={
            entry.dash ? OVERLAY_DASH.map((d) => d * OVERLAY_LINE_WIDTH).join(' ') : undefined
          }
        />
      )}
      {kind === 'point' && (
        <circle
          cx="10"
          cy="6"
          r={OVERLAY_POINT_RADIUS}
          fill={entry.color}
          stroke="#ffffff"
          strokeWidth={1.5}
        />
      )}
    </svg>
  );
}
