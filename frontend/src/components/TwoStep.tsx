/* The quiet two-step destructive control: a text button, then a brick confirm
 * beside Cancel (Delete collection, Stop sharing, Remove from my list, a photo's
 * Smazat). The confirm ignores the second click of a double-click (`detail > 1`):
 * it appears under the cursor, and leaving or deleting cannot be undone here.
 * `touch`: finger-sized (the photo strip); `onDark`: over the photo viewer. */
export function TwoStep({
  idle,
  confirm,
  cancel = 'Cancel',
  confirming,
  setConfirming,
  pending,
  onConfirm,
  label,
  touch = false,
  tone = 'default',
}: {
  idle: string;
  confirm: string;
  cancel?: string;
  confirming: boolean;
  setConfirming: (next: boolean) => void;
  pending: boolean;
  onConfirm: () => void;
  /* Accessible name of the idle button when its text alone is ambiguous in a list. */
  label?: string;
  touch?: boolean;
  tone?: 'default' | 'onDark';
}) {
  const dark = tone === 'onDark';
  const quiet = dark
    ? 'text-white/75 hover:text-white'
    : 'text-[var(--color-ink-3)] hover:text-[var(--color-brick)]';
  const size = touch ? 'h-10 px-3 text-[0.85rem]' : '';
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        aria-label={label}
        className={`${size || 'text-[0.75rem]'} tracking-wide ${quiet} transition-colors`}
      >
        {idle}
      </button>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={(e) => {
          if (e.detail > 1) return;
          onConfirm();
        }}
        disabled={pending}
        className={`${size || 'px-3 py-1 text-[0.75rem]'} tracking-wide rounded-[var(--radius-sm)] ${
          dark
            ? 'bg-[var(--color-brick)] text-white'
            : 'bg-[var(--color-brick-soft)] text-[var(--color-brick)] hover:bg-[var(--color-brick)]/15'
        } disabled:opacity-50`}
      >
        {confirm}
      </button>
      <button
        type="button"
        onClick={() => setConfirming(false)}
        className={`${size || 'px-2.5 py-1 text-[0.7rem]'} tracking-wide ${
          dark ? 'text-white/75 hover:text-white' : 'text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]'
        }`}
      >
        {cancel}
      </button>
    </div>
  );
}
