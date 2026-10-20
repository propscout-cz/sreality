/* Name + colour dialog for saving / updating / editing a saved set (a Browse
 * filter preset, a Stats dashboard — components/SavedSetBar).
 *
 * One small modal serves all flows (the bar owns which one is open and what to
 * do on submit); the words are the instance's. The colour swatches reuse the
 * shared tag palette (TAG_COLORS) so saved sets and tags speak one colour
 * vocabulary. A save or an update may offer ONE checkbox (`extra`): presets ask
 * whether the current map area is part of the preset, dashboards whether the
 * Browse filter is saved with the charts; the metadata-only edit flow never
 * offers it (the stored blob is untouched). Modelled on CreateWatchdogModal for
 * visual consistency. */

import { useEffect, useId, useRef, useState } from 'react';

import { type TagColor } from '@/lib/types';
import Dialog from '@/components/Dialog';
import TagColorPicker from '@/components/TagColorPicker';

/* The instance's words for the dialog chrome. */
export interface SaveModalWords {
  /* The small-caps eyebrow above the title ("Filter preset", "Dashboard"). */
  kind: string;
  nameLabel: string;
  namePlaceholder: string;
  color: string;
  cancel: string;
  saving: string;
}

export interface SavedSetSaveModalProps {
  words: SaveModalWords;
  title: string;
  initialName: string;
  initialColor: TagColor | null;
  submitLabel: string;
  /* The one checkbox this save offers, with its starting state; null = none. */
  extra: { label: string; initial: boolean } | null;
  busy: boolean;
  error: string | null;
  onSubmit: (name: string, extra: boolean, color: TagColor | null) => void;
  onClose: () => void;
}

export default function SavedSetSaveModal({
  words,
  title,
  initialName,
  initialColor,
  submitLabel,
  extra,
  busy,
  error,
  onSubmit,
  onClose,
}: SavedSetSaveModalProps) {
  const [name, setName] = useState(initialName);
  const [checked, setChecked] = useState(extra?.initial ?? false);
  const [color, setColor] = useState<TagColor | null>(initialColor);
  const inputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();

  /* Focus is the primitive's (`initialFocus` below). What this adds is the
   * SELECTION, so the suggested name can be typed straight over. Mount-only:
   * a dependency here is the focus-theft bug lib/useDialog describes. */
  useEffect(() => {
    inputRef.current?.select();
  }, []);

  const trimmed = name.trim();
  const submit = () => {
    if (trimmed.length === 0 || busy) return;
    onSubmit(trimmed, extra != null && checked, color);
  };

  /* Escape, the focus trap, initial + restored focus, the backdrop click and
   * the body scroll lock all come from <Dialog> (lib/useDialog.ts). */
  return (
    <Dialog open onClose={onClose} labelledBy={titleId} initialFocus={inputRef} className="w-full max-w-md p-5">
      <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
        {words.kind}
      </p>
      <h2
        id={titleId}
        className="mt-1 text-xl leading-tight"
        style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
      >
        {title}
      </h2>

      <label className="mt-4 block">
        <span className="text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)]">
          {words.nameLabel}
        </span>
        <input
          ref={inputRef}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit();
          }}
          placeholder={words.namePlaceholder}
          className="mt-1 w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink)] focus-visible:border-[var(--color-copper)]"
        />
      </label>

      <div className="mt-4">
        <span className="text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)]">
          {words.color}
        </span>
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <TagColorPicker
            value={color}
            onChange={setColor}
            showNull
            size="md"
            ringOffsetVar="var(--color-paper)"
          />
        </div>
      </div>

      {extra ? (
        <button
          type="button"
          onClick={() => setChecked((v) => !v)}
          aria-pressed={checked}
          className="mt-3 flex w-full items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2 text-left transition-colors hover:border-[var(--color-rule-strong)]"
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
          <span className="text-[0.8rem] text-[var(--color-ink-2)]">
            {extra.label}
          </span>
        </button>
      ) : null}

      {error ? (
        <p className="mt-3 text-[0.8rem] text-[var(--color-brick)]">{error}</p>
      ) : null}

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
          onClick={submit}
          disabled={trimmed.length === 0 || busy}
          className="px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] transition-colors disabled:opacity-50"
        >
          {busy ? words.saving : submitLabel}
        </button>
      </div>
    </Dialog>
  );
}
