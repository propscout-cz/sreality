/* "Stáhnout" in the property page's header: one zip from the API holding a PDF of
 * the ad, the canonical ad's stored photos and, in moje-fotky/, the originals of the
 * acting identity's own photos. The API builds it because R2 sends no CORS header, so
 * the browser can't read the photo bytes itself. */
import { useState } from 'react';
import { actOpts, useActAs } from '@/lib/acting';
import { fetchPropertyDownload } from '@/lib/api';
import { saveBlob } from '@/lib/shareFiles';
import type { ImagePublic } from '@/lib/types';

export default function DownloadPropertyButton({
  propertyId,
  images,
  variant = 'header',
}: {
  propertyId: number;
  /* The canonical ad's photos (the gallery's) — only to say which are left out. */
  images: ImagePublic[];
  /* `phone`: the last line of the phone property page (#1767), a plain link-weight row. */
  variant?: 'header' | 'phone';
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const actAs = useActAs();
  // Only photos with an R2 copy can be zipped; the rest live on the portal's CDN alone.
  const stored = images.filter((img) => img.storage_path != null).length;

  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      saveBlob(await fetchPropertyDownload(propertyId, ...actOpts(actAs)), `property-${propertyId}.zip`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Stažení selhalo');
    } finally {
      setBusy(false);
    }
  };

  const title =
    stored < images.length
      ? `PDF inzerátu, ${stored} z ${images.length} jeho fotek a vaše vlastní fotky v jednom .zip — ostatní fotky inzerátu zatím nejsou uložené`
      : 'PDF inzerátu, jeho fotky a vaše vlastní fotky v jednom .zip';

  if (variant === 'phone') {
    return (
      <div>
        <button
          type="button"
          onClick={() => void download()}
          disabled={busy}
          title={title}
          className="inline-flex h-11 items-center gap-2 text-[15px] font-medium text-[var(--color-copper)] disabled:opacity-60"
        >
          <DownloadGlyph />
          <span>{busy ? 'Připravuji…' : 'Stáhnout (PDF + fotky)'}</span>
        </button>
        {error && (
          <p className="text-[13px] text-[var(--color-brick)]" role="alert">
            Stažení selhalo: {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <span className="inline-flex items-center gap-2">
      {error && (
        <span className="text-[0.75rem] text-[var(--color-brick)]" title={error} role="alert">
          Stažení selhalo
        </span>
      )}
      <button
        type="button"
        onClick={() => void download()}
        disabled={busy}
        title={title}
        className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-1.5 text-[0.8rem] text-[var(--color-ink-2)] transition-colors hover:border-[var(--color-copper)] hover:text-[var(--color-copper)] disabled:opacity-60 disabled:cursor-wait"
      >
        <DownloadGlyph />
        <span>{busy ? 'Připravuji…' : 'Stáhnout'}</span>
      </button>
    </span>
  );
}

function DownloadGlyph() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M8 2v8M4.5 6.5 8 10l3.5-3.5M2.5 13.5h11"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
