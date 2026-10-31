/* A field edited over server state: the one draft rule of every curation editor
 * (docs/design/curation-sync.md § 3.5, Decision 10).
 *
 * The defect it ends (D3): an editor seeded its draft from the server ONCE and
 * then compared the draft with the server to decide what to save — so a change
 * made elsewhere while the editor stood open read as the user's edit, and a
 * focus and blur wrote the stale value back over it.
 *
 * THE RULE. Untouched, the field shows the server's value, focused or not, and
 * follows every re-read. The user's first change starts a DRAFT over the value
 * they saw then (its base); from that change on the field shows the draft and a
 * server change never touches it. The draft ends at `reset()`, or — whenever the
 * field is not focused — once it holds no net change (it equals its base) or the
 * server has reached it (the save landed, or the same change came from
 * elsewhere). "Not focused" is at once for a control with no focus of its own (a
 * colour swatch) and at the blur for a text field, so nothing moves under the
 * caret. So a blur that changed nothing net resumes following on the spot,
 * showing whatever the server holds by then, and a saved draft hands the field
 * back to the server when the re-read brings its value: no flash of the old one.
 *
 * `edited()` is what to save: the draft (through `normalize`, which every
 * comparison uses too), or null when there is nothing to write — no draft, no
 * net change, or the server already holds it. A server change is never written
 * back, because nothing the user did not type is ever a draft.
 *
 * `reset()` drops the draft and the focus with it: call it when the editor opens
 * or closes (a field that unmounts while focused never blurs). */

import { useState } from 'react';

export interface ServerDraft<T extends string> {
  /** What the field shows: the draft, else the server's value. */
  value: T;
  onChange(v: T): void;
  onFocus(): void;
  onBlur(): void;
  /** What to save, or null: the user changed nothing net, or the server already holds it. */
  edited(): T | null;
  reset(): void;
}

interface Draft<T> {
  value: T;
  base: T;
}

interface State<T> {
  draft: Draft<T> | null;
  focused: boolean;
}

const UNTOUCHED = { draft: null, focused: false } as const;

/* A draft the field does not hold on to (not focused) ends when it holds no net
 * change or the server has reached it. */
function settle<T extends string>(s: State<T>, server: T, normalize: (v: T) => T): State<T> {
  const d = s.draft;
  if (!d || s.focused) return s;
  const v = normalize(d.value);
  return v === normalize(d.base) || v === normalize(server) ? { draft: null, focused: false } : s;
}

const asIs = <T>(v: T): T => v;

/** The `normalize` of a text field saved trimmed. */
export const trimDraft = (v: string): string => v.trim();

/** A field over `server`: follows it while untouched, keeps the user's draft once they change it. */
export function useServerDraft<T extends string>(
  server: T,
  normalize: (v: T) => T = asIs,
): ServerDraft<T> {
  const [state, setState] = useState<State<T>>(UNTOUCHED);
  // The server moved under a draft the field had let go of: React's "adjust state
  // while rendering" — one extra pass, and the draft is gone before anything paints.
  const settled = settle(state, server, normalize);
  if (settled !== state) setState(settled);
  const { draft } = settled;

  return {
    value: draft ? draft.value : server,
    onChange: (v) =>
      setState((s) => settle({ focused: s.focused, draft: { value: v, base: s.draft?.base ?? server } }, server, normalize)),
    onFocus: () => setState((s) => (s.focused ? s : { ...s, focused: true })),
    onBlur: () => setState((s) => settle({ ...s, focused: false }, server, normalize)),
    edited: () => {
      if (!draft) return null;
      const v = normalize(draft.value);
      return v === normalize(draft.base) || v === normalize(server) ? null : v;
    },
    reset: () => setState(UNTOUCHED),
  };
}
