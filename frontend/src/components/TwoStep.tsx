/* The quiet two-step destructive control: a text button, then a brick confirm
 * beside Cancel (Delete collection, Stop sharing, Remove from my list). The
 * confirm ignores the second click of a double-click (`detail > 1`): it appears
 * under the cursor, and leaving or deleting cannot be undone here. */
export function TwoStep({
  idle,
  confirm,
  cancel = 'Cancel',
  confirming,
  setConfirming,
  pending,
  onConfirm,
  label,
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
}) {
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        aria-label={label}
        className="text-[0.75rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-brick)] transition-colors"
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
        className="px-3 py-1 text-[0.75rem] tracking-wide rounded-[var(--radius-sm)] bg-[var(--color-brick-soft)] text-[var(--color-brick)] hover:bg-[var(--color-brick)]/15 disabled:opacity-50"
      >
        {confirm}
      </button>
      <button
        type="button"
        onClick={() => setConfirming(false)}
        className="px-2.5 py-1 text-[0.7rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]"
      >
        {cancel}
      </button>
    </div>
  );
}
