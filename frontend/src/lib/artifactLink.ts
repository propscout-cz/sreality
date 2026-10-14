/* An artifact link is operator-typed, so it is only ever drawn as an anchor when it parses as
 * http(s): a `javascript:` or `data:` value is shown as plain text, never as a clickable href.
 * The API and migration 599 refuse such values; this is the render-side half. */
export function safeHttpUrl(raw: string): string | null {
  try {
    const url = new URL(raw.trim());
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
  } catch {
    return null;
  }
}

/* The link's visible text: host and path without the scheme, a trailing slash or `www.`. */
export function artifactLinkLabel(href: string): string {
  try {
    const url = new URL(href);
    const path = `${url.pathname}${url.search}`.replace(/\/$/, '');
    return `${url.host.replace(/^www\./, '')}${path}`;
  } catch {
    return href;
  }
}
