/* A collection's rename editor, the desktop's (pages/CollectionDetail HeaderEditor)
 * and the phone's (pages/phone/PhoneCollectionDetail Editor) alike: the name and
 * the description follow the collection while untouched, and only what the user
 * changed is saved, so a rename made elsewhere is never written back (D3,
 * lib/useServerDraft). The first field is focused on open: the description for
 * the default collection, whose name is locked. */

import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { updateCollection } from '@/lib/api';
import { revalidateTables } from '@/lib/curationSync';
import type { Collection } from '@/lib/types';
import { trimDraft, useServerDraft } from '@/lib/useServerDraft';

/** The two drafts, their refs, the save and whether it may run; `failed` is the message a save error without one shows. */
export function useCollectionEditor(collection: Collection, { onDone, failed }: { onDone: () => void; failed: string }) {
  const qc = useQueryClient();
  const name = useServerDraft(collection.name, trimDraft);
  const description = useServerDraft(collection.description ?? '', trimDraft);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const descriptionRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    (collection.is_system ? descriptionRef : nameRef).current?.focus();
  }, [collection.is_system]);

  const nextName = name.edited();
  const nextDescription = description.edited();
  const save = useMutation({
    mutationFn: () =>
      updateCollection(collection.id, {
        ...(nextName != null && { name: nextName }),
        /* A cleared description goes as '' until #2208: PATCH /collections reads null
         * as "not given" (coalesce), so null alone is refused and never clears it. */
        ...(nextDescription != null && { description: nextDescription }),
      }),
    onSuccess: () => {
      setError(null);
      void revalidateTables(qc, ['collections']);
      onDone();
    },
    onError: (err: Error) => setError(err.message || failed),
  });
  const canSave = (nextName != null || nextDescription != null) && !save.isPending && name.normalized.length > 0;

  return { name, description, nameRef, descriptionRef, save, canSave, error };
}
