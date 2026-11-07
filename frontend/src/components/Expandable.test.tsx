/* Expandable — the one "show more".
 *
 * Pins:
 *   * no toggle while nothing is cut; a toggle once the content outgrows its cut,
 *     including when it grows AFTER mount (a note added);
 *   * the toggle names the region it opens (aria-controls) and says its state;
 *   * a text cut is a line clamp, a block cut a max height, both gone when open;
 *   * focus landing below the cut opens it; focus above it does not;
 *   * the accessible name, when given, replaces the bare word.
 * jsdom lays nothing out, so each test sets the content's heights by hand. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

import Expandable from './Expandable';

afterEach(() => vi.restoreAllMocks());

/* Every element reports these heights: scrollHeight is the content, clientHeight the cut. */
function heights(scroll: number, client: number) {
  vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockReturnValue(scroll);
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(client);
}

describe('<Expandable>', () => {
  it('shows no toggle while nothing is cut', () => {
    heights(40, 40);
    render(<Expandable lines={4}>Krátký text.</Expandable>);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('opens and closes a cut text, naming what it controls', () => {
    heights(200, 80);
    render(
      <Expandable as="p" lines={6} words={['Celý popis', 'Zkrátit']}>
        Dlouhý popis.
      </Expandable>,
    );
    const text = screen.getByText('Dlouhý popis.');
    expect(text.tagName).toBe('P');
    expect(text).toHaveClass('line-clamp-6');
    const toggle = screen.getByRole('button', { name: 'Celý popis' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls', text.id);

    fireEvent.click(toggle);
    expect(text).not.toHaveClass('line-clamp-6');
    expect(toggle).toHaveTextContent('Zkrátit');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(toggle);
    expect(text).toHaveClass('line-clamp-6');
  });

  it('cuts a block at its max height until opened', () => {
    heights(900, 384);
    render(
      <Expandable maxHeight="24rem">
        <ul data-testid="list">
          <li>poznámka</li>
        </ul>
      </Expandable>,
    );
    const region = screen.getByTestId('list').parentElement!;
    expect(region).toHaveStyle({ maxHeight: '24rem', overflow: 'hidden' });
    fireEvent.click(screen.getByRole('button', { name: 'Zobrazit více' }));
    expect(region).not.toHaveStyle({ maxHeight: '24rem' });
    expect(screen.getByRole('button', { name: 'Zobrazit méně' })).toBeInTheDocument();
  });

  it('offers the toggle once the content outgrows the cut after mount', async () => {
    let scroll = 100;
    vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockImplementation(() => scroll);
    vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(100);
    const { rerender } = render(
      <Expandable maxHeight="24rem">
        <ul>
          <li>jedna</li>
        </ul>
      </Expandable>,
    );
    expect(screen.queryByRole('button')).toBeNull();

    scroll = 600;
    rerender(
      <Expandable maxHeight="24rem">
        <ul>
          <li>jedna</li>
          <li>dvě</li>
        </ul>
      </Expandable>,
    );
    /* The MutationObserver reads the added row; its callback is a microtask. */
    await act(async () => {});
    expect(screen.getByRole('button', { name: 'Zobrazit více' })).toBeInTheDocument();
  });

  it('opens when focus lands below the cut, not above it', () => {
    heights(900, 384);
    render(
      <Expandable maxHeight="24rem">
        <button type="button">nahoře</button>
        <button type="button">dole</button>
      </Expandable>,
    );
    const region = screen.getByRole('button', { name: 'nahoře' }).parentElement!;
    const rect = (top: number, bottom: number) => ({ top, bottom }) as DOMRect;
    vi.spyOn(region, 'getBoundingClientRect').mockReturnValue(rect(0, 384));
    vi.spyOn(screen.getByRole('button', { name: 'nahoře' }), 'getBoundingClientRect').mockReturnValue(rect(0, 30));
    vi.spyOn(screen.getByRole('button', { name: 'dole' }), 'getBoundingClientRect').mockReturnValue(rect(700, 730));

    fireEvent.focus(screen.getByRole('button', { name: 'nahoře' }));
    expect(screen.getByRole('button', { name: 'Zobrazit více' })).toHaveAttribute('aria-expanded', 'false');

    fireEvent.focus(screen.getByRole('button', { name: 'dole' }));
    expect(screen.getByRole('button', { name: 'Zobrazit méně' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('takes an accessible name that keeps the visible word', () => {
    heights(80, 40);
    render(
      <Expandable lines={2} words={['more', 'less']} names={['Show more of the message', 'Show less of the message']}>
        A long message.
      </Expandable>,
    );
    const toggle = screen.getByRole('button', { name: 'Show more of the message' });
    expect(toggle).toHaveTextContent(/^more$/);
    fireEvent.click(toggle);
    expect(toggle).toHaveAccessibleName('Show less of the message');
  });
});
