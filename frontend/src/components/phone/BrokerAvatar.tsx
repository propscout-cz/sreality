/* A broker's initials in a ring, in Fraunces: the phone's one broker avatar (the
 * leaderboard, the broker page, the property page's strip and Makléři). The letters
 * come from lib/personName, so a degree is never an initial ("Ing. Jan Novák" → JN). */

import { nameInitials } from '@/lib/personName';

const SIZE = {
  sm: 'h-8 w-8 text-[12px]',
  md: 'h-10 w-10 text-[15px]',
  lg: 'h-12 w-12 text-[17px]',
} as const;

export default function BrokerAvatar({
  name,
  size = 'md',
}: {
  name: string | null | undefined;
  size?: keyof typeof SIZE;
}) {
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full border border-[var(--color-rule-strong)] font-display text-[var(--color-ink-2)] ${SIZE[size]}`}
    >
      {nameInitials(name)}
    </span>
  );
}
