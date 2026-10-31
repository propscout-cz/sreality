/* The one transport, for the content script and the side panel alike: every
 * network call goes to the background worker through chrome.runtime.sendMessage
 * (host_permissions and the portal's CORS do not apply there); neither fetches. */

import { EXTENSION_RELOADED_DETAIL } from './config';
import type { ApiMessage, ApiResult } from './types';

/* An extension reload, update, disable or removal orphans the content script in an
 * open tab (Chrome re-injects only on page load): sendMessage then throws "Extension
 * context invalidated." synchronously. send() resolves with a failure instead of
 * rejecting — every busy flag waits for a result — and says what fixes it (a detail
 * the search page's notice recognises, to offer a reload instead of a retry). */
function runtimeDetail(message: string | undefined): string {
  if (message != null && /context invalidated/i.test(message)) return EXTENSION_RELOADED_DETAIL;
  return message ?? 'runtime error';
}

export function send(message: ApiMessage): Promise<ApiResult<unknown>> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(message, (response: ApiResult<unknown>) => {
        const error = chrome.runtime.lastError;  // always read: Chrome logs an unchecked one
        resolve(error ? { ok: false, status: 0, detail: runtimeDetail(error.message) } : response);
      });
    } catch (err) {
      resolve({ ok: false, status: 0, detail: runtimeDetail(err instanceof Error ? err.message : String(err)) });
    }
  });
}
