import { describe, expect, it } from 'vitest';
import {
  hasUnacknowledgedIncident,
  normalizePipelineStatus,
  pipelineCheckLabel,
  pipelineCheckStatus,
  pipelineChecksBadgeText,
  pipelineCheckValueLabel,
  sortPipelineChecks,
  summarizePipelineChecks,
} from './pipelineChecks';
import { fmtCount } from './format';
import type { PipelineCheckIncident, PipelineCheckRow } from './queries';

function check(
  check_key: string,
  status: string,
  value: number | null = null,
  effective_status: string | null = null,
  incidents: PipelineCheckIncident[] = [],
): PipelineCheckRow {
  return {
    check_key,
    status,
    value,
    details: null,
    run_at: '2026-07-05T13:34:00Z',
    label: null,
    unit: null,
    effective_status,
    incidents,
  };
}

/* A newest stored Problem the view reads Not measured: older than its staleness budget. */
const stale = (key: string) => check(key, 'fail', 3, 'unknown');

function incident(over: Partial<PipelineCheckIncident> = {}): PipelineCheckIncident {
  return {
    id: 1,
    subject: 'sreality',
    first_seen_at: '2026-10-18T06:40:00Z',
    resolved_at: null,
    acknowledged_at: null,
    notice_count: 1,
    ...over,
  };
}

describe('pipelineCheckLabel', () => {
  it('uses the registry label the view serves', () => {
    expect(pipelineCheckLabel({ check_key: 'llm_errors', label: 'LLM errors' })).toBe('LLM errors');
    expect(pipelineCheckLabel({ check_key: 'ppm2_basis_floor_share', label: 'K\u010d/m\u00b2 price floor' }))
      .toBe('K\u010d/m\u00b2 price floor');
  });
  it('falls back to the key when the registry has no label for the row', () => {
    expect(pipelineCheckLabel({ check_key: 'some_new_check', label: null })).toBe('some_new_check');
    expect(pipelineCheckLabel({ check_key: 'street_debt', label: '' })).toBe('street_debt');
  });
});

describe('pipelineCheckValueLabel', () => {
  it('renders decimals to 2 dp', () => {
    expect(pipelineCheckValueLabel(0.9583)).toBe('0.96');
    expect(pipelineCheckValueLabel(1351.76)).toBe('1351.76');
  });
  it('renders integers compactly', () => {
    expect(pipelineCheckValueLabel(35_479)).toBe(fmtCount(35_479));
  });
  it('renders an em-dash for a null value, whatever the unit', () => {
    expect(pipelineCheckValueLabel(null)).toBe('\u2014');
    expect(pipelineCheckValueLabel(null, '\u00d7')).toBe('\u2014');
  });
  it("suffixes the registry's unit and leaves a unit-less check bare", () => {
    // "20.03" reads as a count; the share it actually is has to say so.
    expect(pipelineCheckValueLabel(20.03, '%')).toBe('20.03%');
    expect(pipelineCheckValueLabel(7.455, '\u00d7')).toBe('7.46\u00d7');
    expect(pipelineCheckValueLabel(1440, 'px')).toBe(fmtCount(1440) + 'px');
    expect(pipelineCheckValueLabel(1351.76, '')).toBe('1351.76');
    expect(pipelineCheckValueLabel(1351.76, null)).toBe('1351.76');
  });
});

describe('normalizePipelineStatus', () => {
  it('passes a measured ok/warn/fail through', () => {
    expect(normalizePipelineStatus('fail')).toBe('fail');
    expect(normalizePipelineStatus('warn')).toBe('warn');
    expect(normalizePipelineStatus('ok')).toBe('ok');
  });
  it('reads everything else as not measured, never as ok', () => {
    expect(normalizePipelineStatus('unknown')).toBe('unknown');
    expect(normalizePipelineStatus('mystery')).toBe('unknown');
    // A registered check with no result yet is served with a null status (migration 619).
    expect(normalizePipelineStatus(null)).toBe('unknown');
    expect(normalizePipelineStatus('')).toBe('unknown');
  });
});

