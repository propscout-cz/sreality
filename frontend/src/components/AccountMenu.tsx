import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '@/lib/auth';
import { ROUTES } from '@/lib/routes';
import { readDesktopView, switchDesktopView } from '@/lib/phoneShell';
import { COLOR_SCHEMES, useScheme, useTheme, type ThemeMode } from '@/lib/theme';
import SchemeSwatch from './SchemeSwatch';

const MODES: ReadonlyArray<{ value: ThemeMode; label: string }> = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
];

/* Additive UI only — does not gate anything. Anonymous users see a "Sign in"
 * link; logged-in users see who they are + a sign-out affordance. */
export default function AccountMenu() {
  const { user, loading, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  /* Set from the phone shell's account sheet ("Desktopová verze"); this is the
   * way back, and it shows only on a device that took that switch. */
  const [desktopView] = useState(readDesktopView);
  const [scheme, setScheme] = useScheme();
  const [mode, setMode] = useTheme();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  if (loading) return null;

  if (!user) {
    return (
      <Link
        to={ROUTES.login.build()}
        className="inline-block px-3 py-1.5 text-sm tracking-wide rounded-[var(--radius-xs)] border border-[var(--color-chrome-ink-2)]/40 text-[var(--color-chrome-ink-2)] hover:text-[var(--color-chrome-ink)] hover:bg-[var(--color-chrome-active)]"
      >
        Sign in
      </Link>
    );
  }

  const label = user.email ?? 'Account';

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        className={[
          'flex items-center justify-center w-8 h-8 rounded-full transition-colors',
          /* It sits on the top bar, so it draws in the bar's own tokens. */
          open
            ? 'bg-[var(--color-chrome-accent)] text-[var(--color-chrome)]'
            : 'bg-[var(--color-chrome-active)] text-[var(--color-chrome-ink)] hover:bg-[var(--color-chrome-accent)] hover:text-[var(--color-chrome)]',
        ].join(' ')}
      >
        <UserIcon />
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+4px)] z-30 w-[15.5rem] rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper)] py-1 shadow-lg"
        >
          <div className="px-3 py-1.5 text-[0.7rem] tracking-wide text-[var(--color-ink-4)] truncate">
            Signed in as {label}
          </div>
          <div className="my-1 h-px bg-[var(--color-rule)]" aria-hidden />
          {/* Every account picks its own look; the choice stays on this device. */}
          <div role="group" aria-label="Colour scheme" className="px-3 pt-1 pb-1.5">
            <p className="text-[0.7rem] tracking-wide text-[var(--color-ink-3)]">Colour scheme</p>
            <div className="mt-1.5 grid grid-cols-3 gap-1">
              {COLOR_SCHEMES.map((s) => (
                <button
                  key={s.value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={scheme === s.value}
                  onClick={() => setScheme(s.value)}
                  className={[
                    'flex flex-col items-center gap-1 rounded-[var(--radius-sm)] border px-1 py-1.5 text-[0.75rem]',
                    scheme === s.value
                      ? 'border-[var(--color-copper)] bg-[var(--color-copper-soft)] text-[var(--color-copper)] font-medium'
                      : 'border-[var(--color-rule)] text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)] hover:text-[var(--color-ink)]',
                  ].join(' ')}
                >
                  <SchemeSwatch colors={s.swatch} />
                  {s.label}
                </button>
              ))}
            </div>
          </div>
          <div role="group" aria-label="Light or dark" className="px-3 pb-2">
            <div className="grid grid-cols-3 gap-0.5 rounded-[var(--radius-sm)] bg-[var(--color-inset)] p-0.5">
              {MODES.map((m) => (
                <button
                  key={m.value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={mode === m.value}
                  onClick={() => setMode(m.value)}
                  className={[
                    'h-6 rounded-[var(--radius-xs)] text-[0.75rem]',
                    mode === m.value
                      ? 'bg-[var(--color-paper-3)] text-[var(--color-ink)] font-medium shadow-sm'
                      : 'text-[var(--color-ink-2)] hover:text-[var(--color-ink)]',
                  ].join(' ')}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>
          <div className="my-1 h-px bg-[var(--color-rule)]" aria-hidden />
          {desktopView && (
            <button
              type="button"
              role="menuitem"
              onClick={() => switchDesktopView(false)}
              className="block w-full px-3 py-1.5 text-left text-[0.8rem] text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
            >
              Mobilní verze
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void signOut();
            }}
            className="block w-full px-3 py-1.5 text-left text-[0.8rem] text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)] hover:text-[var(--color-brick)]"
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}

function UserIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 14 14" fill="currentColor" aria-hidden>
      <circle cx="7" cy="4.2" r="2.6" />
      <path d="M1 13c0-3 2.7-5 6-5s6 2 6 5" />
    </svg>
  );
}
