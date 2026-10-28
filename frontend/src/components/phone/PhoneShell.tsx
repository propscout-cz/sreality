/* The phone frame around the routes (#1766, design #1762): a 3.5 rem header with
 * the page's title, a back affordance off the tab roots, the bell (the unread
 * count, opening Notifications; notifications push W5a, ruling R4) and the
 * account button, whose sheet holds the theme, this iPhone's push switch (W5b),
 * the desktop view and sign-out; a bottom tab bar for the six areas (W1's four,
 * Odkaz, W2 #1767, and Prohlídky). Shell.tsx picks this frame or the desktop one by
 * `usePhoneShell()`; the route body is the same either way.
 *
 * THE HEADER IS EXACTLY `h-14`, as the desktop TopBar is (Shell.tsx explains
 * why): Filters, BrowseExperience and Pipeline pin panes at `top-14` /
 * `calc(100dvh-3.5rem)`, and those desktop pages still render inside this
 * frame on the phone. */

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation, useNavigate, useNavigationType } from 'react-router-dom';

import CollectionMark from '@/components/CollectionMark';
import SkipLink from '@/components/SkipLink';
import { SegmentedBar } from '@/components/controls';
import { FunnelIcon } from '@/components/icons';
import { useAuth } from '@/lib/auth';
import { APP_SHORT } from '@/lib/brand';
import { czPlural } from '@/lib/format';
import { useTitleSegment } from '@/lib/pageTitle';
import {
  PHONE_TABS,
  TAB_BAR_INSET,
  TAB_BAR_SPACE,
  isPhoneRoot,
  phoneTabFor,
  switchDesktopView,
  type PhoneTab,
  type PhoneTabKey,
} from '@/lib/phoneShell';
import { ROUTES } from '@/lib/routes';
import { COLOR_SCHEMES, THEME_MODES, useScheme, useTheme, type ThemeMode } from '@/lib/theme';
import SchemeSwatch from '@/components/SchemeSwatch';
import { useUnreadCount } from '@/lib/unreadCount';
import { MAIN_ID } from '@/lib/useRouteFocus';

import BottomSheet, { SheetContext } from './BottomSheet';
import { BackIcon, BellIcon, CalendarIcon, LinkIcon, PersonIcon, RollIcon } from './phoneIcons';
import PushSwitch from './PushSwitch';

/* A short line a page may set beside the header's title (the feed's "5 nových
 * dnes"). Cleared when the page unmounts. A no-op outside the phone frame. */
const AsideSetterContext = createContext<(text: string | null) => void>(() => {});

export function usePhoneHeaderAside(text: string | null): void {
  const set = useContext(AsideSetterContext);
  useEffect(() => {
    set(text);
    return () => set(null);
  }, [set, text]);
}

const NON_TEXT_INPUTS = new Set([
  'button', 'checkbox', 'color', 'file', 'hidden', 'image', 'radio', 'range', 'reset', 'submit',
]);

export const isTextEntry = (el: Element | null): boolean =>
  el instanceof HTMLTextAreaElement ||
  el instanceof HTMLSelectElement ||
  (el instanceof HTMLInputElement && !NON_TEXT_INPUTS.has(el.type)) ||
  (el instanceof HTMLElement && el.isContentEditable);

/* True while a text field has focus: the tab bar steps aside for the keyboard
 * (#1763), which on iOS would otherwise push it up over the field. */
export function useTypingFocus(): boolean {
  const [typing, setTyping] = useState(false);
  useEffect(() => {
    const onIn = (e: FocusEvent) => setTyping(isTextEntry(e.target as Element | null));
    /* On focusout the next element is not focused yet; read it a frame later. */
    const onOut = () => requestAnimationFrame(() => setTyping(isTextEntry(document.activeElement)));
    document.addEventListener('focusin', onIn);
    document.addEventListener('focusout', onOut);
    return () => {
      document.removeEventListener('focusin', onIn);
      document.removeEventListener('focusout', onOut);
    };
  }, []);
  return typing;
}

