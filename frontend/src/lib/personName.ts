/* A person's name as a portal prints it, read into its parts: the saved contact's
 * structured name (lib/vcard) and every broker avatar's initials read it here, so a
 * degree is never a surname or an initial. */

/* Degrees as portals print them without their dots; any longer word with a dot is a degree
 * too ("Ing.", "Ph.D."), while a one-letter initial ("N.") is part of the name. */
const DOTLESS_DEGREES = new Set([
  'bc', 'bca', 'ing', 'mgr', 'mga', 'mudr', 'mddr', 'mvdr', 'judr', 'phdr', 'rndr', 'pharmdr', 'paeddr', 'thdr',
  'icdr', 'doc', 'prof', 'phd', 'csc', 'drsc', 'dis', 'mba', 'bba', 'dba', 'mpa', 'llm', 'msc',
]);

const isDegree = (word: string): boolean =>
  (word.length > 2 && word.includes('.')) || DOTLESS_DEGREES.has(word.toLowerCase());

export interface NameParts {
  family: string;
  given: string;
  prefix: string;
  suffix: string;
}

/* "Ing. Jan Novák, Ph.D." → Novák / Jan / Ing. / Ph.D.: degrees before the name to the
 * prefix, after it (or behind a comma) to the suffix, the last remaining word the
 * surname, the rest the given names. A one-word name is a surname. */
export function splitName(full: string): NameParts {
  const [head, ...tail] = full.split(',');
  const words = head.split(/\s+/).filter(Boolean);
  const prefix: string[] = [];
  const suffix: string[] = tail.join(' ').split(/\s+/).filter(Boolean);
  /* "Mgr. et Mgr.": the "et" between two degrees is part of them. */
  const joinsDegrees = () => prefix.length > 0 && words[0]?.toLowerCase() === 'et' && isDegree(words[1] ?? '');
  while (words.length > 0 && (isDegree(words[0]) || joinsDegrees())) prefix.push(words.shift()!);
  while (words.length > 0 && isDegree(words[words.length - 1])) suffix.unshift(words.pop()!);
  const family = words.pop() ?? '';
  return { family, given: words.join(' '), prefix: prefix.join(' '), suffix: suffix.join(' ') };
}

/* "Jana Kubíčková" → "JK", "Ing. Petr van der Berg" → "PB"; "?" with no name left. */
export function nameInitials(name: string | null | undefined): string {
  const { given, family } = splitName(name ?? '');
  return (given.charAt(0) + family.charAt(0)).toLocaleUpperCase('cs-CZ') || '?';
}
