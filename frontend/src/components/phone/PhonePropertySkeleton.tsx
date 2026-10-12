/* The phone property page's loading face (#1767): the photo's place, the price
 * and two lines, the curation row. Its own module so PropertyDetail can show it
 * while the property reads AND while the PhoneProperty chunk loads — one face,
 * no jump. */

export default function PhonePropertySkeleton() {
  return (
    <div aria-busy>
      <p className="sr-only" role="status">
        Načítám nemovitost…
      </p>
      <div
        aria-hidden
        className="aspect-[4/3] w-full animate-pulse bg-[var(--color-inset)] motion-reduce:animate-none"
      />
      <div aria-hidden className="h-6" />
      <div aria-hidden className="flex flex-col gap-2 px-4 pb-4 pt-2">
        <div className="h-8 w-48 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
        <div className="h-3.5 w-28 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
        <div className="mt-1 h-4 w-64 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
        <div className="h-3.5 w-52 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
      </div>
      <div aria-hidden className="h-16 border-y border-[var(--color-rule)]" />
    </div>
  );
}
