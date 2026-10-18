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

/* The keys scripts/verify_pipeline.py still emits. Historical rows from retired
 * checks (the dedup decision-engine ones) fall through to the humanizer below. */
const CHECK_LABELS: Record<string, string> = {
  llm_errors: 'LLM errors',
  llm_burn_rate: 'LLM burn rate',
  long_open_transaction: 'Oldest open transaction',
  db_saturation: 'DB saturation',
  worker_liveness: 'Worker liveness',
  dual_write_parity: 'Dual-write parity',
  property_maintenance: 'Property maintenance',
  broker_resolution_freshness: 'Broker resolution',
  ppm2_median_shift: 'Kč/m² median shift',
  ppm2_basis_floor_share: 'Kč/m² price floor',
  area_vs_usable_divergence: 'Area vs usable area',
  ppm2_measure_coverage: 'Kč/m² coverage',
  location_payload_shape_drift: 'Location payload shape',
  outbound_url_coverage: 'Portal URL coverage',
  outbound_url_parity: 'Portal URL parity',
  outbound_url_conformance: 'Portal URL conformance',
  sreality_image_template: 'Sreality image template',
  false_delist_share: 'False delist share',
};

/* The four per-m² plausibility checks emit a share or a ratio, and the number is
 * meaningless without its unit — "20.03" reads as a count. Every older check's value
 * is a bare count/rate whose meaning lives in its own message, so they stay bare. */
const CHECK_VALUE_UNIT: Record<string, string> = {
  /* The image canary's value is the measured width of the frame the CDN served \u2014
   * "1600" alone reads as a count of something. */
  sreality_image_template: 'px',
  ppm2_median_shift: '\u00d7',
  ppm2_basis_floor_share: '%',
  area_vs_usable_divergence: '%',
  ppm2_measure_coverage: '%',
  location_payload_shape_drift: '%',
  outbound_url_conformance: '%',
  false_delist_share: '%',
};

export function pipelineCheckLabel(key: string): string {
  return (
    CHECK_LABELS[key] ??
    key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

/* Every surviving check's `value` is a ratio / percentage / count / minutes whose
 * unit lives in the check itself, so we show the bare number (integers compact,
 * decimals to 2 dp). */
export function pipelineCheckValueLabel(value: number | null, checkKey?: string): string {
  if (value == null) return '—';
  const shown = Number.isInteger(value) ? fmtCount(value) : value.toFixed(2);
  return shown + (checkKey ? (CHECK_VALUE_UNIT[checkKey] ?? '') : '');
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
