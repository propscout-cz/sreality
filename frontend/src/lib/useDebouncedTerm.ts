import { useEffect, useState } from 'react';

/* A free-text query and its trimmed, debounced twin: [typed, settled, setTyped].
 * The brokers' name search and firm picker (desktop and phone) debounce the same
 * way; only what happens after the settle differs. */
export function useDebouncedTerm(delayMs = 200): [string, string, (next: string) => void] {
  const [q, setQ] = useState('');
  const [debounced, setDebounced] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), delayMs);
    return () => clearTimeout(t);
  }, [q, delayMs]);
  return [q, debounced, setQ];
}
