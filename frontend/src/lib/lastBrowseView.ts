/* The Browse view the operator last had on screen, remembered per browser.
 *
 * WHY. Browse keeps its whole view in the URL, so a same-tab round trip (card,
 * then the browser's Back or the header's "Back to browse" step) lands on the
 * exact filtered URL. Two routes back did not go through history and landed on a
 * bare `/browse`, an unfiltered page: the nav's "Browse" entry, and the
 * property page's crumb when the property was opened in a new tab or from a map
 * pin's plain link (no earlier entry to step back to). Both now go to this
 * remembered view instead.
 *
 * localStorage, not sessionStorage: a card opened in a new tab is the case that
 * needs it, and a new tab starts with its own, empty session storage. With
 * several Browse tabs open the last one touched wins, which is what "the page as
 * I left it" means when you were last working in that one.
 *
 * Only the nav entry while OFF Browse and the crumb read it. The nav entry while
 * ON Browse stays the bare URL — clicking the page you are on is the reset — and
 * the page then writes that bare view here, so a reset is remembered too. */

import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { readValue, writeValue } from './persistedFlag';
import { ROUTES } from './routes';

const LAST_BROWSE_SEARCH_KEY = 'sreality.browse.lastSearch';

/* The remembered query string, `?…` or '' for a bare view. Anything else in the
 * slot (a hand-edited or foreign value) reads as bare, never as a destination. */
export function readLastBrowseSearch(): string {
  const raw = readValue(LAST_BROWSE_SEARCH_KEY, '');
  return raw.startsWith('?') ? raw : '';
}

export const writeLastBrowseSearch = (search: string): void =>
  writeValue(LAST_BROWSE_SEARCH_KEY, search);

/* `/browse` with the remembered view's query. */
export const lastBrowsePath = (): string => ROUTES.browse.build() + readLastBrowseSearch();

/* Mounted by the Browse page: every URL it shows, filters and view knobs alike,
 * is the view to come back to. */
export function useRememberBrowseView(): void {
  const { search } = useLocation();
  useEffect(() => {
    writeLastBrowseSearch(search);
  }, [search]);
}
