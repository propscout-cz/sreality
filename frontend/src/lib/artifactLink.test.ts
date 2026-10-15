import { describe, expect, it } from 'vitest';
import { artifactLinkLabel, safeHttpUrl } from './artifactLink';

describe('safeHttpUrl', () => {
  it('keeps http and https, trimmed and normalised', () => {
    expect(safeHttpUrl('  https://claude.ai/artifact/abc ')).toBe('https://claude.ai/artifact/abc');
    expect(safeHttpUrl('HTTP://Example.cz/a?b=1')).toBe('http://example.cz/a?b=1');
  });

  it.each([
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'ftp://example.cz/file',
    'file:///etc/passwd',
    'example.cz/path',
    '//example.cz/path',
    '',
    '   ',
  ])('refuses %j', (raw) => {
    expect(safeHttpUrl(raw)).toBeNull();
  });
});

describe('artifactLinkLabel', () => {
  it('drops the scheme, www and a trailing slash', () => {
    expect(artifactLinkLabel('https://www.example.cz/')).toBe('example.cz');
    expect(artifactLinkLabel('https://claude.ai/artifact/abc')).toBe('claude.ai/artifact/abc');
    expect(artifactLinkLabel('https://example.cz/a/?q=1')).toBe('example.cz/a/?q=1');
  });
});
