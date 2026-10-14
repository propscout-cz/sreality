import { useId, type ReactNode } from 'react';

import { OverlayChip, OverlayPanel } from '@/components/OverlayChip';
import {
  OVERLAY_DASH,
  OVERLAY_FILL_OPACITY,
  OVERLAY_LINE_WIDTH,
  OVERLAY_POINT_RADIUS,
  formatOverlayNumber,
  overlayDataDate,
  overlayScaleWords,
} from '@/lib/mapOverlayDraw';
import {
  activeVariant,
  layersOf,
  parseLayers,
  serializeLayers,
  type LayerSelection,
  type MapOverlayRegistry,
  type OverlayLayer,
  type OverlayLegendEntry,
  type OverlayScale,
  type OverlaySection,
} from '@/lib/mapOverlays';

type Edit = (apply: (next: Map<string, string | null>) => void) => void;

/* The reference-overlay toggles: one chip per registry section, checked when
 * every layer of it is on. While any is on, the panel under the chip holds the
 * per-layer toggles, variant radios, the legend, the zoom hint, the source's
 * caveats and its provenance. `compact` is the chips alone. */
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
    <div className={compact ? 'flex flex-wrap gap-1' : 'flex flex-col gap-2 items-end'}>
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
            else if (!next.has(layer.key)) next.set(layer.key, null);
          }
        })
      }
    />
  );
  if (compact) return chip;
  return (
    <div className="flex flex-col gap-2 items-end">
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
    const toggle = (key: string, checked: boolean) =>
      edit((next) => {
        if (checked) next.set(key, null);
        else next.delete(key);
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
              onChange={(checked) => toggle(layer.key, checked)}
            />
          ))}
        </div>
      ),
    });
  }
  for (const layer of section.layers) {
    if (!sel.has(layer.key)) continue;
    if ((layer.variants?.length ?? 0) > 1) {
      const pick = (variant: string) =>
        edit((next) => {
          next.set(layer.key, variant);
        });
      blocks.push({
        key: `variants-${layer.key}`,
        node: (
          <VariantRadios
            layer={layer}
            value={activeVariant(layer, sel.get(layer.key))?.key ?? null}
            onChange={pick}
          />
        ),
      });
    }
    if (layer.scale) {
      blocks.push({ key: `scale-${layer.key}`, node: <ScaleLegend scale={layer.scale} /> });
    } else if (layer.legend.length > 0 && (!multi || layer.legend.length > 1)) {
      /* A one-class layer of a multi-layer section wears its swatch on its toggle. */
      blocks.push({ key: `legend-${layer.key}`, node: <ClassLegend legend={layer.legend} /> });
    }
  }
  blocks.push({ key: 'about', node: <SectionAbout section={section} zoom={zoom} /> });
  return (
    <OverlayPanel className="min-w-[210px] max-w-[260px]">
      {blocks.map((block, i) => (
        <div
          key={block.key}
          className={i > 0 ? 'border-t border-[var(--color-rule)] pt-1.5' : undefined}
        >
          {block.node}
        </div>
      ))}
    </OverlayPanel>
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

function VariantRadios({
  layer,
  value,
  onChange,
}: {
  layer: OverlayLayer;
  value: string | null;
  onChange: (variantKey: string) => void;
}) {
  // A literal radio `name` is page-global; two mounted maps would share one group.
  const name = useId();
  return (
    <div role="radiogroup" aria-label={layer.label} className="flex flex-col gap-1">
      {(layer.variants ?? []).map((variant) => (
        <label
          key={variant.key}
          className="inline-flex items-center gap-1.5 text-[0.75rem] text-[var(--color-ink-2)] cursor-pointer"
        >
          <input
            type="radio"
            name={name}
            checked={value === variant.key}
            onChange={() => onChange(variant.key)}
          />
          <span>{variant.label}</span>
        </label>
      ))}
    </div>
  );
}

function ClassLegend({ legend }: { legend: readonly OverlayLegendEntry[] }) {
  return (
    <ul aria-label="Legenda" className="flex flex-col gap-1">
      {legend.map((entry) => (
        <li
          key={entry.class}
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
 * scale — for the rent map, word for word the legend it has always had. */
function ScaleLegend({ scale }: { scale: OverlayScale }) {
  const stops = [...scale.stops].sort((a, b) => a[0] - b[0]);
  if (stops.length === 0) return null;
  const colors = stops.map(([, color]) => color);
  const { caption } = overlayScaleWords(scale.unit);
  return (
    <div className="flex flex-col gap-1">
      {caption && <span className="text-[0.65rem] text-[var(--color-ink-3)]">{caption}</span>}
      <div
        aria-hidden="true"
        className="h-1.5 rounded-sm"
        style={{
          background: `linear-gradient(to right, ${(colors.length > 1 ? colors : [colors[0], colors[0]]).join(', ')})`,
        }}
      />
      <div className="flex justify-between text-[0.65rem] text-[var(--color-ink-3)] tabular-nums">
        <span>{formatOverlayNumber(stops[0][0])}</span>
        <span>{formatOverlayNumber(stops[stops.length - 1][0])}</span>
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
