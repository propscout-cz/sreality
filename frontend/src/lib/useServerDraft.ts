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
 * `saved()` says the draft went to the server. An editor that stays open after a
 * save (the stage row) calls it, because a change made elsewhere can land between
 * the save and its re-read: the server then never reaches the draft. A saved
 * draft also ends, unfocused, at the first server value other than the one it was
 * saved over, so the field follows whatever the server holds next instead of
 * keeping the saved text to write it back. Typing again unmarks it.
 *
 * `edited()` is what to save: the draft (through `normalize`, which every
 * comparison uses too), or null when there is nothing to write — no draft, no
 * net change, or the server already holds it. A server change is never written
 * back, because nothing the user did not type is ever a draft.
 *
 * `bind` wires an input or a textarea in one spread: its value, its change, its
 * focus and its blur. Only a free-text draft (`T` = string) has one: a field
 * types any string, which a narrowed draft (a colour) would take unchecked; such
 * a draft takes its value through `onChange`. `reset()` drops the draft and the
 * focus with it: call it when the editor opens or closes (a field that unmounts
 * while focused never blurs). */

import { useState, type ChangeEvent } from 'react';

/** What `<input {...draft.bind} />` and `<textarea {...draft.bind} />` take. */
export interface DraftBind {
  value: string;
  onChange(e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>): void;
  onFocus(): void;
  onBlur(): void;
}

export interface ServerDraft<T extends string> {
  /** What the field shows: the draft, else the server's value. */
  value: T;
  /** `value` through `normalize`: what a save would send of it. */
  normalized: T;
  onChange(v: T): void;
  onFocus(): void;
  onBlur(): void;
  /** A free-text draft's field wiring; a narrowed draft has none. */
  bind: string extends T ? DraftBind : never;
  /** What to save, or null: the user changed nothing net, or the server already holds it. */
  edited(): T | null;
  /** The draft went to the server: the next server value, whatever it is, ends it. */
  saved(): void;
  reset(): void;
}

interface Draft<T> {
  value: T;
  base: T;
  /* The server's value when the draft was saved; null while unsaved. */
  savedOver: T | null;
}

interface State<T> {
  draft: Draft<T> | null;
  focused: boolean;
}

const UNTOUCHED = { draft: null, focused: false } as const;

/* A draft the field does not hold on to (not focused) ends when it holds no net
 * change, the server has reached it, or — saved — the server moved since. */
function dropFinishedDraft<T extends string>(s: State<T>, server: T, normalize: (v: T) => T): State<T> {
  const d = s.draft;
  if (!d || s.focused) return s;
  const v = normalize(d.value);
  const now = normalize(server);
  const moved = d.savedOver != null && now !== normalize(d.savedOver);
  return v === normalize(d.base) || v === now || moved ? { draft: null, focused: false } : s;
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
  const current = dropFinishedDraft(state, server, normalize);
  if (current !== state) setState(current);
  const { draft } = current;
  const value = draft ? draft.value : server;

  const onChange = (v: T): void =>
    setState((s) =>
      dropFinishedDraft(
        { focused: s.focused, draft: { value: v, base: s.draft?.base ?? server, savedOver: null } },
        server,
        normalize,
      ),
    );
  const onFocus = (): void => setState((s) => (s.focused ? s : { ...s, focused: true }));
  const onBlur = (): void => setState((s) => dropFinishedDraft({ ...s, focused: false }, server, normalize));
  /* Typed for T = string only (ServerDraft['bind']), so the field's string is a T. */
  const bind: DraftBind = { value, onChange: (e) => onChange(e.target.value as T), onFocus, onBlur };

  return {
    value,
    normalized: normalize(value),
    onChange,
    onFocus,
    onBlur,
    bind: bind as ServerDraft<T>['bind'],
    edited: () => {
      if (!draft) return null;
      const v = normalize(draft.value);
      return v === normalize(draft.base) || v === normalize(server) ? null : v;
    },
    saved: () =>
      setState((s) =>
        s.draft ? dropFinishedDraft({ ...s, draft: { ...s.draft, savedOver: server } }, server, normalize) : s,
      ),
    reset: () => setState(UNTOUCHED),
  };
}
