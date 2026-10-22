/* Odkaz — paste a portal link, land on its property (the Phone app's fifth tab,
 * W2 #1767; #1760 Q14). iOS hands an installed web app no share-sheet route
 * (#1763: `share_target` unsupported, a Shortcut opens Safari), so the paste IS
 * the path: copy the link in the portal's app or Safari, open Odkaz, paste, add.
 *
 * The field takes whatever the share sheet copied — the link alone or a whole
 * message around it; `readPastedLink` pulls out the first URL and runs the
 * extension's portal-id rules for the instant check line and the two refusals
 * that need no server (no link at all, a portal page that is not one ad).
 * Everything else runs the "Nová nemovitost" flow inline (lib/useNewPropertyFlow,
 * docs/design/new-property-from-link.md §3.6): the server reads the link, an ad
 * we lack is fetched and its property born, the card is added, and the tab lands
 * on the property. That flow is admins-only in v1 (ruling 2); anyone else keeps
 * the quick path, a portal link straight to `/listing/:source/:nativeId` with the
 * URL in the navigation state, and any other link gets the server's own W1
 * sentence without a request. The last five links stay on this device as a
 * roll under the field. */

import { useId, useMemo, useRef, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { NewPropertyProgress } from '@/components/NewPropertyDialog';
import { SectionLabel } from '@/components/section';
import { CheckIcon, ChevronIcon, PasteIcon } from '@/components/phone/phoneIcons';
import { useAuth } from '@/lib/auth';
import { fmtShortDate, fmtTime24 } from '@/lib/format';
import {
  readPastedLink,
  readRecentLinks,
  rememberLink,
  type PastedLink,
  type RecentLink,
} from '@/lib/pastedLink';
import { portalBadge, portalLabel } from '@/lib/portals';
import { ROUTES } from '@/lib/routes';
import { isBusy, useNewPropertyFlow } from '@/lib/useNewPropertyFlow';

/* What the property page's alias reads back from the navigation state. */
export interface PastedLinkState {
  pastedUrl: string;
}

/* The server's W1 sentence for a site it does not read: a non-admin cannot ask it. */
const NOT_READ = 'Tuto stránku zatím nečteme.';

/* Why a paste names no listing, in one sentence the operator can act on. Any
 * other link goes to the server, which says what it reads. */
export function pasteProblem(link: Extract<PastedLink, { kind: 'no-url' | 'portal-page' }>): string {
  switch (link.kind) {
    case 'no-url':
      return 'V textu není žádný odkaz. Vložte odkaz na inzerát.';
    case 'portal-page':
      return `Tohle je stránka portálu ${portalLabel(link.source) ?? link.source}, ale ne jeden inzerát. Otevřete inzerát a zkopírujte jeho odkaz.`;
  }
}

export default function LinkPage() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const fieldId = useId();
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [recent, setRecent] = useState<RecentLink[]>(readRecentLinks);
  const pastedUrl = useRef('');
  const flow = useNewPropertyFlow({
    stay: false,
    onDone: (s) => {
      if (s.source && s.source_id_native) {
        setRecent(rememberLink({ url: pastedUrl.current, source: s.source, nativeId: s.source_id_native }));
      }
    },
  });
  const busy = isBusy(flow.step);

  const read = useMemo(() => (text.trim() ? readPastedLink(text) : null), [text]);

  const open = (link: { url: string; source: string; nativeId: string }) => {
    setRecent(rememberLink(link));
    fieldRef.current?.blur();
    navigate(ROUTES.listingCanonical.build({ source: link.source, nativeId: link.nativeId }), {
      state: { pastedUrl: link.url } satisfies PastedLinkState,
    });
  };

  const submit = (value: string) => {
    if (busy) return;
    const link = readPastedLink(value);
    if (link.kind === 'no-url' || link.kind === 'portal-page') {
      setProblem(pasteProblem(link));
      return;
    }
    if (!isAdmin) {
      if (link.kind === 'listing') open(link);
      else setProblem(NOT_READ);
      return;
    }
    pastedUrl.current = link.url;
    fieldRef.current?.blur();
    void flow.start(value);
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit(text);
  };

  const pasteFromClipboard = async () => {
    setProblem(null);
    if (flow.step.kind !== 'idle') flow.cancel();
    try {
      /* iOS asks once ("Vložit" callout); a refusal or an old browser throws. */
      const clip = await navigator.clipboard.readText();
      setText(clip);
      if (!clip.trim()) setProblem('Schránka je prázdná.');
    } catch {
      setProblem('Schránku se nepodařilo přečíst. Podržte prst v poli a zvolte Vložit.');
      fieldRef.current?.focus();
    }
  };

  return (
    <div className="px-4 pb-8 pt-4">
      <form onSubmit={onSubmit} noValidate>
        {/* SectionLabel's type on a <label> (a <p> may not sit inside one). */}
        <label
          htmlFor={fieldId}
          className="block text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--color-ink-3)]"
        >
          Odkaz na inzerát
        </label>
        <textarea
          id={fieldId}
          ref={fieldRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setProblem(null);
            if (flow.step.kind !== 'idle') flow.cancel();
          }}
          rows={3}
          /* Typing would cancel the flow mid-write: the card lands, the tab never says so. */
          disabled={flow.step.kind === 'adding'}
          /* 16 px: iOS zooms the page into any smaller field it focuses. */
          className="mt-2 block min-h-[104px] w-full resize-none rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] p-3 text-[16px] leading-snug text-[var(--color-ink)] outline-none placeholder:text-[var(--color-ink-4)] focus:border-[var(--color-focus)]"
          placeholder="Vložte odkaz z portálu — klidně celou zprávu, ve které je"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          enterKeyHint="go"
          aria-describedby={`${fieldId}-status`}
          onKeyDown={(e) => {
            /* Return opens, as the keyboard's "go" key promises; a pasted
             * message never needs a line break typed into it. */
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit(text);
            }
          }}
        />
        <div id={`${fieldId}-status`} aria-live="polite" className="min-h-[28px] pt-2 text-[13px]">
          {problem ? (
            <p role="alert" className="text-[var(--color-brick)]">
              {problem}
            </p>
          ) : flow.step.kind !== 'idle' ? (
            <NewPropertyProgress step={flow.step} />
          ) : read?.kind === 'listing' ? (
            <p className="flex items-center gap-1.5 text-[var(--color-ink-2)]">
              <CheckIcon className="h-4 w-4 text-[var(--color-sage)]" />
              <span>{portalLabel(read.source) ?? read.source}</span>
              <span className="text-[var(--color-ink-4)]">·</span>
              <span className="min-w-0 truncate font-mono text-[12px] tabular-nums">{read.nativeId}</span>
            </p>
          ) : null}
        </div>
        <div className="mt-1 grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => void pasteFromClipboard()}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper)] px-3 text-[14px] text-[var(--color-ink-2)] active:bg-[var(--color-inset)]"
          >
            <PasteIcon className="h-[18px] w-[18px]" />
            Vložit ze schránky
          </button>
          <button
            type="submit"
            disabled={!text.trim() || busy}
            className="h-11 rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 text-[14px] font-medium text-white active:bg-[var(--color-copper-2)] disabled:opacity-40"
          >
            {isAdmin ? 'Přidat' : 'Otevřít'}
          </button>
        </div>
      </form>

      <p className="mt-4 text-[12px] leading-relaxed text-[var(--color-ink-3)]">
        {isAdmin
          ? 'Inzerát přidáme do pipeline a otevřeme jeho nemovitost. Inzerát, který ještě nemáme, nejdřív stáhneme z portálu — obvykle do minuty.'
          : 'Inzerát, který máme, otevře nemovitost se všemi jejími inzeráty.'}
      </p>

      {recent.length > 0 && (
        <section aria-label="Naposledy vložené" className="mt-6">
          <SectionLabel>Naposledy vložené</SectionLabel>
          <ul className="mt-2 border-t border-[var(--color-rule)]">
            {recent.map((r) => (
              <li key={`${r.source}:${r.nativeId}`} className="border-b border-[var(--color-rule-soft)]">
                <button
                  type="button"
                  onClick={() => open(r)}
                  className="flex h-12 w-full items-center gap-3 text-left active:bg-[var(--color-paper-2)]"
                >
                  <span className="inline-flex h-5 shrink-0 items-center whitespace-nowrap rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] px-1 text-[11px] text-[var(--color-ink-2)]">
                    {portalBadge(r.source, r.url)}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[12px] tabular-nums text-[var(--color-ink)]">
                    {r.nativeId}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--color-ink-3)]">
                    {fmtShortDate(r.at)} {fmtTime24(r.at)}
                  </span>
                  <ChevronIcon className="h-4 w-4 shrink-0 text-[var(--color-ink-4)]" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
