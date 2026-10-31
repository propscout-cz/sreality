/* What the store package may ask for, written out by hand. The release compares the built
 * manifest with this for EQUALITY (package.ts), so a new permission, portal site or host
 * permission fails every pull request until it is added here: a deliberate, reviewed edit.
 * Never generate this from the build or from the portal registry. */

export interface AllowedList {
  permissions: readonly string[];
  hostPermissions: readonly string[];
  contentScriptMatches: readonly string[];
}

export const ALLOWED: AllowedList = {
  permissions: ['storage', 'identity', 'alarms', 'webNavigation', 'sidePanel'],
  // Empty: the background worker reaches the API and the auth service under ordinary CORS.
  hostPermissions: [],
  contentScriptMatches: [
    'https://www.sreality.cz/*',
    'https://sreality.cz/*',
    'https://reality.bazos.cz/*',
    'https://www.bezrealitky.cz/*',
    'https://bezrealitky.cz/*',
    'https://www.bezrealitky.com/*',
    'https://bezrealitky.com/*',
    'https://reality.idnes.cz/*',
    'https://nemovitosti.maxima.cz/*',
    'https://www.remax-czech.cz/*',
    'https://remax-czech.cz/*',
    'https://www.mmreality.cz/*',
    'https://mmreality.cz/*',
    'https://www.ceskereality.cz/*',
    'https://ceskereality.cz/*',
    'https://realitymix.cz/*',
    'https://www.realitymix.cz/*',
  ],
};

/* Hosts of ours that must never appear in the manifest, besides the build's own API, auth and
 * app hosts (package.ts reads those from the build env). The display name "PropScout.cz" is a
 * name, not one of these. */
export const DEPLOYED_HOST_MARKERS: readonly string[] = [
  'api.propscout.cz',
  'app.propscout.cz',
  'supabase.co',
  'railway.app',
];

/* The store's limit for the manifest description. */
export const DESCRIPTION_MAX_CHARS = 132;
