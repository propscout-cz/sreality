/* The single app-wide toast surface. Mounted once in Shell; renders whatever
 * lib/toast.ts holds. Palette uses existing civic-archive tokens only. */

import { useToasts, dismissToast, type Toast, type ToastKind } from '@/lib/toast';
import { TAB_BAR_SPACE } from '@/lib/phoneShell';

const TONE: Record<ToastKind, string> = {
  err: 'border-[var(--color-brick)]/30 bg-[var(--color-brick-soft)] text-[var(--color-brick)]',
  ok: 'border-[var(--color-sage)]/30 bg-[var(--color-sage)]/10 text-[var(--color-sage)]',
  info: 'border-[var(--color-copper)]/30 bg-[var(--color-copper-soft)] text-[var(--color-copper)]',
};

/* The phone frame (#1762): a full-width paper slip just above the tab bar, ink
 * text and a copper action — colour there means the stage, so only an error
 * keeps its tone. */
const PHONE_TONE: Record<ToastKind, string> = {
  err: TONE.err,
  ok: 'border-[var(--color-rule-strong)] bg-[var(--color-paper-3)] text-[var(--color-ink)]',
  info: 'border-[var(--color-rule-strong)] bg-[var(--color-paper-3)] text-[var(--color-ink)]',
};

export default function ToastViewport({
  placement = 'corner',
}: {
  /* `corner` is the desktop shell's; `phone` sits over the phone tab bar. */
  placement?: 'corner' | 'phone';
}) {
  const toasts = useToasts();
  if (toasts.length === 0) return null;
  if (placement === 'phone') return <PhoneToasts toasts={toasts} />;
  return (
    <div
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm"
      role="status"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex items-start gap-3 p-3 rounded-[var(--radius-sm)] border text-sm shadow-sm ${TONE[t.kind]}`}
        >
          <span className="flex-1 break-words">{t.message}</span>
          {t.action && (
            <button
              type="button"
              onClick={t.action.onClick}
              className="shrink-0 underline underline-offset-2 hover:opacity-80"
            >
              {t.action.label}
            </button>
          )}
          <button
            type="button"
            onClick={() => dismissToast(t.id)}
            className="shrink-0 leading-none opacity-60 hover:opacity-100"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

function PhoneToasts({ toasts }: { toasts: ReadonlyArray<Toast> }) {
  return (
    <div
      className="fixed inset-x-4 z-50 flex flex-col gap-2"
      style={{ bottom: `calc(${TAB_BAR_SPACE} + 8px)` }}
      role="status"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex min-h-11 items-center justify-between gap-2 rounded-[var(--radius-md)] border py-1 pl-3 pr-1 text-[13px] ${PHONE_TONE[t.kind]}`}
        >
          <span className="min-w-0 flex-1 break-words">{t.message}</span>
          {t.action && (
            <button
              type="button"
              onClick={t.action.onClick}
              className="h-9 shrink-0 px-3 font-medium text-[var(--color-copper)]"
            >
              {t.action.label}
            </button>
          )}
          <button
            type="button"
            onClick={() => dismissToast(t.id)}
            className="h-9 w-9 shrink-0 leading-none text-[var(--color-ink-3)]"
            aria-label="Zavřít"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
