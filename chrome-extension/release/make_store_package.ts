/* `npm run package` (after `npm run build`): writes store/ from dist/ and fails when the store
 * package breaks release/allowed.ts. CI runs it on every pull request against the real build. */

import { ALLOWED } from './allowed.ts';
import { checkPackage, hostOf, makeStorePackage, readPackage } from './package.ts';

const [dist = 'dist', out = 'store'] = process.argv.slice(2);
const env = process.env;
const deployed = [env.VITE_API_BASE_URL, env.VITE_SUPABASE_URL, env.VITE_APP_BASE_URL]
  .map(hostOf)
  .filter((h): h is string => h != null);

makeStorePackage(dist, out);
const violations = checkPackage(readPackage(out), ALLOWED, deployed);
if (violations.length > 0) {
  console.error(`The store package breaks release/allowed.ts:\n- ${violations.join('\n- ')}`);
  process.exitCode = 1;
} else {
  console.log(`Store package OK: ${out}/ (no key, no source maps, permissions as allowed).`);
}
