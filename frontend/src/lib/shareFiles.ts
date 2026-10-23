/* Handing a user's own files on: the phone's share sheet where the browser can share files (its
 * "Uložit obrázek" is how a photo reaches Photos — the app itself never writes to the camera
 * roll), else a download of each file. Share needs the tap's user activation, so callers hold the
 * bytes before the tap (the viewer has the original on screen; Vybrat fetches as it selects). */

/* Whether this browser hands files to a share sheet (a phone, macOS Safari) or downloads them. */
export const canShareFiles = (): boolean =>
  typeof navigator.canShare === 'function' &&
  navigator.canShare({ files: [new File([''], 'foto.jpg', { type: 'image/jpeg' })] });

/* 'cancelled': the user closed the sheet; a refusal (no activation left, a type the sheet will not
 * take) throws. */
export async function shareFiles(files: File[], title: string): Promise<'shared' | 'saved' | 'cancelled'> {
  if (typeof navigator.canShare === 'function' && navigator.canShare({ files })) {
    try {
      await navigator.share({ files, title });
      return 'shared';
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return 'cancelled';
      throw err;
    }
  }
  for (const file of files) saveBlob(file, file.name);
  return 'saved';
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Firefox starts the save asynchronously; revoking at once can cancel it.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
