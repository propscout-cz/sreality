/* A contact as a vCard file, for "Uložit kontakt": the phone hands it to its contacts
 * app (iOS opens a .vcf as "Create New Contact"), a desktop imports it into its contacts
 * (Google, macOS; Outlook for Windows may need the file opened as UTF-8). Version 3.0
 * (RFC 2426), not 4.0 (RFC 6350): every contacts app reads 3.0, Outlook nothing newer. The text rules are RFC 6350's, which 3.0 shares: values
 * escaped, lines folded at 75 octets without splitting a UTF-8 character, CRLF between
 * lines. Built in the browser from what the page already shows; nothing is sent anywhere. */

import { splitName } from './personName';

export interface VCardContact {
  /* A stable URI per person, so a re-import can be matched to the earlier one. */
  uid: string;
  /* The person's name as the portal prints it, titles included; null when only the
   * office is known, which then names the card. */
  name: string | null;
  org?: string | null;
  title?: string | null;
  /* A dialable number (lib/brokers `dialable`). */
  phone?: string | null;
  email?: string | null;
  url?: string | null;
  note?: string | null;
}

/* RFC 6350 §3.4: a backslash, comma, semicolon and line break are escaped in a text value. */
const escape = (v: string): string =>
  v.replace(/\\/g, '\\\\').replace(/\r\n|\r|\n/g, '\\n').replace(/,/g, '\\,').replace(/;/g, '\\;');

/* RFC 6350 §3.2: a line longer than 75 octets continues on the next line after CRLF and
 * one space, never inside a multi-byte character. */
function fold(line: string): string {
  const encoder = new TextEncoder();
  const out: string[] = [];
  let current = '';
  let octets = 0;
  for (const char of line) {
    const size = encoder.encode(char).length;
    const limit = out.length === 0 ? 75 : 74;
    if (octets + size > limit) {
      out.push(current);
      current = '';
      octets = 0;
    }
    current += char;
    octets += size;
  }
  out.push(current);
  return out.join('\r\n ');
}

/* What the card is called: the person, else the office. */
export const contactName = (c: Pick<VCardContact, 'name' | 'org'>): string => c.name || c.org || 'Kontakt';

export function buildVCard(c: VCardContact, now: Date = new Date()): string {
  const n = c.name ? splitName(c.name) : { family: '', given: '', prefix: '', suffix: '' };
  const lines = [
    'BEGIN:VCARD',
    'VERSION:3.0',
    'PRODID:-//PropScout//CZ',
    `UID:${c.uid}`,
    `N:${[n.family, n.given, '', n.prefix, n.suffix].map(escape).join(';')}`,
    `FN:${escape(contactName(c))}`,
  ];
  if (c.org) lines.push(`ORG:${escape(c.org)}`);
  if (c.title) lines.push(`TITLE:${escape(c.title)}`);
  if (c.phone) lines.push(`TEL;TYPE=WORK,VOICE:${c.phone}`);
  if (c.email) lines.push(`EMAIL;TYPE=INTERNET,WORK:${escape(c.email)}`);
  if (c.url) lines.push(`URL:${c.url}`);
  if (c.note) lines.push(`NOTE:${escape(c.note)}`);
  lines.push(`REV:${now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')}`, 'END:VCARD');
  return lines.map(fold).join('\r\n') + '\r\n';
}

/* The file's name: the card's, without the characters a file system refuses. */
export function vcardFileName(c: Pick<VCardContact, 'name' | 'org'>): string {
  const base = contactName(c).replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, ' ').trim();
  return `${base || 'Kontakt'}.vcf`;
}

export const VCARD_TYPE = 'text/vcard;charset=utf-8';
