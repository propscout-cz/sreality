import { useEffect, useState } from 'react';

/* Bytes the API serves behind the JWT (an <img> cannot send it) as an object URL, freed on
 * unmount. `load` runs again whenever `key` (what it reads) changes; null loads nothing. */
export function useBlobUrl(
  load: (() => Promise<Blob>) | null,
  key: string,
): { url: string | null; failed: boolean } {
  const [state, setState] = useState<{ url: string | null; failed: boolean }>({
    url: null,
    failed: false,
  });
  const enabled = load !== null;
  useEffect(() => {
    if (!load) return;
    let cancelled = false;
    let url: string | null = null;
    load()
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setState({ url, failed: false });
      })
      .catch(() => {
        if (!cancelled) setState({ url: null, failed: true });
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` names everything `load` reads
  }, [key, enabled]);
  return state;
}
