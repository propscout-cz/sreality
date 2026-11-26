/* "Uložit kontakt": the contact as a .vcf file (lib/vcard). A phone opens it in its
 * contacts app ("Create New Contact" on iOS); a desktop downloads it for Outlook or
 * Google Contacts. Through lib/shareFiles' saveBlob, the path the property download
 * already takes inside the installed app. */

import type { ReactNode } from 'react';

import { saveBlob } from '@/lib/shareFiles';
import { buildVCard, VCARD_TYPE, vcardFileName, type VCardContact } from '@/lib/vcard';

export default function SaveContactButton({
  contact,
  className,
  children = 'Uložit kontakt',
  label,
}: {
  contact: VCardContact;
  className: string;
  children?: ReactNode;
  /* The accessible name, when the visible words alone do not say whose contact it is. */
  label?: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={() => saveBlob(new Blob([buildVCard(contact)], { type: VCARD_TYPE }), vcardFileName(contact))}
      className={className}
    >
      {children}
    </button>
  );
}
