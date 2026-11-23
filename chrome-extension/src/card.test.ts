/* `npm test` (node --test; Node >= 22.18 strips the types). Pins the section
 * card's markup (X1) with a stand-in document: the extension has no DOM runner. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildCard } from './card.ts';

class FakeEl {
  tagName: string;
  className = '';
  textContent = '';
  children: FakeEl[] = [];

  constructor(tag: string) {
    this.tagName = tag.toLowerCase();
  }

  appendChild(child: FakeEl): FakeEl {
    this.children.push(child);
    return child;
  }
}

const doc = { createElement: (tag: string) => new FakeEl(tag) } as unknown as Document;

/* tag.class[children] — the card's skeleton in one comparable string. */
function shape(el: FakeEl): string {
  const self = `${el.tagName}.${el.className}`;
  return el.children.length === 0 ? self : `${self}[${el.children.map(shape).join(',')}]`;
}

function fake(el: HTMLElement): FakeEl {
  return el as unknown as FakeEl;
}

test('a card is a section: title bar (title, count) over a body', () => {
  const card = buildCard({ title: 'Poznámky', count: 3 }, doc);
  assert.equal(
    shape(fake(card.section)),
    'section.card[div.card-bar[h2.card-title,span.card-count],div.card-body]',
  );
  const [bar, body] = fake(card.section).children;
  assert.equal(bar.children[0].textContent, 'Poznámky');
  assert.equal(bar.children[1].textContent, '3', 'a bare number, no brackets');
  assert.equal(card.title, bar.children[0] as unknown as HTMLElement);
  assert.equal(card.body, body as unknown as HTMLElement);
});

test('an empty section keeps its count 0; an unknown count draws none', () => {
  const zero = fake(buildCard({ title: 'Poznámky', count: 0 }, doc).section);
  assert.equal(zero.children[0].children[1].textContent, '0');
  for (const count of [null, undefined]) {
    const bare = fake(buildCard({ title: 'Poznámky', count }, doc).section);
    assert.equal(shape(bare), 'section.card[div.card-bar[h2.card-title],div.card-body]');
  }
});

test('a variant adds its class to the section', () => {
  assert.equal(fake(buildCard({ title: 'AI agenti', variant: 'agcard' }, doc).section).className, 'card agcard');
});

test('actions sit at the bar end, after the count', () => {
  const action = new FakeEl('button') as unknown as HTMLElement;
  const card = fake(buildCard({ title: 'Odhad', count: 2, actions: [action] }, doc).section);
  assert.equal(
    shape(card.children[0]),
    'div.card-bar[h2.card-title,span.card-count,div.card-actions[button.]]',
  );
});
