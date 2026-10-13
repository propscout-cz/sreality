/* The phone pipeline's loading face (#1768): the strip's place and three card
 * rows. Its own module so pages/Pipeline can show it while the PhonePipeline
 * chunk loads AND PhonePipeline while the board reads — one face, no jump. */

export default function PhonePipelineSkeleton() {
  return (
    <div aria-busy>
      <p className="sr-only" role="status">
        Načítám pipeline…
      </p>
      <div aria-hidden className="flex h-12 items-center gap-4 border-b border-[var(--color-rule)] px-4">
        {[72, 96, 64].map((w) => (
          <span key={w} className="h-4 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" style={{ width: w }} />
        ))}
      </div>
      <div aria-hidden className="h-[52px]" />
      {[0, 1, 2].map((i) => (
        <div aria-hidden key={i} className="flex gap-3 border-t border-[var(--color-rule)] py-3 pl-4 pr-4">
          <div className="aspect-[4/3] w-[104px] shrink-0 animate-pulse rounded-[var(--radius-sm)] bg-[var(--color-inset)] motion-reduce:animate-none" />
          <div className="flex flex-1 flex-col gap-2 pt-1">
            <div className="h-5 w-32 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
            <div className="h-3.5 w-24 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
            <div className="h-3.5 w-40 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          </div>
        </div>
      ))}
    </div>
  );
}
