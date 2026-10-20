/* The delegated stamp on a note, a card or a stage (lib/delegatedStamp holds
 * the rule). Renders nothing — and reads nothing — without a delegated edit;
 * a board row that names only an account reads my share lists to name it
 * (never while acting: sharing is always the caller's own). */

import { useMemo } from 'react';

import { useActing } from '@/lib/acting';
import {
  delegatedStamp,
  delegatedStampTitle,
  stampNeedsParties,
  type DelegatedStampSource,
  type StampLang,
} from '@/lib/delegatedStamp';
import { usePipelineShares } from '@/lib/pipelineShares';

const BASE = 'text-[0.68rem] tracking-wide text-[var(--color-ink-3)]';

export default function DelegatedStamp({
  stamp,
  lang = 'cs',
  className = '',
}: {
  stamp: DelegatedStampSource;
  lang?: StampLang;
  className?: string;
}) {
  if (!stamp.edited_at) return null;
  return <StampLine stamp={stamp} lang={lang} className={className} />;
}

function StampLine({
  stamp,
  lang,
  className,
}: {
  stamp: DelegatedStampSource;
  lang: StampLang;
  className: string;
}) {
  const acting = useActing();
  const sharesQ = usePipelineShares(acting == null && stampNeedsParties(stamp));
  const recipients = sharesQ.data?.shared_with;
  const parties = useMemo(
    () =>
      acting == null && recipients
        ? new Map(recipients.map((r) => [r.account_id, r.email] as const))
        : undefined,
    [acting, recipients],
  );
  const text = delegatedStamp(stamp, { lang, parties });
  if (text == null) return null;
  return (
    <p className={`${BASE} ${className}`} title={delegatedStampTitle(stamp, lang) ?? undefined}>
      {text}
    </p>
  );
}
