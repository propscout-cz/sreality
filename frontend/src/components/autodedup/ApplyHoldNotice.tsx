/* AUTODEDUP · why the engine left a proposed group unmerged.
 *
 * The engine decided these adverts are one property and the apply step did not
 * merge them. The reason is apply's own newest ledger row for the group, so the
 * card says what the engine saw, with the properties it would have merged.
 */

import { Link } from 'react-router-dom';

import { type AutodedupApplyHold } from '@/lib/api';
import { ROUTES } from '@/lib/routes';

/* The filter's options, in the order the operator reviews them. A reason not
 * listed here still renders on its card, under its stored name. */
export const HOLD_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'any', label: 'všechny nesloučené' },
  { value: 'carries_legacy_merged_listing', label: 'zbytky po starém enginu' },
  { value: 'category_type_mix', label: 'prodej × pronájem' },
  { value: 'category_main_incompatible', label: 'různý druh nemovitosti' },
  { value: 'refused', label: 'odmítnuto při slučování' },
  { value: 'failed', label: 'chyba při slučování' },
  { value: 'property_spans_groups', label: 'staré pravidlo: nemovitost ve více skupinách' },
  { value: 'carries_ungrouped_listings', label: 'staré pravidlo: inzeráty mimo skupinu' },
];
export const HOLD_VALUES: readonly string[] = HOLD_OPTIONS.map((o) => o.value);

const REASON_TEXT: Record<string, string> = {
  carries_legacy_merged_listing:
    'Nesloučeno: některá z nemovitostí obsahuje inzeráty, které k ní přidal starý engine. ' +
    'Vaše „Stejné“ na této skupině ji uvolní a engine ji sloučí.',
  category_type_mix: 'Nesloučeno: skupina míchá prodej a pronájem.',
  category_main_incompatible: 'Nesloučeno: skupina míchá druhy nemovitostí, které se neslučují.',
  member_out_of_scope: 'Nesloučeno: některý inzerát leží mimo oblast, kde engine slučuje.',
  unattached_member: 'Nesloučeno: některý inzerát zatím nemá nemovitost.',
  property_not_active: 'Nesloučeno: některá z nemovitostí už není aktivní.',
  operator_group_verdict: 'Nesloučeno: skupinu jste označili jinak než Stejné.',
  operator_pair_verdict: 'Nesloučeno: dvojici ve skupině jste označili jako různé.',
  must_not_link: 'Nesloučeno: dvojici ve skupině jste zakázali sloučit.',
  refused_at_chokepoint_before: 'Nesloučeno: sloučení už dříve odmítla kontrola kategorií.',
  changed_since_plan: 'Nesloučeno: skupina se změnila během slučování, engine ji přečte znovu.',
  property_spans_groups:
    'Nesloučeno podle starého pravidla. Engine skupinu přečte znovu podle nového.',
  carries_ungrouped_listings:
    'Nesloučeno podle starého pravidla. Engine skupinu přečte znovu podle nového.',
  carries_out_of_scope_listings:
    'Nesloučeno podle starého pravidla. Engine skupinu přečte znovu podle nového.',
};

export function applyHoldText(hold: AutodedupApplyHold): string {
  if (hold.outcome === 'refused') return `Sloučení odmítnuto: ${hold.reason ?? 'bez důvodu'}`;
  if (hold.outcome === 'failed') return `Sloučení selhalo: ${hold.reason ?? 'bez důvodu'}`;
  return REASON_TEXT[hold.reason ?? ''] ?? `Nesloučeno: ${hold.reason ?? hold.outcome}`;
}

export default function ApplyHoldNotice({ hold }: { hold: AutodedupApplyHold | null }) {
  if (!hold) return null;
  const legacy = hold.detail?.legacy_merged_listings ?? [];
  return (
    <p
      role="note"
      data-testid="apply-hold-notice"
      className="rounded-[var(--radius-sm)] border border-[var(--color-ochre)] bg-[var(--color-ochre-soft)] px-3 py-2 text-[0.72rem] leading-relaxed text-[var(--color-ink-2)]"
    >
      <span className="text-[var(--color-ink)]">{applyHoldText(hold)}</span>
      {hold.held_at ? ` ${hold.held_at.slice(0, 10)}.` : ''}
      {hold.property_ids.length > 0 && (
        <>
          {' '}Nemovitosti:{' '}
          {hold.property_ids.map((id, i) => (
            <span key={id}>
              {i > 0 ? ', ' : ''}
              <Link
                to={ROUTES.property.build({ propertyId: id })}
                target="_blank"
                rel="noreferrer"
                className="font-mono tabular-nums underline"
              >
                #{id}
              </Link>
            </span>
          ))}
          .
        </>
      )}
      {legacy.length > 0 && (
        <span className="text-[var(--color-ink-3)]">
          {' '}Inzeráty od starého enginu:{' '}
          <span className="font-mono tabular-nums">{legacy.map((id) => `#${id}`).join(', ')}</span>.
        </span>
      )}
    </p>
  );
}