export default function PhoneFrame({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const navType = useNavigationType();
  const typing = useTypingFocus();
  const [aside, setAside] = useState<string | null>(null);

  /* The tab a page outside the four areas (a property, a building) was opened
   * from stays marked and names the back button, as the prototype does. */
  const owner = phoneTabFor(pathname);
  const lastTab = useRef<PhoneTabKey>('feed');
  if (owner) lastTab.current = owner.key;
  const marked = PHONE_TABS.find((t) => t.key === lastTab.current) ?? PHONE_TABS[0];

  /* The page scrolls the window here, so a pushed page would open at the
   * previous one's depth; a POP keeps the browser's (or the feed's) restore. */
  useEffect(() => {
    if (navType !== 'POP') window.scrollTo(0, 0);
  }, [pathname, navType]);

  return (
    <AsideSetterContext.Provider value={setAside}>
      <div className="min-h-dvh flex flex-col bg-[var(--color-paper)] text-[var(--color-ink)]">
        <SkipLink />
        <PhoneHeader root={isPhoneRoot(pathname)} tab={owner} backTab={marked} aside={aside} />
        <main
          id={MAIN_ID}
          tabIndex={-1}
          className="flex-1 outline-none"
          style={{ paddingBottom: TAB_BAR_SPACE }}
        >
          {children}
        </main>
        <TabBar pathname={pathname} marked={marked} hidden={typing} />
      </div>
    </AsideSetterContext.Provider>
  );
}

function PhoneHeader({
  root,
  tab,
  backTab,
  aside,
}: {
  root: boolean;
  tab: PhoneTab | null;
  backTab: PhoneTab;
  aside: string | null;
}) {
  const navigate = useNavigate();
  const segment = useTitleSegment();
  const unread = useUnreadCount();
  const [accountOpen, setAccountOpen] = useState(false);
  const title = root && tab ? tab.label : (segment ?? APP_SHORT);

  const back = () => {
    /* BrowserRouter numbers its entries; 0 means this tab opened on a deep
     * link (or the installed app launched here), and "back" would leave the app. */
    const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0;
    if (idx > 0) navigate(-1);
    else navigate(ROUTES.feed.build());
  };

  return (
    <header className="sticky top-0 z-30 border-b border-[var(--color-rule)] bg-[var(--color-paper)]">
      <div className={`h-14 flex items-center gap-2 pr-3 ${root ? 'pl-4' : 'pl-1'}`}>
        {!root && (
          <button
            type="button"
            onClick={back}
            className="flex h-11 shrink-0 items-center gap-0.5 pl-1 pr-2 text-[15px] text-[var(--color-copper)]"
          >
            <BackIcon className="h-[22px] w-[22px]" />
            <span>{backTab.label}</span>
          </button>
        )}
        <h1
          className={[
            'min-w-0 flex-1 truncate font-display font-medium text-[var(--color-ink)]',
            root ? 'text-[22px] leading-tight tracking-[-0.015em]' : 'text-[17px] leading-snug',
          ].join(' ')}
        >
          {title}
        </h1>
        {aside && (
          <span className="shrink-0 font-mono text-[12px] tabular-nums text-[var(--color-ink-3)]">
            {aside}
          </span>
        )}
        {unread.visible && <NotificationBell count={unread.count} />}
        <button
          type="button"
          onClick={() => setAccountOpen(true)}
          aria-haspopup="dialog"
          aria-label="Účet"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--color-copper-soft)] text-[var(--color-copper)]"
        >
          <PersonIcon className="h-5 w-5" />
        </button>
      </div>
      {accountOpen && <AccountSheet onClose={() => setAccountOpen(false)} />}
    </header>
  );
}

/* The way to the notifications feed: Shell's one unread count (lib/unreadCount),
 * the number the desktop badge and the app icon show, on a copper tally (copper
 * = unread, as the feed's own dots) cut from the glyph by a paper rim. Hidden
 * with the feed where the plan hides it. /notifications belongs to no tab, so
 * the page opens with the header's back affordance, as a property does. */
function NotificationBell({ count }: { count: number }) {
  const { pathname } = useLocation();
  const to = ROUTES.notifications.build();
  const here = pathname === to;
  return (
    <Link
      to={to}
      aria-label={
        count > 0
          ? `Upozornění: ${count} ${czPlural(count, 'nepřečtené', 'nepřečtená', 'nepřečtených')}`
          : 'Upozornění'
      }
      aria-current={here ? 'page' : undefined}
      className={[
        'relative flex h-11 w-11 shrink-0 items-center justify-center',
        here ? 'text-[var(--color-ink)]' : 'text-[var(--color-ink-2)] active:text-[var(--color-ink)]',
      ].join(' ')}
    >
      <BellIcon className="h-[22px] w-[22px]" />
      {count > 0 && (
        <span
          aria-hidden
          className="absolute left-[22px] top-[5px] flex h-[18px] min-w-[18px] items-center justify-center rounded-full border-[1.5px] border-[var(--color-paper)] bg-[var(--color-copper)] px-[3px] font-mono text-[10px] font-medium leading-none tabular-nums text-[var(--color-paper)]"
        >
          {count > 99 ? '99+' : count}
        </span>
      )}
    </Link>
  );
}

const TAB_ICON: Record<PhoneTabKey, ReactNode> = {
  feed: <RollIcon className="h-[22px] w-[22px]" />,
  pipeline: <FunnelIcon className="h-[22px] w-[22px]" />,
  viewings: <CalendarIcon className="h-[22px] w-[22px]" />,
  collections: <CollectionMark className="h-[22px] w-[22px]" />,
  brokers: <PersonIcon className="h-[22px] w-[22px]" />,
  link: <LinkIcon className="h-[22px] w-[22px]" />,
};

