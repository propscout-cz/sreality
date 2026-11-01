/* "Moje": the operator's own state on the property — the deal pipeline (rule
 * #22), collections and tags (rule #18), the hide toggle and the notes. Every
 * curation write goes through the one store (store.ts: optimistic, put back once
 * on failure, the same routes the app writes through); the notes are this card's
 * own, read per property. Wording is the old panel's wherever it had the string. */

import { COLLECTION_SAVE_LABEL, isMine, shareAudience } from '../../frontend/src/lib/collectionRules';
import { REMOVE_QUESTION, removeWarning, stageAccent, stageBadge } from '../../frontend/src/lib/pipelineStage';
import type { Note, PipelineStage, Tag } from '../../frontend/src/lib/types';
import { buildCard } from './card';
import { buildChecklist, buildCollectionChecklist, stateLine } from './checklist';
import { APP_BASE_URL, friendlyDetail } from './config';
import { h, setBusy } from './dom';
import { bellIconSvg, bookmarkIconSvg, codeBadge, eyeOffIconSvg, funnelIconSvg, lineIcon, savedCollections } from './glyphs';
import { cutChips, fmtDate, stageControl } from './panel_logic';
import { isLiveDeal, type Pending, type Store } from './store';
import type { PortalListing } from './types';

export interface MineDeps {
  store: Store;
  repaint: () => void;
  /* After a user action only: focus the control with this data-key once repainted. */
  focus: (key: string) => void;
}

type Picker = 'collections' | 'tags';

interface Notes {
  list: Note[] | null;
  failed: boolean;
  expanded: boolean;
  busy: boolean;
  rowBusy: boolean;
  editingId: number | null;
  confirmDeleteId: number | null;
  error: string | null;
}

