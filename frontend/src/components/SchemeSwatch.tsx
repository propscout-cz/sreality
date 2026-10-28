/* A colour scheme drawn as three bands — canvas, accent, live price — so the
 * picker shows what a scheme looks like before it is chosen. The colours are
 * the scheme's own light values (lib/appearance COLOR_SCHEMES), never the current
 * tokens: every swatch must look like ITS scheme whichever one is on. */
export default function SchemeSwatch({ colors }: { colors: readonly [string, string, string] }) {
  return (
    <span
      aria-hidden
      className="flex h-3.5 w-10 shrink-0 overflow-hidden rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)]"
    >
      <span className="flex-[2]" style={{ background: colors[0] }} />
      <span className="flex-[2]" style={{ background: colors[1] }} />
      <span className="flex-1" style={{ background: colors[2] }} />
    </span>
  );
}
