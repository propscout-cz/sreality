interface Props {
  /* Omitted: no heading row, for a caller whose card already states the middle. */
  label?: string;
  range: { low: number; mid: number; high: number };
  /* Names the three numbers, low · mid · high ("p25 · median · p75"): a
   * strip never leaves its reader guessing what the ends are. */
  caption: string;
  format: (n: number) => string;
  /* Other figures read against the band, as ink ticks. One outside it pins to
   * the end it passed and says so: the strip never scrolls sideways. */
  marks?: ReadonlyArray<{ label: string; value: number }>;
}

/* A printer's-ruler row: thin grey baseline with three notches.
 * The middle tick is slightly thicker and copper-coloured. */
export default function RangeStrip({ label, range, caption, format, marks = [] }: Props) {
  const { low, mid, high } = range;
  const span = Math.max(1, high - low);
  const midPct = ((mid - low) / span) * 100;
  const at = (v: number) => Math.min(100, Math.max(0, ((v - low) / span) * 100));

  return (
    <div>
      {label && (
        <div className="flex items-baseline justify-between">
          <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
            {label}
          </p>
          <p className="font-display text-[1.55rem] leading-none tabular-nums text-[var(--color-ink)]">
            {format(mid)}
          </p>
        </div>
      )}

      <div className={label ? 'mt-3' : undefined}>
        {/* A mark's label grows toward the wider side of its tick, so it always fits. */}
        {marks.map((m) => (
          <p key={m.label} className="relative h-4 text-[0.65rem] text-[var(--color-ink-3)]">
            <span
              className={`absolute top-0 whitespace-nowrap border-[var(--color-ink-2)] ${at(m.value) <= 50 ? 'border-l pl-1' : 'border-r pr-1 -translate-x-full'}`}
              style={{ left: `${at(m.value)}%` }}
            >
              {m.label} <span className="font-mono tabular-nums text-[var(--color-ink-2)]">{format(m.value)}</span>
              {(m.value < low || m.value > high) && ' · mimo pásmo'}
            </span>
          </p>
        ))}
        <div className={`${marks.length > 0 ? 'mt-1 ' : ''}relative h-2`}>
          <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-[var(--color-rule-strong)]" />
          <span
            className="absolute top-0 left-0 w-px h-2 bg-[var(--color-ink-3)]"
            aria-hidden
          />
          <span
            className="absolute top-0 right-0 w-px h-2 bg-[var(--color-ink-3)]"
            aria-hidden
          />
          <span
            className="absolute top-[-1px] w-[2px] h-3 bg-[var(--color-copper)]"
            style={{ left: `calc(${midPct}% - 1px)` }}
            aria-hidden
          />
          {marks.map((m) => (
            <span key={m.label} className="absolute top-[-2px] w-px h-3 bg-[var(--color-ink-2)]"
              style={{ left: `calc(${at(m.value)}% - 0.5px)` }} aria-hidden />
          ))}
        </div>
      </div>

      {/* The ends float to the ruler's ends and the caption centres between them; with no room there (a
        * phone card) it drops under them, still centred, so the three never run together. */}
      <div className="mt-1.5 flow-root text-center text-[0.7rem] tabular-nums text-[var(--color-ink-3)] font-mono">
        <span className="float-left mr-3">{format(low)}</span>
        <span className="float-right ml-3">{format(high)}</span>
        <span className="inline-block text-[0.65rem] tracking-wide uppercase text-[var(--color-ink-4)]">
          {caption}
        </span>
      </div>
    </div>
  );
}
