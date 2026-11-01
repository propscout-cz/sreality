import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { AuthProvider } from './lib/auth';
import { isTransientApiError } from './lib/api';
import { applySyncQueryDefaults } from './lib/curationSync';
import { createMutationCache } from './lib/mutationCache';
import { applyAppearance } from './lib/theme';
import { registerServiceWorker } from './lib/serviceWorker';
import './styles/globals.css';

applyAppearance();
registerServiceWorker();

/* The global "a write failed" toast — see lib/mutationCache. */
const mutationCache = createMutationCache();

const queryClient = new QueryClient({
  mutationCache,
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 5 * 60_000,
      /* Off, and it stays off: a focus refetch would re-read every mounted
       * query, market data included, at each alt-tab. The curation sync owner
       * (lib/curationSyncOwner, mounted by AuthProvider) supersedes it for
       * curation: focus, a show, reconnecting and a write in another tab each
       * read the curation clock, and only what moved is re-read
       * (docs/design/curation-sync.md, Decision 12). The rest follows its
       * staleTime at the next mount. */
      refetchOnWindowFocus: false,
      /* One more attempt ONLY when it can help: a network blip or a
       * 502/503/504/520 (the API answers a busy database with 503 db_busy;
       * 520 is Cloudflare in front of Supabase). Never a 4xx, a 500 or a
       * transport-deadline timeout — those are deterministic, and the old
       * blanket `retry: 1` turned each of them into a double-length silent
       * spinner (the 2026-09-30 Brokers hang was 2 × 120 s exactly this way).
       * ONE rule for both adapters: lib/api.ts and lib/pgRead.ts (every
       * PostgREST read) both throw ApiError, and pgRead switches postgrest-js's
       * own hidden retry off, so a busy or unreachable PostgREST read retries
       * here exactly like a FastAPI one — and nowhere else. */
      retry: (failureCount, error) => failureCount < 1 && isTransientApiError(error),
    },
  },
});

/* For the client's whole life: no curation read refetches on reconnect. The curation
 * sync owner (lib/curationSyncOwner) is the one reconnect path for curation, but it
 * runs only while someone is signed in and Supabase is configured; outside that,
 * a curation read follows its staleTime at its next mount. A hidden tab
 * reconnecting re-reads nothing. */
applySyncQueryDefaults(queryClient);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
