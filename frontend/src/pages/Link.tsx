/* Odkaz — paste a portal link, land on its property (the Phone app's fifth tab,
 * W2 #1767; #1760 Q14). iOS hands an installed web app no share-sheet route
 * (#1763: `share_target` unsupported, a Shortcut opens Safari), so the paste IS
 * the path: copy the link in the portal's app or Safari, open Odkaz, paste, open.
 *
 * The field takes whatever the share sheet copied — the link alone or a whole
 * message around it; `readPastedLink` pulls out the first URL and runs the
 * extension's portal-id rules. A hit goes to `/listing/:source/:nativeId`, whose
 * alias resolves the advert's property (`AdvertRedirect`), carrying the URL in
 * the navigation state so "we don't have it" can still offer the portal's page.
 * The last five links opened stay on this device as a roll under the field. */

import { useId, useMemo, useRef, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { SectionLabel } from '@/components/section';
import { CheckIcon, ChevronIcon, PasteIcon } from '@/components/phone/phoneIcons';
import { fmtShortDate, fmtTime24 } from '@/lib/format';
import {
  READ_PORTALS,
  readPastedLink,
  readRecentLinks,
  rememberLink,
  type PastedLink,
  type RecentLink,
} from '@/lib/pastedLink';
import { portalLabel, portalShort } from '@/lib/portals';
import { ROUTES } from '@/lib/routes';

/* What the property page's alias reads back from the navigation state. */
export interface PastedLinkState {
  pastedUrl: string;
}

const portalList = (): string => READ_PORTALS.map((s) => portalLabel(s) ?? s).join(', ');

/* Why a paste names no listing, in one sentence the operator can act on. */
export function pasteProblem(link: Exclude<PastedLink, { kind: 'listing' }>): string {
  switch (link.kind) {
    case 'no-url':
      return 'V textu není žádný odkaz. Vložte odkaz na inzerát.';
    case 'portal-page':
      return `Tohle je stránka portálu ${portalLabel(link.source) ?? link.source}, ale ne jeden inzerát. Otevřete inzerát a zkopírujte jeho odkaz.`;
    case 'other':
      return `Tento odkaz nevede na žádný z portálů, které čteme: ${portalList()}.`;
  }
}

export default function LinkPage() {
  const navigate = useNavigate();
  const fieldId = useId();
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [recent, setRecent] = useState<RecentLink[]>(readRecentLinks);

  const read = useMemo(() => (text.trim() ? readPastedLink(text) : null), [text]);

  const open = (link: { url: string; source: string; nativeId: string }) => {
    setRecent(rememberLink(link));
    fieldRef.current?.blur();
    navigate(ROUTES.listingCanonical.build({ source: link.source, nativeId: link.nativeId }), {
      state: { pastedUrl: link.url } satisfies PastedLinkState,
    });
  };

  const submit = (value: string) => {
    const link = readPastedLink(value);
    if (link.kind === 'listing') open(link);
    else setProblem(pasteProblem(link));
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit(text);
  };

  const pasteFromClipboard = async () => {
    setProblem(null);
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
          }}
          rows={3}
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
            disabled={!text.trim()}
            className="h-11 rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 text-[14px] font-medium text-white active:bg-[var(--color-copper-2)] disabled:opacity-40"
          >
            Otevřít
          </button>
        </div>
      </form>

      <p className="mt-4 text-[12px] leading-relaxed text-[var(--color-ink-3)]">
        Čteme tyto portály: {portalList()}. Inzerát, který máme, otevře nemovitost se všemi
        jejími inzeráty.
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
                    {portalShort(r.source)}
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
