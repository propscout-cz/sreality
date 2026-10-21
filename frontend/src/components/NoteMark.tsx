/* The note mark (MS16): how many notes the caller's account holds on a property,
 * on every Browse card, from ONE whole-set read shared by all of them.
 * No notes, no mark; a failed read is a retry, never "no notes". */

import { useQuery } from '@tanstack/react-query';

import { PencilIcon } from '@/components/icons';
import ReadFailedMark, { readFailed } from '@/components/ReadFailedMark';
import { curationKeys, fetchNoteCounts } from '@/lib/queries';

export default function NoteMark({ property_id }: { property_id: number }) {
  const q = useQuery({
    queryKey: curationKeys.noteCounts,
    queryFn: fetchNoteCounts,
    staleTime: 30_000,
  });
  if (readFailed(q)) {
    return <ReadFailedMark what="Poznámky" onRetry={() => void q.refetch()} />;
  }
  const count = q.data?.get(property_id) ?? 0;
  if (count === 0) return null;
  const label = `Poznámky: ${count}`;
  return (
    <span
      title={label}
      className="flex h-6 items-center gap-0.5 rounded-[var(--radius-xs)] border border-[var(--color-rule)] bg-[var(--color-paper-3)]/90 px-1 text-[var(--color-ink-2)] backdrop-blur"
    >
      <PencilIcon className="h-3 w-3 shrink-0" strokeWidth={1.1} />
      <span aria-hidden className="font-mono text-[0.6rem] font-medium tabular-nums">
        {count}
      </span>
      <span className="sr-only">{label}</span>
    </span>
  );
}
