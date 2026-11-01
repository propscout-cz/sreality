import { describe, expect, it } from 'vitest';
import { buildInput, citedIds, fieldsOf, fmtRunCost, isLive, pollMs, stepsByAttempt, toTrace, waitingNote, type AgentRunStep } from './agentRuns';

const step = (over: Partial<AgentRunStep>): AgentRunStep => ({
  attempt: 1, n: 1, kind: 'tool', name: 'search_properties', summary: '{"data":[]}', input: { town: 'Brno' }, text: null,
  cost_usd: 0, duration_ms: 12, created_at: '2026-11-01T10:00:00Z', ...over,
});

describe('isLive', () => {
  it('holds only while the worker may still act', () => {
    expect(['queued', 'running', 'succeeded', 'failed', 'cancelled'].filter((s) => isLive(s))).toEqual(['queued', 'running']);
  });
});

describe('pollMs', () => {
  it('reads often while a run can move, rarely while the queue holds it, and not once none is live', () => {
    expect(pollMs(['running', 'queued'], 'dark')).toBe(3000);
    expect(pollMs(['queued'], 'open')).toBe(3000);
    expect(pollMs(['queued'], undefined)).toBe(3000);
    expect(pollMs(['queued'], 'dark')).toBe(30_000);
    expect(pollMs(['queued', 'succeeded'], 'parked')).toBe(30_000);
    expect(pollMs(['succeeded', 'failed'], 'open')).toBe(false);
    expect(pollMs([], 'open')).toBe(false);
  });
});

describe('fmtRunCost', () => {
  it('keeps a model call\'s fourth decimal and reads nothing spent as $0.00', () => {
    expect([fmtRunCost(0), fmtRunCost(0.0123), fmtRunCost(1.2345)]).toEqual(['$0.00', '$0.0123', '$1.23']);
  });
});

describe('toTrace', () => {
  it('maps a model call to reasoning with its words, and a tool call to its name and arguments', () => {
    const [model, tool] = toTrace([
      step({ n: 1, kind: 'model', name: 'claude-opus-5-5', text: ['One.', 'Two.'], input: { max_tokens: 9 }, cost_usd: 0.0123 }),
      step({ n: 2 }),
    ]).steps;
    expect(model).toMatchObject({ kind: 'reasoning', n: 1 });
    expect(model.output_summary).toEqual({ text: 'One.\n\nTwo.', model: 'claude-opus-5-5', cost: '$0.0123' });
    expect(tool).toMatchObject({ kind: 'tool_call', tool: 'search_properties', input: { arguments: { town: 'Brno' } } });
    expect(tool.output_summary).toEqual({ result: '{"data":[]}' });
  });

  it('keeps a tool argument that looks like money out of the formatter: no Kč the arguments did not say', () => {
    const [tool] = toTrace([step({ input: { price_per_m2: 90000 } })]).steps;
    expect(Object.keys((tool as { input: Record<string, unknown> }).input)).toEqual(['arguments']);
  });

  it('names a file step by the file', () => {
    const [file] = toTrace([step({ kind: 'file', name: 'file_1', summary: '{"name":"a.xlsx"}' })]).steps;
    expect(file).toMatchObject({ kind: 'computation', label: 'file file_1' });
  });
});

describe('stepsByAttempt', () => {
  it('keeps a retry apart, since n starts over with each attempt', () => {
    const grouped = stepsByAttempt([step({ attempt: 1, n: 1 }), step({ attempt: 1, n: 2 }), step({ attempt: 2, n: 1 })]);
    expect(grouped.map(([attempt, steps]) => [attempt, steps.length])).toEqual([[1, 2], [2, 1]]);
  });
});

describe('citedIds', () => {
  it('passes only positive whole numbers, whatever the row holds', () => {
    expect(citedIds([12, 0, -3, 1.5, 'javascript:alert(1)', '7', null, 99] as unknown as number[])).toEqual([12, 99]);
    expect(citedIds(undefined)).toEqual([]);
  });
});

describe('waitingNote', () => {
  const row = { status: 'queued' as const, attempts: 1, cancel_requested_at: null };
  it('says why a queued run waits, and that waiting never fails it', () => {
    expect(waitingNote(row, 'dark')).toMatch(/switched off/);
    expect(waitingNote(row, 'parked')).toMatch(/paused/);
    expect(waitingNote(row, 'open')).toMatch(/free slot/);
    expect(waitingNote(row, undefined)).toMatch(/queued/i);
  });
  it('says a stop is on its way and that a retry began again', () => {
    expect(waitingNote({ ...row, status: 'running' as const, cancel_requested_at: 'x' as string | null }, 'open')).toMatch(/next step/);
    expect(waitingNote({ ...row, status: 'running' as const, attempts: 2 }, 'open')).toMatch(/interrupted/);
    expect(waitingNote({ ...row, status: 'running' as const }, 'open')).toBeNull();
    expect(waitingNote({ ...row, status: 'succeeded' as const }, 'dark')).toBeNull();
  });
});

const SCHEMA = {
  type: 'object',
  required: ['town', 'budget'],
  properties: {
    town: { type: 'string', description: 'Where.' },
    budget: { type: 'integer' },
    yield_min: { type: 'number' },
    layout: { type: 'string', enum: ['2+1', '3+kk'] },
    new_only: { type: 'boolean' },
    districts: { type: 'array', items: { type: 'integer' } },
    words: { type: 'array', items: { type: 'string' } },
    band: { type: 'object', properties: { low: { type: 'number' } } },
  },
};

describe('fieldsOf + buildInput', () => {
  const fields = fieldsOf(SCHEMA);

  it('turns the top level of an input schema into one field each, required first as declared', () => {
    expect(fields.map((f) => [f.name, f.kind, f.required])).toEqual([
      ['town', 'text', true], ['budget', 'integer', true], ['yield_min', 'number', false], ['layout', 'choice', false],
      ['new_only', 'boolean', false], ['districts', 'list', false], ['words', 'list', false], ['band', 'json', false],
    ]);
    expect(fields[3].options).toEqual(['2+1', '3+kk']);
    expect(fields[0].description).toBe('Where.');
  });

  it('reads no field from a schema with no properties', () => {
    expect(fieldsOf({ type: 'object' })).toEqual([]);
  });

  it('types each value and leaves an empty optional out', () => {
    const { input, errors } = buildInput(fields, {
      town: ' Brno ', budget: '5000000', yield_min: '4.5', layout: '3+kk', new_only: true,
      districts: '554791, 582786', words: 'a, b', band: '{"low": 1}',
    });
    expect(errors).toEqual({});
    expect(input).toEqual({
      town: 'Brno', budget: 5000000, yield_min: 4.5, layout: '3+kk', new_only: true,
      districts: [554791, 582786], words: ['a', 'b'], band: { low: 1 },
    });
    expect(buildInput(fields, { town: 'Brno', budget: '1' }).input).toEqual({ town: 'Brno', budget: 1 });
  });

  it('says what is wrong with each field in words, sending nothing', () => {
    const { errors } = buildInput(fields, { budget: '1.5', yield_min: 'x', districts: '1, two', band: '{nope' });
    expect(errors).toEqual({
      town: 'Required.',
      budget: 'A whole number.',
      yield_min: 'A number.',
      districts: 'Whole numbers, separated by commas.',
      band: 'Not valid JSON.',
    });
  });
});
