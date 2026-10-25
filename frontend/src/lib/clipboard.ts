import { pushToast } from './toast';

/* What each "Kopírovat" says afterwards: what was copied, or what was selected for the
 * system's own Copy. Spelled per thing so the sentence agrees with its noun. */
export const COPY_WORDS = {
  phone: { done: 'Číslo zkopírováno.', selected: 'Číslo je označené, zkopírujte ho.' },
  email: { done: 'E-mail zkopírován.', selected: 'E-mail je označený, zkopírujte ho.' },
  reference: {
    done: 'Číslo zakázky zkopírováno.',
    selected: 'Číslo zakázky je označené, zkopírujte ho.',
  },
} as const;
export type CopyWords = (typeof COPY_WORDS)[keyof typeof COPY_WORDS];

/* ONE copy behaviour for every "Kopírovat" — the property page's broker card on both
 * shells and the phone broker page: the clipboard where the page may write it, else the
 * shown text selected for the system's own Copy. `writeText` is absent or rejects on a
 * non-secure host (a LAN preview), under a denied permission, and in an iOS web app without
 * a user gesture; the toast says which of the two happened. The desktop card used to swallow
 * the rejection, which left a button that silently did nothing. */
export async function copyText(text: string, node: Element | null, words: CopyWords): Promise<void> {
  const select = () => {
    if (node) {
      const range = document.createRange();
      range.selectNodeContents(node);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    }
    pushToast('info', words.selected);
  };
  if (!navigator.clipboard?.writeText) {
    select();
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    pushToast('ok', words.done);
  } catch {
    select();
  }
}
