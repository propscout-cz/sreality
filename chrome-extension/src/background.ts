/* Background service worker. The single context that talks to the
 * FastAPI service: its calls leave from the extension's origin, which the
 * API's CORS allowlist names, never through a portal page's CORS. Content
 * scripts and the side panel post messages here; we route them to the api
 * helpers. It also opens Chrome's side panel (sidepanel.ts). */

import {
  addNote,
  addPipelineCard,
  addToCollection,
  attachTag,
  createEstimation,
  deleteNote,
  detachTag,
  dismissProperty,
  getBillingMe,
  getEstimation,
  getPriceHistory,
  ingestListing,
  ingestState,
  listCollections,
  listNotes,
  listPipelineStages,
  listTags,
  lookupListings,
  movePipelineCard,
  patchScenario,
  removeFromCollection,
  removePipelineCard,
  undismissProperty,
  updateNote,
} from './api';
import { getAuthState, signInWithGoogle, signOut } from './auth';
import type { ApiMessage, ApiResult, PanelCall } from './types';

/* The toolbar icon opens and closes the side panel — one global panel per window,
 * so it stays open across the window's tabs. */
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((err: unknown) => {
  console.error('[mf-ext] side panel behaviour', err);
});

chrome.runtime.onMessage.addListener(
  (
    message: ApiMessage | PanelCall,
    sender,
    sendResponse: (response: ApiResult<unknown>) => void,
  ) => {
    /* A tab's call to the side panel, answered by nobody. open() before any await:
     * Chrome opens a side panel only while the click's user gesture is live. */
    if (message.type === 'panel') {
      if (message.open && sender.tab != null) {
        chrome.sidePanel.open({ windowId: sender.tab.windowId }).catch((err: unknown) => {
          console.error('[mf-ext] side panel open', err);
        });
      }
      return false;
    }
    handleMessage(message)
      .then(sendResponse)
      .catch((err: unknown) => {
        sendResponse({
          ok: false,
          status: 0,
          detail:
            err instanceof Error
              ? err.message
              : `background error: ${String(err)}`,
        });
      });
    /* Return true to keep the message channel open until sendResponse
     * fires asynchronously — required for the Promise.then path. */
    return true;
  },
);

async function handleMessage(
  message: ApiMessage,
): Promise<ApiResult<unknown>> {
  switch (message.type) {
    case 'lookup_listings':
      return lookupListings(message.items);
    case 'ingest_listing':
      return ingestListing(message.url);
    case 'ingest_state':
      return ingestState(message.source, message.native_id);
    case 'patch_scenario':
      return patchScenario(message.run_id, message.body);
    case 'create_estimation':
      return createEstimation(message.listing_id, {
        estimate_kind: message.estimate_kind,
        category_main: message.category_main,
      });
    case 'get_estimation':
      return getEstimation(message.run_id);
    case 'get_price_history':
      return getPriceHistory(message.property_id);
    case 'add_pipeline_card':
      return addPipelineCard(message.property_id);
    case 'remove_pipeline_card':
      return removePipelineCard(message.property_id);
    case 'move_pipeline_card':
      return movePipelineCard(message.property_id, message.stage_id);
    case 'list_pipeline_stages':
      return listPipelineStages();
    case 'dismiss_property':
      return dismissProperty(message.property_id);
    case 'undismiss_property':
      return undismissProperty(message.property_id);
    case 'list_collections':
      return listCollections();
    case 'list_tags':
      return listTags();
    case 'attach_tag':
      return attachTag(message.tag_id, message.property_id);
    case 'detach_tag':
      return detachTag(message.tag_id, message.property_id);
    case 'add_to_collection':
      return addToCollection(message.collection_id, message.property_id);
    case 'remove_from_collection':
      return removeFromCollection(message.collection_id, message.property_id);
    case 'list_notes':
      return listNotes(message.property_id);
    case 'add_note':
      return addNote(message.property_id, message.body, message.shared, message.origin_listing_ref_id);
    case 'update_note':
      return updateNote(message.property_id, message.note_id, message.patch);
    case 'delete_note':
      return deleteNote(message.property_id, message.note_id);
    case 'sign_in': {
      const res = await signInWithGoogle();
      return res.ok
        ? { ok: true, data: undefined }
        : { ok: false, status: 0, detail: res.detail };
    }
    case 'sign_out':
      await signOut();
      return { ok: true, data: undefined };
    case 'get_auth_state':
      return { ok: true, data: await getAuthState() };
    case 'get_billing':
      return getBillingMe();
  }
}