describe('pipelineCheckStatus', () => {
  it("judges the view's effective status, not the stored one", () => {
    expect(pipelineCheckStatus(stale('a'))).toBe('unknown');
    expect(pipelineCheckStatus(check('b', 'ok', 1, 'ok'))).toBe('ok');
  });
  it('falls back to the stored status only when the row carries no judgement', () => {
    expect(pipelineCheckStatus(check('a', 'warn'))).toBe('warn');
    expect(pipelineCheckStatus({ status: null, effective_status: null })).toBe('unknown');
  });
});

describe('hasUnacknowledgedIncident', () => {
  it('is true only for an open incident nobody acknowledged', () => {
    expect(hasUnacknowledgedIncident(check('a', 'fail', 1, 'fail', [incident()]))).toBe(true);
    expect(hasUnacknowledgedIncident(check('a', 'fail'))).toBe(false);
    const acked = incident({ acknowledged_at: '2026-10-18T07:00:00Z' });
    const closed = incident({ id: 2, resolved_at: '2026-10-18T08:00:00Z' });
    expect(hasUnacknowledgedIncident(check('a', 'ok', 0, 'ok', [acked, closed]))).toBe(false);
    expect(
      hasUnacknowledgedIncident(check('a', 'fail', 1, 'fail', [acked, incident({ id: 3 })])),
    ).toBe(true);
  });
});

describe('summarizePipelineChecks', () => {
  it('counts a stale stored Problem as not measured, never as a live one', () => {
    const s = summarizePipelineChecks([stale('a'), check('b', 'ok', 0, 'ok')]);
    expect(s).toEqual({ ok: 1, warn: 0, fail: 0, unknown: 1, worst: 'unknown' });
  });
  it('counts each band and reports the worst', () => {
    const s = summarizePipelineChecks([
      check('a', 'ok'),
      check('b', 'warn'),
      check('c', 'fail'),
      check('d', 'ok'),
      check('e', 'unknown'),
    ]);
    expect(s).toEqual({ ok: 2, warn: 1, fail: 1, unknown: 1, worst: 'fail' });
  });
  it('worst is warn when there are no fails', () => {
    expect(summarizePipelineChecks([check('a', 'unknown'), check('b', 'warn')]).worst).toBe('warn');
  });
  it('an unmeasured check outranks ok, so the set never reads all clear', () => {
    const s = summarizePipelineChecks([check('a', 'ok'), check('b', 'mystery')]);
    expect(s).toEqual({ ok: 1, warn: 0, fail: 0, unknown: 1, worst: 'unknown' });
  });
  it('worst is ok only when every check measured ok', () => {
    expect(summarizePipelineChecks([check('a', 'ok'), check('b', 'ok')]).worst).toBe('ok');
  });
  it('an empty set measured nothing, so its worst is unknown', () => {
    expect(summarizePipelineChecks([]).worst).toBe('unknown');
  });
});

describe('pipelineChecksBadgeText', () => {
  it("names the worst band in the page's words", () => {
    expect(pipelineChecksBadgeText(summarizePipelineChecks([check('a', 'fail'), check('b', 'fail')])))
      .toBe('2 problem');
    expect(pipelineChecksBadgeText(summarizePipelineChecks([check('a', 'warn'), check('b', 'ok')])))
      .toBe('1 watch');
    expect(pipelineChecksBadgeText(summarizePipelineChecks([stale('a'), check('b', 'ok')])))
      .toBe('1 not measured');
    expect(pipelineChecksBadgeText(summarizePipelineChecks([check('a', 'ok')]))).toBe('OK');
  });
});

describe('sortPipelineChecks', () => {
  it('ranks a stale stored Problem with the unmeasured rows, below a live one', () => {
    // keys chosen so the stored status alone would put the stale row first
    const sorted = sortPipelineChecks([check('ok', 'ok'), stale('a_stale'), check('z_live', 'fail')]);
    expect(sorted.map((c) => c.check_key)).toEqual(['z_live', 'a_stale', 'ok']);
  });
  it('orders fail, then warn, then not measured, then ok, stable by key', () => {
    const sorted = sortPipelineChecks([
      check('zeta', 'ok'),
      check('alpha', 'ok'),
      check('epsilon', 'mystery'),
      check('beta', 'fail'),
      check('delta', 'unknown'),
      check('gamma', 'warn'),
    ]);
    expect(sorted.map((c) => c.check_key)).toEqual([
      'beta', 'gamma', 'delta', 'epsilon', 'alpha', 'zeta',
    ]);
  });
});
