import type { PipelineCheckRow } from './queries';
import { fmtCount } from './format';

/* Presentation + rollup helpers for the pipeline verification checks
 * (migration 274, pipeline_checks_public). One row per registered check (migration
 * 619), stamped ok/warn/fail when measured and unknown when not, and judged by the view
 * (`effective_status`), so these helpers only humanize the key/value and roll the view's
 * statuses up for the Health panel's header badge. */

export type PipelineCheckStatus = 'ok' | 'warn' | 'fail' | 'unknown';

/* The page's words for the four statuses (GLOSSARY.md § Health). */
export const PIPELINE_STATUS_WORD: Record<PipelineCheckStatus, string> = {
  ok: 'OK',
  warn: 'Watch',
  fail: 'Problem',
  unknown: 'Not measured',
};

/* The registry (health_checks, served by the view) names every check; a row without a
 * label is served for a key the registry does not know, so its key stands in. */
export function pipelineCheckLabel(row: Pick<PipelineCheckRow, 'check_key' | 'label'>): string {
  return row.label || row.check_key;
}

/* A value with the unit the registry declares for it ('%', '×', 'h', 'px', ...); integers
 * compact, decimals to 2 dp, an em-dash when nothing was measured. */
export function pipelineCheckValueLabel(value: number | null, unit?: string | null): string {
  if (value == null) return '—';
  const shown = Number.isInteger(value) ? fmtCount(value) : value.toFixed(2);
  return shown + (unit ?? '');
}

/* Only a measured ok/warn/fail passes through. Anything else (unknown, the null status of
 * a registered check with no result yet, a future word) is Not measured, never ok. */
export function normalizePipelineStatus(status: string | null | undefined): PipelineCheckStatus {
  return status === 'ok' || status === 'warn' || status === 'fail' ? status : 'unknown';
}

/* What a row shows: the view's judgement, which reads a stored result older than the key's
 * staleness budget as Not measured; the stored status only when the row carries none. */
export function pipelineCheckStatus(
  row: Pick<PipelineCheckRow, 'status' | 'effective_status'>,
): PipelineCheckStatus {
  return normalizePipelineStatus(row.effective_status ?? row.status);
}

/* Acknowledge has something to stop only while an incident is open and unacknowledged. */
export function hasUnacknowledgedIncident(row: Pick<PipelineCheckRow, 'incidents'>): boolean {
  return (row.incidents ?? []).some((i) => i.resolved_at == null && i.acknowledged_at == null);
}

export interface PipelineChecksSummary {
  ok: number;
  warn: number;
  fail: number;
  unknown: number;
  worst: PipelineCheckStatus;
}

// Worst first: Problem, Watch, Not measured, OK.
const WORST_FIRST: readonly PipelineCheckStatus[] = ['fail', 'warn', 'unknown', 'ok'];

/* An empty set measured nothing, so its worst is unknown rather than ok. */
export function summarizePipelineChecks(rows: PipelineCheckRow[]): PipelineChecksSummary {
  const acc: Record<PipelineCheckStatus, number> = { ok: 0, warn: 0, fail: 0, unknown: 0 };
  for (const r of rows) acc[pipelineCheckStatus(r)] += 1;
  const worst = WORST_FIRST.find((s) => acc[s] > 0) ?? 'unknown';
  return { ...acc, worst };
}

/* The header badge in the page's words: the worst band with its count, or OK. */
export function pipelineChecksBadgeText(s: PipelineChecksSummary): string {
  return s.worst === 'ok' ? 'OK' : `${s[s.worst]} ${PIPELINE_STATUS_WORD[s.worst].toLowerCase()}`;
}

// Worst first, stable by check_key inside a status band.
export function sortPipelineChecks(rows: PipelineCheckRow[]): PipelineCheckRow[] {
  const rank = (r: PipelineCheckRow) => WORST_FIRST.indexOf(pipelineCheckStatus(r));
  return [...rows].sort((a, b) => rank(a) - rank(b) || a.check_key.localeCompare(b.check_key));
}
