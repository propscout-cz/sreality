/* The delegated stamp (migration 641, design §3.5, ruling 5): a note, card or
 * stage last changed by someone acting as its owner says who and when. ONE rule
 * for every surface:
 *
 * - The API's projections (notes, cards) carry `edited_by_email` — shown as is.
 * - The board views carry only `edited_by_account_id`; the SPA names that
 *   account only from `parties`, the accounts GET /pipeline/shares labels for
 *   the caller. On my own board those are my recipients (a delegate who acted
 *   on it); a delegate on a shared board knows only the owner, whose own writes
 *   are never stamped, so there it shows the time alone. A former delegate no
 *   longer listed shows the time alone too.
 * - No `edited_at` = no delegated edit since the owner's own: nothing shown. */

import { fmtAbsolute } from './format';

export interface DelegatedStampSource {
  edited_by_email?: string | null;
  edited_by_account_id?: string | null;
  edited_at?: string | null;
}

/* Desktop pages in English say "edited by"; the board, the phone and the
 * Czech surfaces say "upravil". */
export type StampLang = 'cs' | 'en';

/** Account id → the email the caller may show for it. */
type StampParties = ReadonlyMap<string, string | null>;

/** "upravil petr@example.cz · 20. 10. 2026 14:03", "upraveno · …" when the
 * account cannot be named, or null when there is no delegated edit. */
export function delegatedStamp(
  src: DelegatedStampSource,
  { lang = 'cs', parties }: { lang?: StampLang; parties?: StampParties } = {},
): string | null {
  if (!src.edited_at) return null;
  const time = fmtAbsolute(src.edited_at);
  const who =
    src.edited_by_email ||
    (src.edited_by_account_id ? parties?.get(src.edited_by_account_id) : null) ||
    null;
  if (lang === 'en') return who ? `edited by ${who} · ${time}` : `edited · ${time}`;
  return who ? `upravil ${who} · ${time}` : `upraveno · ${time}`;
}

/** The stamp's hover title: what the line is, and when — null without one. */
export function delegatedStampTitle(src: DelegatedStampSource, lang: StampLang = 'cs'): string | null {
  if (!src.edited_at) return null;
  const time = fmtAbsolute(src.edited_at);
  return lang === 'en'
    ? `Last changed through a pipeline share, ${time}`
    : `Naposledy změněno přes sdílení pipeline, ${time}`;
}

/** Does naming this stamp need the caller's share lists? (Only a view row
 * that names an account and no email.) */
export const stampNeedsParties = (src: DelegatedStampSource): boolean =>
  !!src.edited_at && !src.edited_by_email && !!src.edited_by_account_id;
