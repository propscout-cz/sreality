/* The side panel: our own page (sidepanel.html) in Chrome's side panel, one per
 * window, open across its tabs, shrinking the portal page beside it. It shows the
 * subject of its window's ACTIVE tab (panel_logic.ts createSubjectRouter), told by
 * that tab's content script over the port this page opens to every tab of its
 * window (tabs.connect: as it opens, as a tab says it loaded, as one is dragged
 * in). The tab's content script hides its edge tab while that port is up. Its own
 * store over the one transport; the page never fetches. */

import tokens from '../../frontend/src/styles/tokens.css?inline';
import styles from './panel.css?inline';
import { APP_NAME } from '../../frontend/src/lib/brand';
import { followAppearance, themeHost } from './appearance';
import { PANEL_PORT } from './config';
import { createPanel } from './panel';
import { createSubjectRouter } from './panel_logic';
import { onAccountChange } from './session';
import { createStore, sleep } from './store';
import { send } from './transport';
import type { AuthState, PanelCall, PortMessage } from './types';

document.title = APP_NAME;
const style = document.createElement('style');
style.textContent = tokens + styles;
document.head.appendChild(style);
themeHost(document.documentElement);

const ports = new Map<number, chrome.runtime.Port>();
const store = createStore({
  send,
  sleep,
  onAccountChange,
  written: (propertyId) => {
    const message: PortMessage = { type: 'property_written', property_id: propertyId };
    for (const port of ports.values()) port.postMessage(message);
  },
});
const panel = createPanel(store);
const router = createSubjectRouter((subject) => {
  if (subject == null) panel.clear();
  else void panel.open(subject.ref, subject.url);
});

function drop(tabId: number, port: chrome.runtime.Port): void {
  if (ports.get(tabId) !== port) return;
  ports.delete(tabId);
  router.forget(tabId);
}

function connect(tabId: number): void {
  if (ports.has(tabId)) return;
  const port = chrome.tabs.connect(tabId, { name: PANEL_PORT });
  ports.set(tabId, port);
  router.connecting(tabId);
  port.onMessage.addListener((message: PortMessage) => {
    if (message.type === 'subject') router.tell(tabId, message.subject);
    else store.reread(message.property_id);
  });
  port.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;  // a tab without our content script; read: Chrome logs an unchecked one
    drop(tabId, port);
  });
}

chrome.runtime.onMessage.addListener((message: PanelCall, sender) => {
  if (message.type === 'panel' && sender.tab?.id != null && router.ours(sender.tab)) connect(sender.tab.id);
});
chrome.tabs.onActivated.addListener(({ tabId, windowId }) => { router.activate(tabId, windowId); });
chrome.tabs.onAttached.addListener((tabId, { newWindowId }) => {
  if (router.ours({ windowId: newWindowId })) connect(tabId);
});
chrome.tabs.onDetached.addListener((tabId, { oldWindowId }) => {
  const port = ports.get(tabId);
  if (port == null || !router.ours({ windowId: oldWindowId })) return;
  port.disconnect();
  drop(tabId, port);
});

void chrome.windows.getCurrent().then(async ({ id }) => {
  if (id == null) return;
  const tabs = await chrome.tabs.query({ windowId: id });
  for (const tab of tabs) if (tab.id != null) connect(tab.id);
  router.start(id, tabs.find((t) => t.active)?.id ?? null);
});

// The profile's scheme and theme on this page (appearance.ts).
followAppearance(async () => {
  const auth = await store.call<AuthState>({ type: 'get_auth_state' });
  return auth.ok ? auth.data.appearance : null;
});
