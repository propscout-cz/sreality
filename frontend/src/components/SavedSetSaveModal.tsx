/* Name + colour dialog for saving / updating / editing a saved set (a Browse
 * filter preset, a Stats dashboard — components/SavedSetBar), in the instance's
 * words. The swatches are the shared tag palette (TAG_COLORS), so saved sets and
 * tags speak one colour vocabulary. A save or an update may offer ONE checkbox:
 * presets ask whether the current map area is part of the preset, dashboards
 * whether the Browse filter is saved with the charts; the metadata-only edit
 * never offers it (the stored blob is untouched). */

import { useEffect, useId, useRef, useState } from 'react';

import { ApiError } from '@/lib/api';
import { type TagColor } from '@/lib/types';
import Dialog from '@/components/Dialog';
import TagColorPicker from '@/components/TagColorPicker';

/* The instance's words for the dialog: `kind` is the small-caps eyebrow above
 * the title ("Filter preset", "Dashboard"); `dialog` / `submit` per flow. */
export type SaveModalWords = Record<'kind' | 'nameLabel' | 'namePlaceholder' | 'color' | 'cancel' | 'saving' | 'failed', string> &
  Record<'dialog' | 'submit', Record<'save' | 'update' | 'edit', string>>;

const CAPTION = 'text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)]';

export interface SavedSetSaveModalProps {
  words: SaveModalWords;
  mode: keyof SaveModalWords['dialog'];
  /* The set being updated or edited; null on a save. */
  initial: { name: string; color: TagColor | null } | null;
  /* The one checkbox this flow offers, with its starting state; null = none. A
   * `disabledHint` greys it out, unticked, and says why under it. */
  checkbox: { label: string; initial: boolean; disabledHint?: string | null } | null;
  /* The write: the dialog stays open and busy until it settles, and says why it failed. */
  onSubmit: (name: string, checked: boolean, color: TagColor | null) => Promise<unknown>;
  onClose: () => void;
}

export default function SavedSetSaveModal({ words, mode, initial, checkbox, onSubmit, onClose }: SavedSetSaveModalProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const off = checkbox?.disabledHint ?? null;
  const [checked, setChecked] = useState(off == null && (checkbox?.initial ?? false));
  const [color, setColor] = useState<TagColor | null>(initial?.color ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();

  /* Focus is the primitive's (`initialFocus` below). What this adds is the
   * SELECTION, so the suggested name can be typed straight over. Mount-only:
   * a dependency here is the focus-theft bug lib/useDialog describes. */
  useEffect(() => {
    inputRef.current?.select();
  }, []);

  const trimmed = name.trim();
  const submit = async () => {
    if (trimmed.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(trimmed, checkbox != null && off == null && checked, color);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : words.failed);
    } finally {
      setBusy(false);
    }
  };

  /* Escape, the focus trap, initial + restored focus, the backdrop click and
   * the body scroll lock all come from <Dialog> (lib/useDialog.ts). */
  return (
    <Dialog open onClose={onClose} labelledBy={titleId} initialFocus={inputRef} className="w-full max-w-md p-5">
      <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">{words.kind}</p>
      <h2 id={titleId} className="mt-1 text-xl leading-tight" style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}>
        {words.dialog[mode]}
      </h2>

      <label className="mt-4 block">
        <span className={CAPTION}>{words.nameLabel}</span>
        <input
          ref={inputRef}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void submit();
          }}
          placeholder={words.namePlaceholder}
          className="mt-1 w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink)] focus-visible:border-[var(--color-copper)]"
        />
      </label>

      <div className="mt-4">
        <span className={CAPTION}>{words.color}</span>
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <TagColorPicker value={color} onChange={setColor} showNull size="md" ringOffsetVar="var(--color-paper)" />
        </div>
      </div>

      {checkbox ? (
        <>
        <button
          type="button"
          onClick={() => setChecked((v) => !v)}
          aria-pressed={checked}
          disabled={off != null}
          aria-describedby={off != null ? `${titleId}-off` : undefined}
          className="mt-3 flex w-full items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2 text-left transition-colors hover:border-[var(--color-rule-strong)] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-[var(--color-rule)]"
        >
          <span
            className={[
              'flex h-4 w-4 shrink-0 items-center justify-center rounded-[3px] border text-[0.6rem]',
              checked
                ? 'border-[var(--color-copper)] bg-[var(--color-copper)] text-white'
                : 'border-[var(--color-rule-strong)] text-transparent',
            ].join(' ')}
            aria-hidden
          >
            ✓
          </span>
          <span className="text-[0.8rem] text-[var(--color-ink-2)]">{checkbox.label}</span>
        </button>
        {off != null ? (
          <p id={`${titleId}-off`} className="mt-1.5 text-[0.75rem] text-[var(--color-ink-3)]">
            {off}
          </p>
        ) : null}
        </>
      ) : null}

      {error ? <p className="mt-3 text-[0.8rem] text-[var(--color-brick)]">{error}</p> : null}

      <div className="mt-5 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="px-3 py-1.5 text-sm rounded-[var(--radius-sm)] border border-[var(--color-rule)] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] hover:border-[var(--color-rule-strong)] transition-colors"
        >
          {words.cancel}
        </button>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={trimmed.length === 0 || busy}
          className="px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] transition-colors disabled:opacity-50"
        >
          {busy ? words.saving : words.submit[mode]}
        </button>
      </div>
    </Dialog>
  );
}