function TabBar({ pathname, marked, hidden }: { pathname: string; marked: PhoneTab; hidden: boolean }) {
  return (
    <nav
      aria-label="Hlavní navigace"
      hidden={hidden}
      className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--color-rule-strong)] bg-[var(--color-paper)]"
      style={{ paddingBottom: TAB_BAR_INSET }}
    >
      <div className="grid h-14 grid-cols-6">
        {PHONE_TABS.map((t) => {
          const owns = t.owns(pathname);
          const on = owns || t.key === marked.key;
          return (
            <Link
              key={t.key}
              to={t.to}
              /* "page" on the tab's own pages; "true" (current in the set) on a
               * page opened from it, which belongs to no tab. */
              aria-current={owns ? 'page' : on ? 'true' : undefined}
              onClick={() => {
                if (pathname === t.to) window.scrollTo({ top: 0, behavior: 'smooth' });
              }}
              className={[
                'relative flex flex-col items-center justify-center gap-1 text-center text-[10.5px] font-medium tracking-[0.01em] transition-colors',
                on
                  ? 'text-[var(--color-ink)] before:absolute before:-top-px before:left-1/2 before:-ml-2.5 before:h-0.5 before:w-5 before:bg-[var(--color-copper)]'
                  : 'text-[var(--color-ink-3)] active:text-[var(--color-ink)]',
              ].join(' ')}
            >
              {TAB_ICON[t.key]}
              <span>{t.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

/* The shared modes in the sheet's Czech. */
const THEME_LABEL: Record<ThemeMode, string> = { light: 'Světlý', dark: 'Tmavý', system: 'Systém' };
const THEMES = THEME_MODES.map((m) => ({ ...m, label: THEME_LABEL[m.value] }));

function AccountSheet({ onClose }: { onClose: () => void }) {
  const { user, signOut, isAdmin } = useAuth();
  const [mode, setMode] = useTheme();
  const [scheme, setScheme] = useScheme();
  /* Push is the bell's other half: where the plan hides the feed, no switch. */
  const { visible: notifications } = useUnreadCount();
  return (
    <BottomSheet
      label="Účet"
      onClose={onClose}
      context={user?.email ? <SheetContext text={user.email} /> : undefined}
    >
      <div className="px-4 pb-1 pt-3">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]">
          Barevné schéma
        </p>
        <div role="radiogroup" aria-label="Barevné schéma" className="mt-2 grid grid-cols-3 gap-2">
          {COLOR_SCHEMES.map((sc) => (
            <button
              key={sc.value}
              type="button"
              role="radio"
              aria-checked={scheme === sc.value}
              onClick={() => setScheme(sc.value)}
              className={[
                'flex h-14 flex-col items-center justify-center gap-1.5 rounded-[var(--radius-sm)] border text-[13px]',
                scheme === sc.value
                  ? 'border-[var(--color-copper)] bg-[var(--color-copper-soft)] font-medium text-[var(--color-copper)]'
                  : 'border-[var(--color-rule-strong)] text-[var(--color-ink-2)]',
              ].join(' ')}
            >
              <SchemeSwatch colors={sc.swatch} />
              {sc.label}
            </button>
          ))}
        </div>
      </div>
      <div className="px-4 pb-1 pt-3">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]">
          Vzhled
        </p>
        <div className="mt-2">
          <SegmentedBar tone="phone" fill label="Vzhled" options={THEMES} value={mode} onChange={setMode} />
        </div>
      </div>
      <div className="mt-3 border-t border-[var(--color-rule-soft)]">
        {notifications && <PushSwitch />}
        {/* The phone has no Admin menu, and system alerts never reach the bell
          * (ruling R2b), so this row is its one way to Health; admin-only, as
          * the page is. */}
        {isAdmin && (
          <Link
            to={ROUTES.health.build()}
            onClick={onClose}
            className="flex h-12 w-full items-center border-b border-[var(--color-rule-soft)] px-4 text-[15px] text-[var(--color-ink-2)] active:bg-[var(--color-paper-2)]"
          >
            Health
          </Link>
        )}
        <button
          type="button"
          onClick={() => switchDesktopView(true)}
          className="flex w-full flex-col items-start justify-center gap-0.5 px-4 py-3 text-left active:bg-[var(--color-paper-2)]"
        >
          <span className="text-[15px] text-[var(--color-ink-2)]">Desktopová verze</span>
          <span className="text-[12px] text-[var(--color-ink-3)]">
            Na tomto zařízení celé rozhraní jako na počítači. Zpět přes účet → Mobilní verze.
          </span>
        </button>
        <button
          type="button"
          onClick={() => {
            onClose();
            void signOut();
          }}
          className="flex h-12 w-full items-center border-t border-[var(--color-rule-soft)] px-4 text-left text-[15px] text-[var(--color-ink-2)] active:bg-[var(--color-brick-soft)] active:text-[var(--color-brick)]"
        >
          Odhlásit
        </button>
      </div>
    </BottomSheet>
  );
}
