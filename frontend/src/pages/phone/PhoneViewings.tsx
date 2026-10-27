/* Prohlídky — the Phone app's sixth tab (operator request 2026-10-23): every
 * viewing I booked, ONE DAY AT A TIME, in time order, each row opening its
 * property. A viewing is a note with a date (migration 666, lib/viewings), so
 * this tab only reads: a viewing is booked, moved and dropped in its property's
 * Poznámky section.
 *
 * THE DAY. Today on first open; the arrows and a sideways swipe step one day,
 * the title opens the date wheel, "Dnes" comes back, and the foot of a day jumps
 * to the nearest day that holds a viewing either way (an empty day offers the
 * next one). Day and scope live in the URL, replaced rather than pushed, so
 * opening a property and coming back lands on the same day.
 *
 * THE SCOPE. Nadcházející (today on; the default) or Vše. Upcoming never steps
 * before today; today's page keeps its earlier viewings, under a "now" rule.
 *
 * THE READS. GET /viewings (the caller's own, earliest first), the rows'
 * properties from `properties_public`, their covers from the hydration layer,
 * and the pipeline stage from the shared members read. */

import { Fragment, useEffect, useMemo, useRef, useState, type TouchEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { readFailed } from '@/components/ReadFailedMark';
import { SegmentedBar } from '@/components/controls';
import { PHOTO_DARK } from '@/components/phone/CardPhotoStrip';
import { feedLayout, feedPrice } from '@/components/phone/FeedCard';
import { usePipelineLook } from '@/components/phone/MarginMarks';
import { usePhoneHeaderAside } from '@/components/phone/PhoneShell';
import { RollEmpty } from '@/components/phone/RollParts';
import { BackIcon, CalendarIcon, ChevronIcon } from '@/components/phone/phoneIcons';
import { listViewings } from '@/lib/api';
import { pragueClock, pragueDayKey, shiftDay } from '@/lib/feed';
import { czPlural, fmtArea } from '@/lib/format';
import { CardHydrationProvider, useCardHydration } from '@/lib/hydration';
import { propertyPath } from '@/lib/listingUrl';
import { areaKindOf } from '@/lib/measure';
import { curationKeys, fetchPropertySummaries, type PropertySummary } from '@/lib/queries';
import type { Note } from '@/lib/types';
import {
  byDay,
  dayLine,
  dayTitle,
  neighbours,
  scoped,
  shortDay,
  viewingSlot,
  type ViewingScope,
} from '@/lib/viewings';

const NO_NOTES: Note[] = [];
const NO_SUMMARIES = new Map<number, PropertySummary>();
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const SWIPE_PX = 60;

const SCOPES: ReadonlyArray<{ value: ViewingScope; label: string }> = [
  { value: 'upcoming', label: 'Nadcházející' },
  { value: 'all', label: 'Vše' },
];

export const viewingsWord = (n: number): string => `${n} ${czPlural(n, 'prohlídka', 'prohlídky', 'prohlídek')}`;

const reducedMotion = (): boolean =>
  typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* The page's clock, a minute at a time: "today" and the now rule move on their own. */
function useMinuteClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export default function PhoneViewings() {
  const now = useMinuteClock();
  const todayKey = pragueDayKey(now);
  const [params, setParams] = useSearchParams();
  const scope: ViewingScope = params.get('scope') === 'all' ? 'all' : 'upcoming';
  const asked = params.get('day');
  const day = asked && DAY_RE.test(asked) && (scope === 'all' || asked >= todayKey) ? asked : todayKey;
  const canBack = scope === 'all' || day > todayKey;

  const go = (next: { day?: string; scope?: ViewingScope }) => {
    const p = new URLSearchParams(params);
    const sc = next.scope ?? scope;
    const d = sc === 'upcoming' && (next.day ?? day) < todayKey ? todayKey : (next.day ?? day);
    if (d === todayKey) p.delete('day');
    else p.set('day', d);
    if (sc === 'all') p.set('scope', 'all');
    else p.delete('scope');
    setParams(p, { replace: true });
  };
  const step = (by: number) => {
    if (by < 0 && !canBack) return;
    go({ day: shiftDay(day, by) });
  };

  const viewingsQ = useQuery({
    queryKey: curationKeys.viewings,
    queryFn: listViewings,
    staleTime: 30_000,
  });
  const all = viewingsQ.data?.data ?? NO_NOTES;
  const inScope = useMemo(() => scoped(all, scope, now), [all, scope, now]);
  const days = useMemo(() => byDay(inScope), [inScope]);
  const rows = days.get(day) ?? NO_NOTES;
  const { prev, next } = neighbours([...days.keys()], day);

  const propertyIds = useMemo(() => [...new Set(all.map((v) => v.property_id))].sort((a, b) => a - b), [all]);
  const summariesQ = useQuery({
    queryKey: [...curationKeys.viewings, 'properties', propertyIds],
    queryFn: ({ signal }) => fetchPropertySummaries(propertyIds, { signal }),
    enabled: propertyIds.length > 0,
    staleTime: 60_000,
  });
  const summaries = summariesQ.data ?? NO_SUMMARIES;
  const listingIds = useMemo(
    () => [...summaries.values()].map((s) => s.listing_id).filter((id): id is number => id != null),
    [summaries],
  );

  const loaded = viewingsQ.data !== undefined;
  const failed = readFailed(viewingsQ);
  usePhoneHeaderAside(loaded && !failed ? viewingsWord(inScope.length) : null);

  /* A sideways swipe flips the day: wide enough, and more across than down. */
  const touch = useRef<{ x: number; y: number } | null>(null);
  const onTouchStart = (e: TouchEvent) => {
    const t = e.touches[0];
    touch.current = t ? { x: t.clientX, y: t.clientY } : null;
  };
  const onTouchEnd = (e: TouchEvent) => {
    const start = touch.current;
    touch.current = null;
    const t = e.changedTouches[0];
    if (!start || !t) return;
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
    if (Math.abs(dx) < SWIPE_PX || Math.abs(dx) < 1.5 * Math.abs(dy)) return;
    step(dx < 0 ? 1 : -1);
  };

  /* The new day slides in from the side it was flipped towards. */
  const pageRef = useRef<HTMLDivElement>(null);
  const shownDay = useRef(day);
  useEffect(() => {
    if (shownDay.current === day) return;
    const dir = day > shownDay.current ? 1 : -1;
    shownDay.current = day;
    const el = pageRef.current;
    if (!el || reducedMotion() || typeof el.animate !== 'function') return;
    el.animate(
      [{ transform: `translateX(${dir * 28}px)`, opacity: 0 }, { transform: 'none', opacity: 1 }],
      { duration: 180, easing: 'cubic-bezier(.2,.7,.3,1)' },
    );
  }, [day]);

  const title = dayTitle(day, todayKey);
  const relativeTitle = title === 'Dnes' || title === 'Zítra' || title === 'Včera';
  const countLine = loaded ? (rows.length ? viewingsWord(rows.length) : 'žádná prohlídka') : '';

  return (
    <div>
      <div className="sticky top-14 z-20 border-b border-[var(--color-rule)] bg-[var(--color-paper)]">
        <div className="flex items-center gap-2 px-4 pt-3">
          <SegmentedBar
            tone="phone"
            label="Které prohlídky"
            options={SCOPES}
            value={scope}
            onChange={(v) => go({ scope: v })}
          />
          {day !== todayKey && (
            <button
              type="button"
              onClick={() => go({ day: todayKey })}
              className="ml-auto inline-flex h-8 items-center rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] px-3 text-[13px] text-[var(--color-ink-2)] active:bg-[var(--color-inset)]"
            >
              Dnes
            </button>
          )}
        </div>
        <div className="flex h-16 items-center gap-1 px-1">
          <button
            type="button"
            aria-label="Předchozí den"
            onClick={() => step(-1)}
            disabled={!canBack}
            className="flex h-11 w-11 shrink-0 items-center justify-center text-[var(--color-copper)] disabled:text-[var(--color-ink-4)]"
          >
            <BackIcon className="h-[22px] w-[22px]" />
          </button>
          {/* The title is the date wheel's face: the transparent field over it opens the picker. */}
          <label className="relative flex min-w-0 flex-1 flex-col items-center justify-center text-center">
            <span className="truncate font-display text-[20px] font-medium leading-tight tracking-[-0.01em] text-[var(--color-ink)] first-letter:uppercase">
              {title}
            </span>
            <span className="truncate font-mono text-[11.5px] tabular-nums text-[var(--color-ink-3)]">
              {[relativeTitle ? dayLine(day) : null, countLine].filter(Boolean).join(' · ')}
            </span>
            <input
              type="date"
              aria-label="Vybrat den"
              value={day}
              min={scope === 'upcoming' ? todayKey : undefined}
              onChange={(e) => {
                const picked = e.target.value;
                if (DAY_RE.test(picked)) go({ day: picked });
              }}
              className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
            />
          </label>
          <button
            type="button"
            aria-label="Další den"
            onClick={() => step(1)}
            className="flex h-11 w-11 shrink-0 items-center justify-center text-[var(--color-copper)]"
          >
            <ChevronIcon className="h-[22px] w-[22px]" />
          </button>
        </div>
      </div>

      <div ref={pageRef} onTouchStart={onTouchStart} onTouchEnd={onTouchEnd} className="min-h-[60dvh]">
        {failed ? (
          <div className="pt-4">
            <RollEmpty
              title="Prohlídky se nepodařilo načíst."
              detail="Spojení nebo databáze teď neodpověděly. Zkuste to znovu."
              action={{ label: 'Zkusit znovu', onClick: () => void viewingsQ.refetch() }}
            />
          </div>
        ) : !loaded ? (
          <DaySkeleton />
        ) : rows.length === 0 ? (
          <EmptyDay
            hasAny={all.length > 0}
            next={next}
            prev={scope === 'all' ? prev : null}
            todayKey={todayKey}
            onGo={(d) => go({ day: d })}
          />
        ) : (
          <CardHydrationProvider listingIds={listingIds} renders={{ covers: true }}>
            <DayList rows={rows} summaries={summaries} now={now} isToday={day === todayKey} />
            <DayJumps prev={canBack ? prev : null} next={next} todayKey={todayKey} onGo={(d) => go({ day: d })} />
          </CardHydrationProvider>
        )}
      </div>
    </div>
  );
}

function DayList({
  rows,
  summaries,
  now,
  isToday,
}: {
  rows: readonly Note[];
  summaries: Map<number, PropertySummary>;
  now: Date;
  isToday: boolean;
}) {
  /* Today's "now" rule sits before the first viewing still ahead (after the last when none is). */
  const ahead = rows.findIndex((v) => new Date(v.viewing_at!) > now);
  const nowAt = isToday ? (ahead === -1 ? rows.length : ahead) : -1;
  return (
    <ol>
      {rows.map((v, i) => (
        <Fragment key={v.id}>
          {i === nowAt && <NowRule now={now} />}
          <ViewingRow note={v} summary={summaries.get(v.property_id) ?? null} past={isToday && i < nowAt} />
        </Fragment>
      ))}
      {nowAt === rows.length && <NowRule now={now} />}
    </ol>
  );
}

function NowRule({ now }: { now: Date }) {
  return (
    <li aria-hidden className="flex items-center gap-3 py-1 pl-4 pr-4">
      <span className="w-12 shrink-0 text-right font-mono text-[11px] tabular-nums text-[var(--color-copper)]">
        {pragueClock(now)}
      </span>
      <span className="h-px flex-1 bg-[var(--color-copper)]" />
    </li>
  );
}

function ViewingRow({
  note,
  summary,
  past,
}: {
  note: Note;
  summary: PropertySummary | null;
  past: boolean;
}) {
  const { coverFor } = useCardHydration();
  const src = summary?.listing_id != null ? coverFor(summary.listing_id) : null;
  const [broken, setBroken] = useState<string | null>(null);
  const { member, badge } = usePipelineLook(note.property_id);

  const { time } = viewingSlot(note.viewing_at!);
  const place = summary?.display_label?.trim() || null;
  const price = summary ? feedPrice(summary) : null;
  const facts = summary
    ? [
        feedLayout(summary),
        summary.area_m2 != null ? fmtArea(summary.area_m2, areaKindOf(summary.category_main)) : null,
        price?.text ? `${price.text}${price.perMonth ? ' /měs' : ''}` : null,
      ].filter(Boolean)
    : [];

  return (
    <li className="relative flex items-start gap-3 border-b border-[var(--color-rule)] py-3 pl-4 pr-4">
      <time
        dateTime={note.viewing_at!}
        className={`w-12 shrink-0 pt-0.5 text-right font-mono text-[15px] font-medium tabular-nums ${past ? 'text-[var(--color-ink-3)]' : 'text-[var(--color-ink)]'}`}
      >
        {time}
      </time>
      <div className="relative isolate aspect-[4/3] w-[76px] shrink-0 overflow-hidden rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)]">
        {src && broken !== src && (
          <img
            src={src}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setBroken(src)}
            className={`block h-full w-full object-cover ${PHOTO_DARK} ${past ? 'opacity-70' : ''}`}
          />
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <Link
          to={propertyPath(note.property_id)}
          aria-label={`${time} · ${[place ?? 'Lokalita neurčena', ...facts].join(', ')}`}
          className="stretched-link line-clamp-2 font-display text-[16px] font-medium leading-[1.25] text-[var(--color-ink)]"
        >
          {place ?? <span className="font-sans text-[14px] text-[var(--color-ink-3)]">Lokalita neurčena</span>}
        </Link>
        {facts.length > 0 && <p className="truncate text-[13px] text-[var(--color-ink-2)]">{facts.join(' · ')}</p>}
        {(member || (summary && !summary.is_active)) && (
          <p className="flex flex-wrap items-center gap-x-2 font-mono text-[11px] tabular-nums text-[var(--color-ink-3)]">
            {member && (
              <span className="inline-flex items-center gap-1">
                <span className="rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] px-1 leading-[1.4]">
                  {badge}
                </span>
                <span className="font-sans">{member.stage_label}</span>
              </span>
            )}
            {summary && !summary.is_active && <span>neaktivní</span>}
          </p>
        )}
        {note.body && (
          <p className="mt-1 line-clamp-2 whitespace-pre-line text-[13px] leading-snug text-[var(--color-ink-2)]">
            {note.body}
          </p>
        )}
      </div>
    </li>
  );
}

function DayJumps({
  prev,
  next,
  todayKey,
  onGo,
}: {
  prev: string | null;
  next: string | null;
  todayKey: string;
  onGo: (day: string) => void;
}) {
  if (!prev && !next) return null;
  const JUMP = 'flex h-11 items-center gap-1 px-2 text-[13px] text-[var(--color-copper)]';
  return (
    <nav aria-label="Dny s prohlídkou" className="flex items-center justify-between px-2 pt-1">
      {prev ? (
        <button type="button" onClick={() => onGo(prev)} aria-label={`Předchozí prohlídka: ${shortDay(prev, todayKey)}`} className={JUMP}>
          <BackIcon className="h-4 w-4" />
          {shortDay(prev, todayKey)}
        </button>
      ) : (
        <span />
      )}
      {next && (
        <button type="button" onClick={() => onGo(next)} aria-label={`Další prohlídka: ${shortDay(next, todayKey)}`} className={JUMP}>
          {shortDay(next, todayKey)}
          <ChevronIcon className="h-4 w-4" />
        </button>
      )}
    </nav>
  );
}

function EmptyDay({
  hasAny,
  next,
  prev,
  todayKey,
  onGo,
}: {
  hasAny: boolean;
  next: string | null;
  prev: string | null;
  todayKey: string;
  onGo: (day: string) => void;
}) {
  return (
    <div className="flex flex-col items-center px-6 pb-6 pt-10 text-center">
      <CalendarIcon className="h-9 w-9 text-[var(--color-ink-4)]" />
      <p className="mt-3 font-display text-[18px] text-[var(--color-ink)]">Žádná prohlídka</p>
      <p className="mt-1 max-w-[18rem] text-[13px] text-[var(--color-ink-3)]">
        {hasAny
          ? next
            ? 'Tento den nic. Nejbližší prohlídka je níže.'
            : 'Tento den nic a dál už žádná naplánovaná.'
          : 'Prohlídku naplánujete na stránce nemovitosti v sekci Poznámky.'}
      </p>
      {next && (
        <button
          type="button"
          onClick={() => onGo(next)}
          className="mt-5 inline-flex h-11 items-center gap-1.5 rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-4 text-[14px] font-medium text-[var(--color-on-copper)] active:bg-[var(--color-copper-2)]"
        >
          Další prohlídka · {shortDay(next, todayKey)}
          <ChevronIcon className="h-4 w-4" />
        </button>
      )}
      {prev && (
        <button
          type="button"
          onClick={() => onGo(prev)}
          className="mt-2 inline-flex h-11 items-center gap-1 px-3 text-[13px] text-[var(--color-copper)]"
        >
          <BackIcon className="h-4 w-4" />
          Předchozí · {shortDay(prev, todayKey)}
        </button>
      )}
    </div>
  );
}

function DaySkeleton() {
  return (
    <>
      <p className="sr-only" role="status">
        Načítám…
      </p>
      {[0, 1].map((i) => (
        <div key={i} aria-hidden className="flex items-start gap-3 border-b border-[var(--color-rule)] py-3 pl-4 pr-4">
          <div className="mt-1 h-4 w-12 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          <div className="aspect-[4/3] w-[76px] animate-pulse rounded-[var(--radius-sm)] bg-[var(--color-inset)] motion-reduce:animate-none" />
          <div className="flex flex-1 flex-col gap-2 pt-1">
            <div className="h-4 w-40 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
            <div className="h-3.5 w-28 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          </div>
        </div>
      ))}
    </>
  );
}
