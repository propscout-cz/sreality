import type { BoardMeta, BoardRow } from '@/lib/brokerBoard';

/* One broker board row for a fixture: no firm, no contact, nothing followed; override what the test is about. */
export const boardRow = (broker_id: number, extra: Partial<BoardRow> = {}): BoardRow => ({
  broker_id,
  display_name: `Makléř ${broker_id}`,
  firm_id: null,
  firm_name: null,
  firm_domain: null,
  has_email: false,
  has_phone: false,
  properties: 1,
  n: 0,
  exited: 0,
  unknown: 0,
  share: null,
  rank: null,
  ...extra,
});

/* A board's metadata: 500 properties, 400 attributed, the market 10 of 40 within 30 days. */
export const BOARD_META: BoardMeta = {
  queried_at: '2026-10-29T12:00:00Z',
  cohort_properties: 500,
  attributed_properties: 400,
  unattributed_share: 0.2,
  market: { n: 40, exited: 10, share: 0.25 },
  unknown: 4,
  horizon_days: 30,
  clean_exits_since: '2026-09-08',
  margin_days: 7,
  min_n: 5,
  speed_refused: null,
  speed_refused_by: [],
  uncovered_portals: ['bazos', 'bezrealitky'],
};
