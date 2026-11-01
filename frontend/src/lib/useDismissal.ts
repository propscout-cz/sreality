/* The one place a property is dismissed or restored from (migration 536) — the
 * Browse cards and the listing header both share this hook.
 *
 * Dismissing is optimistic and list-aware: it is one case of the curation
 * settle (lib/curationSettle, lens 'dismissed') — the property leaves every
 * cached Browse list that hides dismissed properties at once, those lists are
 * NOT refetched, their exact counts drop one, and Stats and the map re-read.
 * Restoring re-reads every Browse read, because a hidden row has to come back
 * (and leave the phone's hidden roll), and so does a FAILED dismissal: its
 * rollback takes back only its own patch (every other pending or confirmed
 * patch stays painted), but a page that landed meanwhile may already hold it.
 * Both writes go through lib/useOptimisticWrite (rollback from `onSettled`,
 * never `onError`).
 */

import { useQuery } from '@tanstack/react-query';

import { actingKey, actOpts, useActAs } from '@/lib/acting';
import { dismissProperty, undismissProperty } from '@/lib/api';
import { browseKeys } from '@/lib/browseKeys';
import { browseReads, settlePatches, settleRereads } from '@/lib/curationSettle';
import { dismissalKeys, fetchIsDismissed, notificationKeys } from '@/lib/queries';
import { dismissToast, pushToast } from '@/lib/toast';
import { cachePatch } from '@/lib/revalidate';
import { useOptimisticWrite } from '@/lib/useOptimisticWrite';

/* One undo offer on screen at a time: an action toast stays until closed (so
 * its button stays reachable), and a triage run would otherwise stack them. */
let undoToast: number | null = null;

/* Acting as an owner (lib/acting) it reads and writes the OWNER's dismissal,
 * under the owner's keys; Browse never acts, so then no Browse read moves. */
export function useDismissal(property_id: number) {
  const actAs = useActAs();
  const stateKey = actingKey(actAs, dismissalKeys.state(property_id));
  const countKey = actingKey(actAs, dismissalKeys.count);
  /* A hidden property's alerts leave the bell and the feed; an owner's dismissal, made
   * acting, moves no bell of mine (lib/curationSync `property_dismissals`). */
  const bell = actAs ? [] : [notificationKeys.all];
  const state = useQuery({
    queryKey: stateKey,
    queryFn: () => fetchIsDismissed(property_id, ...actOpts(actAs)),
    staleTime: 60_000,
  });

  const restore = useOptimisticWrite({
    mutationKey: actingKey(actAs, ['write', 'dismissal', 'restore']),
    mutationFn: () => undismissProperty(property_id, ...actOpts(actAs)),
    patch: () => [cachePatch({ queryKey: stateKey, exact: true }, () => false)],
    revalidate: [stateKey, countKey, ...bell, ...browseReads(actAs, browseKeys.all)],
  });

  const dismiss = useOptimisticWrite({
    mutationKey: actingKey(actAs, ['write', 'dismissal', 'dismiss']),
    mutationFn: () => dismissProperty(property_id, ...actOpts(actAs)),
    patch: (_vars, qc) => [
      cachePatch({ queryKey: stateKey, exact: true }, () => true),
      ...settlePatches(qc, property_id, 'dismissed', actAs),
    ],
    revalidate: (_vars, failed, qc) => [
      stateKey,
      countKey,
      ...bell,
      ...settleRereads(qc, property_id, 'dismissed', failed, actAs),
    ],
    onSuccess: () => {
      if (undoToast != null) dismissToast(undoToast);
      const id = pushToast('info', 'Nemovitost skryta', 0, {
        label: 'Vrátit',
        onClick: () => {
          dismissToast(id);
          restore.mutate();
        },
      });
      undoToast = id;
    },
  });

  return {
    dismissed: state.data ?? null,
    dismiss,
    restore,
    pending: dismiss.isPending || restore.isPending,
  };
}
