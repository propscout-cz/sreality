/* The Stats sample lens's name and its URL guard, apart from lib/stats so the
 * Browse URL adapter (lib/browseState, on every first visit) reads `market=`
 * without pulling the stats module in (scripts/bundle-budget.mjs). */
export type StatsMarket = 'filter' | 'sold' | 'all';
export const isStatsMarket = (v: string | null): v is StatsMarket => v === 'filter' || v === 'sold' || v === 'all';
