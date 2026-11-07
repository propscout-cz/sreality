/* vcard — the saved contact a contacts app reads (RFC 2426 3.0, RFC 6350 text rules).
 *
 * Pins: the card's lines and order; the structured name; escaping; folding at 75 octets
 * without splitting a UTF-8 character, CRLF throughout; an office-only card; the file name. */

import { describe, expect, it } from 'vitest';

import { buildVCard, vcardFileName, type VCardContact } from './vcard';

const NOW = new Date('2026-11-07T10:15:30.123Z');

const JAN: VCardContact = {
  uid: 'https://app.propscout.cz/brokers/7',
  name: 'Ing. Jan Novák, Ph.D.',
  org: 'Reality Praha s.r.o.',
  title: 'Realitní makléř',
  phone: '+420731404040',
  email: 'jan@reality.cz',
  url: 'https://app.propscout.cz/property/42',
  note: 'Nemovitost: Byt 3+kk · 68 m² · Praha 5\nsreality: č. zakázky 123',
};

const lines = (card: string) => card.split('\r\n');

describe('buildVCard', () => {
  it('writes every field a contacts app reads, CRLF-terminated', () => {
    const card = buildVCard(JAN, NOW);
    expect(card.endsWith('\r\n')).toBe(true);
    expect(card.replace(/\r\n/g, '')).not.toMatch(/[\r\n]/);
    expect(lines(card)).toEqual([
      'BEGIN:VCARD',
      'VERSION:3.0',
      'PRODID:-//PropScout//CZ',
      'UID:https://app.propscout.cz/brokers/7',
      'N:Novák;Jan;;Ing.;Ph.D.',
      'FN:Ing. Jan Novák\\, Ph.D.',
      'ORG:Reality Praha s.r.o.',
      'TITLE:Realitní makléř',
      'TEL;TYPE=WORK,VOICE:+420731404040',
      'EMAIL;TYPE=INTERNET,WORK:jan@reality.cz',
      'URL:https://app.propscout.cz/property/42',
      'NOTE:Nemovitost: Byt 3+kk · 68 m² · Praha 5\\nsreality: č. zakázky 123',
      'REV:20261107T101530Z',
      'END:VCARD',
      '',
    ]);
  });

  it('escapes the characters a value may not carry bare', () => {
    const card = buildVCard({ uid: 'u', name: 'A;B\\C', note: 'jedna, dvě; tři' }, NOW);
    expect(card).toContain(String.raw`FN:A\;B\\C`);
    expect(card).toContain(String.raw`NOTE:jedna\, dvě\; tři`);
  });

  it('folds a long line at 75 octets, never inside a character', () => {
    const note = 'Žluťoučký kůň úpěl ďábelské ódy. '.repeat(6);
    const card = buildVCard({ uid: 'u', name: 'X', note }, NOW);
    const encoder = new TextEncoder();
    const physical = lines(card);
    for (const line of physical) expect(encoder.encode(line).length).toBeLessThanOrEqual(75);
    /* Unfolding (CRLF + one space removed) gives the value back whole. */
    const unfolded = card.replace(/\r\n /g, '');
    expect(unfolded).toContain(`NOTE:${note}`);
    expect(physical.some((l) => l.startsWith(' '))).toBe(true);
  });

  it('names an office-only card by the office, with an empty structured name', () => {
    const card = buildVCard({ uid: 'u', name: null, org: 'RE/MAX Alfa', phone: '+420222333444' }, NOW);
    expect(card).toContain('N:;;;;');
    expect(card).toContain('FN:RE/MAX Alfa');
  });

  it('leaves out what is not known', () => {
    const card = buildVCard({ uid: 'u', name: 'Eva Malá', email: 'eva@x.cz' }, NOW);
    expect(card).not.toMatch(/^(ORG|TITLE|TEL|URL|NOTE)/m);
  });
});

describe('vcardFileName', () => {
  it('is the card name without the characters a file system refuses', () => {
    expect(vcardFileName({ name: 'Jan Novák', org: null })).toBe('Jan Novák.vcf');
    expect(vcardFileName({ name: null, org: 'RE/MAX Alfa' })).toBe('RE MAX Alfa.vcf');
    expect(vcardFileName({ name: null, org: null })).toBe('Kontakt.vcf');
  });
});