export function createMine(deps: MineDeps) {
  const { store, repaint, focus } = deps;
  let propertyId: number | null = null;
  let listing: PortalListing | null = null;
  let closeOpen = false;
  let confirmRemove = false;
  let picker: Picker | null = null;
  let error: string | null = null;
  let pickerError: string | null = null;
  let notes: Notes = blankNotes();
  /* Drafts live outside the render (typing never repaints), reset with the property —
   * bar a half-typed note, which waits for its property's re-open (a tab switch back). */
  let draft = '';
  const drafts = new Map<number, string>();
  let shareDraft = false;
  let editDraft = '';
  store.onAccountChange(() => {
    drafts.clear();
    draft = '';
  });

  function blankNotes(): Notes {
    return {
      list: null, failed: false, expanded: false, busy: false, rowBusy: false,
      editingId: null, confirmDeleteId: null, error: null,
    };
  }

  /* A new panel open (another ad, or the same one for another account): everything
   * this card held goes, the notes are read afresh. */
  function reset(next: PortalListing | null): void {
    if (propertyId != null) {
      if (draft.trim() !== '') drafts.set(propertyId, draft);
      else drafts.delete(propertyId);
    }
    listing = next;
    closeOpen = false;
    confirmRemove = false;
    picker = null;
    error = null;
    pickerError = null;
    shareDraft = false;
    editDraft = '';
    const id = next?.found ? next.property_id : null;
    propertyId = id;
    draft = id != null ? drafts.get(id) ?? '' : '';
    notes = blankNotes();
    notes.expanded = draft !== '';
    if (id == null) return;
    store.load('stages');
    store.load('collections', true);
    store.load('tags');
    void loadNotes(id);
  }

  /* Curation writes: the store holds the state; this card says a failure where it can be seen. */
  async function write(pending: Pending, failed: (detail: string, on: boolean) => string, inPicker = false): Promise<void> {
    const id = propertyId;
    if (pending == null) return;
    if (inPicker) pickerError = null;
    else error = null;
    repaint();
    const res = await pending;
    if (id !== propertyId) return;
    if (!res.ok) {
      const message = failed(friendlyDetail(res.detail), res.on);
      if (inPicker && picker != null) pickerError = message;
      else error = message;
    }
    repaint();
  }

  // ---- notes (per property, never cached; the old panel's CRUD) ----------------

  /* Every notes answer lands only in the notes it was asked for: a reset replaces the object. */
  async function loadNotes(id: number): Promise<void> {
    const mine = notes;
    if (mine.list != null) return;
    if (mine.failed) {
      mine.failed = false;
      repaint();
    }
    const res = await store.retry(() => store.call<Note[]>({ type: 'list_notes', property_id: id }));
    if (res == null || mine !== notes) return;
    if (res.ok) mine.list = res.data;
    else mine.failed = true;
    repaint();
  }

  /* Who a SHARED note would reach (the app's rule); null while the collection list is unknown. */
  function audience(): string[] | null {
    const ids = listing?.collection_ids ?? [];
    if (ids.length === 0) return [];
    const collections = store.list('collections').data;
    if (collections == null) return null;
    return shareAudience(ids, new Map(collections.map((c) => [c.id, c])));
  }

  /* `landed` runs on success even when the panel has moved on (what it settles is not this card's). */
  async function noteWrite<T>(
    message: Parameters<Store['call']>[0], failed: string, row: boolean, apply: (list: Note[], data: T) => Note[],
    landed?: () => void,
  ): Promise<boolean> {
    const mine = notes;
    if (propertyId == null) return false;
    if (row) mine.rowBusy = true;
    else mine.busy = true;
    mine.error = null;
    repaint();
    const res = await store.call<T>(message);
    if (res.ok) landed?.();
    if (mine !== notes) return false;
    mine.busy = false;
    mine.rowBusy = false;
    if (!res.ok) {
      mine.error = `${failed}: ${friendlyDetail(res.detail)}`;
      repaint();
      return false;
    }
    if (mine.list != null) mine.list = apply(mine.list, res.data);
    return true;
  }

  async function addNote(): Promise<void> {
    const body = draft.trim();
    if (body === '' || propertyId == null) return;
    const shared = shareDraft && (audience()?.length ?? 0) > 0;
    const id = propertyId;
    const ok = await noteWrite<Note>({
      type: 'add_note', property_id: id, body, shared, origin_listing_ref_id: listing?.listing_id ?? null,
    }, 'Uložení poznámky selhalo', false, (list, note) => [note, ...list], () => {
      // Saved: its draft goes, wherever the panel is now.
      drafts.delete(id);
      if (id === propertyId) {
        draft = '';
        shareDraft = false;
      }
    });
    if (!ok) return;
    // A list never read is not a list of one: read the whole one, the new note included.
    if (notes.list == null) void loadNotes(id);
    repaint();
  }

  async function saveEdit(noteId: number): Promise<void> {
    const body = editDraft.trim();
    if (body === '' || propertyId == null) return;
    const ok = await noteWrite<Note>({ type: 'update_note', property_id: propertyId, note_id: noteId, patch: { body } },
      'Úprava poznámky selhala', true, (list, note) => list.map((n) => (n.id === noteId ? note : n)));
    if (ok) notes.editingId = null;
    repaint();
  }

  async function toggleShared(note: Note): Promise<void> {
    if (propertyId == null || notes.rowBusy) return;
    await noteWrite<Note>({ type: 'update_note', property_id: propertyId, note_id: note.id, patch: { shared: !note.shared } },
      'Sdílení poznámky se nepodařilo změnit', true, (list, next) => list.map((n) => (n.id === note.id ? next : n)));
    repaint();
  }

  async function deleteNote(noteId: number): Promise<void> {
    if (propertyId == null) return;
    const ok = await noteWrite<{ deleted: true }>({ type: 'delete_note', property_id: propertyId, note_id: noteId },
      'Smazání poznámky selhalo', true, (list) => list.filter((n) => n.id !== noteId));
    if (ok) notes.confirmDeleteId = null;
    repaint();
  }

  // ---- rendering ---------------------------------------------------------------

  function dismissToggle(view: PortalListing, id: number, tall: boolean): HTMLElement | null {
    if (view.dismissed == null || isLiveDeal(view.pipeline, store.list('stages').data)) return null;
    const on = view.dismissed;
    const btn = h('button', tall ? 'q q--tall' : 'q');
    btn.dataset.key = 'dismiss';
    btn.setAttribute('aria-pressed', String(on));
    btn.title = on ? 'Skryto — znovu zobrazit' : 'Skrýt nemovitost';
    btn.innerHTML = eyeOffIconSvg(on);
    btn.appendChild(h('span', '', on ? 'Skryto' : 'Skrýt'));
    setBusy(btn, store.busy('dismissed', id));
    btn.onclick = () => {
      if (store.busy('dismissed', id)) return;
      void write(store.toggleDismiss(id), (d) => `Skrytí se nepodařilo uložit: ${d}`);
    };
    return btn;
  }

  /* A stage as the close menu names it: its badge (a mark beside the label, hidden from AT) and its label. */
  function stageName(stage: PipelineStage, stages: readonly PipelineStage[]): Node[] {
    const label = h('span', '', stage.label);
    const code = codeBadge(stage, stages);
    if (code == null) return [label];
    code.setAttribute('aria-hidden', 'true');
    return [code, label];
  }

  /* A move answers the open "remove?" question (and the menu) another way: both close. */
  function move(id: number, stageId: number): void {
    if (store.busy('pipeline', id)) return;
    closeOpen = false;
    confirmRemove = false;
    void write(store.moveStage(id, stageId), (d) => `Změna fáze selhala: ${d}`);
  }

  function pipelineGroup(view: PortalListing, id: number): HTMLElement {
    const grp = h('div', 'grp');
    const busy = store.busy('pipeline', id);
    const p = view.pipeline;
    if (!p?.in_pipeline) {
      const line = h('div', 'line');
      const add = h('button', 'cta');
      add.dataset.key = 'pipeline-add';
      add.innerHTML = funnelIconSvg(false);
      add.appendChild(h('span', '', 'Přidat do pipeline'));
      setBusy(add, busy);
      add.onclick = () => {
        if (store.busy('pipeline', id)) return;
        void write(store.addToPipeline(id), (d) => `Uložení do pipeline selhalo: ${d}`);
        focus('close-trigger');  // the button just became the stage bar
      };
      line.appendChild(add);
      const hide = dismissToggle(view, id, true);
      if (hide != null) line.appendChild(hide);
      grp.appendChild(line);
      return withError(grp);
    }

    const { data: stages, status } = store.list('stages');
    const current = stages?.find((s) => s.id === p.stage_id) ?? null;
    const closed = current?.is_terminal === true;
    const pipe = h('div', 'pipe');
    const head = h('div', 'line');
    head.appendChild(h('p', 'l4', 'Pipeline'));
    if (p.entered_stage_at != null) head.appendChild(h('span', 'meta num', `ve fázi od ${fmtDate(p.entered_stage_at)}`));
    const push = h('span', 'push line');
    const hide = dismissToggle(view, id, false);
    if (hide != null) push.appendChild(hide);
    const trigger = h('button', 'q edge');
    trigger.dataset.key = 'close-trigger';
    trigger.dataset.menu = 'close';
    trigger.setAttribute('aria-haspopup', 'menu');
    trigger.setAttribute('aria-expanded', String(closeOpen));
    if (closed && current != null && stages != null) {
      trigger.append(...stageName(current, stages));
      trigger.setAttribute('aria-label', `Uzavřeno: ${current.label}`);
    } else {
      trigger.appendChild(h('span', '', 'Uzavřít'));
    }
    trigger.insertAdjacentHTML('beforeend', lineIcon('down'));
    trigger.onclick = () => {
      closeOpen = !closeOpen;
      confirmRemove = false;
      repaint();
      if (closeOpen) focus('close-first');
    };
    trigger.onkeydown = (e) => {
      if (e.key !== 'Escape' || !closeOpen) return;
      closeOpen = false;
      repaint();
    };
    push.appendChild(trigger);
    head.appendChild(push);
    pipe.appendChild(head);

    if (stages == null) {
      pipe.appendChild(status === 'failed'
        ? stateLine('Fáze se nepodařilo načíst.', () => { store.load('stages', true); }, 'err')
        : h('p', 'meta', p.stage_label ?? 'Načítám fáze…'));
    } else if (stageControl(stages) === 'bar') {
      const open = stages.filter((s) => !s.is_terminal);
      const at = open.findIndex((s) => s.id === p.stage_id);
      const accent = current != null && !closed ? stageAccent(current).fg : null;
      const steps = h('div', 'steps');
      steps.setAttribute('role', 'group');
      steps.setAttribute('aria-label', 'Fáze v pipeline');
      open.forEach((stage, i) => {
        const step = h('button', 'stg');
        step.dataset.key = `stage-${stage.id}`;
        step.setAttribute('aria-pressed', String(stage.id === p.stage_id));
        setBusy(step, busy);
        const bar = h('span', 'stg-bar');
        if (accent != null && i <= at) bar.style.background = accent;
        const name = h('span', 'stg-name');
        name.append(h('span', 'code', stageBadge(stage, stages) ?? ''), h('span', '', stage.label));
        step.append(bar, name);
        step.onclick = () => { if (stage.id !== p.stage_id) move(id, stage.id); };
        steps.appendChild(step);
      });
      pipe.appendChild(steps);
    } else {
      // Today's dropdown over the open stages (closing a deal is the "Uzavřít" menu's), the audited move on change.
      const select = h('select', '');
      select.dataset.key = 'stage-select';
      select.title = 'Změnit fázi';
      select.setAttribute('aria-label', 'Fáze v pipeline');
      select.disabled = busy;
      for (const s of stages.filter((x) => !x.is_terminal || x.id === p.stage_id)) {
        const opt = h('option', '', s.label);
        opt.value = String(s.id);
        opt.selected = s.id === p.stage_id;
        select.appendChild(opt);
      }
      select.onchange = () => move(id, Number(select.value));
      pipe.appendChild(select);
    }

    if (closeOpen) pipe.appendChild(closeMenu(id, stages ?? [], p.stage_id));
    if (confirmRemove) pipe.appendChild(removeConfirm(id, stages ?? [], busy));
    grp.appendChild(pipe);
    return withError(grp);
  }

  function withError(grp: HTMLElement): HTMLElement {
    if (error != null) {
      const err = h('p', 'err', error);
      err.setAttribute('role', 'alert');
      grp.appendChild(err);
    }
    return grp;
  }

  /* "Uzavřít ▾": the closed stages, then removal behind the two-step confirm. */
  function closeMenu(id: number, stages: readonly PipelineStage[], currentId: number | null): HTMLElement {
    const menu = h('div', 'menu');
    menu.dataset.menu = 'close';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', 'Uzavřít obchod');
    const items: HTMLElement[] = [];
    for (const stage of stages.filter((s) => s.is_terminal)) {
      const item = h('button', 'mi');
      item.setAttribute('role', 'menuitemradio');
      item.setAttribute('aria-checked', String(stage.id === currentId));
      item.dataset.key = items.length === 0 ? 'close-first' : `close-${stage.id}`;
      item.append(...stageName(stage, stages));
      item.onclick = () => {
        closeOpen = false;
        if (stage.id !== currentId) move(id, stage.id);
        else repaint();
        focus('close-trigger');
      };
      items.push(item);
    }
    const remove = h('button', 'mi mi--danger', 'Odebrat z pipeline…');
    remove.setAttribute('role', 'menuitem');
    remove.dataset.key = items.length === 0 ? 'close-first' : 'close-remove';
    remove.onclick = () => {
      closeOpen = false;
      confirmRemove = true;
      repaint();
      focus('remove-yes');
    };
    items.push(remove);
    menu.append(...items.slice(0, -1));
    if (items.length > 1) {
      const sep = h('div', 'sep');
      sep.setAttribute('role', 'separator');
      menu.appendChild(sep);
    }
    menu.appendChild(remove);
    menu.onkeydown = (e) => {
      if (e.key === 'Escape') {
        closeOpen = false;
        repaint();
        focus('close-trigger');
        return;
      }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      e.preventDefault();
      const at = items.indexOf(e.target as HTMLElement);
      items[(at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
    };
    return menu;
  }

  /* Removal is the one pipeline action with no undo — the card's dates go and
   * re-adding restamps them — so it asks, and points at the closed stages. */
  function removeConfirm(id: number, stages: readonly PipelineStage[], busy: boolean): HTMLElement {
    const box = h('div', 'confirm');
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', REMOVE_QUESTION);
    box.append(h('p', 'confirm-q', REMOVE_QUESTION), h('p', 'confirm-d', removeWarning(stages)));
    const actions = h('div', 'row-actions');
    const yes = h('button', 'danger', busy ? 'Odebírám…' : 'Odebrat');
    yes.dataset.key = 'remove-yes';
    setBusy(yes, busy);
    yes.onclick = () => {
      if (store.busy('pipeline', id)) return;
      void write(store.removeFromPipeline(id), (d) => `Odebrání z pipeline selhalo: ${d}`)
        .then(() => { confirmRemove = false; repaint(); });
      focus('pipeline-add');  // the card is out: the confirm became the CTA
    };
    const no = h('button', 'q', 'Zrušit');
    no.dataset.key = 'remove-no';
    no.onclick = () => {
      confirmRemove = false;
      repaint();
      focus('close-trigger');
    };
    actions.append(yes, no);
    box.appendChild(actions);
    box.onkeydown = (e) => {
      if (e.key !== 'Escape') return;
      no.click();
    };
    return box;
  }

  function openPicker(kind: Picker): void {
    picker = picker === kind ? null : kind;
    pickerError = null;
    if (picker != null) store.load(kind, true);
    repaint();
  }

  /* "+" and "+N" both open the picker; Escape inside it closes it. */
  function chipRow<T>(items: readonly T[], kind: Picker, chip: (item: T) => HTMLElement, addLabel: string): HTMLElement {
    const row = h('div', 'chips');
    const { shown, more } = cutChips(items);
    for (const item of shown) {
      const c = chip(item);
      c.onclick = () => openPicker(kind);
      row.appendChild(c);
    }
    if (more > 0) {
      const rest = h('button', 'chip chip--more', `+${more}`);
      rest.dataset.key = `${kind}-more`;
      rest.setAttribute('aria-label', `Další (${more})`);
      rest.setAttribute('aria-expanded', String(picker === kind));
      rest.onclick = () => openPicker(kind);
      row.appendChild(rest);
    }
    // Escape on a chip or "+" shuts the open picker too (focus stays on the chip).
    row.onkeydown = (e) => {
      if (e.key !== 'Escape' || picker !== kind) return;
      picker = null;
      repaint();
    };
    const add = h('button', 'add');
    add.dataset.key = `${kind}-add`;
    add.innerHTML = lineIcon('plus');
    add.setAttribute('aria-label', addLabel);
    add.title = addLabel;
    add.setAttribute('aria-expanded', String(picker === kind));
    add.onclick = () => openPicker(kind);
    row.appendChild(add);
    return row;
  }

  function pickerBox(kind: Picker, label: string, fill: (box: HTMLElement) => void): HTMLElement {
    const box = h('div', 'picker');
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', label);
    fill(box);
    box.onkeydown = (e) => {
      if (e.key !== 'Escape') return;
      picker = null;
      repaint();
      focus(`${kind}-add`);
    };
    return box;
  }

  function collectionsBlock(view: PortalListing, id: number): HTMLElement {
    const grp = h('div', 'block');
    grp.appendChild(h('p', 'l4', 'Kolekce'));
    const list = store.list('collections');
    const saved = savedCollections(view.collection_ids, list.data);
    if (saved == null) {
      grp.appendChild(list.status === 'failed'
        ? stateLine('Kolekce se nepodařilo načíst.', () => { store.load('collections', true); }, 'err')
        : h('p', 'meta', 'Načítám kolekce…'));
    } else {
      grp.appendChild(chipRow(saved, 'collections', (c) => {
        const chip = h('button', 'chip');
        chip.dataset.key = `coll-chip-${c.id}`;
        chip.innerHTML = bookmarkIconSvg(true);
        chip.appendChild(h('span', '', c.name));
        chip.title = c.name;
        if (c.monitoring_enabled) {
          const bell = h('span', 'bell');
          bell.title = 'Sledovaná — upozorní na změny';
          bell.innerHTML = bellIconSvg();
          chip.appendChild(bell);
        }
        return chip;
      }, COLLECTION_SAVE_LABEL));
    }
    if (picker === 'collections') {
      grp.appendChild(pickerBox('collections', COLLECTION_SAVE_LABEL, (box) => buildCollectionChecklist(box, {
        collections: list.data,
        failed: list.status === 'failed',
        memberIds: new Set(view.collection_ids ?? []),
        busy: store.busy('collections', id),
        error: pickerError,
        retry: () => { store.load('collections', true); },
        toggle: (collectionId) => {
          if (store.busy('collections', id)) return;
          void write(store.toggleCollection(id, collectionId),
            (d, add) => `${add ? 'Uložení do kolekce' : 'Odebrání z kolekce'} se nepodařilo: ${d}`, true);
        },
      })));
    }
    return grp;
  }

  function tagChip(tag: Tag, tagName: 'span' | 'button' = 'span'): HTMLElement {
    const chip = h(tagName, 'chip chip--tag');
    const accent = stageAccent(tag);
    chip.style.color = accent.fg;
    chip.style.borderColor = accent.fg;
    chip.style.background = accent.soft;
    chip.appendChild(h('span', '', tag.name));
    chip.title = tag.name;
    return chip;
  }

  function tagsBlock(view: PortalListing, id: number): HTMLElement {
    const grp = h('div', 'block');
    grp.appendChild(h('p', 'l4', 'Štítky'));
    const list = store.list('tags');
    const ids = view.tag_ids ?? [];
    if (list.data == null && ids.length > 0) {
      grp.appendChild(list.status === 'failed'
        ? stateLine('Štítky se nepodařilo načíst.', () => { store.load('tags', true); }, 'err')
        : h('p', 'meta', 'Načítám štítky…'));
    } else {
      const attached = (list.data ?? []).filter((t) => ids.includes(t.id));
      grp.appendChild(chipRow(attached, 'tags', (t) => {
        const chip = tagChip(t, 'button');
        chip.dataset.key = `tag-chip-${t.id}`;
        return chip;
      }, 'Přidat štítek'));
    }
    if (picker === 'tags') {
      grp.appendChild(pickerBox('tags', 'Štítky', (box) => buildChecklist<Tag>(box, {
        items: list.data,
        failed: list.status === 'failed',
        text: { loading: 'Načítám štítky…', failed: 'Štítky se nepodařilo načíst.', empty: 'Zatím nemáte žádný štítek.' },
        retry: () => { store.load('tags', true); },
        key: (t) => `tag-${t.id}`,
        member: (t) => ids.includes(t.id),
        busy: store.busy('tags', id),
        label: (t) => [tagChip(t)],
        toggle: (t) => {
          if (store.busy('tags', id)) return;
          void write(store.toggleTag(id, t.id),
            (d, attach) => `${attach ? 'Přidání štítku' : 'Odebrání štítku'} se nepodařilo: ${d}`, true);
        },
        error: pickerError,
        // The app's listing page holds the assign list that also creates and edits tags.
        manage: APP_BASE_URL && listing != null ? {
          href: `${APP_BASE_URL}/listing/${encodeURIComponent(listing.source)}/${encodeURIComponent(listing.source_id)}`,
          text: 'Nový štítek v aplikaci →',
        } : null,
      })));
    }
    return grp;
  }

  function noteMeta(n: Note, who: string[] | null): HTMLElement {
    const meta = h('p', 'note-meta meta');
    const date = h('span', 'num', fmtDate(n.created_at) + (n.updated_at != null ? ' (upraveno)' : ''));
    date.title = n.updated_at != null ? `Napsáno ${n.created_at} · upraveno ${n.updated_at}` : n.created_at;
    if (!isMine(n)) {
      date.textContent = `od ${n.owner_email} · ${date.textContent}`;
      meta.appendChild(date);
      return meta;
    }
    meta.appendChild(date);
    if (n.shared) meta.appendChild(h('span', '', who != null && who.length === 0 ? 'Sdíleno · zatím to nikdo nevidí' : 'Sdíleno'));
    return meta;
  }

  function noteItem(n: Note, who: string[] | null): HTMLElement {
    if (!isMine(n)) {
      const item = h('div', 'note note--foreign');
      item.setAttribute('role', 'group');
      item.setAttribute('aria-label', `Poznámka od ${n.owner_email} · ${fmtDate(n.created_at)}`);
      item.append(h('p', 'note-body', n.body), noteMeta(n, who));
      return item;
    }
    const item = h('div', 'note');
    if (n.id === notes.editingId) {
      const ta = h('textarea', '');
      ta.rows = 2;
      ta.maxLength = 4000;
      ta.value = editDraft;
      ta.dataset.key = 'note-edit';
      ta.setAttribute('aria-label', 'Upravit poznámku');
      ta.disabled = notes.rowBusy;
      ta.addEventListener('input', () => { editDraft = ta.value; });
      const actions = h('div', 'row-actions');
      const save = h('button', 'cta', notes.rowBusy ? 'Ukládám…' : 'Uložit');
      save.dataset.key = `note-${n.id}-save`;
      setBusy(save, notes.rowBusy);
      save.onclick = () => { if (!notes.rowBusy) void saveEdit(n.id); };
      const cancel = h('button', 'q', 'Zrušit');
      cancel.dataset.key = `note-${n.id}-cancel`;
      cancel.onclick = () => { notes.editingId = null; repaint(); focus(`note-${n.id}-edit`); };
      actions.append(save, cancel);
      item.append(ta, actions);
      return item;
    }
    const top = h('div', 'note-top');
    top.appendChild(h('p', 'note-body', n.body));
    const edit = h('button', 'icon-btn');
    edit.dataset.key = `note-${n.id}-edit`;
    edit.innerHTML = lineIcon('pencil');
    edit.setAttribute('aria-label', 'Upravit poznámku');
    edit.title = 'Upravit poznámku';
    edit.onclick = () => {
      editDraft = n.body;
      notes.editingId = n.id;
      notes.confirmDeleteId = null;
      repaint();
      focus('note-edit');
    };
    const del = h('button', 'icon-btn icon-btn--danger');
    del.dataset.key = `note-${n.id}-del`;
    del.innerHTML = lineIcon('trash');
    del.setAttribute('aria-label', 'Smazat poznámku');
    del.title = 'Smazat poznámku';
    del.onclick = () => { notes.confirmDeleteId = n.id; notes.editingId = null; repaint(); };
    top.append(edit, del);
    item.appendChild(top);

    if (notes.confirmDeleteId === n.id) {
      const confirm = h('div', 'row-actions');
      confirm.appendChild(h('span', 'meta', 'Smazat tuto poznámku?'));
      const yes = h('button', 'danger', notes.rowBusy ? 'Mažu…' : 'Smazat');
      yes.dataset.key = `note-${n.id}-del-yes`;
      setBusy(yes, notes.rowBusy);
      yes.onclick = () => { if (!notes.rowBusy) void deleteNote(n.id); };
      const no = h('button', 'q', 'Zrušit');
      no.dataset.key = `note-${n.id}-del-no`;
      no.onclick = () => { notes.confirmDeleteId = null; repaint(); focus(`note-${n.id}-del`); };
      confirm.append(yes, no);
      item.appendChild(confirm);
      return item;
    }
    const meta = noteMeta(n, who);
    // Making private is always safe; sharing names who it reaches, once that is known.
    if (n.shared || (who != null && who.length > 0)) {
      const toggle = h('button', 'q', n.shared ? 'Nesdílet' : `Sdílet s ${(who ?? []).join(', ')}`);
      toggle.dataset.key = `note-${n.id}-share`;
      setBusy(toggle, notes.rowBusy);
      toggle.onclick = () => { void toggleShared(n); };
      meta.appendChild(toggle);
    }
    item.appendChild(meta);
    return item;
  }

  function composer(who: string[] | null): HTMLElement[] {
    const ta = h('textarea', '');
    ta.rows = 2;
    ta.maxLength = 4000;  // the server's CreateNoteIn cap, the app's textarea's
    ta.placeholder = 'Přidat poznámku…';
    ta.setAttribute('aria-label', 'Nová poznámka');
    ta.value = draft;
    ta.dataset.key = 'note';
    ta.disabled = notes.busy;
    ta.addEventListener('input', () => { draft = ta.value; });
    const out: HTMLElement[] = [ta];
    // Off by default (consent), offered only when someone would read it, and named.
    if (who != null && who.length > 0) {
      const share = h('label', 'share');
      const box = h('input', '');
      box.type = 'checkbox';
      box.checked = shareDraft;
      box.disabled = notes.busy;
      box.dataset.key = 'note-share';
      box.addEventListener('change', () => { shareDraft = box.checked; });
      share.append(box, h('span', '', `Sdílet s ${who.join(', ')}`));
      out.push(share);
    } else if (who == null && store.list('collections').status === 'failed') {
      out.push(stateLine('Sdílení se nepodařilo načíst.', () => { store.load('collections', true); }, 'err'));
    }
    const save = h('button', 'cta', notes.busy ? 'Ukládám…' : 'Uložit poznámku');
    save.dataset.key = 'note-save';
    setBusy(save, notes.busy);
    save.onclick = () => { if (!notes.busy) void addNote(); };
    out.push(save);
    return out;
  }

  function notesGroup(): HTMLElement {
    const grp = h('div', 'grp');
    const list = notes.list;
    const head = h('div', 'line');
    head.appendChild(h('p', 'l4', 'Poznámky'));
    if (list != null) head.appendChild(h('span', 'mono meta', String(list.length)));
    if (list != null && list.length > 0) {
      const all = h('button', 'q q--accent edge push', notes.expanded ? 'Sbalit' : 'Všechny');
      all.dataset.key = 'notes-toggle';
      all.setAttribute('aria-expanded', String(notes.expanded));
      all.onclick = () => { notes.expanded = !notes.expanded; repaint(); };
      head.appendChild(all);
    }
    grp.appendChild(head);
    const who = audience();

    if (notes.failed) {
      grp.appendChild(stateLine('Poznámky se nepodařilo načíst.', () => { if (propertyId != null) void loadNotes(propertyId); }, 'err'));
    } else if (list == null) {
      grp.appendChild(h('p', 'meta', 'Načítám poznámky…'));
    } else if (notes.expanded) {
      if (list.length > 0) {
        const all = h('div', 'notes');
        for (const n of list) all.appendChild(noteItem(n, who));
        grp.appendChild(all);
      }
    } else if (list.length > 0) {
      const latest = list[0];
      grp.append(h('p', 'note-latest note-body', latest.body), noteMeta(latest, who));
    }

    if (notes.expanded) {
      grp.append(...composer(who));
    } else if (!notes.failed) {
      const add = h('button', 'q note-add');
      add.dataset.key = 'note-add';
      add.innerHTML = lineIcon('plus');
      add.appendChild(h('span', '', 'Přidat poznámku'));
      add.onclick = () => { notes.expanded = true; repaint(); focus('note'); };
      grp.appendChild(add);
    }
    if (notes.error != null) {
      const err = h('p', 'err', notes.error);
      err.setAttribute('role', 'alert');
      grp.appendChild(err);
    }
    return grp;
  }

  /* The card, for a held ad with a property; null before attach (nothing to own yet). */
  function card(view: PortalListing): HTMLElement | null {
    const id = view.found ? view.property_id : null;
    if (id == null) return null;
    listing = view;
    const c = buildCard({ title: 'Moje', variant: 'mine' });
    const saved = h('div', 'grp saved');
    saved.appendChild(collectionsBlock(view, id));
    saved.appendChild(tagsBlock(view, id));
    c.body.append(pipelineGroup(view, id), saved, notesGroup());
    return c.section;
  }

  /* A click outside the open "Uzavřít" menu closes it. */
  function closeMenus(): void {
    if (!closeOpen) return;
    closeOpen = false;
    repaint();
  }

  return { reset, card, closeMenus };
}
